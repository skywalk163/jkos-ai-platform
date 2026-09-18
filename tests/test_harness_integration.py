"""M16 智能中枢 - 真实 harness runtime 集成测试（默认 skip）

AC-1：启动 sidecar → 创建会话 → 发 prompt → 收到 assistant 事件。

默认不运行（避免常规回归触发真实 LLM 调用与子进程）：
  JKOS_HARNESS_INTEGRATION=1  显式开启
  deepseek-harness-sdk        已安装（缺则 skip，不 fail）
  DEEPSEEK_API_KEY            已配置

FreeBSD 上还需 JKOS_HARNESS_DSH_BIN 指向源码构建的 launcher。
"""
import os

import pytest

from jkos_core.auth.context import TenantContext
from jkos_core.harness.config import HarnessConfig
from jkos_core.harness.gateway import HarnessGateway
from jkos_core.harness.runtime import sdk_available

pytestmark = pytest.mark.skipif(
    os.getenv("JKOS_HARNESS_INTEGRATION") != "1",
    reason="需 JKOS_HARNESS_INTEGRATION=1 显式开启（会启动子进程并调用真实 LLM）",
)


@pytest.fixture
def gateway(tmp_path):
    if not sdk_available():
        pytest.skip("deepseek-harness-sdk 未安装")
    if not os.getenv("DEEPSEEK_API_KEY"):
        pytest.skip("未配置 DEEPSEEK_API_KEY")

    config = HarnessConfig.from_env()
    config.enabled = True
    config.dsh_home = os.getenv("DSH_HOME") or str(tmp_path / "home")
    config.workspace = str(tmp_path / "ws")
    gateway = HarnessGateway(config)
    try:
        yield gateway
    finally:
        gateway.close()


def _ctx() -> TenantContext:
    return TenantContext(tenant_id="dev", tenant_code="dev", user_id="admin")


class TestRealRuntime:
    async def test_sidecar_roundtrip(self, gateway):
        """AC-1：完整闭环（此用例会真实调用 LLM，耗时较长）

        注意：必须校验 turn 是**成功**结束。仅断言「收到 turn/end」会被失败轮
        掩盖（finish_reason=error 时同样产生 turn/end），因此这里显式断言
        finish_reason == "completed" 并把错误信息透出。
        """
        session = gateway.create_session(_ctx())

        events = [event async for event in gateway.stream_turn(session, "只回答两个字：你好")]

        kinds = [e["type"] for e in events]
        assert kinds[0] == "session/start"
        turn_end = next((e for e in events if e["type"] == "turn/end"), None)
        assert turn_end is not None, f"未收到 turn/end：{events}"
        assert turn_end["finish_reason"] == "completed", (
            f"轮次未成功结束（finish_reason={turn_end['finish_reason']}）："
            f"{turn_end.get('final_response') or events}"
        )
        assert gateway.sidecar.started is True

    async def test_health_after_start(self, gateway):
        session = gateway.create_session(_ctx())
        [event async for event in gateway.stream_turn(session, "只回答两个字：你好")]

        health = gateway.health()

        assert health["status"] in ("ok", "degraded")
        assert health["started"] is True
        assert health["start_count"] == 1

    def test_close_reaps_sidecar(self, gateway):
        gateway.close()

        assert gateway.sidecar.started is False