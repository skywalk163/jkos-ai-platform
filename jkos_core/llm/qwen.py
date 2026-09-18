"""DSH LLM 供应商 - 通义千问 (Qwen)

M10 任务 10.1：通义千问供应商实现。

API 文档: https://help.aliyun.com/zh/dashscope/developer-reference/what-is-dashscope
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from jkos_core.llm.base import LLMMessage, LLMProvider, LLMResult

logger = logging.getLogger("dsh.llm.qwen")


class QwenProvider(LLMProvider):
    """通义千问供应商

    环境变量:
      DSH_QWEN_API_KEY / DSH_LLM_API_KEY: API 密钥
      DSH_QWEN_BASE_URL: API 基础 URL (默认 https://dashscope.aliyuncs.com)
      DSH_QWEN_MODEL: 模型名称 (默认 qwen-turbo)
    """

    name = "qwen"
    base_url = "https://dashscope.aliyuncs.com"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}
        self._api_key = config.get(
            "api_key",
            config.get("api_key", ""),
        )
        self._base_url = config.get("base_url", self.base_url).rstrip("/")
        self._model = config.get("model", "qwen-turbo")
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
        """调用通义千问 API"""
        import time

        start = time.monotonic()
        model_name = model or self._model
        url = f"{self._base_url}/compatible-mode/v1/chat/completions"

        # 转换消息格式
        openai_messages = []
        for msg in messages:
            openai_messages.append({"role": msg.role, "content": msg.content})

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model_name,
            "messages": openai_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = await response.json()

            # 解析响应
            choice = data.get("choices", [{}])[0]
            usage = data.get("usage", {})

            content = choice.get("message", {}).get("content", "")
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

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
            logger.error("Qwen API 调用失败: %s", e)
            raise


# ─── 便捷函数 ───

def build_qwen_provider(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> QwenProvider:
    """构建通义千问供应商"""
    import os

    config = {}
    config["api_key"] = api_key or os.getenv("DSH_QWEN_API_KEY") or os.getenv("DSH_LLM_API_KEY", "")
    config["base_url"] = base_url or os.getenv("DSH_QWEN_BASE_URL", "https://dashscope.aliyuncs.com")
    config["model"] = model or os.getenv("DSH_QWEN_MODEL", "qwen-turbo")

    return QwenProvider(config)
