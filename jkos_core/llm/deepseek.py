"""DSH LLM 供应商 - DeepSeek API 接入（M3 任务 3.1）

实现 DeepSeek API 的接入，支持真实 API 调用和模拟模式。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from jkos_core.llm.base import LLMError, LLMMessage, LLMProvider, LLMResult

logger = logging.getLogger("dsh.llm.deepseek")


class DeepSeekProvider(LLMProvider):
    """DeepSeek API 供应商

    环境变量：
      - DSH_DEEPSEEK_API_KEY: API 密钥
      - DSH_DEEPSEEK_BASE_URL: API 基础 URL（默认 https://api.deepseek.com）
      - DSH_DEEPSEEK_MODEL: 默认模型（默认 deepseek-chat）
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        # 支持多种环境变量名：DSH_DEEPSEEK_API_KEY, OPENAI_API_KEY
        self._api_key = os.getenv("DSH_DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        self._base_url = os.getenv("DSH_DEEPSEEK_BASE_URL") or os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
        self._default_model = os.getenv("DSH_DEEPSEEK_MODEL") or os.getenv("OPENAI_MODEL", "deepseek-chat")
        self._healthy = bool(self._api_key)

    @property
    def name(self) -> str:
        return "deepseek"

    def healthy(self) -> bool:
        return self._healthy

    async def chat(
        self,
        messages: List[LLMMessage],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: float = 60.0,
    ) -> LLMResult:
        """调用 DeepSeek API"""
        if not self._api_key:
            # 无 API key，返回模拟结果
            return self._simulate(messages, model or self._default_model)

        model_name = model or self._default_model
        payload = {
            "model": model_name,
            "messages": [
                {"role": m.role, "content": m.content} for m in messages
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self._base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

            # 解析响应
            choice = data["choices"][0]
            content = choice["message"]["content"]
            usage = data.get("usage", {})

            return LLMResult(
                content=content,
                provider=self.name,
                model=model_name,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                simulated=False,
            )
        except Exception as e:
            logger.error("DeepSeek API 调用失败: %s", e)
            self._healthy = False
            raise LLMError(f"DeepSeek API 错误: {e}")

    async def chat_text(self, prompt: str, *, system: Optional[str] = None,
                        model: Optional[str] = None, temperature: float = 0.7,
                        max_tokens: int = 1024, timeout: float = 60.0) -> LLMResult:
        """便捷方法：单轮文本对话"""
        from jkos_core.llm.base import LLMMessage
        messages: List[LLMMessage] = []
        if system:
            messages.append(LLMMessage(role="system", content=system))
        messages.append(LLMMessage(role="user", content=prompt))
        return await self.chat(messages, model=model, temperature=temperature,
                               max_tokens=max_tokens, timeout=timeout)

    def _simulate(self, messages: List[LLMMessage], model: str) -> LLMResult:
        """模拟 DeepSeek 响应（无 API key 时）"""
        user_prompt = ""
        for m in messages:
            if m.role == "user":
                user_prompt += m.content + "\n"

        # 生成模拟响应
        response = f"[DeepSeek 模拟响应 - 模型: {model}]\n"
        response += f"用户问题: {user_prompt.strip()}\n"
        response += "这是模拟的 LLM 响应内容。在生产环境中，这将来自真实的 DeepSeek API。"

        return LLMResult(
            content=response,
            provider=self.name,
            model=model,
            prompt_tokens=len(user_prompt),
            completion_tokens=len(response),
            total_tokens=len(user_prompt) + len(response),
            simulated=True,
        )
