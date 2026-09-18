"""极快AI操作系统 - 智能中枢 REST / SSE 路由（M16）

对外暴露 /api/v1/harness/*：
  GET  /health                      sidecar 探活（公开，与 /api/v1/health 一致）
  POST /sessions                    创建会话（需 JWT，201）
  GET  /sessions                    列出本租户会话（需 JWT）
  POST /sessions/{id}/messages      发消息并建立 SSE 事件流（需 JWT）

鉴权复用 jkos_core.auth.dependencies.get_tenant_context（无凭证 401）；
配额复用 jkos_core.utils.ratelimit 的租户级令牌桶（超限 429 + Retry-After）。
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from jkos_core.auth.context import TenantContext
from jkos_core.auth.dependencies import get_tenant_context
from jkos_core.harness.gateway import HarnessGateway
from jkos_core.utils.ratelimit import _global_limiter

logger = logging.getLogger("dsh.harness")

_RETRY_AFTER_SECONDS = "1"


class MessageRequest(BaseModel):
    """发消息请求"""

    content: str = Field(..., description="用户输入内容", min_length=1, max_length=100_000)


def _enforce_quota(ctx: TenantContext) -> None:
    """租户级配额校验：超限返回 429 + Retry-After（与 MCP 网关同一模式）"""
    if not _global_limiter.acquire_by_tenant(ctx.tenant_id):
        raise HTTPException(
            status_code=429,
            detail="中枢请求过于频繁，请稍后重试",
            headers={"Retry-After": _RETRY_AFTER_SECONDS},
        )


def create_harness_router(gateway: HarnessGateway) -> APIRouter:
    """创建智能中枢路由（gateway 缺省由调用方按配置构建）"""
    router = APIRouter(prefix="/api/v1/harness", tags=["Harness"])

    # ─── 探活（公开） ───

    @router.get("/health")
    async def harness_health():
        """sidecar 状态（不含密钥，配置项经 describe() 脱敏）"""
        return gateway.health()

    # ─── 会话 ───

    @router.post("/sessions", status_code=201)
    async def create_session(ctx: TenantContext = Depends(get_tenant_context)):
        """创建中枢会话（身份取自 JWT）"""
        _enforce_quota(ctx)
        session = gateway.create_session(ctx)
        return session.to_dict()

    @router.get("/sessions")
    async def list_sessions(ctx: TenantContext = Depends(get_tenant_context)):
        """列出本租户会话"""
        rows = gateway.list_sessions(ctx.tenant_id)
        return {"sessions": [s.to_dict() for s in rows], "count": len(rows)}

    @router.post("/sessions/{session_id}/messages")
    async def send_message(
        session_id: str,
        req: MessageRequest,
        ctx: TenantContext = Depends(get_tenant_context),
    ):
        """发消息并返回 SSE 事件流"""
        _enforce_quota(ctx)
        session = gateway.get_session(session_id, ctx.tenant_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")

        async def event_stream() -> AsyncIterator[str]:
            try:
                async for event in gateway.stream_turn(session, req.content):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as exc:  # noqa: BLE001 - 流内异常转为 error 事件，不中断连接
                logger.warning("中枢事件流异常：%s", exc)
                payload = json.dumps({"type": "error", "message": str(exc)},
                                     ensure_ascii=False)
                yield f"data: {payload}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router