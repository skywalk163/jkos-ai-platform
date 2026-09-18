"""DSH LLM 供应商 - Claude (Anthropic)

M10 任务 10.1：Claude 供应商实现。

API 文档: https://docs.anthropic.com/en/api
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from dsh_core.llm.base import LLMMessage, LLMProvider, LLMResult

logger = logging.getLogger("dsh.llm.claude")


class ClaudeProvider(LLMProvider):
    """Claude 供应商

    环境变量:
      DSH_CLAUDE_API_KEY / DSH_LLM_API_KEY: API 密钥
      DSH_CLAUDE_BASE_URL: API 基础 URL (默认 https://api.anthropic.com)
      DSH_CLAUDE_MODEL: 模型名称 (默认 claude-3-5-sonnet-20241022)
    """

    name = "claude"
    base_url = "https://api.anthropic.com"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}
        self._api_key = config.get("api_key", "")
        self._base_url = config.get("base_url", self.base_url).rstrip("/")
        self._model = config.get("model", "claude-3-5-sonnet-20241022")
        self._timeout = config.get("timeout", 60.0)

    def healthy(self) -> bool:
        return bool(self._api_key)

    async def chat(
        self,
        messages: List[LLMMessage],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: float = 60.0,
    ) -> LLMResult:
        """调用 Claude API"""
        import time

        start = time.monotonic()
        model_name = model or self._model
        url = f"{self._base_url}/v1/messages"

        # 转换消息格式 (Claude 使用 system + user/assistant)
        system_message = None
        claude_messages = []
        for msg in messages:
            if msg.role == "system":
                system_message = msg.content
            else:
                claude_messages.append({"role": msg.role, "content": msg.content})

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": model_name,
            "max_tokens": max_tokens,
            "messages": claude_messages,
        }
        if temperature != 0.7:
            payload["temperature"] = temperature
        if system_message:
            payload["system"] = system_message

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = await response.json()

            # 解析响应
            content = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    content += block.get("text", "")

            usage = data.get("usage", {})
            prompt_tokens = usage.get("input_tokens", 0)
            completion_tokens = usage.get("output_tokens", 0)
            total_tokens = prompt_tokens + completion_tokens

            duration_ms = int((time.monotonic() - start) * 1000)

            return LLMResult(
                content=content,
                provider=self.name,
                model=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                duration_ms=duration_ms,
                simulated=False,
            )

        except Exception as e:
            logger.error("Claude API 调用失败: %s", e)
            raise


# ─── 便捷函数 ───

def build_claude_provider(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> ClaudeProvider:
    """构建 Claude 供应商"""
    import os

    config = {}
    config["api_key"] = api_key or os.getenv("DSH_CLAUDE_API_KEY") or os.getenv("DSH_LLM_API_KEY", "")
    config["base_url"] = base_url or os.getenv("DSH_CLAUDE_BASE_URL", "https://api.anthropic.com")
    config["model"] = model or os.getenv("DSH_CLAUDE_MODEL", "claude-3-5-sonnet-20241022")

    return ClaudeProvider(config)
