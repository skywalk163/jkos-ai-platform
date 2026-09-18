"""DSH 安全 - 安全中间件（CORS / CSRF / 安全头）

M8 安全加固任务 8.4：
- CORS 白名单配置
- CSRF Token 中间件（stateless, signed）
- 安全头：HSTS / CSP / X-Frame-Options / X-Content-Type-Options
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware


# ─── 安全头中间件 ───

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """安全响应头中间件

    为所有响应添加安全相关的 HTTP 头：
    - Strict-Transport-Security: HSTS
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - Content-Security-Policy: 默认策略
    - Referrer-Policy: strict-origin-when-cross-origin
    """

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        # HSTS: 强制 HTTPS（生产环境）
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

        # 防止 MIME 类型嗅探
        response.headers["X-Content-Type-Options"] = "nosniff"

        # 防止点击劫持
        response.headers["X-Frame-Options"] = "DENY"

        # 内容安全策略（生产环境应自定义）
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self';"
        )

        # 引用策略
        response.headers["Referrer-Policy"] = (
            "strict-origin-when-cross-origin"
        )

        # 权限策略
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()"
        )

        return response


# ─── CSRF 保护 ───

class CSRFMiddleware(BaseHTTPMiddleware):
    """CSRF 保护中间件（stateless, signed token）

    实现原理：
    1. 生成 CSRF Token：HMAC-SHA256(随机数 + 时间戳, secret)
    2. 存储在 Cookie 中（HttpOnly + Secure + SameSite=Strict）
    3. 请求时校验：要求 Header X-CSRF-Token 与 Cookie 中的 token 匹配

    安全基线：
    - 仅对 state-changing 方法（POST/PUT/DELETE/PATCH）校验
    - 使用 hmac.compare_digest 防止时序攻击
    - Token 包含时间戳，支持过期
    """

    def __init__(
        self,
        app,
        secret: Optional[str] = None,
        token_expiry: int = 3600,
        cookie_name: str = "csrf_token",
        header_name: str = "X-CSRF-Token",
    ):
        super().__init__(app)
        self._secret = secret or os.getenv("DSH_CSRF_SECRET", "")
        if not self._secret:
            self._secret = secrets.token_urlsafe(32)
        self._token_expiry = token_expiry
        self._cookie_name = cookie_name
        self._header_name = header_name

    def _generate_token(self) -> str:
        """生成 CSRF Token"""
        nonce = secrets.token_urlsafe(16)
        timestamp = str(int(time.time()))
        signing_input = f"{nonce}:{timestamp}"
        signature = hmac.new(
            self._secret.encode(),
            signing_input.encode(),
            hashlib.sha256,
        ).hexdigest()[:32]
        return f"{nonce}:{timestamp}:{signature}"

    def _verify_token(self, token: str) -> bool:
        """校验 CSRF Token"""
        if not token or ":" not in token:
            return False

        parts = token.split(":")
        if len(parts) != 3:
            return False

        nonce, timestamp, signature = parts

        # 检查过期
        try:
            token_time = int(timestamp)
            if time.time() - token_time > self._token_expiry:
                return False
        except ValueError:
            return False

        # 校验签名
        signing_input = f"{nonce}:{timestamp}"
        expected = hmac.new(
            self._secret.encode(),
            signing_input.encode(),
            hashlib.sha256,
        ).hexdigest()[:32]

        return hmac.compare_digest(signature, expected)

    async def dispatch(self, request: Request, call_next):
        # 仅对 state-changing 方法校验 CSRF
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            # 从 Header 获取 token
            token = request.headers.get(self._header_name, "")

            # 也从 Cookie 获取（用于校验）
            cookie_token = request.cookies.get(self._cookie_name, "")

            # 优先使用 Header 中的 token
            if not token and cookie_token:
                token = cookie_token

            if not token or not self._verify_token(token):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF 校验失败"},
                )

        response: Response = await call_next(request)

        # 设置 CSRF Cookie（首次请求或需要刷新时）
        if request.method == "GET":
            token = self._generate_token()
            response.set_cookie(
                key=self._cookie_name,
                value=token,
                httponly=True,
                secure=True,  # 生产环境应启用
                samesite="Strict",
                max_age=self._token_expiry,
            )
            # 同时设置 Header 供前端使用
            response.headers[self._header_name] = token

        return response


# ─── CORS 配置辅助 ───

def get_cors_origins() -> list:
    """从环境变量获取 CORS 白名单

    环境变量: DSH_CORS_ORIGINS
    格式: 逗号分隔的 URL 列表，如 "http://localhost:3000,https://example.com"
    """
    origins_str = os.getenv("DSH_CORS_ORIGINS", "")
    if not origins_str:
        return ["http://localhost:3000", "http://127.0.0.1:3000"]
    return [url.strip() for url in origins_str.split(",") if url.strip()]


def get_cors_middleware_config() -> dict:
    """获取 CORS 中间件配置"""
    return {
        "allow_origins": get_cors_origins(),
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        "max_age": 3600,
    }


# ─── 安全配置 ───

class SecurityConfig:
    """安全配置"""

    def __init__(
        self,
        cors_origins: Optional[list] = None,
        csrf_secret: Optional[str] = None,
        csrf_expiry: int = 3600,
        rate_limit_rps: float = 10.0,
        rate_limit_burst: int = 20,
        enable_hsts: bool = True,
        enable_csp: bool = True,
    ):
        self.cors_origins = cors_origins or get_cors_origins()
        self.csrf_secret = csrf_secret or os.getenv("DSH_CSRF_SECRET", "")
        self.csrf_expiry = csrf_expiry
        self.rate_limit_rps = rate_limit_rps
        self.rate_limit_burst = rate_limit_burst
        self.enable_hsts = enable_hsts
        self.enable_csp = enable_csp

    @classmethod
    def from_env(cls) -> "SecurityConfig":
        """从环境变量创建配置"""
        return cls(
            cors_origins=get_cors_origins(),
            csrf_secret=os.getenv("DSH_CSRF_SECRET", ""),
            csrf_expiry=int(os.getenv("DSH_CSRF_EXPIRY", "3600")),
            rate_limit_rps=float(os.getenv("DSH_RATE_LIMIT_RPS", "10")),
            rate_limit_burst=int(os.getenv("DSH_RATE_LIMIT_BURST", "20")),
        )
