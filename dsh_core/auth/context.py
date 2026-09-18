"""DSH 认证 - 租户上下文（设计文档 §2.8 / §8.4.3）

ContextVar 实现：一次请求内贯穿传递租户/用户/角色，无需在函数间层层透传。
MVP 角色集为十角色矩阵（§2.8）的精简子集，M3 酒厂阶段扩全。
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Optional, Tuple

# MVP 角色集（§2.8 十角色矩阵精简子集，M3 扩全）
ROLE_ADMIN = "admin"        # 租户管理员：全权限
ROLE_APPROVER = "approver"  # 审批人：高风险动作会签
ROLE_OPERATOR = "operator"  # 操作员：可触发工作流/调用工具
ROLE_VIEWER = "viewer"      # 只读

ROLE_ALL = (ROLE_ADMIN, ROLE_APPROVER, ROLE_OPERATOR, ROLE_VIEWER)


class PermissionDenied(Exception):
    """角色不满足要求"""


@dataclass(frozen=True)
class TenantContext:
    """请求级租户上下文：认证成功后构建，贯穿整个请求生命周期"""
    tenant_id: str
    tenant_code: str
    user_id: str
    roles: Tuple[str, ...] = ()
    expires_at: int = 0

    def has_role(self, role: str) -> bool:
        # admin 隐式继承所有角色（§2.8 职责分离原则下的超级角色）
        return role in self.roles or ROLE_ADMIN in self.roles

    def require_role(self, role: str) -> None:
        if not self.has_role(role):
            raise PermissionDenied(f"需要角色 {role}，当前角色: {self.roles or ('无',)}")


_current: "ContextVar[Optional[TenantContext]]" = ContextVar("dsh_tenant_context", default=None)


def current_context() -> Optional[TenantContext]:
    """获取当前请求的租户上下文（无认证时为 None）"""
    return _current.get()


def set_context(ctx: TenantContext) -> "Token":
    return _current.set(ctx)


def reset_context(token: "Token") -> None:
    _current.reset(token)
