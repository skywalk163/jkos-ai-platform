"""认证模块单元测试（任务 0.5）— 纯 assert，兼容 pytest 与 m0_selftest 直跑"""
import time

from jkos_core.auth import jwt as jwt_lib
from jkos_core.auth.context import (
    PermissionDenied,
    TenantContext,
    current_context,
    reset_context,
    set_context,
)
from jkos_core.auth.dependencies import AuthConfig, JWTManager

SECRET = "unit-test-secret-0123456789"


def test_jwt_roundtrip():
    token = jwt_lib.encode({"sub": "u1", "tid": "t1", "roles": ["admin"]}, SECRET, expires_in=60)
    claims = jwt_lib.decode(token, SECRET)
    assert claims["sub"] == "u1" and claims["roles"] == ["admin"]
    assert claims["exp"] > int(time.time())


def test_jwt_expired():
    token = jwt_lib.encode({"sub": "u1"}, SECRET, expires_in=-10)
    raised = False
    try:
        jwt_lib.decode(token, SECRET)
    except jwt_lib.TokenExpired:
        raised = True
    assert raised


def test_jwt_bad_signature():
    token = jwt_lib.encode({"sub": "u1"}, SECRET, expires_in=60)
    raised = False
    try:
        jwt_lib.decode(token, "another-secret-0123456789")
    except jwt_lib.TokenInvalid:
        raised = True
    assert raised


def test_jwt_garbage():
    raised = False
    try:
        jwt_lib.decode("not-a-token", SECRET)
    except jwt_lib.JWTError:
        raised = True
    assert raised


def test_jwt_short_secret_rejected():
    raised = False
    try:
        jwt_lib.encode({"sub": "u1"}, "short", expires_in=60)
    except jwt_lib.JWTError:
        raised = True
    assert raised


def test_tenant_context_roles():
    ctx = TenantContext(tenant_id="t", tenant_code="dev", user_id="u", roles=("operator",))
    assert ctx.has_role("operator")
    assert ctx.has_role("admin") is False
    ctx.require_role("operator")
    raised = False
    try:
        ctx.require_role("approver")
    except PermissionDenied:
        raised = True
    assert raised
    # admin 隐式继承
    admin = TenantContext(tenant_id="t", tenant_code="dev", user_id="a", roles=("admin",))
    assert admin.has_role("viewer")


def test_context_var_set_reset():
    ctx = TenantContext(tenant_id="t", tenant_code="dev", user_id="u", roles=("viewer",))
    token = set_context(ctx)
    assert current_context() is ctx
    reset_context(token)
    assert current_context() is None


def test_jwt_manager_issue_and_verify():
    mgr = JWTManager(AuthConfig(secret=SECRET, expires_in=3600))
    token = mgr.issue_token("tid-1", "dev", "alice", ["approver", "operator"])
    ctx = mgr.verify(token)
    assert isinstance(ctx, TenantContext)
    assert ctx.tenant_code == "dev" and ctx.user_id == "alice"
    assert set(ctx.roles) == {"approver", "operator"}
    assert ctx.expires_at > int(time.time())


def test_jwt_unsupported_algorithm_rejected():
    """M6.1: header.alg != HS256 必须抛 TokenInvalid（jwt.py:66）"""
    import hashlib
    import hmac
    import json

    header = {"alg": "RS256", "typ": "JWT"}
    body = {"sub": "u1", "iat": int(time.time()), "exp": int(time.time()) + 60}
    signing_input = (
        f"{jwt_lib._b64url_encode(json.dumps(header, separators=(',', ':')).encode())}."
        f"{jwt_lib._b64url_encode(json.dumps(body, separators=(',', ':')).encode())}"
    )
    # 签名部分仍用 HS256 计算（能通过签名校验），但 header 声称 RS256 以触发算法检查
    sig = hmac.new(SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    token = f"{signing_input}.{jwt_lib._b64url_encode(sig)}"

    raised = False
    try:
        jwt_lib.decode(token, SECRET)
    except jwt_lib.TokenInvalid as e:
        raised = True
        assert "不支持的算法" in str(e)
    assert raised
