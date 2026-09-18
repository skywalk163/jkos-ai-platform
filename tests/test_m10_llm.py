"""M10 任务 10.1：LLM 供应商扩展 — 通义千问 (Qwen) / Claude (Anthropic)

测试覆盖：
- QwenProvider 健康检查
- QwenProvider chat 方法
- ClaudeProvider 健康检查
- ClaudeProvider chat 方法
- build_llm_router 包含新供应商
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from dsh_core.llm import (
    QwenProvider,
    ClaudeProvider,
    build_qwen_provider,
    build_claude_provider,
    build_llm_router,
    LLMMessage,
)


# ─── Qwen 供应商测试 ───


class TestQwenProvider:
    """通义千问供应商测试"""

    def test_healthy_with_api_key(self):
        """有 API Key 时健康"""
        provider = QwenProvider({"api_key": "test-key"})
        assert provider.healthy()

    def test_healthy_without_api_key(self):
        """无 API Key 时不健康"""
        provider = QwenProvider({"api_key": ""})
        assert not provider.healthy()

    def test_name_and_base_url(self):
        """供应商名称和基础 URL"""
        provider = QwenProvider({"api_key": "test"})
        assert provider.name == "qwen"
        assert provider.base_url == "https://dashscope.aliyuncs.com"

    @pytest.mark.asyncio
    async def test_chat_success(self):
        """chat 方法成功调用"""
        mock_response_data = {
            "choices": [{"message": {"content": "Hello, world!"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_response = AsyncMock()
            # httpx Response.raise_for_status() 是同步方法，需用同步 mock 避免产生未 await 的协程
            mock_response.raise_for_status = MagicMock()
            # httpx response.json() is an async method
            mock_response.json = AsyncMock(return_value=mock_response_data)
            mock_client.post.return_value = mock_response

            provider = QwenProvider({"api_key": "test-key"})
            messages = [LLMMessage(role="user", content="Hello")]
            result = await provider.chat(messages)

            assert result.content == "Hello, world!"
            assert result.provider == "qwen"
            assert result.prompt_tokens == 10
            assert result.completion_tokens == 20

    @pytest.mark.asyncio
    async def test_chat_with_system_message(self):
        """chat 方法处理系统消息"""
        mock_response_data = {
            "choices": [{"message": {"content": "Response"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_response = AsyncMock()
            # httpx Response.raise_for_status() 是同步方法，需用同步 mock 避免产生未 await 的协程
            mock_response.raise_for_status = MagicMock()
            mock_response.json = AsyncMock(return_value=mock_response_data)
            mock_client.post.return_value = mock_response

            provider = QwenProvider({"api_key": "test-key"})
            messages = [
                LLMMessage(role="system", content="You are a helper"),
                LLMMessage(role="user", content="Hello"),
            ]
            result = await provider.chat(messages)

            assert result.content == "Response"

    def test_build_qwen_provider(self):
        """构建 Qwen 供应商"""
        provider = build_qwen_provider(
            api_key="test-key",
            model="qwen-plus",
        )
        assert provider.name == "qwen"
        assert provider._model == "qwen-plus"


# ─── Claude 供应商测试 ───


class TestClaudeProvider:
    """Claude 供应商测试"""

    def test_healthy_with_api_key(self):
        """有 API Key 时健康"""
        provider = ClaudeProvider({"api_key": "test-key"})
        assert provider.healthy()

    def test_healthy_without_api_key(self):
        """无 API Key 时不健康"""
        provider = ClaudeProvider({"api_key": ""})
        assert not provider.healthy()

    def test_name_and_base_url(self):
        """供应商名称和基础 URL"""
        provider = ClaudeProvider({"api_key": "test"})
        assert provider.name == "claude"
        assert provider.base_url == "https://api.anthropic.com"

    @pytest.mark.asyncio
    async def test_chat_success(self):
        """chat 方法成功调用"""
        mock_response_data = {
            "content": [{"type": "text", "text": "Hello, world!"}],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_response = AsyncMock()
            # httpx Response.raise_for_status() 是同步方法，需用同步 mock 避免产生未 await 的协程
            mock_response.raise_for_status = MagicMock()
            mock_response.json = AsyncMock(return_value=mock_response_data)
            mock_client.post.return_value = mock_response

            provider = ClaudeProvider({"api_key": "test-key"})
            messages = [LLMMessage(role="user", content="Hello")]
            result = await provider.chat(messages)

            assert result.content == "Hello, world!"
            assert result.provider == "claude"
            assert result.prompt_tokens == 10
            assert result.completion_tokens == 20

    @pytest.mark.asyncio
    async def test_chat_with_system_message(self):
        """chat 方法处理系统消息"""
        mock_response_data = {
            "content": [{"type": "text", "text": "Response"}],
            "usage": {"input_tokens": 5, "output_tokens": 10},
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_response = AsyncMock()
            # httpx Response.raise_for_status() 是同步方法，需用同步 mock 避免产生未 await 的协程
            mock_response.raise_for_status = MagicMock()
            mock_response.json = AsyncMock(return_value=mock_response_data)
            mock_client.post.return_value = mock_response

            provider = ClaudeProvider({"api_key": "test-key"})
            messages = [
                LLMMessage(role="system", content="You are a helper"),
                LLMMessage(role="user", content="Hello"),
            ]
            result = await provider.chat(messages)

            assert result.content == "Response"

    @pytest.mark.asyncio
    async def test_chat_multiple_text_blocks(self):
        """chat 方法处理多个文本块"""
        mock_response_data = {
            "content": [
                {"type": "text", "text": "First part"},
                {"type": "text", "text": "Second part"},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_response = AsyncMock()
            # httpx Response.raise_for_status() 是同步方法，需用同步 mock 避免产生未 await 的协程
            mock_response.raise_for_status = MagicMock()
            mock_response.json = AsyncMock(return_value=mock_response_data)
            mock_client.post.return_value = mock_response

            provider = ClaudeProvider({"api_key": "test-key"})
            messages = [LLMMessage(role="user", content="Hello")]
            result = await provider.chat(messages)

            assert "First part" in result.content
            assert "Second part" in result.content

    def test_build_claude_provider(self):
        """构建 Claude 供应商"""
        provider = build_claude_provider(
            api_key="test-key",
            model="claude-3-opus-20240229",
        )
        assert provider.name == "claude"
        assert provider._model == "claude-3-opus-20240229"


# ─── Router 集成测试 ───


class TestLLMRouterWithNewProviders:
    """Router 包含新供应商测试"""

    def test_build_llm_router_includes_qwen(self):
        """build_llm_router 包含 Qwen 供应商"""
        router = build_llm_router()
        provider_names = [p.name for p in router.providers]
        assert "qwen" in provider_names

    def test_build_llm_router_includes_claude(self):
        """build_llm_router 包含 Claude 供应商"""
        router = build_llm_router()
        provider_names = [p.name for p in router.providers]
        assert "claude" in provider_names

    def test_build_llm_router_order(self):
        """Router 供应商顺序"""
        router = build_llm_router()
        provider_names = [p.name for p in router.providers]
        # 顺序: deepseek → openai → qwen → claude → simulated
        assert provider_names.index("deepseek") < provider_names.index("openai")
        assert provider_names.index("openai") < provider_names.index("qwen")
        assert provider_names.index("qwen") < provider_names.index("claude")
        assert provider_names.index("claude") < provider_names.index("simulated")
