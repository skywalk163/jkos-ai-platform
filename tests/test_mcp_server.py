"""MCP Server 单元测试 - ToolExecutor / MCPClient / MCPServer 路由"""

import json
import runpy
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
import uvicorn
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from dsh_core.mcp import server as mcp_server_module
from dsh_core.mcp.server import (
    MCPServer,
    MCPClient,
    MCPToolResult,
    PREDEFINED_TOOLS,
    ToolExecutor,
    create_app,
    main,
)


# ─── MCPToolResult 模型 ───

class TestMCPToolResult:
    """MCPToolResult 模型测试"""

    def test_defaults(self):
        result = MCPToolResult(content={"a": 1})
        assert result.is_error is False
        assert result.error_message is None
        assert result.duration_ms == 0.0

    def test_extra_fields_allowed(self):
        result = MCPToolResult(content="x", extra_field="value")
        assert result.extra_field == "value"


# ─── ToolExecutor：通用调度 ───

class TestToolExecutorDispatch:
    """ToolExecutor 调度与异常路径"""

    @pytest.fixture
    def executor(self):
        return ToolExecutor()

    async def test_unknown_tool(self, executor):
        result = await executor.execute("no_such_tool", {})
        assert result.is_error is True
        assert result.error_message == "Unknown tool: no_such_tool"

    async def test_generic_exception_returns_error_result(self, executor):
        executor._execute_text_query = AsyncMock(side_effect=RuntimeError("boom"))
        result = await executor.execute("dsh_text_query", {})
        assert result.is_error is True
        assert result.error_message == "boom"
        assert result.content is None

    async def test_execute_text_query(self, executor):
        result = await executor.execute(
            "dsh_text_query",
            {"table": "users", "conditions": {"id": 1}, "limit": 10},
        )
        assert result.is_error is False
        assert result.content["table"] == "users"
        assert result.content["conditions"] == {"id": 1}
        assert result.content["limit"] == 10
        assert result.content["total"] == 2
        assert len(result.content["results"]) == 2

    async def test_execute_text_query_defaults(self, executor):
        result = await executor.execute("dsh_text_query", {})
        assert result.content["table"] == "unknown"
        assert result.content["limit"] == 100


# ─── ToolExecutor：dsh_text_generate（真实 HTTP 路径）───

class TestToolExecutorTextGenerate:
    """dsh_text_generate 的 DeepSeek API 调用路径"""

    @pytest.fixture
    def executor(self):
        return ToolExecutor()

    @pytest.fixture
    def mock_post(self, executor):
        mock = AsyncMock()
        executor._http_client.post = mock
        return mock

    async def test_prompt_required(self, executor):
        result = await executor.execute("dsh_text_generate", {})
        assert result.is_error is True
        assert result.error_message == "prompt is required"

    async def test_success_path(self, executor, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": "生成的文本"}}]},
        )
        result = await executor.execute(
            "dsh_text_generate", {"prompt": "你好", "model": "deepseek-v3"}
        )
        assert result.is_error is False
        assert result.content == {
            "text": "生成的文本",
            "model": "deepseek-v3",
            "prompt": "你好",
        }
        kwargs = mock_post.await_args.kwargs
        assert kwargs["json"]["model"] == "deepseek-v3"
        assert kwargs["json"]["messages"] == [{"role": "user", "content": "你好"}]
        assert kwargs["json"]["max_tokens"] == 1024
        assert kwargs["json"]["temperature"] == 0.7
        assert kwargs["timeout"] == 60.0
        assert kwargs["headers"]["Authorization"].startswith("Bearer ")

    async def test_api_error_returns_simulated(self, executor, mock_post):
        mock_post.return_value = MagicMock(status_code=500)
        result = await executor.execute(
            "dsh_text_generate", {"prompt": "hi", "model": "deepseek"}
        )
        assert result.is_error is False
        assert result.content["note"] == "API 调用失败，返回模拟结果"
        assert "hi" in result.content["text"]

    async def test_api_exception_returns_simulated(self, executor, mock_post):
        mock_post.side_effect = httpx.ConnectError("no network")
        result = await executor.execute(
            "dsh_text_generate", {"prompt": "hi", "model": "deepseek"}
        )
        assert result.is_error is False
        assert result.content["note"] == "API 不可用，返回模拟结果"
        assert "hi" in result.content["text"]


# ─── ToolExecutor：各内置工具 ───

class TestToolExecutorHandlers:
    """OCR / ASR / 视频 / RAG / 会话 / 资源 工具"""

    @pytest.fixture
    def executor(self):
        return ToolExecutor()

    async def test_ocr_missing_image_url(self, executor):
        result = await executor.execute("dsh_ocr_extract", {})
        assert result.is_error is True
        assert result.error_message == "image_url is required"

    async def test_ocr_success(self, executor):
        result = await executor.execute(
            "dsh_ocr_extract", {"image_url": "http://img/a.png", "language": "en"}
        )
        assert result.is_error is False
        assert result.content["confidence"] == 0.95
        assert result.content["language"] == "en"
        assert len(result.content["lines"]) == 2

    async def test_asr_missing_audio_url(self, executor):
        result = await executor.execute("dsh_audio_transcribe", {})
        assert result.is_error is True
        assert result.error_message == "audio_url is required"

    async def test_asr_success(self, executor):
        result = await executor.execute(
            "dsh_audio_transcribe", {"audio_url": "http://audio/a.mp3"}
        )
        assert result.is_error is False
        assert result.content["confidence"] == 0.92
        assert result.content["duration"] == 5.2

    async def test_video_missing_video_url(self, executor):
        result = await executor.execute("dsh_video_analyze", {})
        assert result.is_error is True
        assert result.error_message == "video_url is required"

    async def test_video_success(self, executor):
        result = await executor.execute(
            "dsh_video_analyze", {"video_url": "http://video/v.mp4"}
        )
        assert result.is_error is False
        assert result.content["key_frames"] == 12
        assert result.content["duration"] == 120
        assert "演示" in result.content["tags"]

    async def test_rag_missing_query(self, executor):
        result = await executor.execute("dsh_rag_query", {})
        assert result.is_error is True
        assert result.error_message == "query is required"

    async def test_rag_success(self, executor):
        result = await executor.execute(
            "dsh_rag_query", {"query": "什么是 RAG", "top_k": 3}
        )
        assert result.is_error is False
        assert "什么是 RAG" in result.content["answer"]
        assert result.content["top_k"] == 3
        assert len(result.content["sources"]) == 2

    async def test_session_list(self, executor):
        result = await executor.execute("dsh_session_list", {"user_id": "u1"})
        assert result.is_error is False
        assert result.content["total"] == 1
        assert result.content["sessions"][0]["id"] == "session_1"

    async def test_resource_list(self, executor):
        result = await executor.execute("dsh_resource_list", {"category": "image"})
        assert result.is_error is False
        assert result.content["total"] == 0
        assert result.content["resources"] == []
        assert result.content["category"] == "image"


# ─── ToolExecutor：审批工具 ───

class TestToolExecutorApproval:
    """审批工具（未装配引擎时须报错）"""

    async def test_require_engine_list(self):
        result = await ToolExecutor().execute("dsh_approval_list", {})
        assert result.is_error is True
        assert "工作流引擎未装配" in result.error_message

    async def test_require_engine_decide(self):
        result = await ToolExecutor().execute(
            "dsh_approval_decide", {"task_id": "t1", "decision": "approve"}
        )
        assert result.is_error is True
        assert "工作流引擎未装配" in result.error_message

    async def test_require_engine_sweep(self):
        result = await ToolExecutor().execute("dsh_approval_sweep", {})
        assert result.is_error is True
        assert "工作流引擎未装配" in result.error_message


class TestToolExecutorApprovalWithEngine:
    """审批工具（装配 mock 引擎）"""

    @pytest.fixture
    def executor(self):
        engine = MagicMock()
        engine.list_pending_approvals.return_value = [
            {"task_id": "t1", "title": "审批 1"},
            {"task_id": "t2", "title": "审批 2"},
        ]
        engine.approve_task = AsyncMock(return_value={"status": "approved"})
        engine.sweep_timeouts = AsyncMock(return_value=3)
        return ToolExecutor(engine=engine), engine

    async def test_approval_list(self, executor):
        executor, engine = executor
        result = await executor.execute(
            "dsh_approval_list", {"tenant_code": "dsh", "limit": 5}
        )
        assert result.is_error is False
        assert result.content["total"] == 2
        assert len(result.content["tasks"]) == 2
        engine.list_pending_approvals.assert_called_once_with(
            tenant_code="dsh", limit=5
        )

    async def test_approval_decide_approve(self, executor):
        executor, engine = executor
        result = await executor.execute(
            "dsh_approval_decide",
            {"task_id": "t1", "decision": "approve", "approver": "admin", "reason": "ok"},
        )
        assert result.is_error is False
        assert result.content["task_id"] == "t1"
        assert result.content["decision"] == "approve"
        assert result.content["instance"] == {"status": "approved"}
        engine.approve_task.assert_awaited_once_with(
            "t1", decision=True, approver="admin", reason="ok"
        )

    async def test_approval_decide_reject(self, executor):
        executor, engine = executor
        result = await executor.execute(
            "dsh_approval_decide", {"task_id": "t1", "decision": "reject"}
        )
        assert result.content["decision"] == "reject"
        engine.approve_task.assert_awaited_once_with(
            "t1", decision=False, approver="admin", reason=None
        )

    async def test_approval_decide_invalid_decision(self, executor):
        executor, engine = executor
        result = await executor.execute(
            "dsh_approval_decide", {"task_id": "t1", "decision": "maybe"}
        )
        assert result.is_error is True
        assert "decision 必须是 approve 或 reject" in result.error_message
        engine.approve_task.assert_not_awaited()

    async def test_approval_sweep(self, executor):
        executor, engine = executor
        result = await executor.execute("dsh_approval_sweep", {})
        assert result.is_error is False
        assert result.content == {"handled": 3}
        engine.sweep_timeouts.assert_awaited_once_with()

    async def test_executor_cleanup(self):
        executor = ToolExecutor()
        executor._http_client.aclose = AsyncMock()
        await executor.cleanup()
        executor._http_client.aclose.assert_awaited_once()


# ─── MCPClient ───

class TestMCPClient:
    """MCPClient 基础与 HTTP 交互"""

    def test_endpoint_normalized(self):
        assert MCPClient("http://x:3000/").endpoint == "http://x:3000"
        assert MCPClient("http://x:3000").endpoint == "http://x:3000"

    async def test_lazy_client_creation_and_close(self):
        client = MCPClient("http://mcpserver")
        assert client._client is None
        c1 = client._get_client()
        assert isinstance(c1, httpx.AsyncClient)
        assert client._client is c1
        assert client._get_client() is c1  # 缓存复用
        await client.close()
        assert client._client is None
        await client.close()  # 幂等

    @respx.mock
    async def test_health(self):
        client = MCPClient("http://mcpserver/")
        respx.get("http://mcpserver/health").mock(
            return_value=httpx.Response(200, json={"status": "ok", "service": "dsh-mcp"})
        )
        assert await client.health() == {"status": "ok", "service": "dsh-mcp"}
        await client.close()

    @respx.mock
    async def test_health_http_error(self):
        client = MCPClient("http://mcpserver")
        respx.get("http://mcpserver/health").mock(return_value=httpx.Response(500))
        with pytest.raises(httpx.HTTPStatusError):
            await client.health()
        await client.close()

    @respx.mock
    async def test_list_tools(self):
        client = MCPClient("http://mcpserver/")
        respx.get("http://mcpserver/tools").mock(
            return_value=httpx.Response(200, json={"count": 1, "tools": [{"name": "t"}]})
        )
        data = await client.list_tools()
        assert data["count"] == 1
        assert data["tools"][0]["name"] == "t"
        await client.close()

    @respx.mock
    async def test_call_tool_payload(self):
        client = MCPClient("http://mcpserver/")
        respx.post("http://mcpserver/tools/call").mock(
            return_value=httpx.Response(200, json={"content": {"ok": 1}, "is_error": False})
        )
        result = await client.call_tool("dsh_text_query", {"table": "users"})
        assert result["content"] == {"ok": 1}
        assert len(respx.calls) == 1
        body = json.loads(respx.calls.last.request.content)
        assert body["name"] == "dsh_text_query"
        assert body["arguments"] == {"table": "users"}
        assert body["call_id"].startswith("call-")
        await client.close()

    @respx.mock
    async def test_call_tool_default_arguments(self):
        client = MCPClient("http://mcpserver")
        respx.post("http://mcpserver/tools/call").mock(
            return_value=httpx.Response(200, json={"is_error": False})
        )
        await client.call_tool("dsh_session_list")
        body = json.loads(respx.calls.last.request.content)
        assert body["arguments"] == {}
        await client.close()


# ─── MCPServer 生命周期 ───

class TestMCPServerLifecycle:
    """initialize / cleanup 幂等性"""

    async def test_initialize_idempotent(self):
        server = MCPServer(host="127.0.0.1", port=0)
        assert server._initialized is False
        await server.initialize()
        assert server._initialized is True
        await server.initialize()  # 再次调用不报错
        assert server._initialized is True

    async def test_cleanup(self):
        server = MCPServer(host="127.0.0.1", port=0)
        server._executor.cleanup = AsyncMock()
        await server.cleanup()
        server._executor.cleanup.assert_awaited_once()

    def test_create_app(self):
        app = create_app()
        assert isinstance(app, FastAPI)


# ─── MCPServer HTTP 路由 ───

class TestMCPServerRoutes:
    """FastAPI 路由测试"""

    @pytest.fixture
    def client(self):
        with TestClient(create_app()) as tc:
            yield tc

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": "dsh-mcp"}

    def test_list_tools(self, client):
        resp = client.get("/tools")
        data = resp.json()
        assert data["count"] == len(PREDEFINED_TOOLS)
        names = [t["name"] for t in data["tools"]]
        assert "dsh_text_query" in names
        assert "dsh_ocr_extract" in names

    def test_call_tool_success(self, client):
        resp = client.post(
            "/tools/call",
            json={"name": "dsh_text_query", "arguments": {"table": "users"}},
        )
        data = resp.json()
        assert resp.status_code == 200
        assert data["is_error"] is False
        assert data["content"]["table"] == "users"
        assert data["error_message"] is None

    def test_call_tool_not_found(self, client):
        resp = client.post("/tools/call", json={"name": "no_such_tool", "arguments": {}})
        assert resp.status_code == 404
        assert "Tool not found" in resp.json()["error"]

    def test_call_tool_invalid_request(self, client):
        resp = client.post("/tools/call", json={"name": 123})
        assert resp.status_code == 400
        assert "Invalid request" in resp.json()["error"]

    def test_mcp_initialize(self, client):
        resp = client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        data = resp.json()
        assert data["result"]["protocolVersion"] == "2024-11-05"
        assert data["result"]["serverInfo"]["name"] == "DSH MCP Server"
        assert data["result"]["capabilities"]["tools"]["listChanged"] is True

    def test_mcp_tools_list(self, client):
        resp = client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        data = resp.json()
        assert len(data["result"]["tools"]) == len(PREDEFINED_TOOLS)

    def test_mcp_tools_call_success(self, client):
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "dsh_ocr_extract", "arguments": {"image_url": "http://img/a.png"}},
            },
        )
        data = resp.json()
        assert data["result"]["isError"] is False
        content = json.loads(data["result"]["content"][0]["text"])
        assert content["confidence"] == 0.95

    def test_mcp_tools_call_not_found(self, client):
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "no_such_tool", "arguments": {}},
            },
        )
        data = resp.json()
        assert data["error"]["code"] == -32601
        assert "Tool not found" in data["error"]["message"]

    def test_mcp_method_not_found(self, client):
        resp = client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 5, "method": "unknown_method", "params": {}}
        )
        data = resp.json()
        assert data["error"]["code"] == -32601
        assert "Method not found" in data["error"]["message"]

    def test_mcp_invalid_json(self, client):
        resp = client.post(
            "/mcp", content="not-json", headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 400
        assert resp.json() == {"error": "Invalid JSON"}

    def test_mcp_get(self, client):
        resp = client.get("/mcp")
        assert resp.json()["status"] == "MCP endpoint available"

    def test_sse_post(self, client):
        resp = client.post("/sse")
        assert resp.json() == {"status": "SSE endpoint available"}

    @patch("dsh_core.mcp.server.httpx.get", side_effect=RuntimeError("no server"))
    def test_sse_stream(self, mock_get, client):
        resp = client.get("/sse")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "connected" in resp.text
        mock_get.assert_called()

    def test_register_tool(self, client):
        resp = client.post(
            "/tools/register",
            json={
                "name": "custom_tool",
                "description": "自定义测试工具",
                "input_schema": {"type": "object"},
                "output_schema": {},
            },
        )
        assert resp.json() == {"status": "registered", "tool_name": "custom_tool"}
        data = client.get("/tools").json()
        assert data["count"] == len(PREDEFINED_TOOLS) + 1

    def test_register_tool_invalid(self, client):
        resp = client.post("/tools/register", json={"name": 123})
        assert resp.status_code == 400
        assert "Invalid tool definition" in resp.json()["error"]

    def test_unregister_tool(self, client):
        resp = client.delete("/tools/dsh_text_query")
        assert resp.json() == {"status": "unregistered", "tool_name": "dsh_text_query"}
        # 再次注销应 404
        resp2 = client.delete("/tools/dsh_text_query")
        assert resp2.status_code == 404
        assert "Tool not found" in resp2.json()["error"]

    def test_delete_tool_not_found(self, client):
        resp = client.delete("/tools/no_such_tool")
        assert resp.status_code == 404
        assert "Tool not found" in resp.json()["error"]

    def test_index_page(self, client, monkeypatch):
        """覆盖 GET / 路由 (server.py:582-583 FileResponse)"""
        def fake_file_response(path, *args, **kwargs):
            return Response(content=b"<html>dsh index</html>", media_type="text/html")
        monkeypatch.setattr("fastapi.responses.FileResponse", fake_file_response)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "dsh index" in resp.text

    def test_mcp_tools_call_no_content(self, client):
        """覆盖 server.py:686 result.content is None -> "No content" 兜底"""
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "dsh_ocr_extract", "arguments": {}},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"]["content"][0]["text"] == "No content"
        assert data["result"]["isError"] is True

    async def test_sse_stream_heartbeat(self, client):
        """覆盖 server.py:728-730 SSE 心跳循环。"""
        with patch(
            "dsh_core.mcp.server.httpx.get",
            AsyncMock(side_effect=[MagicMock(), MagicMock(), RuntimeError("stop")]),
        ), patch("dsh_core.mcp.server.time.sleep"):
            resp = client.get("/sse")
        assert resp.status_code == 200
        assert "connected" in resp.text
        assert "heartbeat" in resp.text


# ─── 服务器入口点 (run / main / __main__) ───

class TestMCPServerEntrypoint:
    """MCPServer.run / main() / __main__ 守卫测试"""

    def test_run_uses_uvicorn(self, monkeypatch):
        """覆盖 server.py:782-783 run() 调用 uvicorn.run"""
        calls = []

        class FakeUvicorn:
            @staticmethod
            def run(app, host, port):
                calls.append((app, host, port))

        monkeypatch.setattr(uvicorn, "run", FakeUvicorn.run)
        server = MCPServer(host="127.0.0.1", port=9999)
        server.run()
        assert len(calls) == 1
        app, host, port = calls[0]
        assert host == "127.0.0.1"
        assert port == 9999

    def test_main_cli(self, monkeypatch):
        """覆盖 server.py:796-805 命令行参数解析"""
        calls = []

        class FakeUvicorn:
            @staticmethod
            def run(app, host, port):
                calls.append((host, port))

        monkeypatch.setattr(uvicorn, "run", FakeUvicorn.run)
        monkeypatch.setattr(
            sys, "argv", ["dsh-mcp", "--host", "127.0.0.1", "--port", "4321"]
        )
        main()
        assert calls == [("127.0.0.1", 4321)]

    def test_main_guard(self, monkeypatch):
        """覆盖 server.py:808-809 __main__ 守卫"""
        calls = []

        class FakeUvicorn:
            @staticmethod
            def run(app, host, port):
                calls.append((host, port))

        monkeypatch.setattr(uvicorn, "run", FakeUvicorn.run)
        monkeypatch.setattr(sys, "argv", ["dsh-mcp"])
        runpy.run_path(str(Path(mcp_server_module.__file__)), run_name="__main__")
        assert calls == [("0.0.0.0", 3000)]

    def test_index_page(self, client, monkeypatch):
        """覆盖 GET / 路由 (server.py:582-583 FileResponse)"""
        def fake_file_response(path, *args, **kwargs):
            return Response(content=b"<html>dsh index</html>", media_type="text/html")
        monkeypatch.setattr("fastapi.responses.FileResponse", fake_file_response)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "dsh index" in resp.text

    def test_mcp_tools_call_no_content(self, client):
        """覆盖 server.py:686 result.content is None -> "No content" 兜底"""
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "dsh_ocr_extract", "arguments": {}},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"]["content"][0]["text"] == "No content"
        assert data["result"]["isError"] is True

    async def test_sse_stream_heartbeat(self, client):
        """覆盖 server.py:728-730 SSE 心跳循环。"""
        with patch(
            "dsh_core.mcp.server.httpx.get",
            AsyncMock(side_effect=[MagicMock(), MagicMock(), RuntimeError("stop")]),
        ), patch("dsh_core.mcp.server.time.sleep"):
            resp = client.get("/sse")
        assert resp.status_code == 200
        assert "connected" in resp.text
        assert "heartbeat" in resp.text


# ─── 服务器入口点 (run / main / __main__) ───

class TestMCPServerEntrypoint:
    """MCPServer.run / main() / __main__ 守卫测试"""

    def test_run_uses_uvicorn(self, monkeypatch):
        """覆盖 server.py:782-783 run() 调用 uvicorn.run"""
        calls = []

        class FakeUvicorn:
            @staticmethod
            def run(app, host, port):
                calls.append((app, host, port))

        monkeypatch.setattr(uvicorn, "run", FakeUvicorn.run)
        server = MCPServer(host="127.0.0.1", port=9999)
        server.run()
        assert len(calls) == 1
        app, host, port = calls[0]
        assert host == "127.0.0.1"
        assert port == 9999

    def test_main_cli(self, monkeypatch):
        """覆盖 server.py:796-805 命令行参数解析"""
        calls = []

        class FakeUvicorn:
            @staticmethod
            def run(app, host, port):
                calls.append((host, port))

        monkeypatch.setattr(uvicorn, "run", FakeUvicorn.run)
        monkeypatch.setattr(
            sys, "argv", ["dsh-mcp", "--host", "127.0.0.1", "--port", "4321"]
        )
        main()
        assert calls == [("127.0.0.1", 4321)]

    def test_main_guard(self, monkeypatch):
        """覆盖 server.py:808-809 __main__ 守卫"""
        calls = []

        class FakeUvicorn:
            @staticmethod
            def run(app, host, port):
                calls.append((host, port))

        monkeypatch.setattr(uvicorn, "run", FakeUvicorn.run)
        monkeypatch.setattr(sys, "argv", ["dsh-mcp"])
        runpy.run_path(str(Path(mcp_server_module.__file__)), run_name="__main__")
        assert calls == [("0.0.0.0", 3000)]