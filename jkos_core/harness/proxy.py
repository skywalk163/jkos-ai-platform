"""极快AI操作系统 - harness Web UI 反向代理（M16）

harness Web UI 默认监听 127.0.0.1:3080，且带 loopback 信任围栏：非 loopback
访问一律 403（见 deepseek-harness FREEBSD.md §0）。JKOS 与 harness 同机，
经 127.0.0.1 转发可满足该围栏；转发时改写 Host 头，避免客户端 Host 触发围栏。

SPA 资源路径以 "/" 开头，直接挂在子路径下会导致资源 404，因此对 HTML 响应
做一次绝对路径前缀改写。
"""
from __future__ import annotations

import logging
import re
from typing import Dict

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from jkos_core.harness.config import HarnessConfig

logger = logging.getLogger("dsh.harness")

UI_PREFIX = "/harness/ui"

# 逐跳头：不应由代理转发
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
    "content-encoding",
}

# HTML 中的绝对路径引用（href="/x"、src='/x' 等）
_ABSOLUTE_REF = re.compile(r"""(?P<attr>\b(?:href|src|action)=)(?P<q>["'])(?P<path>/[^"']*)""")

_UPSTREAM_TIMEOUT = 30.0


def _rewrite_html(body: bytes, prefix: str) -> bytes:
    """把 HTML 里的绝对资源路径改写到代理前缀下"""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body
    # 已经是前缀的引用保持原样
    text = _ABSOLUTE_REF.sub(
        lambda m: f"{m.group('attr')}{m.group('q')}{prefix}{m.group('path')}"
        if not m.group("path").startswith(prefix) else m.group(0),
        text,
    )
    return text.encode("utf-8")


def create_ui_proxy_router(config: HarnessConfig,
                           client: httpx.AsyncClient | None = None) -> APIRouter:
    """创建 Web UI 反代路由（挂载于 /harness/ui）"""
    router = APIRouter(tags=["Harness"])
    upstream_base = config.ui_base_url.rstrip("/")
    http = client or httpx.AsyncClient(timeout=_UPSTREAM_TIMEOUT)

    @router.api_route(UI_PREFIX + "/{path:path}",
                      methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
    async def proxy_ui(path: str, request: Request):
        """转发到 harness Web UI（同机 127.0.0.1）"""
        url = f"{upstream_base}/{path}" if path else f"{upstream_base}/"
        headers: Dict[str, str] = {
            k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP
        }
        # 固定 Host 为上游地址：满足 harness 的 loopback 信任围栏
        headers["host"] = upstream_base.split("://", 1)[-1]

        # harness Web UI 需令牌（dsh web 启动时打印 ?token=...）；
        # 由 JKOS 注入，调用方无需感知（令牌来自环境变量，不进代码/日志）
        params = dict(request.query_params)
        if config.ui_token:
            params["token"] = config.ui_token

        try:
            upstream = await http.request(
                request.method, url,
                params=params,
                headers=headers,
                content=await request.body(),
                # harness 在令牌校验后会 303 重定向；在代理内部跟随，
                # 避免把上游内部地址（/）暴露给浏览器（否则会跳出 /harness/ui 前缀）
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            logger.warning("harness Web UI 上游不可达：%s", exc)
            return Response(
                content=f"harness Web UI 上游不可达：{exc}".encode("utf-8"),
                status_code=502,
                media_type="text/plain; charset=utf-8",
            )

        out_headers = {
            k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP
        }
        body = upstream.content
        content_type = upstream.headers.get("content-type", "")
        if "text/html" in content_type:
            body = _rewrite_html(body, UI_PREFIX)
            out_headers.pop("etag", None)
        return Response(content=body, status_code=upstream.status_code,
                        headers=out_headers)

    return router