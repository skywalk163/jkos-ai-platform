"""LLM 供应商 HTTP 调用覆盖测试（M6：openai.py / deepseek.py 实网路径与 chat_text 分支）

使用 respx mock httpx，避免真实外呼，覆盖：
  - 真实 API 成功路径（解析 choices/usage、请求头 Bearer）
  - HTTP 错误路径（raise LLMError 且 healthy=False）
  - chat_text 带 system 参数（system 消息置顶）
  - 无 API key 时的 _simulate 路径与显式 model 覆盖
"""
import json

import httpx
import pytest
import respx

from jkos_core.llm import DeepSeekProvider, OpenAIProvider
from jkos_core.llm.base import LLMError

CHAT_COMPLETIONS = "/v1/chat/completions"


class TestOpenAIProvider:
    @respx.mock
    async def test_chat_success(self, monkeypatch):
        """真实 API 成功路径：解析 choices/usage，请求头带 Bearer"""
        monkeypatch.setenv("DSH_OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("DSH_OPENAI_BASE_URL", "http://llm-test.local")
        provider = OpenAIProvider()
        respx.post(f"http://llm-test.local{CHAT_COMPLETIONS}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "你好，我是 AI"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
        )

        result = await provider.chat_text("你好")

        assert result.provider == "openai"
        assert result.simulated is False
        assert result.content == "你好，我是 AI"
        assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (10, 5, 15)
        req = respx.calls.last.request
        assert req.headers["Authorization"] == "Bearer sk-test"
        body = json.loads(req.content)
        assert body["model"] == "gpt-4o-mini"
        assert body["messages"] == [{"role": "user", "content": "你好"}]

    @respx.mock
    async def test_chat_http_error(self, monkeypatch):
        """HTTP 错误 → LLMError，并置 healthy=False"""
        monkeypatch.setenv("DSH_OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("DSH_OPENAI_BASE_URL", "http://llm-test.local")
        provider = OpenAIProvider()
        respx.post(f"http://llm-test.local{CHAT_COMPLETIONS}").mock(
            return_value=httpx.Response(500, json={"error": "boom"})
        )

        with pytest.raises(LLMError):
            await provider.chat_text("你好")

        assert provider.healthy() is False

    @respx.mock
    async def test_chat_text_with_system(self, monkeypatch):
        """chat_text 带 system → system 消息置顶"""
        monkeypatch.setenv("DSH_OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("DSH_OPENAI_BASE_URL", "http://llm-test.local")
        provider = OpenAIProvider()
        respx.post(f"http://llm-test.local{CHAT_COMPLETIONS}").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        )

        result = await provider.chat_text("你好", system="你是助手")

        assert result.simulated is False
        body = json.loads(respx.calls.last.request.content)
        assert body["messages"] == [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ]

    async def test_simulate_model_override(self, monkeypatch):
        """无 API key → 模拟路径，显式 model 生效"""
        monkeypatch.delenv("DSH_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        provider = OpenAIProvider()

        result = await provider.chat_text("测试", model="custom-model")

        assert result.simulated is True
        assert result.model == "custom-model"
        assert "[OpenAI 模拟响应 - 模型: custom-model]" in result.content


class TestDeepSeekProvider:
    @respx.mock
    async def test_chat_success(self, monkeypatch):
        """真实 API 成功路径：解析 choices/usage"""
        monkeypatch.setenv("DSH_DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setenv("DSH_DEEPSEEK_BASE_URL", "http://llm-test.local")
        provider = DeepSeekProvider()
        respx.post(f"http://llm-test.local{CHAT_COMPLETIONS}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "deepseek 回复"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
                },
            )
        )

        result = await provider.chat_text("你好")

        assert result.provider == "deepseek"
        assert result.simulated is False
        assert result.content == "deepseek 回复"
        assert result.total_tokens == 7
        body = json.loads(respx.calls.last.request.content)
        assert body["model"] == "deepseek-chat"
        assert body["messages"][0] == {"role": "user", "content": "你好"}

    @respx.mock
    async def test_chat_http_error(self, monkeypatch):
        """HTTP 错误 → LLMError，并置 healthy=False"""
        monkeypatch.setenv("DSH_DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setenv("DSH_DEEPSEEK_BASE_URL", "http://llm-test.local")
        provider = DeepSeekProvider()
        respx.post(f"http://llm-test.local{CHAT_COMPLETIONS}").mock(
            return_value=httpx.Response(500, json={"error": "boom"})
        )

        with pytest.raises(LLMError):
            await provider.chat_text("你好")

        assert provider.healthy() is False

    @respx.mock
    async def test_chat_text_with_system(self, monkeypatch):
        """chat_text 带 system → system 消息置顶"""
        monkeypatch.setenv("DSH_DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setenv("DSH_DEEPSEEK_BASE_URL", "http://llm-test.local")
        provider = DeepSeekProvider()
        respx.post(f"http://llm-test.local{CHAT_COMPLETIONS}").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        )

        result = await provider.chat_text("你好", system="你是助手")

        assert result.simulated is False
        body = json.loads(respx.calls.last.request.content)
        assert body["messages"] == [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ]

    @respx.mock
    async def test_chat_text_with_system(self, monkeypatch):
        """chat_text 带 system → system 消息置顶"""
        monkeypatch.setenv("DSH_DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setenv("DSH_DEEPSEEK_BASE_URL", "http://llm-test.local")
        provider = DeepSeekProvider()
        respx.post(f"http://llm-test.local{CHAT_COMPLETIONS}").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        )

        result = await provider.chat_text("你好", system="你是助手")

        assert result.simulated is False
        body = json.loads(respx.calls.last.request.content)
        assert body["messages"] == [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ]

    async def test_simulate_model_override(self, monkeypatch):
        """无 API key → 模拟路径，显式 model 生效"""
        monkeypatch.delenv("DSH_DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        provider = DeepSeekProvider()

        result = await provider.chat_text("测试", model="custom-deepseek")

        assert result.simulated is True
        assert result.model == "custom-deepseek"
        assert "[DeepSeek 模拟响应 - 模型: custom-deepseek]" in result.content