"""DSH 认证 - 租户上下文 / JWT / FastAPI 依赖（M0 任务 0.5，设计文档 §2.8/§8.4）

M8 安全加固新增：
- RBAC 权限模型 (dsh_core.auth.rbac)
- API 密钥管理 (dsh_core.auth.apikey)
- JWT 刷新令牌 (dsh_core.auth.jwt: issue_access_token/issue_refresh_token/refresh_access_token)
"""
from dsh_core.auth.apikey import APIKey, APIKeyError, APIKeyManager, EncryptionDisabled, configure_api_key_manager, get_api_key_manager
from dsh_core.auth.context import (
    ROLE_ADMIN,
    ROLE_ALL,
    ROLE_APPROVER,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    PermissionDenied,
    TenantContext,
    current_context,
    reset_context,
    set_context,
)
from dsh_core.auth.dependencies import (
    AuthConfig,
    JWTManager,
    configure_auth,
    get_optional_context,
    get_tenant_context,
    mint_dev_token,
    refresh_token_endpoint,
    require_permission,
)
from dsh_core.auth.jwt import (
    issue_access_token,
    issue_refresh_token,
    refresh_access_token,
    blacklist_refresh_token,
    is_refresh_token_blacklisted,
)
from dsh_core.auth.rbac import (
    check_permission,
    get_all_permissions,
    get_permissions_for_role,
    get_role_hierarchy,
    is_valid_role,
    require_permission_direct,
)

__all__ = [
    # 租户上下文
    "TenantContext",
    "current_context",
    "set_context",
    "reset_context",
    # 角色
    "ROLE_ADMIN",
    "ROLE_APPROVER",
    "ROLE_OPERATOR",
    "ROLE_VIEWER",
    "ROLE_ALL",
    # 权限
    "PermissionDenied",
    "check_permission",
    "require_permission",
    "require_permission_direct",
    "require_permission_direct",
    "get_all_permissions",
    "get_role_hierarchy",
    "is_valid_role",
    "get_permissions_for_role",
    # JWT
    "JWTManager",
    "AuthConfig",
    "configure_auth",
    "get_optional_context",
    "get_tenant_context",
    "mint_dev_token",
    "issue_access_token",
    "issue_refresh_token",
    "refresh_access_token",
    "blacklist_refresh_token",
    "is_refresh_token_blacklisted",
    "refresh_token_endpoint",
    # API 密钥
    "APIKeyManager",
    "APIKey",
    "APIKeyError",
    "EncryptionDisabled",
    "get_api_key_manager",
    "configure_api_key_manager",
]
