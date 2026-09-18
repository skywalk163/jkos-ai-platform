"""DSH 测试配置"""

import pytest
import asyncio


@pytest.fixture
def event_loop():
    """创建事件循环"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def plugin_context():
    """创建插件上下文"""
    from jkos_core.plugins import PluginContext
    return PluginContext(
        request_id="test-request-001",
        user_id="test-user",
        tenant_id="test-tenant",
    )


@pytest.fixture
def mock_storage():
    """模拟存储"""
    from jkos_core.storage import StorageManager, StorageConfig
    config = StorageConfig(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="dsh_test",
        postgres_user="test",
        postgres_password="test",
    )
    return StorageManager(config)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """M14: 每个用例前后清空全局多租户限流器字典，避免跨用例限流状态泄漏"""
    from jkos_core.utils.ratelimit import _global_limiter
    _global_limiter._limiters.clear()
    yield
    _global_limiter._limiters.clear()
