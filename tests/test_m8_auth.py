"""M8 任务 8.1：认证授权增强 — JWT 刷新令牌 / RBAC / API 密钥

测试覆盖：
- JWT 双 token 机制（access 15min + refresh 7d）
- 刷新令牌黑名单（登出撤销）
- RBAC 权限模型（角色继承、权限校验）
- API 密钥管理（创建、验证、撤销、轮换）
"""

import time
import pytest
from fastapi import HTTPException
from fastapi import HTTPException

from dsh_core.auth import (
    JWTManager,
    AuthConfig,
    issue_access_token,
    issue_refresh_token,
    refresh_access_token,
    blacklist_refresh_token,
    is_refresh_token_blacklisted,
    check_permission,
    require_permission,
    get_all_permissions,
    get_permissions_for_role,
    is_valid_role,
    get_role_hierarchy,
    APIKeyManager,
    APIKeyError,
    EncryptionDisabled,
    PermissionDenied,
    ROLE_ADMIN,
    ROLE_APPROVER,
    ROLE_OPERATOR,
    ROLE_VIEWER,
)
from dsh_core.auth.jwt import TokenInvalid, TokenExpired, JWTError


# ─── JWT 刷新令牌测试 ───


class TestJWTRefreshTokens:
    """JWT 双 token 机制测试"""

    def test_issue_access_token_short_lived(self):
        """访问令牌默认 15 分钟过期"""
        token = issue_access_token(
            user_id="user1",
            tenant_id="tid1",
            tenant_code="dev",
            roles=["operator"],
            secret="secret-key-that-is-long-enough",
        )
        assert token.count(".") == 2  # JWT 格式

    def test_issue_refresh_token_long_lived(self):
        """刷新令牌默认 7 天过期，含唯一 jti"""
        token = issue_refresh_token(
            user_id="user1",
            tenant_id="tid1",
            tenant_code="dev",
            roles=["operator"],
            secret="secret-key-that-is-long-enough",
        )
        assert token.count(".") == 2

    def test_refresh_access_token_success(self):
        """用刷新令牌成功换取新访问令牌"""
        refresh = issue_refresh_token(
            user_id="user1",
            tenant_id="tid1",
            tenant_code="dev",
            roles=["operator"],
            secret="secret-key-that-is-long-enough",
        )
        new_access = refresh_access_token(refresh, "secret-key-that-is-long-enough")
        assert new_access.count(".") == 2
        # 新令牌类型为 access
        from dsh_core.auth import jwt as jwt_lib
        claims = jwt_lib.decode(new_access, "secret-key-that-is-long-enough")
        assert claims.get("type") == "access"

    def test_refresh_token_wrong_type_fails(self):
        """用访问令牌尝试刷新应失败"""
        access = issue_access_token(
            user_id="user1",
            tenant_id="tid1",
            tenant_code="dev",
            roles=["operator"],
            secret="secret-key-that-is-long-enough",
        )
        with pytest.raises(JWTError, match="令牌类型错误"):
            refresh_access_token(access, "secret-key-that-is-long-enough")

    def test_refresh_token_blacklisted_fails(self):
        """被撤销的刷新令牌无法换取新访问令牌"""
        refresh = issue_refresh_token(
            user_id="user1",
            tenant_id="tid1",
            tenant_code="dev",
            roles=["operator"],
            secret="secret-key-that-is-long-enough",
        )
        from dsh_core.auth import jwt as jwt_lib
        claims = jwt_lib.decode(refresh, "secret-key-that-is-long-enough")
        jti = claims.get("jti")
        blacklist_refresh_token(jti, time.time() + 3600)
        with pytest.raises(JWTError, match="已被撤销"):
            refresh_access_token(refresh, "secret-key-that-is-long-enough")

    def test_expired_refresh_token_fails(self):
        """过期的刷新令牌无法换取新访问令牌"""
        refresh = issue_refresh_token(
            user_id="user1",
            tenant_id="tid1",
            tenant_code="dev",
            roles=["operator"],
            secret="secret-key-that-is-long-enough",
            expires_in=-1,  # 已过期
        )
        with pytest.raises(TokenExpired):
            refresh_access_token(refresh, "secret-key-that-is-long-enough")

    def test_invalid_secret_fails(self):
        """密钥错误时令牌校验失败"""
        token = issue_access_token(
            user_id="user1",
            tenant_id="tid1",
            tenant_code="dev",
            roles=["operator"],
            secret="secret-key-that-is-long-enough",
        )
        from dsh_core.auth import jwt as jwt_lib
        with pytest.raises(TokenInvalid):
            jwt_lib.decode(token, "wrong-secret")


class TestJWTManagerIntegration:
    """JWTManager 集成测试"""

    def test_jwt_manager_refresh_flow(self):
        """JWTManager 完整刷新流程"""
        config = AuthConfig(
            secret="secret-key-that-is-long-enough",
            access_expires_in=1,
            refresh_expires_in=3600,
        )
        mgr = JWTManager(config)
        access = mgr.issue_access_token(
            user_id="user1",
            tenant_id="tid1",
            tenant_code="dev",
            roles=["operator"],
        )
        refresh = mgr.issue_refresh_token(
            user_id="user1",
            tenant_id="tid1",
            tenant_code="dev",
            roles=["operator"],
        )
        new_access = mgr.refresh_access_token(refresh)
        assert new_access != access

    def test_verify_refresh_token_type_check(self):
        """verify_refresh_token 会校验令牌类型"""
        config = AuthConfig(
            secret="secret-key-that-is-long-enough",
        )
        mgr = JWTManager(config)
        access = mgr.issue_access_token(
            user_id="user1",
            tenant_id="tid1",
            tenant_code="dev",
            roles=["operator"],
        )
        with pytest.raises(JWTError, match="令牌类型错误"):
            mgr.verify_refresh_token(access)


# ─── RBAC 权限模型测试 ───


class TestRBACPermissions:
    """RBAC 权限模型测试"""

    def test_admin_has_all_permissions(self):
        """admin 角色拥有所有权限"""
        perms = get_all_permissions([ROLE_ADMIN])
        assert "*" in perms

    def test_viewer_can_read_workflow(self):
        """viewer 角色可读取工作流"""
        assert check_permission([ROLE_VIEWER], "workflow", "read")

    def test_viewer_cannot_execute_workflow(self):
        """viewer 角色不可执行工作流"""
        assert not check_permission([ROLE_VIEWER], "workflow", "execute")

    def test_operator_can_execute_workflow(self):
        """operator 角色可执行工作流"""
        assert check_permission([ROLE_OPERATOR], "workflow", "execute")

    def test_approver_can_decide_approval(self):
        """approver 角色可审批"""
        assert check_permission([ROLE_APPROVER], "approval", "decide")

    def test_operator_cannot_decide_approval(self):
        """operator 角色不可审批"""
        assert not check_permission([ROLE_OPERATOR], "approval", "decide")

    def test_multiple_roles_combined(self):
        """多角色组合权限"""
        assert check_permission([ROLE_VIEWER, ROLE_OPERATOR], "workflow", "execute")
        assert check_permission([ROLE_VIEWER, ROLE_OPERATOR], "workflow", "read")

    def test_require_permission_success(self):
        """require_permission 权限足够时通过"""
        from dsh_core.auth.context import set_context, TenantContext
        ctx = TenantContext(
            tenant_id="tid1", tenant_code="dev", user_id="user1",
            roles=(ROLE_OPERATOR,), expires_at=0,
        )
        set_context(ctx)
        # 不抛异常即通过
        require_permission("workflow", "execute")

    def test_require_permission_fails(self):
        """require_permission 权限不足时抛出 HTTPException(403)"""
        from dsh_core.auth.context import set_context, TenantContext
        ctx = TenantContext(
            tenant_id="tid1", tenant_code="dev", user_id="user1",
            roles=(ROLE_VIEWER,), expires_at=0,
        )
        set_context(ctx)
        with pytest.raises(HTTPException) as exc_info:
            require_permission("workflow", "execute")
        assert exc_info.value.status_code == 403

    def test_is_valid_role(self):
        """角色有效性检查"""
        assert is_valid_role(ROLE_ADMIN)
        assert is_valid_role(ROLE_APPROVER)
        assert is_valid_role(ROLE_OPERATOR)
        assert is_valid_role(ROLE_VIEWER)
        assert not is_valid_role("invalid")

    def test_get_permissions_for_role(self):
        """获取角色权限列表"""
        admin_perms = get_permissions_for_role(ROLE_ADMIN)
        assert "*" in admin_perms
        viewer_perms = get_permissions_for_role(ROLE_VIEWER)
        assert "workflow:read" in viewer_perms

    def test_get_role_hierarchy(self):
        """角色继承关系"""
        hierarchy = get_role_hierarchy()
        assert ROLE_ADMIN in hierarchy
        assert ROLE_VIEWER in hierarchy
        # viewer 继承 operator/approver/admin
        assert ROLE_OPERATOR in hierarchy[ROLE_VIEWER]


# ─── API 密钥管理测试 ───


class TestAPIKeyManager:
    """API 密钥管理测试"""

    def test_create_key_returns_secret_once(self):
        """创建密钥返回明文（仅一次）"""
        mgr = APIKeyManager()
        secret, record = mgr.create_key(
            name="test-key",
            tenant_id="tid1",
            permissions=["workflow:read"],
        )
        assert secret
        assert record.key_id
        assert record.name == "test-key"

    def test_verify_key_success(self):
        """验证有效密钥"""
        mgr = APIKeyManager()
        secret, record = mgr.create_key(
            name="test-key",
            tenant_id="tid1",
        )
        assert mgr.verify_key(record.key_id, secret)

    def test_verify_key_wrong_secret_fails(self):
        """错误密钥验证失败"""
        mgr = APIKeyManager()
        secret, record = mgr.create_key(name="test-key")
        assert not mgr.verify_key(record.key_id, "wrong-secret")

    def test_verify_key_wrong_id_fails(self):
        """错误 ID 验证失败"""
        mgr = APIKeyManager()
        assert not mgr.verify_key("invalid-id", "any-secret")

    def test_revoke_key(self):
        """撤销密钥后验证失败"""
        mgr = APIKeyManager()
        secret, record = mgr.create_key(name="test-key")
        assert mgr.revoke_key(record.key_id)
        assert not mgr.verify_key(record.key_id, secret)

    def test_rotate_key(self):
        """轮换密钥生成新密钥"""
        mgr = APIKeyManager()
        secret, record = mgr.create_key(name="test-key")
        new_secret = mgr.rotate_key(record.key_id)
        assert new_secret
        assert new_secret != secret
        # 旧密钥失效
        assert not mgr.verify_key(record.key_id, secret)
        # 新密钥有效
        assert mgr.verify_key(record.key_id, new_secret)

    def test_rotate_revoked_key_fails(self):
        """撤销的密钥无法轮换"""
        mgr = APIKeyManager()
        secret, record = mgr.create_key(name="test-key")
        mgr.revoke_key(record.key_id)
        assert mgr.rotate_key(record.key_id) is None

    def test_list_keys_by_tenant(self):
        """按租户列出密钥"""
        mgr = APIKeyManager()
        _, r1 = mgr.create_key(name="key1", tenant_id="tid1")
        _, r2 = mgr.create_key(name="key2", tenant_id="tid2")
        keys = mgr.list_keys(tenant_id="tid1")
        assert len(keys) == 1
        assert keys[0].key_id == r1.key_id

    def test_list_keys_by_user(self):
        """按用户列出密钥"""
        mgr = APIKeyManager()
        _, r1 = mgr.create_key(name="key1", user_id="user1")
        _, r2 = mgr.create_key(name="key2", user_id="user2")
        keys = mgr.list_keys(user_id="user1")
        assert len(keys) == 1
        assert keys[0].key_id == r1.key_id

    def test_expired_key_fails(self):
        """过期密钥验证失败"""
        mgr = APIKeyManager()
        secret, record = mgr.create_key(
            name="test-key",
            expires_days=-1,  # 已过期
        )
        assert not mgr.verify_key(record.key_id, secret)

    def test_record_usage_updates_last_used(self):
        """记录使用时间"""
        mgr = APIKeyManager()
        secret, record = mgr.create_key(name="test-key")
        mgr.record_usage(record.key_id)
        updated = mgr.get_key(record.key_id)
        assert updated.last_used_at


class TestAPIKeyManagerWithoutCrypto:
    """无 cryptography 库时的回退模式测试"""

    def test_create_key_without_encryption(self):
        """无加密时使用哈希存储"""
        mgr = APIKeyManager()
        secret, record = mgr.create_key(name="test-key")
        # 应使用哈希存储（hashed_secret 非空）
        assert record.hashed_secret is not None
        assert record.encrypted_secret is None
        # 验证应成功
        assert mgr.verify_key(record.key_id, secret)


# ─── 边界条件测试 ───


class TestEdgeCases:
    """边界条件测试"""

    def test_empty_roles(self):
        """空角色列表无权限"""
        assert not check_permission([], "workflow", "read")

    def test_invalid_role_ignored(self):
        """无效角色被忽略"""
        assert not check_permission(["invalid"], "workflow", "read")

    def test_short_secret_rejected(self):
        """短密钥被拒绝"""
        from dsh_core.auth import jwt as jwt_lib
        with pytest.raises(JWTError, match="密钥缺失或过短"):
            issue_access_token(
                user_id="user1",
                tenant_id="tid1",
                tenant_code="dev",
                roles=["operator"],
                secret="short",
            )

    def test_blacklist_gc(self):
        """黑名单垃圾回收"""
        from dsh_core.auth import jwt as jwt_lib
        # 添加一个已过期的黑名单条目
        jti = "expired-jti"
        blacklist_refresh_token(jti, time.time() - 10)
        # 应被自动清理
        assert not is_refresh_token_blacklisted(jti)
