"""M16 智能中枢 - REST / SSE 路由测试

覆盖 AC-2（201 + SSE 事件转发）、AC-4（无凭证 401 / 身份进入会话）、
AC-5（租户配额 429 + Retry-After）。

用注入 fake SDK 的 gateway 验证，不依赖真实 harness runtime。
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jkos_core.auth import AuthConfig, JWTManager, issue_access_token
from jkos_core.auth import dependencies as auth_deps
from jkos_core.auth.context import TenantContext
from jkos_core.harness.config import HarnessConfig
from jkos_core.harness.gateway import HarnessGateway
from jkos_core.harness.routes import create_harness_router
from jkos_core.harness.runtime import SidecarManager
from jkos_core.utils.ratelimit import _global_limiter

_SECRET = "harness-test-secret"
_TENANT = "t1"


def _auth(tenant_id: str = _TENANT, tenant_code: str = "dev",
          user_id: str = "admin") -> dict:
    token = issue_access_token(
        user_id=user_id, tenant_id=tenant_id, tenant_code=tenant_code,
        roles=["admin"], secret=_SECRET, expires_in=3600,
    )
    return {"Authorization": f"Bearer {token}"}


class FakeRunResult:
    def __init__(self, session_id):
        self.session_id = session_id
        self.final_response = "已完成"
        self.finish_reason = "completed"
        self.events = []
        self.notifications = []


class FakeClient:
    def __init__(self, config):
        self.config = config
        self.prompts = []

    def run(self, input, *, session_id=None, on_notification=None):
        self.prompts.append(input)
        if on_notification is not None:
            on_notification(SimpleNamespace(
                method="session.event",
                payload={"sessionId": session_id, "event": {"type": "message"}},
            ))
        return FakeRunResult(session_id)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _install_auth():
    """注入全局 JWTManager，结束后恢复（照 M14 测试范式）"""
    old = auth_deps._jwt_manager
    auth_deps._jwt_manager = JWTManager(AuthConfig(secret=_SECRET))
    yield
    auth_deps._jwt_manager = old


@pytest.fixture
def gateway(tmp_path):
    config = HarnessConfig(enabled=True, dsh_home=str(tmp_path / "home"),
                           workspace=str(tmp_path / "ws"))
    sidecar = SidecarManager(config, client_factory=FakeClient)
    return HarnessGateway(config, sidecar=sidecar)


@pytest.fixture
def tc(gateway):
    app = FastAPI()
    app.include_router(create_harness_router(gateway))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def session_id(tc):
    resp = tc.post("/api/v1/harness/sessions", headers=_auth())
    assert resp.status_code == 201
    return resp.json()["session_id"]


class TestHealth:
    def test_health_is_public(self, tc):
        """探活端点无需凭证（与 /api/v1/health 一致）"""
        resp = tc.get("/api/v1/harness/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["gateway"] == "harness"
        assert body["status"] == "idle"

    def test_health_does_not_leak_api_key(self, tc, gateway):
        gateway.config.api_key = "sk-should-not-leak"

        body = tc.get("/api/v1/harness/health").json()

        assert "sk-should-not-leak" not in str(body)


class TestSessionsAuth:
    def test_no_token_401(self, tc):
        """AC-4：无凭证请求被拒"""
        resp = tc.post("/api/v1/harness/sessions")

        assert resp.status_code == 401

    def test_invalid_token_401(self, tc):
        resp = tc.post("/api/v1/harness/sessions",
                       headers={"Authorization": "Bearer not-a-token"})

        assert resp.status_code == 401

    def test_list_requires_token(self, tc):
        assert tc.get("/api/v1/harness/sessions").status_code == 401


class TestCreateSession:
    def test_create_returns_201_with_identity(self, tc):
        """AC-2：创建会话返回 201，身份取自 JWT"""
        resp = tc.post("/api/v1/harness/sessions",
                       headers=_auth(user_id="alice"))

        assert resp.status_code == 201
        body = resp.json()
        assert body["session_id"].startswith("jks-")
        assert body["tenant_id"] == _TENANT
        assert body["user_id"] == "alice"
        assert body["turns"] == 0

    def test_list_returns_own_sessions(self, tc):
        tc.post("/api/v1/harness/sessions", headers=_auth())
        tc.post("/api/v1/harness/sessions", headers=_auth())
        tc.post("/api/v1/harness/sessions", headers=_auth(tenant_id="other"))

        body = tc.get("/api/v1/harness/sessions", headers=_auth()).json()

        assert body["count"] == 2


class TestMessages:
    def test_missing_session_404(self, tc):
        resp = tc.post("/api/v1/harness/sessions/nosuch/messages",
                       headers=_auth(), json={"content": "hi"})

        assert resp.status_code == 404

    def test_cross_tenant_session_404(self, tc, gateway):
        """跨租户访问他人会话应视为不存在"""
        other = gateway.create_session(
            TenantContext(tenant_id="other", tenant_code="dev", user_id="bob"))

        resp = tc.post(f"/api/v1/harness/sessions/{other.session_id}/messages",
                       headers=_auth(), json={"content": "hi"})

        assert resp.status_code == 404

    def test_empty_content_422(self, tc, session_id):
        resp = tc.post(f"/api/v1/harness/sessions/{session_id}/messages",
                       headers=_auth(), json={"content": ""})

        assert resp.status_code == 422

    def test_sse_forwards_events(self, tc, session_id):
        """AC-2：SSE 流至少转发 1 个 agent 事件"""
        with tc.stream("POST",
                       f"/api/v1/harness/sessions/{session_id}/messages",
                       headers=_auth(), json={"content": "你好"}) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = "".join(resp.iter_text())

        assert '"type": "session/start"' in body
        assert '"type": "notification"' in body
        assert '"type": "turn/end"' in body

    def test_sse_body_is_well_formed(self, tc, session_id):
        """每行事件体必须是合法 JSON（SSE data: 前缀格式）"""
        import json

        with tc.stream("POST",
                       f"/api/v1/harness/sessions/{session_id}/messages",
                       headers=_auth(), json={"content": "你好"}) as resp:
            body = "".join(resp.iter_text())

        payloads = [line[len("data: "):] for line in body.splitlines()
                    if line.startswith("data: ")]
        assert payloads, "SSE 响应体应包含 data: 行"
        for raw in payloads:
            json.loads(raw)


class TestQuota:
    def test_quota_exceeded_returns_429_with_retry_after(self, tc):
        """AC-5：租户配额超限返回 429 + Retry-After"""
        # 排空该租户令牌桶（burst 20 / rps 10）
        for _ in range(40):
            _global_limiter.acquire_by_tenant(_TENANT)

        resp = tc.post("/api/v1/harness/sessions", headers=_auth())

        assert resp.status_code == 429
        assert "Retry-After" in resp.headers

    def test_quota_is_per_tenant(self, tc):
        """排空 t1 不影响 other 租户"""
        for _ in range(40):
            _global_limiter.acquire_by_tenant(_TENANT)

        resp = tc.post("/api/v1/harness/sessions", headers=_auth(tenant_id="other"))

        assert resp.status_code == 201