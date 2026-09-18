"""DSH 认证 - 基于角色的访问控制 (RBAC)

M8 安全加固任务 8.1：实现 RBAC 权限模型，支持角色继承与权限校验。

角色层级：admin > approver > operator > viewer
权限模型：每个角色拥有权限列表，admin 自动继承所有权限。
"""
from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

# ─── 角色定义 ───
ROLE_ADMIN = "admin"
ROLE_APPROVER = "approver"
ROLE_OPERATOR = "operator"
ROLE_VIEWER = "viewer"

ALL_ROLES = {ROLE_ADMIN, ROLE_APPROVER, ROLE_OPERATOR, ROLE_VIEWER}

# ─── 权限定义 ───
# 格式: "resource:action" 或 "*" (通配符)
PERM_WORKFLOW_READ = "workflow:read"
PERM_WORKFLOW_EXECUTE = "workflow:execute"
PERM_WORKFLOW_MANAGE = "workflow:manage"
PERM_APPROVAL_DECIDE = "approval:decide"
PERM_APPROVAL_MANAGE = "approval:manage"
PERM_USER_MANAGE = "user:manage"
PERM_TENANT_MANAGE = "tenant:manage"
PERM_AUDIT_READ = "audit:read"
PERM_PLUGIN_MANAGE = "plugin:manage"
PERM_ADMIN = "*"  # 通配符，表示所有权限

# ─── 角色-权限映射 ───
# 每个角色拥有的权限列表（低级角色不包含高级角色的权限）
ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    ROLE_ADMIN: {PERM_ADMIN},  # admin 拥有通配符权限
    ROLE_APPROVER: {
        PERM_WORKFLOW_READ,
        PERM_WORKFLOW_EXECUTE,
        PERM_APPROVAL_DECIDE,
        PERM_AUDIT_READ,
    },
    ROLE_OPERATOR: {
        PERM_WORKFLOW_READ,
        PERM_WORKFLOW_EXECUTE,
    },
    ROLE_VIEWER: {
        PERM_WORKFLOW_READ,
        PERM_AUDIT_READ,
    },
}

# ─── 角色继承关系（低级角色 → 高级角色）───
ROLE_HIERARCHY: Dict[str, List[str]] = {
    ROLE_VIEWER: [ROLE_OPERATOR, ROLE_APPROVER, ROLE_ADMIN],
    ROLE_OPERATOR: [ROLE_APPROVER, ROLE_ADMIN],
    ROLE_APPROVER: [ROLE_ADMIN],
    ROLE_ADMIN: [],
}


def get_all_permissions(roles: List[str]) -> Set[str]:
    """获取用户所有权限（含 admin 通配符展开）

    Args:
        roles: 用户角色列表

    Returns:
        权限集合（若含 admin 则返回 {"*"}）
    """
    if not roles:
        return set()

    # admin 角色自动继承所有权限
    if ROLE_ADMIN in roles:
        return {PERM_ADMIN}

    permissions: Set[str] = set()
    for role in roles:
        if role in ROLE_PERMISSIONS:
            permissions.update(ROLE_PERMISSIONS[role])
    return permissions


def check_permission(
    user_roles: List[str],
    resource: str,
    action: str,
) -> bool:
    """检查用户是否拥有指定权限

    Args:
        user_roles: 用户角色列表
        resource: 资源类型 (workflow, approval, user, etc.)
        action: 操作类型 (read, execute, manage, decide, etc.)

    Returns:
        是否拥有权限

    Examples:
        >>> check_permission(["viewer"], "workflow", "read")
        True
        >>> check_permission(["viewer"], "workflow", "execute")
        False
        >>> check_permission(["admin"], "anything", "anything")
        True
    """
    permissions = get_all_permissions(user_roles)

    # admin 通配符权限
    if PERM_ADMIN in permissions:
        return True

    # 精确匹配
    perm = f"{resource}:{action}"
    if perm in permissions:
        return True

    return False


def require_permission_direct(
    user_roles: List[str],
    resource: str,
    action: str,
    user_id: str = "",
) -> None:
    """强制权限检查，无权限时抛出 PermissionDenied

    Args:
        user_roles: 用户角色列表
        resource: 资源类型
        action: 操作类型
        user_id: 用户标识（用于错误信息）

    Raises:
        PermissionDenied: 无权限时抛出
    """
    if not check_permission(user_roles, resource, action):
        raise PermissionDenied(
            user_id=user_id,
            roles=user_roles,
            resource=resource,
            action=action,
        )


class PermissionDenied(Exception):
    """权限不足异常

    Attributes:
        user_id: 用户标识
        roles: 用户角色列表
        resource: 资源类型
        action: 操作类型
    """

    def __init__(
        self,
        user_id: str = "",
        roles: Tuple[str, ...] = (),
        resource: str = "",
        action: str = "",
    ):
        self.user_id = user_id
        self.roles = tuple(roles)
        self.resource = resource
        self.action = action
        super().__init__(
            f"用户 {user_id or 'unknown'} (角色: {list(roles)}) "
            f"无权 {action} {resource}"
        )


def get_role_hierarchy() -> Dict[str, List[str]]:
    """获取角色继承关系（用于 UI 展示）"""
    return dict(ROLE_HIERARCHY)


def is_valid_role(role: str) -> bool:
    """检查角色是否有效"""
    return role in ALL_ROLES


def get_all_role_names() -> List[str]:
    """获取所有有效角色名称"""
    return list(ALL_ROLES)


def get_permissions_for_role(role: str) -> List[str]:
    """获取指定角色的权限列表"""
    if role == ROLE_ADMIN:
        return [PERM_ADMIN]
    return list(ROLE_PERMISSIONS.get(role, []))
