"""M7 任务 7.4：LLM 调用优化 — batch_chat / stream / 响应缓存"""

import asyncio

import pytest

from jkos_core.cache.manager import CacheManager, LLMCachedResponse
from jkos_core.llm.base import LLMError, LLMMessage, LLMProvider, LLMResult
from jkos_core.llm.router import LLMRouter
from jkos_core.llm.simulated import SimulatedProvider


class _FailingProvider(LLMProvider):
    """模拟供应商：chat / stream 一律抛 LLMError"""

    name = "failing"

    def healthy(self) -> bool:
        return True

    async def chat(self, messages, **kwargs):
        raise LLMError("provider down")

    async def stream(self, messages, **kwargs):
        raise LLMError("provider down")
        yield  # pragma: no cover - 使方法成为异步生成器


class _CountingProvider(LLMProvider):
    """记录调用次数的供应商"""

    name = "counting"

    def __init__(self):
        self.calls = 0

    def healthy(self) -> bool:
        return True

    async def chat(self, messages, **kwargs):
        self.calls += 1
        return LLMResult(content=f"reply-{self.calls}", provider=self.name,
                         model=kwargs.get("model"), simulated=True)


class _ProbeProvider(LLMProvider):
    """记录并发峰值的供应商"""

    name = "probe"

    def __init__(self):
        self.active = 0
        self.peak = 0

    def healthy(self) -> bool:
        return True

    async def chat(self, messages, **kwargs):
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return LLMResult(content="ok", provider=self.name,
                         model=kwargs.get("model"))


async def test_chat_uses_response_cache():
    """chat：相同提示词第二次命中缓存，跳过供应商调用"""
    provider = _CountingProvider()
    router = LLMRouter([provider], llm_cache=LLMCachedResponse(CacheManager()))
    msg = [LLMMessage(role="user", content="hello")]
    r1 = await router.chat(msg)
    assert r1.cached is False
    assert r1.content == "reply-1"
    r2 = await router.chat(msg)
    assert r2.cached is True
    assert r2.content == "reply-1"
    assert provider.calls == 1  # 第二次未调用供应商


async def test_chat_all_providers_fail():
    """chat：全部供应商失败 → LLMError"""
    router = LLMRouter([_FailingProvider()])
    with pytest.raises(LLMError):
        await router.chat([LLMMessage(role="user", content="q")])


async def test_chat_text_convenience():
    """chat_text：单轮文本对话，默认走 simulated"""
    router = LLMRouter([SimulatedProvider()])
    result = await router.chat_text("你好")
    assert result.content.startswith("[模拟输出]")
    assert result.simulated is True


async def test_chat_usage_recorder_called():
    """chat：成功调用后通知 usage_recorder（meta 含 tenant_id）"""
    recorded = []

    def recorder(result, meta):
        recorded.append((result, meta))

    router = LLMRouter([SimulatedProvider()], usage_recorder=recorder)
    res = await router.chat([LLMMessage(role="user", content="hi")],
                            tenant_id="t-9", purpose="m7-test")
    assert len(recorded) == 1
    result, meta = recorded[0]
    assert result.content == res.content
    assert meta["tenant_id"] == "t-9"
    assert meta["purpose"] == "m7-test"


async def test_batch_chat_keeps_order_and_merges_params():
    """batch_chat：item 覆盖 defaults，结果按 items 顺序返回"""
    seen = []

    class _RecProvider(LLMProvider):
        name = "rec"

        def healthy(self):
            return True

        async def chat(self, messages, **kwargs):
            seen.append((messages[-1].content, kwargs.get("temperature"),
                         kwargs.get("model")))
            return LLMResult(content="ok", provider=self.name,
                             model=kwargs.get("model"))

    router = LLMRouter([_RecProvider()])
    items = [
        {"messages": [LLMMessage(role="user", content="a")], "temperature": 0.2},
        {"messages": [LLMMessage(role="user", content="b")]},
    ]
    results = await router.batch_chat(items, concurrency=2,
                                      temperature=0.9, model="m7")
    assert [r.content for r in results] == ["ok", "ok"]
    assert [s[0] for s in seen] == ["a", "b"]          # 顺序保真
    assert [s[1] for s in seen] == [0.2, 0.9]          # item 覆盖 defaults
    assert [s[2] for s in seen] == ["m7", "m7"]        # 公共参数生效


async def test_batch_chat_concurrency_limited():
    """batch_chat：并发不超过 concurrency（Semaphore 限流）"""
    probe = _ProbeProvider()
    router = LLMRouter([probe])
    items = [{"messages": [LLMMessage(role="user", content=f"q{i}")]}
             for i in range(6)]
    results = await router.batch_chat(items, concurrency=2)
    assert len(results) == 6
    assert probe.peak <= 2


async def test_batch_chat_invalid_concurrency():
    """batch_chat：concurrency < 1 → ValueError"""
    router = LLMRouter([SimulatedProvider()])
    with pytest.raises(ValueError):
        await router.batch_chat([], concurrency=0)


async def test_stream_chunks_and_writes_cache():
    """stream：逐片产出、聚合内容正确，结束后回写响应缓存（二次命中）"""
    router = LLMRouter([SimulatedProvider()],
                       llm_cache=LLMCachedResponse(CacheManager()))
    msg = [LLMMessage(role="user", content="问一个问题")]
    pieces1 = [p async for p in router.stream(msg)]
    assert pieces1  # 至少一片
    joined = "".join(p.content for p in pieces1)
    assert joined.startswith("[模拟输出] 已收到请求：")
    # 第二次流式：命中缓存，单片返回
    pieces2 = [p async for p in router.stream(msg)]
    assert len(pieces2) == 1
    assert pieces2[0].cached is True
    assert pieces2[0].content == joined


async def test_stream_all_providers_fail():
    """stream：全部供应商失败 → 迭代时抛 LLMError"""
    router = LLMRouter([_FailingProvider()])
    with pytest.raises(LLMError):
        async for _ in router.stream([LLMMessage(role="user", content="q")]):
            pass