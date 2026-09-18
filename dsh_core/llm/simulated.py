"""DSH LLM 路由 - 本地模拟供应商

兜底策略（设计文档 §8.2.4 降级预案）：DeepSeek 不可用时保证管线不中断。
输出带 [模拟输出] 标记，Token 按字符数/4 估算，计量照常落库（status=simulated），
成本报表能区分真实/模拟调用。
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from dsh_core.llm.base import LLMMessage, LLMProvider, LLMResult

logger = logging.getLogger("dsh.llm")


class SimulatedProvider(LLMProvider):
    name = "simulated"

    def healthy(self) -> bool:
        return True  # 永远可用

    async def chat(self, messages: List[LLMMessage], *, model: Optional[str] = None,
                   temperature: float = 0.0, max_tokens: int = 1024,
                   timeout: float = 5.0) -> LLMResult:
        started = time.monotonic()
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )
        head = last_user if len(last_user) <= 200 else last_user[:200] + "..."
        content = f"[模拟输出] 已收到请求：{head}"
        prompt_tokens = sum(len(m.content) for m in messages) // 4 + 1
        completion_tokens = max(1, len(content) // 4)
        return LLMResult(
            content=content,
            provider=self.name,
            model="simulated",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            duration_ms=int((time.monotonic() - started) * 1000),
            simulated=True,
        )

    async def stream(self, messages: List[LLMMessage], *, model: Optional[str] = None,
                     temperature: float = 0.0, max_tokens: int = 1024,
                     timeout: float = 5.0):
        """流式响应（M7 7.4）：将完整输出切成不超过 3 个增量块依次产出"""
        base = await self.chat(
            messages, model=model, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout,
        )
        text = base.content
        step = max(4, (len(text) + 2) // 3)
        tokens_total = 0
        for i in range(0, len(text), step):
            piece = text[i:i + step]
            tokens_total += max(1, len(piece) // 4)
            yield LLMResult(
                content=piece,
                provider=self.name,
                model="simulated",
                prompt_tokens=0,
                completion_tokens=max(1, len(piece) // 4),
                total_tokens=tokens_total,
                duration_ms=0,
                simulated=True,
            )

    async def stream(self, messages: List[LLMMessage], *, model: Optional[str] = None,
                     temperature: float = 0.0, max_tokens: int = 1024,
                     timeout: float = 5.0):
        """流式响应（M7 7.4）：将完整输出切成不超过 3 个增量块依次产出"""
        base = await self.chat(
            messages, model=model, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout,
        )
        text = base.content
        step = max(4, (len(text) + 2) // 3)
        tokens_total = 0
        for i in range(0, len(text), step):
            piece = text[i:i + step]
            tokens_total += max(1, len(piece) // 4)
            yield LLMResult(
                content=piece,
                provider=self.name,
                model="simulated",
                prompt_tokens=0,
                completion_tokens=max(1, len(piece) // 4),
                total_tokens=tokens_total,
                duration_ms=0,
                simulated=True,
            )
