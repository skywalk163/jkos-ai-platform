"""M16 智能中枢 - HarnessGateway 会话与事件流测试

用注入的 fake SDK 客户端验证（无需真实 harness runtime）：
- 会话按租户隔离、跨租户不可见
- 事件流：session/start → notification → turn/end
- 身份前导进入 harness 会话上下文（AC-4）
- 异常语义：runtime 不可用 → 降级；单轮失败 → 不降级
- sidecar 懒启动与幂等关闭
"""
from types import SimpleNamespace

import pytest

from jkos_core.auth.context import TenantContext
from jkos_core.harness.config import HarnessConfig
from jkos_core.harness.gateway import (
    HarnessGateway,
    identity_preamble,
    reset_gateway,
)
from jkos_core.harness.runtime import HarnessUnavailable, SidecarManager


class FakeRunResult:
    def __init__(self, session_id, final_response="完成", finish_reason="completed"):
        self.session_id = session_id
        self.final_response = final_response
        self.finish_reason = finish_reason
        self.events = []
        self.notifications = []


class FakeClient:
    """模拟官方 SDK 的同步客户端"""

    def __init__(self, config, *, error=None, notifications=1):
        self.config = config
        self.error = error
        self.notifications = notifications
        self.prompts = []
        self.closed = False
        self.run_calls = 0

    def run(self, input, *, session_id=None, on_notification=None):
        self.run_calls += 1
        # 模拟真实调用：本方法在工作线程中执行
        self.prompts.append(input)
        if self.error is not None:
            raise self.error
        for i in range(self.notifications):
            if on_notification is not None:
                on_notification(SimpleNamespace(
                    method="session.event",
                    payload={"sessionId": session_id, "event": {"seq": i}},
                ))
        return FakeRunResult(session_id)

    def close(self):
        self.closed = True


@pytest.fixture
def created_clients():
    return []


@pytest.fixture
def make_gateway(monkeypatch, tmp_path, created_clients):
    """构造注入了 fake 客户端的 gateway"""

    def _make(*, error=None, notifications=1):
        config = HarnessConfig(
            enabled=True,
            dsh_home=str(tmp_path / "home"),
            workspace=str(tmp_path / "ws"),
        )

        def factory(cfg):
            client = FakeClient(cfg, error=error, notifications=notifications)
            created_clients.append(client)
            return client

        sidecar = SidecarManager(config, client_factory=factory)
        return HarnessGateway(config, sidecar=sidecar)

    return _make


def _ctx(tenant_id="t1", tenant_code="dev", user_id="admin") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, tenant_code=tenant_code, user_id=user_id)


async def _collect(agen):
    return [item async for item in agen]


class TestSessions:
    def test_create_session_scopes_workspace_by_tenant(self, make_gateway, tmp_path):
        gateway = make_gateway()

        session = gateway.create_session(_ctx())

        assert session.session_id.startswith("jks-")
        assert session.tenant_id == "t1"
        assert session.user_id == "admin"
        # 工作区按租户 + 会话隔离，且目录已创建
        assert session.workspace.startswith(str(tmp_path / "ws" / "t1"))
        assert (tmp_path / "ws" / "t1" / session.session_id).is_dir()

    def test_get_session_is_tenant_scoped(self, make_gateway):
        """跨租户查询视为不存在（安全边界）"""
        gateway = make_gateway()
        session = gateway.create_session(_ctx(tenant_id="t1"))

        assert gateway.get_session(session.session_id, "t1") is session
        assert gateway.get_session(session.session_id, "t2") is None
        assert gateway.get_session("nosuch", "t1") is None

    def test_list_sessions_only_own_tenant(self, make_gateway):
        gateway = make_gateway()
        gateway.create_session(_ctx(tenant_id="t1", user_id="u1"))
        gateway.create_session(_ctx(tenant_id="t1", user_id="u2"))
        gateway.create_session(_ctx(tenant_id="t2", user_id="u3"))

        rows = gateway.list_sessions("t1")

        assert len(rows) == 2
        assert {r.user_id for r in rows} == {"u1", "u2"}


class TestStreamTurn:
    async def test_event_sequence(self, make_gateway):
        """AC-2：事件流按 session/start → notification → turn/end 产出"""
        gateway = make_gateway(notifications=2)
        session = gateway.create_session(_ctx())

        events = await _collect(gateway.stream_turn(session, "你好"))

        assert events[0]["type"] == "session/start"
        assert events[0]["session_id"] == session.session_id
        kinds = [e["type"] for e in events]
        assert kinds.count("notification") == 2
        assert kinds[-1] == "turn/end"
        assert events[-1]["finish_reason"] == "completed"
        assert events[-1]["final_response"] == "完成"

    async def test_identity_enters_prompt(self, make_gateway, created_clients):
        """AC-4：JKOS 身份随会话输入进入 harness 上下文"""
        gateway = make_gateway()
        session = gateway.create_session(_ctx(tenant_id="t1", tenant_code="dev",
                                              user_id="alice"))

        await _collect(gateway.stream_turn(session, "做个总结"))

        prompt = created_clients[0].prompts[0]
        assert "tenant_id=t1" in prompt
        assert "tenant_code=dev" in prompt
        assert "user_id=alice" in prompt
        assert f"session_id={session.session_id}" in prompt
        assert prompt.endswith("做个总结")

    async def test_identity_preamble_format(self):
        from jkos_core.harness.gateway import HubSession

        session = HubSession(session_id="s1", tenant_id="t1", tenant_code="dev",
                             user_id="u1", workspace="/tmp")
        text = identity_preamble(session)

        assert text.startswith("[JKOS 会话上下文]")
        assert "[用户输入]" in text

    async def test_turn_counter_increments(self, make_gateway):
        gateway = make_gateway()
        session = gateway.create_session(_ctx())

        await _collect(gateway.stream_turn(session, "第一轮"))
        await _collect(gateway.stream_turn(session, "第二轮"))

        assert session.turns == 2

    async def test_runtime_unavailable_marks_degraded(self, make_gateway, created_clients):
        """runtime 层不可用 → error 事件 + sidecar 降级"""
        gateway = make_gateway(error=HarnessUnavailable("SDK 未安装"))
        session = gateway.create_session(_ctx())

        events = await _collect(gateway.stream_turn(session, "你好"))

        assert events[-1]["type"] == "error"
        assert "SDK 未安装" in events[-1]["message"]
        assert gateway.sidecar.degraded is True
        assert gateway.health()["status"] == "degraded"

    async def test_turn_failure_does_not_degrade(self, make_gateway):
        """单轮业务失败不应把整个 runtime 标记为降级"""
        gateway = make_gateway(error=ValueError("模型返回异常"))
        session = gateway.create_session(_ctx())

        events = await _collect(gateway.stream_turn(session, "你好"))

        assert events[-1]["type"] == "error"
        assert "ValueError" in events[-1]["message"]
        assert gateway.sidecar.degraded is False

    async def test_notification_payload_is_json_safe(self, make_gateway):
        """SSE 要求事件可 JSON 序列化（不可序列化对象降级为字符串）"""
        import json

        gateway = make_gateway()
        session = gateway.create_session(_ctx())

        events = await _collect(gateway.stream_turn(session, "你好"))

        for event in events:
            json.dumps(event)  # 不抛异常即通过


class TestSidecarLifecycle:
    def test_lazy_start(self, make_gateway, created_clients):
        """sidecar 在首次执行前不启动"""
        gateway = make_gateway()

        gateway.create_session(_ctx())
        assert gateway.sidecar.started is False
        assert created_clients == []

    async def test_started_after_first_turn(self, make_gateway):
        gateway = make_gateway()
        session = gateway.create_session(_ctx())

        await _collect(gateway.stream_turn(session, "你好"))

        assert gateway.sidecar.started is True
        assert gateway.sidecar.start_count == 1

    async def test_single_instance_reused(self, make_gateway, created_clients):
        """多轮 / 多会话复用同一 sidecar 实例"""
        gateway = make_gateway()
        s1 = gateway.create_session(_ctx(tenant_id="t1"))
        s2 = gateway.create_session(_ctx(tenant_id="t2"))

        await _collect(gateway.stream_turn(s1, "a"))
        await _collect(gateway.stream_turn(s2, "b"))

        assert len(created_clients) == 1
        assert created_clients[0].run_calls == 2

    async def test_close_is_idempotent(self, make_gateway, created_clients):
        gateway = make_gateway()
        session = gateway.create_session(_ctx())
        await _collect(gateway.stream_turn(session, "你好"))

        gateway.close()
        gateway.close()  # 二次调用不应抛异常

        assert created_clients[0].closed is True
        assert gateway.sidecar.started is False

    def test_close_without_start_is_safe(self, make_gateway):
        gateway = make_gateway()

        gateway.close()

        assert gateway.sidecar.started is False

    async def test_health_reports_session_count(self, make_gateway):
        gateway = make_gateway()
        gateway.create_session(_ctx())

        health = gateway.health()

        assert health["gateway"] == "harness"
        assert health["sessions"] == 1
        assert health["status"] == "idle"


class TestSingleton:
    def test_get_gateway_returns_same_instance(self, monkeypatch, tmp_path):
        from jkos_core.harness import gateway as gw

        reset_gateway()
        monkeypatch.setenv("JKOS_HARNESS_ENABLED", "true")
        monkeypatch.setenv("DSH_HOME", str(tmp_path))
        try:
            first = gw.get_gateway()
            second = gw.get_gateway()
            assert first is second
        finally:
            reset_gateway()

    def test_reset_gateway_clears(self, monkeypatch, tmp_path):
        from jkos_core.harness import gateway as gw

        monkeypatch.setenv("DSH_HOME", str(tmp_path))
        reset_gateway()
        first = gw.get_gateway()
        reset_gateway()

        assert gw.get_gateway() is not first