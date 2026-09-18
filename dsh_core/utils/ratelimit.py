"""DSH 安全 - 速率限制器（令牌桶算法）

M8 安全加固任务 8.4：实现令牌桶算法的速率限制，支持全局/租户/用户级别限流。

用法:
    from dsh_core.utils.ratelimit import RateLimiter, rate_limit

    # 直接使用
    limiter = RateLimiter(rps=10, burst=20)
    if not limiter.acquire():
        raise HTTPException(429, "请求过于频繁")

    # FastAPI 依赖
    @router.post("/api")
    async def api_call(_=Depends(rate_limit(rps=10, burst=20))):
        ...
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class RateLimitConfig:
    """速率限制配置"""
    rps: float = 10.0  # 每秒请求数
    burst: int = 20  # 突发容量
    enabled: bool = True


class RateLimiter:
    """令牌桶速率限制器（单实例，线程安全）

    实现原理:
    - 桶容量 = burst
    - 每 rps 秒补充 1 个令牌
    - 请求消耗 1 个令牌，桶空则拒绝
    """

    def __init__(self, rps: float = 10.0, burst: int = 20):
        self._config = RateLimitConfig(rps=rps, burst=burst)
        self._tokens: float = float(burst)  # 当前令牌数
        self._last_refill: float = time.monotonic()

    def acquire(self, tokens: int = 1) -> bool:
        """尝试获取令牌

        Args:
            tokens: 需要获取的令牌数

        Returns:
            是否成功获取
        """
        if not self._config.enabled:
            return True

        now = time.monotonic()
        elapsed = now - self._last_refill

        # 补充令牌
        refill = elapsed * self._config.rps
        if refill >= 1.0:
            self._tokens = min(
                float(self._config.burst),
                self._tokens + refill,
            )
            self._last_refill = now

        # 检查令牌是否足够
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True

        return False

    @property
    def remaining(self) -> float:
        """剩余令牌数"""
        return self._tokens

    @property
    def config(self) -> RateLimitConfig:
        return self._config


class MultiTenantRateLimiter:
    """多租户速率限制器

    为每个租户/用户维护独立的令牌桶。
    生产环境应替换为 Redis 实现。
    """

    def __init__(self, rps: float = 10.0, burst: int = 20):
        self._default_rps = rps
        self._default_burst = burst
        self._limiters: Dict[str, RateLimiter] = {}

    def _get_limiter(self, key: str) -> RateLimiter:
        """获取或创建指定 key 的限流器"""
        if key not in self._limiters:
            self._limiters[key] = RateLimiter(
                rps=self._default_rps,
                burst=self._default_burst,
            )
        return self._limiters[key]

    def acquire(self, key: str, tokens: int = 1) -> bool:
        """尝试获取令牌

        Args:
            key: 限流键（如 user_id 或 tenant_id）
            tokens: 需要获取的令牌数

        Returns:
            是否成功获取
        """
        return self._get_limiter(key).acquire(tokens)

    def acquire_by_tenant(self, tenant_id: str, tokens: int = 1) -> bool:
        """按租户限流"""
        return self.acquire(f"tenant:{tenant_id}", tokens)

    def acquire_by_user(self, user_id: str, tokens: int = 1) -> bool:
        """按用户限流"""
        return self.acquire(f"user:{user_id}", tokens)

    def acquire_by_ip(self, ip: str, tokens: int = 1) -> bool:
        """按 IP 限流"""
        return self.acquire(f"ip:{ip}", tokens)


# ─── FastAPI 依赖 ───

_global_limiter = MultiTenantRateLimiter()


def rate_limit(rps: float = 10.0, burst: int = 20, by: str = "ip"):
    """FastAPI 依赖：速率限制

    Args:
        rps: 每秒请求数
        burst: 突发容量
        by: 限流维度 ("ip", "user", "tenant")

    Examples:
        @router.post("/api/endpoint")
        async def endpoint(_=Depends(rate_limit(rps=5, burst=10))):
            ...
    """
    from fastapi import HTTPException, Request
    from starlette.requests import Request as StarletteRequest

    async def _rate_limit_dependency(request: Request):
        limiter = MultiTenantRateLimiter(rps=rps, burst=burst)

        if by == "ip":
            # 获取客户端 IP
            client = getattr(request, "client", None)
            ip = client.host if client else "unknown"
            if not limiter.acquire_by_ip(ip):
                raise HTTPException(
                    status_code=429,
                    detail="请求过于频繁，请稍后重试",
                    headers={"Retry-After": "1"},
                )
        elif by == "user":
            from dsh_core.auth.context import current_context
            ctx = current_context()
            user_id = ctx.user_id if ctx else "anonymous"
            if not limiter.acquire_by_user(user_id):
                raise HTTPException(
                    status_code=429,
                    detail="请求过于频繁，请稍后重试",
                    headers={"Retry-After": "1"},
                )
        elif by == "tenant":
            from dsh_core.auth.context import current_context
            ctx = current_context()
            tenant_id = ctx.tenant_id if ctx else "anonymous"
            if not limiter.acquire_by_tenant(tenant_id):
                raise HTTPException(
                    status_code=429,
                    detail="请求过于频繁，请稍后重试",
                    headers={"Retry-After": "1"},
                )

    return _rate_limit_dependency
