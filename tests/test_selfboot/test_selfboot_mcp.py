"""M20.2 harness 融合：dsh_optimize_execute / dsh_optimize_stats 两条 MCP 工具

验证口径沿用 M16 AC-3（agent 可见 mcp__jkos__* 工具并可调用）：
- 工具在 PREDEFINED_TOOLS 中、分类正确、不新增 ToolCategory；
- REST /tools（蛇形）与标准 /mcp tools/list（驼峰）都能列出；
- 匿名可见（CORE + 无 owner），严格鉴权模式下仍 401；
- execute() 经惰性单例落到自举闭环，cleanup() 释放单例。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jkos_core.mcp import server as mcp_server
from jkos_core.mcp.registry import ToolCategory
from jkos_core.mcp.server import (
    PREDEFINED_TOOLS,
    MCPServer,
    ToolExecutor,
    close_selfboot_engine,
    create_app,
    get_selfboot_engine,
    reset_selfboot_engine,
)
from jkos_core.selfboot.loop import DEFAULT_TASK, SelfBootstrapEngine
from jkos_core.selfboot.d3_testgen import D3Executor

OPTIMIZE_TOOLS = ("dsh_optimize_execute", "dsh_optimize_stats")

# 必须用带独有词的 DEFAULT_TASK：任意文案会被内置 61 个业务种子模板抢走推荐，
# 导致复现的不是本次新建模板（见 TestSeedTemplateCompetition）
SELFBOOT_TASK = DEFAULT_TASK

# 一个与任何既有模板都不匹配的任务，用于验证挂起审批路径
NOVEL_TASK = "为从未见过的目标 zzz 生成单元测试 xyzzy"


@pytest.fixture(autouse=True)
def _isolate_singleton():
    """单例是进程级的，测试前后必须清空，避免相互污染"""
    reset_selfboot_engine()
    yield
    reset_selfboot_engine()


@pytest.fixture
def selfboot_engine(tmp_path, sample_module, runner_cls, monkeypatch):
    """把单例替换为 tmp 隔离 + Fake 运行器的自举引擎（不真跑 pytest、不碰仓库存储）"""
    monkeypatch.setenv("DSH_SELFBOOT_DATA_DIR", str(tmp_path / "data"))
    engine = SelfBootstrapEngine(
        executor=D3Executor(runner=runner_cls(), default_target=f"{sample_module}:label",
                            repo_root=tmp_path),
        data_dir=str(tmp_path / "data"),
        report_dir=str(tmp_path / "reports"),
        target=f"{sample_module}:label",
    )
    monkeypatch.setattr(mcp_server, "_selfboot_engine", engine)
    return engine


class TestToolRegistration:
    """工具定义与分类"""

    def test_both_tools_registered(self):
        names = [t.name for t in PREDEFINED_TOOLS]
        for tool in OPTIMIZE_TOOLS:
            assert tool in names

    def test_execute_schema_requires_task(self):
        tool = next(t for t in PREDEFINED_TOOLS if t.name == "dsh_optimize_execute")
        assert tool.input_schema["required"] == ["task"]
        assert {"task", "target", "require_approval"} <= set(
            tool.input_schema["properties"])

    def test_stats_schema_only_limit(self):
        tool = next(t for t in PREDEFINED_TOOLS if t.name == "dsh_optimize_stats")
        assert set(tool.input_schema["properties"]) == {"limit"}
        assert "required" not in tool.input_schema

    def test_category_is_governance(self):
        for tool in OPTIMIZE_TOOLS:
            assert mcp_server._default_tool_category(tool) is ToolCategory.GOVERNANCE

    def test_no_new_tool_category_added(self):
        """工具路由断言 len(categories) == 9，新增枚举值会打破它"""
        from jkos_core.mcp.registry import get_registry

        assert len(get_registry().get_categories()) == 9

    def test_synced_into_registry_as_core(self, tmp_path):
        server = MCPServer(host="127.0.0.1", port=0)
        names = {t.name for t in server._visible_tools(None)}
        assert set(OPTIMIZE_TOOLS) <= names


class TestVisibility:
    """匿名可见性（M16 安全跟进口径）"""

    def test_anonymous_sees_optimize_tools(self):
        server = MCPServer(host="127.0.0.1", port=0)
        assert set(OPTIMIZE_TOOLS) <= {t.name for t in server._visible_tools(None)}

    def test_tenant_sees_optimize_tools(self):
        from types import SimpleNamespace

        server = MCPServer(host="127.0.0.1", port=0)
        ctx = SimpleNamespace(tenant_id="t-dev")
        assert set(OPTIMIZE_TOOLS) <= {t.name for t in server._visible_tools(ctx)}


class TestHttpEndpoints:
    """两个端点的工具列表（REST 蛇形 / 标准 MCP 驼峰）"""

    @pytest.fixture
    def client(self):
        with TestClient(create_app()) as tc:
            yield tc

    def test_rest_tools_lists_optimize_tools(self, client):
        data = client.get("/tools").json()
        by_name = {t["name"]: t for t in data["tools"]}
        assert set(OPTIMIZE_TOOLS) <= set(by_name)
        # REST 端点保持蛇形字段（M16 约定）
        assert "input_schema" in by_name["dsh_optimize_execute"]

    def test_mcp_tools_list_uses_camel_case_schema(self, client):
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                         "method": "tools/list"})
        tools = {t["name"]: t for t in resp.json()["result"]["tools"]}
        assert set(OPTIMIZE_TOOLS) <= set(tools)
        assert "inputSchema" in tools["dsh_optimize_execute"]
        assert "input_schema" not in tools["dsh_optimize_execute"]

    def test_count_matches_predefined_tools(self, client):
        assert client.get("/tools").json()["count"] == len(PREDEFINED_TOOLS)

    def test_strict_auth_mode_rejects_anonymous(self, client, monkeypatch):
        monkeypatch.setenv("JKOS_MCP_REQUIRE_AUTH", "true")
        with TestClient(create_app()) as strict:
            resp = strict.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                             "method": "tools/list"})
        assert resp.status_code == 401


class TestSingleton:
    """惰性单例的生命周期"""

    @pytest.mark.asyncio
    async def test_get_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DSH_SELFBOOT_DATA_DIR", str(tmp_path / "data"))
        first = await get_selfboot_engine()
        assert await get_selfboot_engine() is first
        assert first._initialized is True
        await close_selfboot_engine()

    @pytest.mark.asyncio
    async def test_close_releases_and_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DSH_SELFBOOT_DATA_DIR", str(tmp_path / "data"))
        engine = await get_selfboot_engine()
        await close_selfboot_engine()
        assert engine._initialized is False
        assert mcp_server._selfboot_engine is None
        await close_selfboot_engine()  # 再次调用不报错

    @pytest.mark.asyncio
    async def test_comps_llm_injection(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DSH_SELFBOOT_DATA_DIR", str(tmp_path / "data"))
        comps = type("C", (), {"llm": "LLM-FROM-COMPS"})()
        engine = await get_selfboot_engine(comps)
        assert engine.llm == "LLM-FROM-COMPS"
        await close_selfboot_engine()

    @pytest.mark.asyncio
    async def test_executor_cleanup_closes_singleton(self, selfboot_engine):
        executor = ToolExecutor(engine=None)
        try:
            assert mcp_server._selfboot_engine is not None
            await executor.cleanup()
            assert mcp_server._selfboot_engine is None
        finally:
            await executor._http_client.aclose()

    def test_selfboot_comps_defaults_to_none(self):
        assert ToolExecutor(engine=None)._selfboot_comps() is None

    def test_selfboot_comps_reads_engine_comps(self):
        from types import SimpleNamespace

        engine = SimpleNamespace(comps="COMPS")
        assert ToolExecutor(engine=engine)._selfboot_comps() == "COMPS"


class TestExecuteTool:
    """dsh_optimize_execute 的行为"""

    @pytest.mark.asyncio
    async def test_runs_closed_loop(self, selfboot_engine):
        executor = ToolExecutor(engine=None)
        try:
            result = await executor.execute(
                "dsh_optimize_execute", {"task": SELFBOOT_TASK})
            assert result.is_error is False
            content = result.content
            assert content["success"] is True
            assert content["template_hit"] is True
            assert content["source"] == "template"
            assert content["savings"]["token_saved_ratio"] > 0.9
            assert [s["name"] for s in content["stages"]] == [
                "explore", "solidify", "template", "automate"]
            # 响应不含生成的用例代码正文（避免撑爆 MCP 响应）
            assert "code" not in str(content)
        finally:
            await executor._http_client.aclose()

    @pytest.mark.asyncio
    async def test_explicit_target_is_honoured(self, selfboot_engine, sample_module):
        executor = ToolExecutor(engine=None)
        try:
            result = await executor.execute("dsh_optimize_execute", {
                "task": SELFBOOT_TASK,
                "target": f"{sample_module}:add",
            })
            assert result.content["target"] == f"{sample_module}:add"
            assert result.content["success"] is True
        finally:
            await executor._http_client.aclose()

    @pytest.mark.asyncio
    async def test_empty_task_is_rejected(self, selfboot_engine):
        executor = ToolExecutor(engine=None)
        try:
            result = await executor.execute("dsh_optimize_execute", {"task": "   "})
            assert result.is_error is True
            assert "task 不能为空" in result.error_message
        finally:
            await executor._http_client.aclose()

    @pytest.mark.asyncio
    async def test_require_approval_parks_novel_task(self, selfboot_engine):
        executor = ToolExecutor(engine=None)
        try:
            result = await executor.execute("dsh_optimize_execute", {
                "task": NOVEL_TASK, "require_approval": True})
            assert result.content["pending_approval"] is True
            assert result.content["success"] is False
            assert result.content["run_id"].startswith("p")
        finally:
            await executor._http_client.aclose()

    @pytest.mark.asyncio
    async def test_require_approval_does_not_park_when_template_matches(
            self, selfboot_engine):
        """既有语义：AutomationEngine 命中模板时直接执行，不挂起审批 —— 如实回传"""
        executor = ToolExecutor(engine=None)
        try:
            await executor.execute("dsh_optimize_execute", {"task": SELFBOOT_TASK})
            result = await executor.execute("dsh_optimize_execute", {
                "task": SELFBOOT_TASK, "require_approval": True})
            assert result.content["pending_approval"] is False
            assert result.content["source"] == "template"
        finally:
            await executor._http_client.aclose()

    @pytest.mark.asyncio
    async def test_works_without_engine_or_llm(self, tmp_path, monkeypatch, runner_cls,
                                              sample_module):
        """comps 缺省（无 llm）时工具仍可用 —— D3 走确定性兜底"""
        monkeypatch.setenv("DSH_SELFBOOT_DATA_DIR", str(tmp_path / "data"))
        engine = SelfBootstrapEngine(
            executor=D3Executor(runner=runner_cls(),
                                default_target=f"{sample_module}:label",
                                repo_root=tmp_path),
            data_dir=str(tmp_path / "data"), report_dir=str(tmp_path / "reports"),
            target=f"{sample_module}:label")
        monkeypatch.setattr(mcp_server, "_selfboot_engine", engine)
        executor = ToolExecutor(engine=None)  # 无 WorkflowEngine
        try:
            result = await executor.execute(
                "dsh_optimize_execute", {"task": SELFBOOT_TASK})
            assert result.is_error is False
            assert result.content["llm"]["used"] is False
            assert result.content["success"] is True
        finally:
            await executor._http_client.aclose()


class TestSeedTemplateCompetition:
    """内置种子模板会参与推荐竞争 —— 这是闭环成立与否的真实边界

    推荐排序是 `task_kw + 0.5 * keyword_score(task, description)`，因此**种子模板
    的描述若覆盖了查询词，得分可以超过本次新建模板的精确 task 匹配**。实测：
    - "为 label 生成单元测试"：种子「生成单元测试」1.333 > 本次 1.042 → 闭环未成立；
    - DEFAULT_TASK（含独有词 selfboot）：本次 1.013 > 次优 0.5 → 闭环成立。
    故演示与验收必须使用有区分度的 task 文案（见 DEFAULT_TASK 的注释）。
    """

    @pytest.mark.asyncio
    async def test_seed_description_can_outrank_exact_task_match(self, selfboot_engine):
        executor = ToolExecutor(engine=None)
        try:
            result = await executor.execute(
                "dsh_optimize_execute", {"task": "为 label 生成单元测试"})
            assert result.content["template_hit"] is False
            assert result.content["success"] is False
            assert "闭环未成立" in result.content["detail"]
        finally:
            await executor._http_client.aclose()

    @pytest.mark.asyncio
    async def test_distinctive_task_closes_the_loop(self, selfboot_engine):
        executor = ToolExecutor(engine=None)
        try:
            result = await executor.execute(
                "dsh_optimize_execute", {"task": SELFBOOT_TASK})
            assert result.content["template_hit"] is True
            assert result.content["success"] is True
        finally:
            await executor._http_client.aclose()


class TestStatsTool:
    """dsh_optimize_stats 的行为"""

    @pytest.mark.asyncio
    async def test_returns_stats_shape(self, selfboot_engine):
        executor = ToolExecutor(engine=None)
        try:
            result = await executor.execute("dsh_optimize_stats", {})
            assert result.is_error is False
            assert result.content["template_count"] >= 61
            assert result.content["target"] == "sample_mod:label"
            assert "usage" in result.content
        finally:
            await executor._http_client.aclose()

    @pytest.mark.asyncio
    async def test_limit_is_honoured(self, selfboot_engine):
        executor = ToolExecutor(engine=None)
        try:
            await executor.execute("dsh_optimize_execute",
                                   {"task": SELFBOOT_TASK})
            result = await executor.execute("dsh_optimize_stats", {"limit": 1})
            assert len(result.content["recent_runs"]) <= 1
        finally:
            await executor._http_client.aclose()

    @pytest.mark.asyncio
    async def test_zero_limit_falls_back_to_default(self, selfboot_engine):
        executor = ToolExecutor(engine=None)
        try:
            result = await executor.execute("dsh_optimize_stats", {"limit": 0})
            assert result.is_error is False
        finally:
            await executor._http_client.aclose()


class TestDispatchRegression:
    """分发链路未被破坏"""

    @pytest.mark.asyncio
    async def test_unknown_tool_still_errors(self):
        executor = ToolExecutor(engine=None)
        try:
            result = await executor.execute("dsh_no_such_tool", {})
            assert result.is_error is True
            assert "Unknown tool" in result.error_message
        finally:
            await executor._http_client.aclose()

    @pytest.mark.asyncio
    async def test_preexisting_tool_still_dispatches(self):
        executor = ToolExecutor(engine=None)
        try:
            result = await executor.execute("dsh_text_query", {"table": "t"})
            assert result.is_error is False
            assert result.content["table"] == "t"
        finally:
            await executor._http_client.aclose()

    @pytest.mark.asyncio
    async def test_approval_tool_requires_workflow_engine(self):
        executor = ToolExecutor(engine=None)
        try:
            result = await executor.execute("dsh_approval_sweep", {})
            assert result.is_error is True
            assert "工作流引擎未装配" in result.error_message
        finally:
            await executor._http_client.aclose()