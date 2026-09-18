"""LLM 路由单元测试（任务 0.3）— 纯 assert，兼容 pytest 与 m0_selftest 直跑"""
import asyncio

from jkos_core.llm import SimulatedProvider, build_llm_router
from jkos_core.llm.base import LLMError, LLMMessage, LLMProvider, LLMResult
from jkos_core.llm.router import CircuitConfig, LLMRouter


class FlakyProvider(LLMProvider):
    """前 N 次调用失败，之后成功（模拟瞬时故障）"""

    name = "flaky"

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise LLMError("flaky 瞬时失败")
        return LLMResult(content="ok-from-flaky", provider=self.name, model="m",
                         prompt_tokens=1, completion_tokens=1, total_tokens=2, duration_ms=1)


class NullProvider(LLMProvider):
    """不可用供应商（healthy=False，应被 Router 跳过）"""

    name = "null"

    def healthy(self):
        return False

    async def chat(self, messages, **kw):
        raise LLMError("不应被调用")


def test_router_fallback_to_simulated():
    recorded = []
    router = LLMRouter(
        [FlakyProvider(999), SimulatedProvider()],
        usage_recorder=lambda r, m: recorded.append((r, m)),
    )
    result = asyncio.run(router.chat_text("你好", purpose="test", tenant_id="dev"))
    assert result.provider == "simulated"
    assert result.simulated is True
    assert result.meta["tenant_id"] == "dev"
    assert result.meta["purpose"] == "test"
    assert len(recorded) == 1 and recorded[0][0] is result


def test_router_skips_unhealthy_and_raises_when_all_fail():
    router = LLMRouter([NullProvider()])
    raised = False
    try:
        asyncio.run(router.chat_text("hi"))
    except LLMError as e:
        raised = True
        assert "不可用" in str(e)
    assert raised


def test_router_recovers_after_transient_failure():
    p = FlakyProvider(1)
    router = LLMRouter([p, SimulatedProvider()])
    r1 = asyncio.run(router.chat_text("hi"))
    r2 = asyncio.run(router.chat_text("hi"))
    assert r1.provider == "simulated"      # 第一次 flaky 失败 → 降级
    assert r2.provider == "flaky"          # 第二次 flaky 恢复 → 优先级恢复
    assert r2.content == "ok-from-flaky"
    assert p.calls == 2


def test_build_default_chain():
    router = build_llm_router()
    desc = router.describe()
    names = [d["name"] for d in desc]
    assert names[0] == "deepseek" and names[-1] == "simulated"
    assert desc[-1]["healthy"] is True  # simulated 永远可用


def test_message_structures():
    msgs = [LLMMessage(role="system", content="s"), LLMMessage(role="user", content="u")]
    assert msgs[0].role == "system"
    r = LLMResult(content="c", provider="p", model="m")
    assert r.total_tokens == 0 and r.simulated is False


class CaptureProvider(LLMProvider):
    """记录收到的 messages，简化断言"""

    name = "capture"

    def __init__(self):
        self.seen = None

    async def chat(self, messages, **kw):
        self.seen = [m.role for m in messages]
        return LLMResult(content="ok", provider=self.name, model="m",
                         prompt_tokens=1, completion_tokens=1, total_tokens=2, duration_ms=1)


def test_router_chat_text_with_system():
    """M6.1: chat_text 带 system 参数 → system 消息置顶（router.py:79）"""
    p = CaptureProvider()
    router = LLMRouter([p])
    asyncio.run(router.chat_text("你好", system="你是助手"))
    assert p.seen == ["system", "user"]


def test_router_recorder_failure_does_not_break_chat():
    """M6.1: usage_recorder 抛异常 → 落库失败仅记日志，主流程不阻断（router.py:70-71）"""

    def broken_recorder(result, meta):
        raise RuntimeError("db down")

    router = LLMRouter([SimulatedProvider()], usage_recorder=broken_recorder)
    result = asyncio.run(router.chat_text("hi"))
    assert result.provider == "simulated"
    assert result.simulated is True


# ─── AI 中台控制层：purpose 路由 + 供应商熔断 ───


def test_purpose_route_picks_only_chain():
    """M-A1: purpose 路由命中 → 仅调度该链内供应商（不跨链）"""
    flaky = FlakyProvider(999)
    cap = CaptureProvider()
    router = LLMRouter(
        [flaky, SimulatedProvider(), cap],
        routes={"chat": ["flaky", "simulated"]},
    )
    result = asyncio.run(router.chat_text("hi", purpose="chat"))
    assert result.provider == "simulated"   # flaky 失败 → 链内降级到 simulated
    assert flaky.calls == 1                 # 链内 primary 先被尝试
    assert cap.seen is None                 # capture 不在 chat 链内，绝不跨链拉取


def test_purpose_unknown_falls_back_to_default_chain():
    """M-A1: purpose 不在路由表 → 回退默认链（providers 传入顺序）"""
    flaky = FlakyProvider(3)
    router = LLMRouter(
        [flaky, SimulatedProvider()],
        routes={"chat": ["simulated"]},
    )
    # "code" 不在路由表 → 默认链先试 flaky（本次失败 1 次），再降级 simulated
    result = asyncio.run(router.chat_text("hi", purpose="code"))
    assert result.provider == "simulated"
    assert flaky.calls == 1     # 默认链对 flaky 只尝试 1 次，失败即降级


def test_purpose_route_skips_unknown_provider_name():
    """M-A1: 路由链引用不存在的供应商名 → 静默跳过，不报错"""
    router = LLMRouter(
        [SimulatedProvider()],
        routes={"chat": ["ghost", "simulated"]},
    )
    result = asyncio.run(router.chat_text("hi", purpose="chat"))
    assert result.provider == "simulated"


def test_circuit_opens_after_max_failures():
    """M-A2: 连续失败达阈值 → 熔断冷却期内被跳过（不再被调用）"""
    flaky = FlakyProvider(999)
    router = LLMRouter(
        [flaky, SimulatedProvider()],
        circuit=CircuitConfig(max_failures=2, cooldown_seconds=999),
    )
    r1 = asyncio.run(router.chat_text("hi"))
    r2 = asyncio.run(router.chat_text("hi"))
    r3 = asyncio.run(router.chat_text("hi"))
    assert r1.provider == "simulated" and r2.provider == "simulated"
    assert r3.provider == "simulated"
    assert flaky.calls == 2     # 第 3 次起 flaky 处于冷却期，被跳过


def test_circuit_recovers_after_success():
    """M-A2: 失败计数未达阈值时成功 → 计数清零，不触发熔断"""
    flaky = FlakyProvider(1)
    router = LLMRouter(
        [flaky, SimulatedProvider()],
        circuit=CircuitConfig(max_failures=3, cooldown_seconds=999),
    )
    r1 = asyncio.run(router.chat_text("hi"))   # flaky 失败(1/3) → simulated
    r2 = asyncio.run(router.chat_text("hi"))   # flaky 成功 → 计数清零
    assert r1.provider == "simulated"
    assert r2.provider == "flaky"
    assert flaky.calls == 2


def test_circuit_reenabled_after_cooldown():
    """M-A2: 冷却期结束后供应商恢复参与调度"""
    flaky = FlakyProvider(1)
    router = LLMRouter(
        [flaky, SimulatedProvider()],
        circuit=CircuitConfig(max_failures=1, cooldown_seconds=0),
    )
    r1 = asyncio.run(router.chat_text("hi"))   # flaky 失败 → 熔断(冷却 0s) → simulated
    r2 = asyncio.run(router.chat_text("hi"))   # 冷却已过 → flaky 恢复并成功
    assert r1.provider == "simulated"
    assert r2.provider == "flaky"


def test_no_circuit_retries_failing_provider_each_call():
    """M-A2: 未配置熔断 → 每次调用都重新尝试失败供应商"""
    flaky = FlakyProvider(999)
    router = LLMRouter([flaky, SimulatedProvider()])   # circuit=None
    asyncio.run(router.chat_text("hi"))
    asyncio.run(router.chat_text("hi"))
    asyncio.run(router.chat_text("hi"))
    assert flaky.calls == 3     # 与熔断场景（仅调 2 次）形成对比


def test_build_llm_router_wires_routes_and_circuit():
    """M-A3: build_llm_router 透传 routes + circuit 配置"""
    from jkos_core.llm import LLMConfig

    router = build_llm_router(
        config=LLMConfig(
            llm_routes={"chat": ["simulated"]},
            circuit_max_failures=1,
            circuit_cooldown_seconds=999,
        )
    )
    assert router.routes == {"chat": ["simulated"]}
    assert router.circuit is not None
    assert router.circuit.max_failures == 1
    assert router.circuit.cooldown_seconds == 999
    # 路由链只含 simulated → 全程无真实网络调用
    result = asyncio.run(router.chat_text("hi", purpose="chat"))
    assert result.provider == "simulated"
    assert result.simulated is True
