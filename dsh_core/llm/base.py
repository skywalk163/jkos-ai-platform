"""DSH LLM 路由 - 基础协议与数据结构（M0 任务 0.3）

供应商抽象：Router 按 healthy() 过滤后依序做故障转移。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class LLMError(Exception):
    """LLM 调用失败（所有供应商失败后由 Router 抛出）"""


@dataclass
class LLMMessage:
    """对话消息（与 OpenAI 兼容协议的 role/content 对齐）"""
    role: str  # system / user / assistant
    content: str


@dataclass
class LLMResult:
    """统一返回结构：真实/模拟供应商共用同一契约"""
    content: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    duration_ms: int = 0
    simulated: bool = False
    cached: bool = False  # M7 7.4：命中响应缓存时由 Router 置位
    meta: Optional[Dict[str, Any]] = None  # 由 Router 附加：tenant_id/purpose/ref_*


class LLMProvider(ABC):
    """供应商抽象基类"""

    name: str = "base"

    @abstractmethod
    async def chat(
        self,
        messages: List[LLMMessage],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: float = 60.0,
    ) -> LLMResult:
        ...

    async def stream(self, messages: List[LLMMessage], **kwargs: Any):
        """流式响应（M7 7.4）：默认一次性返回完整结果，供应商可覆盖为增量块"""
        result = await self.chat(messages, **kwargs)
        yield result

    def healthy(self) -> bool:
        """是否可参与调度（如缺少 API Key 则被 Router 跳过）"""
        return True
