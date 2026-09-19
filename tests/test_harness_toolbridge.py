"""M16 智能中枢 - ToolBridge 测试

覆盖 AC-3（工具桥接的租户边界）与安全缓解措施：
JKOS /mcp 的 _visible_tools(ctx) 在匿名上下文下返回**全部**工具，
因此桥接配置必须携带 token，本模块在缺 token 时拒绝构建。
"""
import pytest
from fastapi.testclient import TestClient

from jkos_core.auth import AuthConfig, JWTManager, issue_access_token
from jkos_core.auth import dependencies as auth_deps
from jkos_core.auth.context import TenantContext
from jkos_core.harness.config import HarnessConfig
from jkos_core.harness.toolbridge import (
    MCP_SERVER_NAME,
    PATCH_RELATIVE_PATH,
    TOKEN_ENV_VAR,
    ToolBridgeConfigError,
    apply_profile_patch,
    llm_provider_row,
    mcp_client_row,
    render_profile_patch,
)
from jkos_core.mcp.server import MCPServer, PREDEFINED_TOOLS

_SECRET = "toolbridge-test-secret"
_TOOL = PREDEFINED_TOOLS[0].name  # dsh_text_query
_SECRET_TOKEN = "jkos-internal-token-value"


def _ctx(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, tenant_code="dev", user_id="admin")


def _auth(tenant_id: str) -> dict:
    token = issue_access_token(
        user_id="admin", tenant_id=tenant_id, tenant_code="dev",
        roles=["admin"], secret=_SECRET, expires_in=3600,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _install_auth():
    old = auth_deps._jwt_manager
    auth_deps._jwt_manager = JWTManager(AuthConfig(secret=_SECRET))
    yield
    auth_deps._jwt_manager = old


@pytest.fixture
def config(tmp_path):
    return HarnessConfig(enabled=True, dsh_home=str(tmp_path / "home"),
                         dsh_bin="/data/dsh/harness/dsh-jkos.sh")


class TestMcpClientRow:
    def test_requires_token(self, config):
        """缺 token 必须拒绝构建 —— 否则匿名连接会导致全租户工具可见"""
        with pytest.raises(ToolBridgeConfigError):
            mcp_client_row(config, "")

    def test_blank_token_rejected(self, config):
        with pytest.raises(ToolBridgeConfigError):
            mcp_client_row(config, "   ")

    def test_row_shape(self, config):
        row = mcp_client_row(config, _SECRET_TOKEN)

        assert row["id"] == f"mcp-{MCP_SERVER_NAME}"
        assert row["name"] == "@deepseek-ai/dsh-mcp-client"
        cfg = row["config"]
        assert cfg["serverName"] == MCP_SERVER_NAME
        assert cfg["transport"] == "streamable-http"
        assert cfg["url"] == config.mcp_url
        assert cfg["failOnStartupError"] is True

    def test_token_not_baked_into_row(self, config):
        """token 经环境变量引用，不写入配置值"""
        row = mcp_client_row(config, _SECRET_TOKEN)

        assert _SECRET_TOKEN not in str(row)
        assert TOKEN_ENV_VAR in str(row["config"]["headers"])


class TestLlmProviderRow:
    def test_defaults_to_chat_completions(self, config):
        """公网 api.deepseek.com 只支持 chat-completions（messages 协议会 404）"""
        row = llm_provider_row(config)

        assert row["id"] == "llm-deepseek"
        assert row["name"] == "@deepseek-ai/dsh-llm-deepseek"
        assert row["config"]["protocol"] == "chat-completions"
        assert row["config"]["apiKeyEnv"] == "DEEPSEEK_API_KEY"

    def test_row_carries_id_to_override_bundle_row(self, config):
        """必须带 id：无 id 的 patch 行会被当作新增项而无法覆盖 bundle 既有行"""
        assert llm_provider_row(config)["id"] == "llm-deepseek"
        assert "- id: llm-deepseek" in render_profile_patch(config)

    def test_base_url_optional(self, config):
        assert "baseURL" not in llm_provider_row(config)["config"]

    def test_base_url_included_when_set(self, tmp_path):
        cfg = HarnessConfig(base_url="https://api.deepseek.com")

        assert llm_provider_row(cfg)["config"]["baseURL"] == "https://api.deepseek.com"

    def test_protocol_from_config(self):
        cfg = HarnessConfig(llm_protocol="messages")

        assert llm_provider_row(cfg)["config"]["protocol"] == "messages"

    def test_row_has_no_secret(self, config):
        assert "apiKeyEnv" in llm_provider_row(config)["config"]
        assert "DEEPSEEK_API_KEY" in str(llm_provider_row(config))


class TestProfilePatch:
    def test_render_contains_no_secret(self, config):
        """patch 文件不含密钥（密钥铁律）"""
        text = render_profile_patch(config)

        assert _SECRET_TOKEN not in text
        assert TOKEN_ENV_VAR in text

    def test_render_contains_llm_row(self, config):
        text = render_profile_patch(config)

        assert "@deepseek-ai/dsh-llm-deepseek" in text
        assert "protocol: chat-completions" in text

    def test_render_uses_insert_for_new_row(self, config):
        """新增条目必须用 insert 语义：Cordis patch 直接写带 id 的新条目会报
        `entry "<id>" not found`（patch 是按 id 合并到既有条目）"""
        text = render_profile_patch(config)

        assert "- insert:" in text
        assert "    - id: mcp-jkos" in text
        # 不得出现裸的顶层 mcp-jkos 条目（那会被当作覆盖 patch 而失败）
        assert "\n- id: mcp-jkos" not in text

    def test_render_fields(self, config):
        text = render_profile_patch(config)

        assert "      name: '@deepseek-ai/dsh-mcp-client'" in text
        assert "        transport: streamable-http" in text
        assert f"        url: {config.mcp_url}" in text
        assert "        failOnStartupError: true" in text

    def test_apply_writes_patch(self, config, tmp_path):
        path = apply_profile_patch(config)

        assert path == tmp_path / "home" / PATCH_RELATIVE_PATH
        assert path.is_file()
        assert TOKEN_ENV_VAR in path.read_text(encoding="utf-8")

    def test_apply_is_idempotent(self, config):
        first = apply_profile_patch(config)
        second = apply_profile_patch(config)

        assert first == second
        assert first.read_text(encoding="utf-8").count("dsh-mcp-client") == 1


class TestMcpWireFormat:
    """/mcp 标准端点的协议规范字段名 —— AC-3 的前置条件

    MCP 规范用驼峰（inputSchema），JKOS 内部模型用蛇形（input_schema）。
    若标准端点输出蛇形字段，外部 MCP 客户端会拒绝整个工具列表，
    表现为「连接成功但工具不可见」（实测 harness dsh-mcp-client 即如此）。
    """

    def test_wire_tool_uses_camel_case(self):
        from jkos_core.mcp.server import MCPTool, to_mcp_wire_tool

        tool = MCPTool(name="t", description="d", input_schema={"type": "object"})

        wire = to_mcp_wire_tool(tool)

        assert wire["inputSchema"] == {"type": "object"}
        assert "input_schema" not in wire

    def test_wire_tool_omits_empty_schemas(self):
        from jkos_core.mcp.server import MCPTool, to_mcp_wire_tool

        wire = to_mcp_wire_tool(MCPTool(name="t", description="d"))

        assert "inputSchema" not in wire
        assert "outputSchema" not in wire

    def test_mcp_endpoint_returns_spec_field_names(self):
        """标准端点 /mcp 必须输出 inputSchema（而非 input_schema）"""
        server = MCPServer()
        with TestClient(server.app) as client:
            resp = client.post("/mcp", json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
            })

        tools = resp.json()["result"]["tools"]

        assert tools, "标准端点应返回工具列表"
        assert all("inputSchema" in t for t in tools)
        assert all("input_schema" not in t for t in tools)

    def test_rest_endpoint_keeps_snake_case(self):
        """REST /tools 为非标接口，保持原字段名（避免破坏既有消费方）"""
        server = MCPServer()
        with TestClient(server.app) as client:
            resp = client.get("/tools")

        tools = resp.json()["tools"]

        assert tools
        assert all("input_schema" in t for t in tools)


class TestVisibilitySemantics:
    """JKOS /mcp 侧的工具可见性（AC-3 的安全边界证据）"""

    def test_cross_tenant_tool_not_listed(self):
        server = MCPServer()
        server._tool_owners[_TOOL] = "t2"

        names = [t.name for t in server._visible_tools(_ctx("t1"))]

        assert _TOOL not in names

    def test_owner_tenant_sees_own_tool(self):
        server = MCPServer()
        server._tool_owners[_TOOL] = "t2"

        names = [t.name for t in server._visible_tools(_ctx("t2"))]

        assert _TOOL in names

    def test_anonymous_lists_shared_only(self):
        """M16 安全加固后：匿名列表只返回共享/CORE 工具，租户自定义工具不泄露"""
        server = MCPServer()
        server._tool_owners[_TOOL] = "t2"

        names = [t.name for t in server._visible_tools(None)]

        assert _TOOL not in names

    def test_cross_tenant_call_rejected(self):
        """越租户调用被拒（404）—— 调用侧守卫是有效的"""
        server = MCPServer()
        server._tool_owners[_TOOL] = "t2"
        with TestClient(server.app) as client:
            resp = client.post("/tools/call", headers=_auth("t1"),
                               json={"name": _TOOL, "arguments": {"table": "t"}})

        assert resp.status_code == 404

    def test_shared_tool_visible_to_all(self):
        """共享工具（owner=None）对所有租户可见"""
        server = MCPServer()
        server._tool_owners.pop(_TOOL, None)

        names = [t.name for t in server._visible_tools(_ctx("t1"))]

        assert _TOOL in names