"""DSH LLM 路由 - 多供应商故障转移 + Token 计量 + purpose 路由/熔断（AI 中台控制层）

路由策略：
  1. 未命中 purpose 路由时，按 providers 列表顺序尝试，跳过 healthy()=False 的供应商；
  2. 支持按 purpose（用途）配置 primary/fallback 路由链（业界"模型路由"最佳实践：
     purpose 为一等公民、模型为二等公民）；命中路由时仅在该链内选择，不跨链；
  3. 供应商失败（网络/5xx/4xx）自动切换下一个；可配置熔断：
     某供应商连续失败 max_failures 次后，进入 cooldown_seconds 秒冷却期再参与调度；
  4. 全部失败抛 LLMError；
  5. 成功后经 usage_recorder 回调把 token 用量落库，回调在独立线程执行且失败不阻断主流程。

后续扩展：按租户配额限流（utils/ratelimit）、按成本路由。
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from dsh_core.cache.manager import LLMCachedResponse
from dsh_core.llm.base import LLMError, LLMMessage, LLMProvider, LLMResult

logger = logging.getLogger("dsh.llm")

# 计量回调签名：(result, meta) -> None
UsageRecorder = Callable[[LLMResult, Dict[str, Any]], None]


@dataclasses.dataclass
class CircuitConfig:
    """供应商熔断配置：连续失败 max_failures 次后进入 cooldown_seconds 秒冷却期"""
    max_failures: int = 3
    cooldown_seconds: float = 30.0


class LLMRouter:
    def __init__(self, providers: List[LLMProvider],
                 usage_recorder: Optional[UsageRecorder] = None,
                 llm_cache: Optional[LLMCachedResponse] = None,
                 routes: Optional[Dict[str, List[str]]] = None,
                 circuit: Optional[CircuitConfig] = None):
        self.providers = providers
        self.usage_recorder = usage_recorder
        self.cache = llm_cache  # M7 7.4：响应缓存（相同提示词直接命中）
        self.routes = routes or {}           # purpose -> [primary, fallback...] 供应商名链
        self.circuit = circuit                # 熔断配置（None 表示关闭熔断）
        self._failures: Dict[str, int] = {}   # 各供应商当前连续失败次数
        self._until: Dict[str, float] = {}    # 各供应商熔断到期时间戳（monotonic）

    async def chat(
        self,
        messages: List[LLMMessage],
        *,
        tenant_id: Optional[str] = None,
        purpose: Optional[str] = None,
        ref_type: Optional[str] = None,
        ref_id: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: float = 60.0,
    ) -> LLMResult:
        meta: Dict[str, Any] = {
            "tenant_id": tenant_id or "system",
            "purpose": purpose,
            "ref_type": ref_type,
            "ref_id": ref_id,
        }
        # M7 7.4 响应缓存：相同提示词直接命中，跳过供应商调用
        cached = await self._get_cached(messages, model, temperature)
        if cached is not None:
            cached.meta = meta
            return cached
        result: Optional[LLMResult] = None
        errors: List[str] = []
        for provider in self._route_providers(purpose):
            try:
                result = await provider.chat(
                    messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, timeout=timeout,
                )
                self._record_success(provider.name)
                break
            except Exception as e:  # 供应商故障 → 换下一个
                self._record_failure(provider.name)
                errors.append(f"{provider.name}: {e}")
                logger.warning("LLM 供应商 %s 失败，尝试下一个: %s", provider.name, e)
        if result is None:
            raise LLMError("所有 LLM 供应商不可用: " + "; ".join(errors))
        result.meta = meta
        await self._set_cached(messages, result, model, temperature)
        if self.usage_recorder:
            try:
                # 落库在独立线程，避免阻塞事件循环；失败不阻断主流程
                await asyncio.to_thread(self.usage_recorder, result, meta)
            except Exception:
                logger.error("LLM 计量落库失败", exc_info=True)
        return result

    async def chat_text(self, prompt: str, *, system: Optional[str] = None,
                        **kwargs: Any) -> LLMResult:
        """便捷方法：单轮文本对话"""
        messages: List[LLMMessage] = []
        if system:
            messages.append(LLMMessage(role="system", content=system))
        messages.append(LLMMessage(role="user", content=prompt))
        return await self.chat(messages, **kwargs)

    def describe(self) -> List[Dict[str, Any]]:
        """供应商链状态（健康检查用）"""
        return [{"name": p.name, "healthy": p.healthy()} for p in self.providers]

    # ─── AI 中台控制层：purpose 路由 + 熔断 ───

    def _route_providers(self, purpose: Optional[str]) -> List[LLMProvider]:
        """筛选本次可调度的供应商：
        - 未配置 purpose 路由（或 purpose 不在路由表）→ 按默认链全量排序；
        - 命中路由 → 仅用该 purpose 的 primary/fallback 链（不跨链）；
        - 双层过滤：healthy()=False 跳过；熔断冷却期内跳过。
        """
        if purpose and purpose in self.routes:
            names = self.routes[purpose]
        else:
            names = [p.name for p in self.providers]
        by_name = {p.name: p for p in self.providers}
        now = time.monotonic()
        ordered: List[LLMProvider] = []
        for n in names:
            p = by_name.get(n)
            if p is None:
                continue
            if not p.healthy():
                continue
            if self.circuit and self._until.get(p.name, 0.0) > now:
                logger.info("LLM 供应商 %s 处于熔断冷却中，跳过", p.name)
                continue
            ordered.append(p)
        return ordered

    def _record_failure(self, name: str) -> None:
        """供应商失败计数；达到阈值后触发熔断冷却"""
        if self.circuit is None:
            return
        self._failures[name] = self._failures.get(name, 0) + 1
        if self._failures[name] >= self.circuit.max_failures:
            self._until[name] = time.monotonic() + self.circuit.cooldown_seconds
            logger.warning(
                "LLM 供应商 %s 连续失败 %d 次，熔断冷却 %.1fs",
                name, self._failures[name], self.circuit.cooldown_seconds,
            )

    def _record_success(self, name: str) -> None:
        """供应商成功调用 → 清零失败计数并解除熔断"""
        self._failures.pop(name, None)
        self._until.pop(name, None)

    # ─── M7 7.4：流式响应 ───

    async def stream(
        self,
        messages: List[LLMMessage],
        *,
        tenant_id: Optional[str] = None,
        purpose: Optional[str] = None,
        ref_type: Optional[str] = None,
        ref_id: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: float = 60.0,
    ):
        """流式响应：逐块产出 LLMResult；缓存命中时一次性产出缓存结果"""
        meta: Dict[str, Any] = {
            "tenant_id": tenant_id or "system",
            "purpose": purpose,
            "ref_type": ref_type,
            "ref_id": ref_id,
        }
        cached = await self._get_cached(messages, model, temperature)
        if cached is not None:
            cached.meta = meta
            yield cached
            return
        errors: List[str] = []
        for provider in self._route_providers(purpose):
            try:
                pieces: List[LLMResult] = []
                async for piece in provider.stream(
                    messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, timeout=timeout,
                ):
                    piece.meta = meta
                    pieces.append(piece)
                    yield piece
                # 流式结束后将完整结果写入响应缓存（M7 7.4：相同提示词二次命中）
                if pieces:
                    first = pieces[0]
                    full = LLMResult(
                        content="".join(p.content for p in pieces),
                        provider=first.provider,
                        model=first.model,
                        prompt_tokens=first.prompt_tokens,
                        completion_tokens=sum(p.completion_tokens for p in pieces),
                        total_tokens=sum(p.total_tokens for p in pieces),
                        duration_ms=first.duration_ms,
                        simulated=first.simulated,
                        meta=meta,
                    )
                    await self._set_cached(messages, full, model, temperature)
                self._record_success(provider.name)
                return
            except Exception as e:  # 供应商故障 → 换下一个
                self._record_failure(provider.name)
                errors.append(f"{provider.name}: {e}")
                logger.warning("LLM 流式供应商 %s 失败，尝试下一个: %s", provider.name, e)
        raise LLMError("所有 LLM 供应商不可用（流式）: " + "; ".join(errors))

    # ─── M7 7.4：请求批处理 ───

    async def batch_chat(self, items: List[Dict[str, Any]], *,
                         concurrency: int = 4, **defaults: Any) -> List[LLMResult]:
        """请求批处理：并发限流执行多个 chat 请求

        Args:
            items: 每项为 chat() 关键字参数字典（须含 messages）
            concurrency: 并发上限（默认 4）
            defaults: 未在单条 item 中指定的公共参数

        Returns:
            按 items 顺序排列的结果列表（批内自动复用响应缓存）
        """
        if concurrency < 1:
            raise ValueError("concurrency 至少为 1")
        sem = asyncio.Semaphore(concurrency)

        async def _run(item: Dict[str, Any]) -> LLMResult:
            kwargs = {**defaults, **item}
            msgs = kwargs.pop("messages")
            async with sem:
                return await self.chat(msgs, **kwargs)

        return list(await asyncio.gather(*(_run(i) for i in items)))

    # ─── M7 7.4：响应缓存辅助 ───

    def _prompt_text(self, messages: List[LLMMessage]) -> Optional[str]:
        """取最后一条 user 消息作为缓存键内容（无 user 消息则跳过缓存）"""
        for m in reversed(messages):
            if m.role == "user":
                return m.content
        return None

    async def _get_cached(self, messages: List[LLMMessage],
                          model: Optional[str], temperature: float) -> Optional[LLMResult]:
        """响应缓存读取；无缓存配置/读取失败/数据损坏均安全降级"""
        if self.cache is None:
            return None
        prompt = self._prompt_text(messages)
        if prompt is None:
            return None
        try:
            data = await self.cache.get_cached(prompt, model or "", temperature)
        except Exception:
            logger.warning("LLM 缓存读取失败，忽略缓存", exc_info=True)
            return None
        if data is None:
            return None
        try:
            result = LLMResult(**data) if isinstance(data, dict) else data
        except Exception:
            logger.warning("LLM 缓存数据无法解析，忽略", exc_info=True)
            return None
        result.cached = True
        return result

    async def _set_cached(self, messages: List[LLMMessage], result: LLMResult,
                          model: Optional[str], temperature: float) -> None:
        """响应缓存写入（存 dataclass dict 以兼容 JSON 序列化后端）"""
        if self.cache is None:
            return
        prompt = self._prompt_text(messages)
        if prompt is None:
            return
        try:
            await self.cache.set_cached(
                prompt, model or "", temperature, dataclasses.asdict(result)
            )
        except Exception:
            logger.warning("LLM 缓存写入失败", exc_info=True)