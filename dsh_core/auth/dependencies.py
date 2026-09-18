"""DSH 认证 - JWT 管理器与 FastAPI 依赖注入

M8 安全加固新增：
- JWT 刷新令牌（access token 15min + refresh token 7d）
- RBAC 权限校验
- API 密钥验证
"""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from typing import List, Optional

from fastapi import Depends, Header, HTTPException

from dsh_core.auth import jwt as jwt_lib
from dsh_core.auth.context import TenantContext
from dsh_core.auth.rbac import check_permission

logger = logging.getLogger("dsh.auth")

# 模块级持有者：bootstrap 时注入，FastAPI 依赖函数读取
_jwt_manager: Optional["JWTManager"] = None


@dataclass
class AuthConfig:
    secret: str = ""
    expires_in: int = 86400
    refresh_expires_in: int = 604800  # 7 天
    access_expires_in: int = 900  # 15 分钟

    @classmethod
    def from_env(cls) -> "AuthConfig":
        secret = os.getenv("DSH_JWT_SECRET", "")
        if not secret:
            # 允许无配置启动（MVP），但给出明确告警：重启后 token 全部失效
            secret = secrets.token_urlsafe(32)
            logger.warning("未配置 DSH_JWT_SECRET，已生成临时密钥（重启后已签发 token 全部失效）")
        return cls(
            secret=secret,
            expires_in=int(os.getenv("DSH_JWT_EXPIRES_IN", "86400")),
            refresh_expires_in=int(os.getenv("DSH_JWT_REFRESH_EXPIRES_IN", "604800")),
            access_expires_in=int(os.getenv("DSH_JWT_ACCESS_EXPIRES_IN", "900")),
        )


class JWTManager:
    """签发/校验服务端 JWT（M8 新增：双 token 机制 + 刷新）"""

    def __init__(self, config: AuthConfig):
        self.config = config

    def issue_token(self, tenant_id: str, tenant_code: str, user_id: str,
                    roles: List[str], expires_in: Optional[int] = None) -> str:
        """签发访问令牌（兼容旧接口，默认 15 分钟）"""
        return jwt_lib.issue_access_token(
            user_id=user_id,
            tenant_id=tenant_id,
            tenant_code=tenant_code,
            roles=roles,
            secret=self.config.secret,
            expires_in=expires_in or self.config.access_expires_in,
        )

    def issue_access_token(self, user_id: str, tenant_id: str, tenant_code: str,
                           roles: List[str], expires_in: Optional[int] = None) -> str:
        """签发访问令牌（默认 15 分钟）"""
        return jwt_lib.issue_access_token(
            user_id=user_id,
            tenant_id=tenant_id,
            tenant_code=tenant_code,
            roles=roles,
            secret=self.config.secret,
            expires_in=expires_in or self.config.access_expires_in,
        )

    def issue_refresh_token(self, user_id: str, tenant_id: str, tenant_code: str,
                            roles: List[str], expires_in: Optional[int] = None) -> str:
        """签发刷新令牌（默认 7 天）"""
        return jwt_lib.issue_refresh_token(
            user_id=user_id,
            tenant_id=tenant_id,
            tenant_code=tenant_code,
            roles=roles,
            secret=self.config.secret,
            expires_in=expires_in or self.config.refresh_expires_in,
        )

    def refresh_access_token(self, refresh_token: str) -> str:
        """用刷新令牌换取新的访问令牌"""
        return jwt_lib.refresh_access_token(refresh_token, self.config.secret)

    def verify(self, token: str) -> TenantContext:
        """校验并解析访问令牌"""
        claims = jwt_lib.decode(token, self.config.secret)
        return TenantContext(
            tenant_id=claims.get("tid", ""),
            tenant_code=claims.get("tenant", ""),
            user_id=claims.get("sub", ""),
            roles=tuple(claims.get("roles", [])),
            expires_at=int(claims.get("exp", 0)),
        )

    def verify_refresh_token(self, refresh_token: str) -> TenantContext:
        """校验并解析刷新令牌（不生成新 token）"""
        claims = jwt_lib.decode(refresh_token, self.config.secret)
        if claims.get("type") != "refresh":
            raise jwt_lib.JWTError("令牌类型错误：需要 refresh token")
        return TenantContext(
            tenant_id=claims.get("tid", ""),
            tenant_code=claims.get("tenant", ""),
            user_id=claims.get("sub", ""),
            roles=tuple(claims.get("roles", [])),
            expires_at=int(claims.get("exp", 0)),
        )


def configure_auth(manager: JWTManager) -> None:
    """bootstrap 时注入全局 JWTManager"""
    global _jwt_manager
    _jwt_manager = manager


async def get_tenant_context(
    authorization: Optional[str] = Header(default=None),
) -> TenantContext:
    """FastAPI 依赖：校验 Bearer Token 并构建租户上下文"""
    if _jwt_manager is None:
        raise HTTPException(status_code=500, detail="认证未初始化")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Bearer Token")
    token = authorization[7:].strip()
    try:
        return _jwt_manager.verify(token)
    except jwt_lib.TokenExpired:
        raise HTTPException(status_code=401, detail="Token 已过期")
    except jwt_lib.JWTError as e:
        raise HTTPException(status_code=401, detail=f"无效 Token: {e}")


async def get_optional_context(
    authorization: Optional[str] = Header(default=None),
) -> Optional[TenantContext]:
    """FastAPI 依赖：可选校验 Bearer Token（M14 多租户升级新增）

    与 get_tenant_context 不同：无认证 / 认证未初始化 / Token 无效时均返回 None
    而非抛出异常，供 MCP 网关对匿名用户放行，同时携带租户上下文用于
    租户级工具可见性与配额限流。
    """
    if _jwt_manager is None:
        return None
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    try:
        return _jwt_manager.verify(token)
    except (jwt_lib.TokenExpired, jwt_lib.JWTError):
        return None


def mint_dev_token(manager: JWTManager, tenant_id: str, tenant_code: str,
                   user_id: str = "admin", days: int = 7) -> str:
    """签发 M0 自测用管理员 token（M3 接入正式用户体系后废弃）"""
    return manager.issue_token(tenant_id, tenant_code, user_id, ["admin"],
                               expires_in=days * 86400)


# ─── 权限校验依赖 ───

def require_permission(
    resource: str,
    action: str,
    ctx: Any = None,
) -> TenantContext:
    """FastAPI 依赖：校验当前用户是否拥有指定权限

    无权限时抛出 HTTPException(403)。
    支持直接调用（传入 TenantContext）和 FastAPI 依赖注入两种模式。

    Examples:
        # FastAPI 依赖注入
        @router.get("/workflows")
        async def list_workflows(
            ctx: TenantContext = Depends(get_tenant_context)
        ):
            require_permission("workflow", "read", ctx)
            ...

        # 直接调用（测试用）
        ctx = TenantContext(...)
        require_permission("workflow", "read", ctx)
    """
    # 支持直接调用：ctx 可能是 TenantContext 或 None
    if ctx is None:
        # FastAPI 依赖注入模式：自动获取当前上下文
        from dsh_core.auth.context import current_context
        ctx = current_context()
        if ctx is None:
            raise HTTPException(status_code=401, detail="未认证")

    if not hasattr(ctx, "roles"):
        raise HTTPException(status_code=403, detail="上下文无效")

    if not check_permission(ctx.roles, resource, action):
        raise HTTPException(
            status_code=403,
            detail=f"权限不足：需要 {resource}:{action}",
        )
    return ctx


# ─── 刷新令牌端点辅助 ───

async def refresh_token_endpoint(
    refresh_token: str = Header(..., alias="X-Refresh-Token"),
) -> dict:
    """刷新令牌端点辅助：用 X-Refresh-Token 换取新 access token

    在 FastAPI 路由中使用：
        @router.post("/auth/refresh")
        async def refresh(token: dict = Depends(refresh_token_endpoint)):
            return token
    """
    if _jwt_manager is None:
        raise HTTPException(status_code=500, detail="认证未初始化")
    try:
        new_access = _jwt_manager.refresh_access_token(refresh_token)
        return {"access_token": new_access, "token_type": "bearer"}
    except jwt_lib.TokenExpired:
        raise HTTPException(status_code=401, detail="刷新令牌已过期，请重新登录")
    except jwt_lib.JWTError as e:
        raise HTTPException(status_code=401, detail=f"无效刷新令牌: {e}")


# ─── 权限校验依赖 ───

def require_permission(
    resource: str,
    action: str,
    ctx: Any = None,
) -> TenantContext:
    """FastAPI 依赖：校验当前用户是否拥有指定权限

    无权限时抛出 HTTPException(403)。
    支持直接调用（传入 TenantContext）和 FastAPI 依赖注入两种模式。

    Examples:
        # FastAPI 依赖注入
        @router.get("/workflows")
        async def list_workflows(
            ctx: TenantContext = Depends(get_tenant_context)
        ):
            require_permission("workflow", "read", ctx)
            ...

        # 直接调用（测试用）
        ctx = TenantContext(...)
        require_permission("workflow", "read", ctx)
    """
    # 支持直接调用：ctx 可能是 TenantContext 或 None
    if ctx is None:
        # FastAPI 依赖注入模式：自动获取当前上下文
        from dsh_core.auth.context import current_context
        ctx = current_context()
        if ctx is None:
            raise HTTPException(status_code=401, detail="未认证")

    if not hasattr(ctx, "roles"):
        raise HTTPException(status_code=403, detail="上下文无效")

    if not check_permission(ctx.roles, resource, action):
        raise HTTPException(
            status_code=403,
            detail=f"权限不足：需要 {resource}:{action}",
        )
    return ctx


# ─── 刷新令牌端点辅助 ───

async def refresh_token_endpoint(
    refresh_token: str = Header(..., alias="X-Refresh-Token"),
) -> dict:
    """刷新令牌端点辅助：用 X-Refresh-Token 换取新 access token

    在 FastAPI 路由中使用：
        @router.post("/auth/refresh")
        async def refresh(token: dict = Depends(refresh_token_endpoint)):
            return token
    """
    if _jwt_manager is None:
        raise HTTPException(status_code=500, detail="认证未初始化")
    try:
        new_access = _jwt_manager.refresh_access_token(refresh_token)
        return {"access_token": new_access, "token_type": "bearer"}
    except jwt_lib.TokenExpired:
        raise HTTPException(status_code=401, detail="刷新令牌已过期，请重新登录")
    except jwt_lib.JWTError as e:
        raise HTTPException(status_code=401, detail=f"无效刷新令牌: {e}")
