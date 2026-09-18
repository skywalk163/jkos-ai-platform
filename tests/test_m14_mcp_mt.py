"""M14: MCP 网关多租户集成测试

覆盖三块需求：
- 租户级工具可见性（注册 / 列表 / 调用 / 匿名）
- 配额限流（REST 429 / JSON-RPC -32029，采用小桶注入实现确定性触发）
- 可选鉴权（缺省 / 无效 token / 有效 token，绝不返回 401）

测试 token 策略：
- 通过 configure_auth 注入全局 JWTManager，缺省/无效 Bearer 走 get_optional_context
  返回 None -> 匿名；有效 Bearer 解析出 TenantContext。
- 模块结束恢复原全局 _jwt_manager，避免影响其它测试模块。
"""

import pytest
from fastapi.testclient import TestClient

from dsh_core.auth import (
    AuthConfig,
    JWTManager,
    issue_access_token,
)
from dsh_core.auth import dependencies as auth_deps
from dsh_core.mcp.server import PREDEFINED_TOOLS, create_app
from dsh_core.utils.ratelimit import RateLimiter, _global_limiter

_SECRET = "m14-test-secret-0123456789abcdef"
_TENANT_A = ("tenant-a", "TA")
_TENANT_B = ("tenant-b", "TB")


@pytest.fixture(scope="module", autouse=True)
def _setup_global_auth():
    """注入全局 JWTManager，结束后恢复原状态"""
    old = auth_deps._jwt_manager
    auth_deps._jwt_manager = JWTManager(AuthConfig(secret=_SECRET))
    yield
    auth_deps._jwt_manager = old


@pytest.fixture
def tc():
    """与 test_mcp_server.py 一致的客户端构造方式"""
    with TestClient(create_app()) as client:
        yield client


def _auth(tenant_id: str, tenant_code: str) -> dict:
    """签发租户访问令牌请求头"""
    token = issue_access_token(
        user_id="admin",
        tenant_id=tenant_id,
        tenant_code=tenant_code,
        roles=["admin"],
        secret=_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


def _tool_payload(name: str, description: str = "M14 tenant tool") -> dict:
    """以内置工具的真实模型形状构造注册载荷，保证 MCPTool(**data) 可反序列化"""
    base = PREDEFINED_TOOLS[0].model_dump()
    base["name"] = name
    base["description"] = description
    return base


def _register(tc, name: str, headers=None) -> dict:
    resp = tc.post("/tools/register", json=_tool_payload(name), headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"status": "registered", "tool_name": name}
    return resp.json()


def _jsonrpc(tc, method: str, params: dict, req_id: int, headers=None) -> dict:
    return tc.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
        headers=headers,
    ).json()


class TestOptionalAuth:
    """可选鉴权：缺省 / 无效 / 有效均不应返回 401"""

    def test_no_token_is_anonymous_200(self, tc):
        resp = tc.get("/tools")
        assert resp.status_code == 200
        assert resp.json()["count"] == len(PREDEFINED_TOOLS)

    def test_invalid_bearer_treated_as_anonymous(self, tc):
        resp = tc.get("/tools", headers={"Authorization": "Bearer invalid.token.here"})
        assert resp.status_code == 200  # 绝不 401
        assert resp.json()["count"] == len(PREDEFINED_TOOLS)

    def test_valid_bearer_resolves_tenant(self, tc):
        resp = tc.get("/tools", headers=_auth(*_TENANT_A))
        assert resp.status_code == 200
        assert resp.json()["count"] == len(PREDEFINED_TOOLS)


class TestRestTenantVisibility:
    """REST /tools 列表与 /tools/call 的租户级可见性"""

    def test_register_and_list_visibility(self, tc):
        _register(tc, "m14_rest_tool", headers=_auth(*_TENANT_A))
        names_a = [t["name"] for t in tc.get("/tools", headers=_auth(*_TENANT_A)).json()["tools"]]
        names_b = [t["name"] for t in tc.get("/tools", headers=_auth(*_TENANT_B)).json()["tools"]]
        names_anon = [t["name"] for t in tc.get("/tools").json()["tools"]]

        assert "m14_rest_tool" in names_a  # 归属租户可见
        assert "m14_rest_tool" not in names_b  # 其它租户不可见
        assert "m14_rest_tool" in names_anon  # 匿名列表可见全部

        assert tc.get("/tools", headers=_auth(*_TENANT_A)).json()["count"] == len(PREDEFINED_TOOLS) + 1
        assert tc.get("/tools", headers=_auth(*_TENANT_B)).json()["count"] == len(PREDEFINED_TOOLS)

    def test_call_visibility(self, tc):
        _register(tc, "m14_call_tool", headers=_auth(*_TENANT_A))

        # 其它租户调用 -> 404
        resp_b = tc.post(
            "/tools/call", json={"name": "m14_call_tool", "arguments": {}}, headers=_auth(*_TENANT_B)
        )
        assert resp_b.status_code == 404
        assert resp_b.json() == {"error": "Tool not found: m14_call_tool"}

        # 匿名调用归属租户工具 -> 404（匿名仅可调用共享/CORE 工具）
        resp_anon = tc.post("/tools/call", json={"name": "m14_call_tool", "arguments": {}})
        assert resp_anon.status_code == 404
        assert resp_anon.json() == {"error": "Tool not found: m14_call_tool"}

        # 归属租户调用 -> 200（自定义工具未注册到执行器，返回结构化错误）
        resp_a = tc.post(
            "/tools/call", json={"name": "m14_call_tool", "arguments": {}}, headers=_auth(*_TENANT_A)
        )
        assert resp_a.status_code == 200
        data = resp_a.json()
        assert data["is_error"] is True
        assert data["error_message"] == "Unknown tool: m14_call_tool"
        assert data["content"] is None

    def test_anonymous_register_becomes_shared(self, tc):
        _register(tc, "m14_shared_tool")  # 无 token -> 共享
        for headers in (None, _auth(*_TENANT_A), _auth(*_TENANT_B)):
            names = [t["name"] for t in tc.get("/tools", headers=headers).json()["tools"]]
            assert "m14_shared_tool" in names


class TestRestRateLimit:
    """REST /tools/call 配额限流 429"""

    def test_rest_429_after_bucket_exhausted(self, tc):
        # 注入 1 容量、无补充的小桶：首次消费后必然 429
        _global_limiter._limiters["tenant:tenant-b"] = RateLimiter(rps=0, burst=1)
        headers = _auth(*_TENANT_B)
        body = {"name": "m14_nope", "arguments": {}}

        resp1 = tc.post("/tools/call", json=body, headers=headers)
        assert resp1.status_code == 404  # 限流通过、工具不存在

        resp2 = tc.post("/tools/call", json=body, headers=headers)
        assert resp2.status_code == 429
        assert resp2.json() == {"error": "请求过于频繁，请稍后重试"}
        assert resp2.headers.get("Retry-After") == "1"

    def test_anonymous_bucket_exhausted_429(self, tc):
        _global_limiter._limiters["tenant:anonymous"] = RateLimiter(rps=0, burst=1)
        body = {"name": "m14_nope", "arguments": {}}

        resp1 = tc.post("/tools/call", json=body)
        assert resp1.status_code == 404

        resp2 = tc.post("/tools/call", json=body)
        assert resp2.status_code == 429


class TestJsonRpc:
    """MCP 标准 JSON-RPC 端点多租户行为"""

    def test_initialize(self, tc):
        data = _jsonrpc(tc, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}, 1)
        assert data["result"]["protocolVersion"] == "2024-11-05"
        assert data["result"]["capabilities"]["tools"]["listChanged"] is True
        assert data["result"]["serverInfo"]["name"] == "DSH MCP Server"

    def test_tools_list_no_count_key(self, tc):
        data = _jsonrpc(tc, "tools/list", {}, 2)
        assert data["id"] == 2
        assert "tools" in data["result"]
        assert "count" not in data["result"]  # JSON-RPC 形状不含 count
        assert len(data["result"]["tools"]) == len(PREDEFINED_TOOLS)

    def test_tools_list_tenant_filter(self, tc):
        _register(tc, "m14_jr_tool", headers=_auth(*_TENANT_A))

        names_b = [t["name"] for t in _jsonrpc(tc, "tools/list", {}, 3, headers=_auth(*_TENANT_B))["result"]["tools"]]
        assert "m14_jr_tool" not in names_b

        names_anon = [t["name"] for t in _jsonrpc(tc, "tools/list", {}, 4)["result"]["tools"]]
        assert "m14_jr_tool" in names_anon

    def test_tools_call_visibility(self, tc):
        _register(tc, "m14_jr_call", headers=_auth(*_TENANT_A))
        params = {"name": "m14_jr_call", "arguments": {}}

        # 其它租户 -> -32601
        err_b = _jsonrpc(tc, "tools/call", params, 5, headers=_auth(*_TENANT_B))
        assert err_b["error"]["code"] == -32601
        assert err_b["error"]["message"] == "Tool not found: m14_jr_call"

        # 匿名 -> -32601
        err_anon = _jsonrpc(tc, "tools/call", params, 6)
        assert err_anon["error"]["code"] == -32601

        # 归属租户 -> 200（自定义工具 -> 执行器返回 isError）
        ok = _jsonrpc(tc, "tools/call", params, 7, headers=_auth(*_TENANT_A))
        assert "result" in ok
        assert ok["result"]["isError"] is True
        assert ok["result"]["content"] == [{"type": "text", "text": "No content"}]

    def test_tools_call_rate_limit_429(self, tc):
        _global_limiter._limiters["tenant:anonymous"] = RateLimiter(rps=0, burst=1)
        params = {"name": "m14_nope", "arguments": {}}

        first = _jsonrpc(tc, "tools/call", params, 8)
        assert first["error"]["code"] == -32601  # 限流通过、工具不存在，令牌被消费

        second = _jsonrpc(tc, "tools/call", params, 9)
        assert second["error"]["code"] == -32029
        assert second["error"]["message"] == "请求过于频繁，请稍后重试"