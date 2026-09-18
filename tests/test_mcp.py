"""MCP Server 测试"""

import pytest
from unittest.mock import Mock, patch

from jkos_core.mcp.server import (
    MCPServer,
    MCPTool,
    MCPToolCall,
    PREDEFINED_TOOLS,
)


class TestMCPTool:
    """MCP 工具测试"""

    def test_tool_creation(self):
        """测试工具创建"""
        tool = MCPTool(
            name="test_tool",
            description="测试工具",
            input_schema={"type": "object"},
        )
        assert tool.name == "test_tool"
        assert tool.description == "测试工具"

    def test_tool_to_dict(self):
        """测试工具转字典"""
        tool = MCPTool(
            name="test_tool",
            description="测试工具",
        )
        data = tool.model_dump()
        assert data["name"] == "test_tool"


class TestMCPToolCall:
    """MCP 工具调用测试"""

    def test_call_creation(self):
        """测试调用创建"""
        call = MCPToolCall(
            name="test_tool",
            arguments={"param": "value"},
        )
        assert call.name == "test_tool"
        assert call.arguments == {"param": "value"}


class TestMCPServer:
    """MCP Server 测试"""

    @pytest.fixture
    def server(self):
        return MCPServer(host="127.0.0.1", port=0)  # 0 = 随机端口

    def test_predefined_tools(self, server):
        """测试预定义工具"""
        assert len(server._tools) == len(PREDEFINED_TOOLS)
        assert "dsh_text_query" in server._tools
        assert "dsh_ocr_extract" in server._tools

    def test_tool_registration(self, server):
        """测试工具注册"""
        tool = MCPTool(
            name="custom_tool",
            description="自定义工具",
        )
        server._tools[tool.name] = tool
        assert "custom_tool" in server._tools

    def test_tool_unregistration(self, server):
        """测试工具注销"""
        tool = MCPTool(
            name="temp_tool",
            description="临时工具",
        )
        server._tools[tool.name] = tool
        del server._tools[tool.name]
        assert "temp_tool" not in server._tools


class TestPredefinedTools:
    """预定义工具测试"""

    def test_all_tools_have_descriptions(self):
        """测试所有工具都有描述"""
        for tool in PREDEFINED_TOOLS:
            assert tool.description, f"工具 {tool.name} 缺少描述"

    def test_ocr_tool_schema(self):
        """测试 OCR 工具的输入 schema"""
        ocr_tool = next(t for t in PREDEFINED_TOOLS if t.name == "dsh_ocr_extract")
        assert "image_url" in ocr_tool.input_schema.get("properties", {})

    def test_asr_tool_schema(self):
        """测试 ASR 工具的输入 schema"""
        asr_tool = next(t for t in PREDEFINED_TOOLS if t.name == "dsh_audio_transcribe")
        assert "audio_url" in asr_tool.input_schema.get("properties", {})

    def test_video_tool_schema(self):
        """测试视频工具的输入 schema"""
        video_tool = next(t for t in PREDEFINED_TOOLS if t.name == "dsh_video_analyze")
        assert "video_url" in video_tool.input_schema.get("properties", {})
