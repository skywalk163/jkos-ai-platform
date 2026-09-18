"""极快AI操作系统 - 智能中枢网关（M16）

HarnessGateway 是 JKOS 对外访问 harness 的唯一入口：会话管理 + 事件流。

线程模型（重要）：官方 SDK 的 run() 是同步阻塞调用，而 JKOS 是 async FastAPI。
因此阻塞调用统一经 asyncio.to_thread 卸载到工作线程，事件由 on_notification
回调跨线程投递，必须走 loop.call_soon_threadsafe 才能安全进入事件循环队列。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from jkos_core.auth.context import TenantContext
from jkos_core.harness.config import HarnessConfig
from jkos_core.harness.runtime import HarnessUnavailable, SidecarManager

logger = logging.getLogger("dsh.harness")

# 事件流终止哨兵（消费端据此结束迭代）
_STREAM_END = object()

_UNSAFE_PATH = re.compile(r"[^A-Za-z0-9_.-]")


def _jsonable(value: Any) -> Any:
    """把 SDK 回调负载转成可 JSON 序列化的形式（SSE 需要）"""
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


def _notification_event(note: Any) -> Dict[str, Any]:
    """SDK Notification → JKOS 事件字典"""
    return {
        "type": "notification",
        "method": getattr(note, "method", None),
        "payload": _jsonable(getattr(note, "payload", None)),
    }


@dataclass
class HubSession:
    """一次中枢会话（harness session 的 JKOS 视图）"""

    session_id: str
    tenant_id: str
    tenant_code: str
    user_id: str
    workspace: str
    created_at: float = field(default_factory=time.time)
    turns: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "tenant_code": self.tenant_code,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "turns": self.turns,
        }


def identity_preamble(session: HubSession) -> str:
    """身份前导：把 JKOS 的认证身份显式带入 harness 会话上下文（AC-4）

    harness 的 SDK 协议没有独立的「调用者身份」字段，身份随会话首条输入
    进入模型上下文，同时会话工作区按租户隔离作为第二重边界。
    """
    return (
        "[JKOS 会话上下文]\n"
        f"tenant_id={session.tenant_id}\n"
        f"tenant_code={session.tenant_code}\n"
        f"user_id={session.user_id}\n"
        f"session_id={session.session_id}\n"
        "\n[用户输入]\n"
    )


class HarnessGateway:
    """harness 会话网关（单实例，由 bootstrap/create_app 共享）"""

    def __init__(self, config: HarnessConfig,
                 sidecar: Optional[SidecarManager] = None):
        self.config = config
        self._sidecar = sidecar or SidecarManager(config)
        self._sessions: Dict[str, HubSession] = {}

    # ─── 状态 ───

    @property
    def sidecar(self) -> SidecarManager:
        return self._sidecar

    def health(self) -> Dict[str, Any]:
        return {"gateway": "harness", **self._sidecar.health(),
                "sessions": len(self._sessions)}

    # ─── 会话 ───

    def create_session(self, ctx: TenantContext) -> HubSession:
        """按认证身份创建会话（工作区按租户 + 会话隔离）"""
        session_id = f"jks-{uuid.uuid4().hex[:16]}"
        workspace = os.path.join(
            self.config.workspace,
            _UNSAFE_PATH.sub("_", ctx.tenant_id) or "default",
            session_id,
        )
        os.makedirs(workspace, exist_ok=True)
        session = HubSession(
            session_id=session_id,
            tenant_id=ctx.tenant_id,
            tenant_code=ctx.tenant_code,
            user_id=ctx.user_id,
            workspace=workspace,
        )
        self._sessions[session_id] = session
        logger.info("中枢会话已创建：%s（租户 %s / 用户 %s）",
                    session_id, ctx.tenant_id, ctx.user_id)
        return session

    def get_session(self, session_id: str, tenant_id: str) -> Optional[HubSession]:
        """按会话 ID 查询，且限定在调用方租户内（跨租户视为不存在）"""
        session = self._sessions.get(session_id)
        if session is None or session.tenant_id != tenant_id:
            return None
        return session

    def list_sessions(self, tenant_id: str) -> List[HubSession]:
        """列出本租户会话（按创建时间倒序）"""
        rows = [s for s in self._sessions.values() if s.tenant_id == tenant_id]
        return sorted(rows, key=lambda s: s.created_at, reverse=True)

    # ─── 事件流 ───

    async def stream_turn(self, session: HubSession, text: str) -> AsyncIterator[Dict[str, Any]]:
        """执行一轮对话并以事件流逐条产出（供 SSE 消费）"""
        loop = asyncio.get_running_loop()
        queue: "asyncio.Queue[Any]" = asyncio.Queue()

        yield {"type": "session/start", **session.to_dict()}

        runner = asyncio.create_task(self._run_turn(session, text, loop, queue))
        try:
            while True:
                item = await queue.get()
                if item is _STREAM_END:
                    break
                yield item
        finally:
            if not runner.done():
                runner.cancel()
            else:
                # 取回异常，避免 "exception was never retrieved" 噪音
                runner.exception()

    async def _run_turn(self, session: HubSession, text: str,
                        loop: asyncio.AbstractEventLoop,
                        queue: "asyncio.Queue[Any]") -> None:
        try:
            result = await asyncio.to_thread(self._run_blocking, session, text, loop, queue)
            await queue.put({
                "type": "turn/end",
                "session_id": session.session_id,
                "finish_reason": getattr(result, "finish_reason", None),
                "final_response": _jsonable(getattr(result, "final_response", None)),
            })
        except HarnessUnavailable as exc:
            # runtime 层问题 → 标记降级，供健康检查与运维告警
            self._sidecar.mark_degraded(f"{type(exc).__name__}: {exc}")
            await queue.put({"type": "error", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 单轮失败不应使 runtime 降级
            logger.warning("中枢会话 %s 本轮执行失败：%s", session.session_id, exc)
            await queue.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            await queue.put(_STREAM_END)

    def _run_blocking(self, session: HubSession, text: str,
                      loop: asyncio.AbstractEventLoop,
                      queue: "asyncio.Queue[Any]") -> Any:
        """在工作线程中执行同步 SDK 调用（不得在事件循环线程内直接调用）"""
        client = self._sidecar.ensure_started()

        def on_notification(note: Any) -> None:
            # 跨线程投递：必须经 call_soon_threadsafe 才能进事件循环队列
            loop.call_soon_threadsafe(queue.put_nowait, _notification_event(note))

        session.turns += 1
        return client.run(
            identity_preamble(session) + text,
            session_id=session.session_id,
            on_notification=on_notification,
        )

    # ─── 关闭 ───

    def close(self) -> None:
        """幂等关闭 sidecar（由 bootstrap 清理路径调用）"""
        self._sidecar.close()


# ─── 进程内单例（bootstrap 与 create_app 共享同一 sidecar） ───

_gateway: Optional[HarnessGateway] = None


def get_gateway(config: Optional[HarnessConfig] = None) -> HarnessGateway:
    """获取进程内单例网关（缺省按环境变量构建）"""
    global _gateway
    if _gateway is None:
        _gateway = HarnessGateway(config or HarnessConfig.from_env())
    return _gateway


def reset_gateway() -> None:
    """释放单例（测试与进程退出清理使用）"""
    global _gateway
    if _gateway is not None:
        _gateway.close()
    _gateway = None