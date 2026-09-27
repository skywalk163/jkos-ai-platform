"""DSH 执行器 SPI（M21 任务 21.1，executor SPI）

职责：工作流引擎将声明了 executor 的节点派发给外部执行器，执行器异步执行，
结果经事件总线（EXEC.RESULT / EXEC.FAILED / EXEC.TIMEOUT）回调引擎续跑。

设计约定（引擎与执行器解耦）：
  - 引擎只依赖本 SPI 抽象，不绑定 dsh / A2A / 异构 agent；
  - dsh、A2A 等真实后端后续经适配器实现本 SPI（M21-b/c/d）；
  - 派发是异步受理：引擎 dispatch 后实例进入 WAITING_AGENT 挂起，
    等待执行器结果事件回调 resume 续跑（见 engine._enter_executor / resume_agent）。

sla_hours：执行器任务挂起超时。超时后引擎做超时补偿（通知人工兜底并可选
升级，见 engine.sweep_timeouts 的 agent_timed_out 分支）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class ExecutorError(RuntimeError):
    """执行器派发/交互失败（引擎转为步骤失败处理）"""


class ExecutorNotAuthorizedError(ExecutorError):
    """租户未被授权使用该执行器（注册表租户白名单校验）"""


@dataclass
class DispatchResult:
    """派发结果：执行器已受理异步任务（引擎据此挂起实例并订阅结果）"""
    task_id: str
    executor: str
    status: str = "ACCEPTED"       # ACCEPTED / QUEUED / RUNNING
    accepted_at: Optional[str] = None
    sla_hours: Optional[float] = None
    context: Dict[str, Any] = field(default_factory=dict)


class Executor:
    """执行器 SPI（异步任务协议）"""

    name: str = "base"

    async def dispatch(
        self,
        instance_id: str,
        step_id: str,
        node_code: str,
        payload: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> DispatchResult:
        raise NotImplementedError

    async def cancel(self, task_id: str) -> None:
        raise NotImplementedError

    async def status(self, task_id: str) -> Dict[str, Any]:
        raise NotImplementedError