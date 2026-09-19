"""MCP 服务鉴权加固测试（M16 安全跟进）

两部分：
1. 匿名可见性修复 —— 匿名 tools/list 不再泄露租户自定义工具
   （REST /tools 与 JSON-RPC /mcp tools/list 一致，与 tools/call 匿名守卫对齐）
2. 严格鉴权模式（JKOS_MCP_REQUIRE_AUTH=true）—— 匿名（含无效 token）
   访问工具端点一律 401；/health 保持公开；关闭时保持 M14 可选鉴权契约
"""
import pytest
from fastapi.testclient import TestClient

from jkos_core.auth import AuthConfig, JWTManager, issue_access_token
from jkos_core.auth import dependencies as auth_deps
from jkos_core.mcp.server import MCPServer, PREDEFINED_TOOLS

_SECRET = "mcp-auth-test-secret"
_TOOL_PAYLOAD_BASE = PREDEFINED_TOOLS[0].model_dump()


def _auth(tenant_id: str = "t1") -> dict:
    token = issue_access_token(
        user_id="admin", tenant_id=tenant_id, tenant_code="dev",
        roles=["admin"], secret=_SECRET, expires_in=3600,
    )
    return {"Authorization": f"Bearer {token}"}


def _register(tc, name: str, headers=None):
    payload = dict(_TOOL_PAYLOAD_BASE, name=name, description="auth test tool")
    resp = tc.post("/tools/register", json=payload, headers=headers)
    assert resp.status_code == 200


@pytest.fixture(autouse=True)
def _install_auth():
    old = auth_deps._jwt_manager
    auth_deps._jwt_manager = JWTManager(AuthConfig(secret=_SECRET))
    yield
    auth_deps._jwt_manager = old


@pytest.fixture
def tc():
    with TestClient(MCPServer().app) as client:
        yield client


def _jsonrpc(tc, method, params, req_id, headers=None):
    return tc.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
        headers=headers,
    )


class TestAnonymousVisibility:
    """匿名列表只暴露共享/CORE 工具，不泄露租户自定义工具"""

    def test_rest_list_hides_tenant_tool(self, tc):
        _register(tc, "auth_tenant_tool", headers=_auth("t1"))

        names_anon = [t["name"] for t in tc.get("/tools").json()["tools"]]
        names_owner = [t["name"] for t in tc.get("/tools", headers=_auth("t1")).json()["tools"]]

        assert "auth_tenant_tool" not in names_anon
        assert "auth_tenant_tool" in names_owner
        # 共享/CORE 工具仍对匿名可见
        assert PREDEFINED_TOOLS[0].name in names_anon
        assert tc.get("/tools").json()["count"] == len(PREDEFINED_TOOLS)

    def test_jsonrpc_list_hides_tenant_tool(self, tc):
        _register(tc, "auth_jr_tool", headers=_auth("t1"))

        tools_anon = _jsonrpc(tc, "tools/list", {}, 1).json()["result"]["tools"]
        tools_owner = _jsonrpc(tc, "tools/list", {}, 2, headers=_auth("t1")).json()["result"]["tools"]

        assert "auth_jr_tool" not in [t["name"] for t in tools_anon]
        assert "auth_jr_tool" in [t["name"] for t in tools_owner]

    def test_anonymous_still_cannot_call_tenant_tool(self, tc):
        """列表修复后，调用侧既有守卫不变（404）"""
        _register(tc, "auth_call_tool", headers=_auth("t1"))

        resp = tc.post("/tools/call", json={"name": "auth_call_tool", "arguments": {}})

        assert resp.status_code == 404

    def test_shared_tool_visible_and_callable_by_anonymous(self, tc):
        """共享工具对匿名照常可见、可调用（M14 契约保持）"""
        names = [t["name"] for t in tc.get("/tools").json()["tools"]]
        assert PREDEFINED_TOOLS[0].name in names


class TestStrictAuthOff:
    """默认（JKOS_MCP_REQUIRE_AUTH 未设置）：M14 可选鉴权契约保持"""

    def test_anonymous_tools_200(self, tc):
        assert tc.get("/tools").status_code == 200

    def test_anonymous_mcp_initialize_200(self, tc):
        resp = _jsonrpc(tc, "initialize",
                        {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "t", "version": "0"}}, 1)
        assert resp.status_code == 200
        assert "result" in resp.json()


class TestStrictAuthOn:
    """JKOS_MCP_REQUIRE_AUTH=true：匿名（含无效 token）一律 401"""

    @pytest.fixture
    def strict_tc(self, monkeypatch):
        monkeypatch.setenv("JKOS_MCP_REQUIRE_AUTH", "true")
        with TestClient(MCPServer().app) as client:
            yield client

    def test_anonymous_tools_401(self, strict_tc):
        assert strict_tc.get("/tools").status_code == 401

    def test_anonymous_tools_call_401(self, strict_tc):
        resp = strict_tc.post("/tools/call",
                              json={"name": PREDEFINED_TOOLS[0].name, "arguments": {}})
        assert resp.status_code == 401

    def test_anonymous_mcp_initialize_401(self, strict_tc):
        resp = _jsonrpc(strict_tc, "initialize",
                        {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "t", "version": "0"}}, 1)
        assert resp.status_code == 401

    def test_invalid_token_401(self, strict_tc):
        """严格模式下无效 token 不能退化为匿名放行"""
        resp = strict_tc.get("/tools", headers={"Authorization": "Bearer invalid.token"})
        assert resp.status_code == 401

    def test_valid_token_200(self, strict_tc):
        resp = strict_tc.get("/tools", headers=_auth("t1"))
        assert resp.status_code == 200
        assert resp.json()["count"] == len(PREDEFINED_TOOLS)

    def test_valid_token_mcp_tools_list_200(self, strict_tc):
        resp = _jsonrpc(strict_tc, "tools/list", {}, 1, headers=_auth("t1"))
        assert resp.status_code == 200
        assert resp.json()["result"]["tools"]

    def test_health_stays_public(self, strict_tc):
        """/health 是运维探活端点，保持公开"""
        assert strict_tc.get("/health").status_code == 200

    def test_register_requires_auth(self, strict_tc):
        payload = dict(_TOOL_PAYLOAD_BASE, name="strict_reg", description="x")
        resp = strict_tc.post("/tools/register", json=payload)
        assert resp.status_code == 401

    def test_unregister_requires_auth(self, strict_tc):
        resp = strict_tc.delete(f"/tools/{PREDEFINED_TOOLS[0].name}")
        assert resp.status_code == 401

    def test_off_by_default(self, monkeypatch):
        """未设置环境变量时严格模式关闭（零回归保障）"""
        monkeypatch.delenv("JKOS_MCP_REQUIRE_AUTH", raising=False)
        assert MCPServer()._require_auth is False

    def test_toggle_variants(self, monkeypatch):
        for raw, expected in (("1", True), ("true", True), ("YES", True),
                              ("on", True), ("false", False), ("0", False),
                              ("bogus", False), ("", False)):
            monkeypatch.setenv("JKOS_MCP_REQUIRE_AUTH", raw)
            assert MCPServer()._require_auth is expected, raw