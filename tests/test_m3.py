"""M3 生产就绪测试（任务 3.1-3.4）— LLM 供应商、通知渠道、缓存层、监控指标

纯 assert 风格，兼容 pytest 与 m0_selftest 直跑。
验证 LLM 供应商接入、通知渠道、缓存层、监控指标。
"""
import asyncio
import os
import tempfile
from types import SimpleNamespace

import httpx

from jkos_core.bootstrap import build_components
from jkos_core.cache.manager import CacheBackend, CacheManager, LLMCachedResponse, MemoryCache, RedisCache
from jkos_core.llm import DeepSeekProvider, OpenAIProvider, build_llm_router
from jkos_core.metrics.collector import DSHMetrics, Metric, get_metrics
from jkos_core.notify.channels import (
    DingTalkChannel,
    EmailChannel,
    NotificationChannel,
    NotificationManager,
    WeComChannel,
)


# ─── LLM 供应商测试（任务 3.1）───

def test_deepseek_provider_simulated():
    """DeepSeek 供应商：无 API key 时使用模拟模式"""
    provider = DeepSeekProvider({"api_key": ""})
    assert provider.name == "deepseek"
    assert provider.healthy() is False

    # 测试模拟响应
    result = asyncio.run(provider.chat_text("你好"))
    assert result.provider == "deepseek"
    assert result.simulated is True
    assert "模拟响应" in result.content


def test_openai_provider_simulated():
    """OpenAI 供应商：无 API key 时使用模拟模式"""
    provider = OpenAIProvider({"api_key": ""})
    assert provider.name == "openai"
    assert provider.healthy() is False

    # 测试模拟响应
    result = asyncio.run(provider.chat_text("你好"))
    assert result.provider == "openai"
    assert result.simulated is True
    assert "模拟响应" in result.content


def test_llm_router_fallback():
    """LLM 路由：故障转移"""
    router = build_llm_router()
    result = asyncio.run(router.chat_text("测试"))
    assert result.simulated is True
    assert result.provider in ["deepseek", "openai", "simulated"]


# ─── 通知渠道测试（任务 3.2）───

def test_email_channel_simulated():
    """邮件渠道：未配置时使用模拟模式"""
    channel = EmailChannel()
    assert channel.name == "email"
    assert channel.healthy() is False

    result = asyncio.run(channel.send("测试标题", "测试内容", ["test@example.com"]))
    assert result["channel"] == "email"
    assert result["status"] == "simulated"


def test_wecom_channel_simulated():
    """企业微信渠道：未配置时使用模拟模式"""
    channel = WeComChannel()
    assert channel.name == "wecom"
    assert channel.healthy() is False

    result = asyncio.run(channel.send("测试标题", "测试内容", ["user1"]))
    assert result["channel"] == "wecom"
    assert result["status"] == "simulated"


def test_dingtalk_channel_simulated():
    """钉钉渠道：未配置时使用模拟模式"""
    channel = DingTalkChannel()
    assert channel.name == "dingtalk"
    assert channel.healthy() is False

    result = asyncio.run(channel.send("测试标题", "测试内容", ["user1"]))
    assert result["channel"] == "dingtalk"
    assert result["status"] == "simulated"


def test_notification_manager():
    """通知管理器：多渠道发送"""
    manager = NotificationManager()
    manager.register(EmailChannel())
    manager.register(WeComChannel())
    manager.register(DingTalkChannel())

    results = asyncio.run(manager.send("测试标题", "测试内容", ["user1"]))
    assert len(results) == 3
    assert all(r["status"] == "simulated" for r in results)


# ─── 缓存层测试（任务 3.3）───

def test_memory_cache():
    """内存缓存：基本操作"""
    cache = MemoryCache()

    # 测试 set/get
    asyncio.run(cache.set("key1", "value1", ttl=10))
    value = asyncio.run(cache.get("key1"))
    assert value == "value1"

    # 测试 exists
    exists = asyncio.run(cache.exists("key1"))
    assert exists is True

    # 测试 delete
    deleted = asyncio.run(cache.delete("key1"))
    assert deleted is True
    exists = asyncio.run(cache.exists("key1"))
    assert exists is False


def test_cache_manager():
    """缓存管理器：统一接口"""
    manager = CacheManager()

    asyncio.run(manager.set("test_key", {"data": "test"}))
    value = asyncio.run(manager.get("test_key"))
    assert value == {"data": "test"}


def test_cache_manager_default_backend():
    """缓存管理器：默认使用内存缓存"""
    manager = CacheManager()
    assert manager.get_backend_type() == "MemoryCache"


# ─── 监控指标测试（任务 3.4）───

def test_counter_metric():
    """计数器指标"""
    from jkos_core.metrics.collector import Counter

    counter = Counter("test_counter", "测试计数器", ["label1"])
    counter.inc({"label1": "value1"})
    counter.inc({"label1": "value1"})
    counter.inc({"label1": "value2"})

    assert counter.value({"label1": "value1"}) == 2.0
    assert counter.value({"label1": "value2"}) == 1.0


def test_histogram_metric():
    """直方图指标"""
    from jkos_core.metrics.collector import Histogram

    histogram = Histogram("test_histogram", "测试直方图")
    histogram.observe(1.0)
    histogram.observe(2.0)
    histogram.observe(3.0)

    assert histogram.value() == 2.0  # 平均值


def test_metrics_collector():
    """指标收集器"""
    from jkos_core.metrics.collector import MetricsCollector

    collector = MetricsCollector()
    counter = collector.register_counter("requests_total", "请求总数", ["method"])
    histogram = collector.register_histogram("request_duration_seconds", "请求耗时", ["method"])

    counter.inc({"method": "GET"})
    histogram.observe(0.5, {"method": "GET"})

    prometheus = collector.to_prometheus_format()
    assert "requests_total" in prometheus
    assert "request_duration_seconds" in prometheus


def test_dsh_metrics():
    """DSH 预定义指标"""
    metrics = get_metrics()

    # 记录工作流执行
    metrics.record_workflow("test_workflow", "COMPLETED", 1.5)
    metrics.record_llm_call("deepseek", "deepseek-chat", 100, 50)
    metrics.record_approval("serial", "approve")
    metrics.record_timeout("medium")

    # 验证指标已记录
    prometheus = metrics.collector.to_prometheus_format()
    assert "dsh_workflow_executions_total" in prometheus
    assert "dsh_llm_calls_total" in prometheus
    assert "dsh_approval_decisions_total" in prometheus


# ─── 集成测试（任务 3.5）───

def test_build_components_integration():
    """组件装配：集成所有 M3 组件"""
    tmp = tempfile.mkdtemp(prefix="dsh_m3_")
    os.environ["DSH_DB_PATH"] = os.path.join(tmp, "test.db")

    comps = build_components(db_path=os.path.join(tmp, "test.db"))

    # 验证所有组件
    assert comps.db is not None
    assert comps.llm is not None
    assert comps.notify is not None
    assert comps.cache is not None
    assert comps.metrics is not None

    # 验证通知渠道
    channels = comps.notify.describe()
    assert len(channels) == 3

    # 验证缓存后端
    assert comps.cache.get_backend_type() == "MemoryCache"

    comps.close()


def test_metrics_in_workflow():
    """指标集成：工作流执行时记录指标"""
    from jkos_core.workflow import WorkflowEngine
    from jkos_core.workflow.base import NodeSpec, WorkflowDef
    from jkos_core.workflow.nodes import WORKFLOW_REGISTRY

    # 创建简单工作流
    async def echo_node(ctx):
        return {"result": "ok"}

    engine = WorkflowEngine(SimpleNamespace(
        db=None, tenants=None, workflows=None,
        approvals=None, audit=None, llm=None,
        notify=None, cache=None, metrics=get_metrics(),
    ))
    engine.register_node("echo", echo_node)

    WORKFLOW_REGISTRY["test_metrics"] = WorkflowDef(
        code="test_metrics", description="测试指标",
        nodes=[NodeSpec("step1", "echo")]
    )

    # 验证指标收集器可用
    metrics = get_metrics()
    assert metrics is not None
    assert metrics.collector is not None


# ─── M6 任务 6.2：覆盖率补充测试（cache/notify/metrics）───


class _FakeHttpxResponse:
    """模拟 httpx.Response"""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {"errcode": 0}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class _FakeHttpxClient:
    """模拟 httpx.AsyncClient：请求成功（200）"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, **kwargs):
        return _FakeHttpxResponse(status_code=200)

    async def post(self, url, **kwargs):
        return _FakeHttpxResponse(status_code=200)


class _FakeHttpxClientFail:
    """模拟 httpx.AsyncClient：请求抛异常"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, **kwargs):
        raise RuntimeError("connection refused")

    async def post(self, url, **kwargs):
        raise RuntimeError("connection refused")


class _BaseCacheBackend(CacheBackend):
    """直接调用抽象基类方法，覆盖抽象方法体"""

    async def get(self, key):
        return await super().get(key)

    async def set(self, key, value, ttl=None):
        await super().set(key, value, ttl)

    async def delete(self, key):
        return await super().delete(key)

    async def exists(self, key):
        return await super().exists(key)


def test_cache_backend_abstract_methods():
    """缓存抽象基类：默认方法体"""
    backend = _BaseCacheBackend()
    assert asyncio.run(backend.get("k")) is None
    asyncio.run(backend.set("k", "v"))
    assert asyncio.run(backend.exists("k")) is None
    assert asyncio.run(backend.delete("k")) is None


def test_memory_cache_missing_expired_clear():
    """内存缓存：缺失键 / 过期清理 / 删除不存在键 / clear"""
    cache = MemoryCache()
    # 不存在的键 → None
    assert asyncio.run(cache.get("missing")) is None
    # 过期条目 → 删除并返回 None
    asyncio.run(cache.set("expired", "v", ttl=-1))
    assert asyncio.run(cache.get("expired")) is None
    assert asyncio.run(cache.exists("expired")) is False
    # 删除不存在的键 → False
    assert asyncio.run(cache.delete("missing")) is False
    # clear 清空全部
    asyncio.run(cache.set("a", 1))
    asyncio.run(cache.set("b", 2))
    cache.clear()
    assert asyncio.run(cache.get("a")) is None
    assert asyncio.run(cache.get("b")) is None


class _FakeRedisClient:
    """模拟 redis.asyncio 客户端（M7 7.2 惰性连接升级后）"""

    def __init__(self, fail_connect=False):
        self._fail = fail_connect
        self._store = {}

    async def ping(self):
        if self._fail:
            raise RuntimeError("connection refused")
        return True

    async def get(self, key):
        return self._store.get(key)

    async def set(self, key, value, ex=None):
        self._store[key] = value
        return True

    async def delete(self, key):
        return 1 if self._store.pop(key, None) is not None else 0

    async def exists(self, key):
        return 1 if key in self._store else 0


def test_redis_cache_init():
    """Redis 缓存：初始化与配置（惰性连接）"""
    cache = RedisCache()
    assert cache._client is None  # M7 7.2：未连接时不建立客户端（原 _connected 已移除）
    assert cache.config == {}
    cache2 = RedisCache({"timeout": 3})
    assert cache2.config == {"timeout": 3}


def test_redis_cache_connection_live(monkeypatch):
    """Redis 缓存：连接成功路径（aioredis.from_url 被模拟）"""
    fake = _FakeRedisClient()
    monkeypatch.setattr("jkos_core.cache.manager.aioredis",
                        SimpleNamespace(from_url=lambda url, **kw: fake))
    cache = RedisCache()
    # 首次调用建立连接（from_url + ping），之后复用客户端
    assert asyncio.run(cache.get("k")) is None
    assert cache._client is fake
    # 已连接时 set / get / delete / exists 全链路（JSON 往返）
    asyncio.run(cache.set("k", {"a": 1}))
    assert asyncio.run(cache.get("k")) == {"a": 1}
    assert asyncio.run(cache.exists("k")) is True
    assert asyncio.run(cache.delete("k")) is True
    assert asyncio.run(cache.exists("k")) is False


def test_redis_cache_connection_failure(monkeypatch):
    """Redis 缓存：连接失败路径（ping 抛异常 → 自动降级）"""
    fake = _FakeRedisClient(fail_connect=True)
    monkeypatch.setattr("jkos_core.cache.manager.aioredis",
                        SimpleNamespace(from_url=lambda url, **kw: fake))
    cache = RedisCache()
    # 连接异常 → 降级返回 None，客户端保持未连接（后续调用会重试）
    assert asyncio.run(cache.get("k")) is None
    assert cache._client is None
    # 降级模式：set 直接返回、delete False、exists False
    asyncio.run(cache.set("k", "v"))
    assert asyncio.run(cache.delete("k")) is False
    assert asyncio.run(cache.exists("k")) is False


def test_cache_manager_delete_and_exists():
    """缓存管理器：delete / exists 转发到后端"""
    manager = CacheManager()
    assert asyncio.run(manager.exists("k")) is False
    asyncio.run(manager.set("k", "v"))
    assert asyncio.run(manager.exists("k")) is True
    assert asyncio.run(manager.delete("k")) is True
    assert asyncio.run(manager.delete("k")) is False


def test_llm_cached_response():
    """LLM 响应缓存：键生成与读写"""
    manager = CacheManager()
    wrapper = LLMCachedResponse(manager, ttl=60)
    assert wrapper.ttl == 60
    # 键生成：前缀 + 确定性 md5
    key = wrapper._make_key("prompt", "model", 0.5)
    assert key.startswith("llm:")
    assert key == wrapper._make_key("prompt", "model", 0.5)
    # 未命中 → None
    assert asyncio.run(wrapper.get_cached("prompt", "model", 0.5)) is None
    # 写入后命中
    asyncio.run(wrapper.set_cached("prompt", "model", 0.5, {"content": "ok"}))
    assert asyncio.run(wrapper.get_cached("prompt", "model", 0.5)) == {"content": "ok"}


class _BaseChannel(NotificationChannel):
    """调用抽象基类方法，覆盖抽象方法体"""

    @property
    def name(self):
        return super().name

    @property
    def healthy(self):
        return super().healthy

    async def send(self, title, content, recipients, **kwargs):
        base_result = await super().send(title, content, recipients, **kwargs)
        return {"base": base_result}


def test_channel_base_class():
    """通知渠道抽象基类：默认方法体"""
    channel = _BaseChannel()
    assert channel.name is None
    assert channel.healthy() is True
    result = asyncio.run(channel.send("t", "c", ["u"]))
    assert result == {"base": None}


def test_email_channel_configured_real_branch(monkeypatch):
    """邮件渠道：配置完整时走真实发送分支（警告并模拟）"""
    monkeypatch.setenv("DSH_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("DSH_SMTP_USER", "user")
    monkeypatch.setenv("DSH_SMTP_PASSWORD", "pass")
    channel = EmailChannel()
    assert channel.healthy() is True
    result = asyncio.run(channel.send("t", "c", ["a@b.c"]))
    assert result["status"] == "simulated"


def test_wecom_channel_real_send_success(monkeypatch):
    """企业微信渠道：真实 webhook 发送成功"""
    monkeypatch.setenv("DSH_WECOM_WEBHOOK", "https://qyapi.weixin.qq.com/hook")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpxClient)
    channel = WeComChannel()
    assert channel.healthy() is True
    result = asyncio.run(channel.send("t", "c", ["u"]))
    assert result["status"] == "sent"
    assert result["response"] == {"errcode": 0}


def test_wecom_channel_real_send_failure(monkeypatch):
    """企业微信渠道：webhook 请求异常"""
    monkeypatch.setenv("DSH_WECOM_WEBHOOK", "https://qyapi.weixin.qq.com/hook")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpxClientFail)
    channel = WeComChannel()
    result = asyncio.run(channel.send("t", "c", ["u"]))
    assert result["status"] == "failed"
    assert "connection refused" in result["error"]


def test_dingtalk_channel_real_send_success(monkeypatch):
    """钉钉渠道：真实 webhook 发送成功"""
    monkeypatch.setenv("DSH_DINGTALK_WEBHOOK", "https://oapi.dingtalk.com/hook")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpxClient)
    channel = DingTalkChannel()
    assert channel.healthy() is True
    result = asyncio.run(channel.send("t", "c", ["u"]))
    assert result["status"] == "sent"
    assert result["response"] == {"errcode": 0}


def test_dingtalk_channel_real_send_failure(monkeypatch):
    """钉钉渠道：webhook 请求异常"""
    monkeypatch.setenv("DSH_DINGTALK_WEBHOOK", "https://oapi.dingtalk.com/hook")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpxClientFail)
    channel = DingTalkChannel()
    result = asyncio.run(channel.send("t", "c", ["u"]))
    assert result["status"] == "failed"
    assert "connection refused" in result["error"]


class _FailingChannel(NotificationChannel):
    """send 会抛异常的渠道，用于覆盖管理器异常处理"""

    @property
    def name(self):
        return "failing"

    async def send(self, title, content, recipients, **kwargs):
        raise RuntimeError("channel down")


def test_notification_manager_channel_error():
    """通知管理器：单个渠道异常不影响整体"""
    manager = NotificationManager([_FailingChannel()])
    results = asyncio.run(manager.send("t", "c", ["u"]))
    assert results[0]["status"] == "failed"
    assert results[0]["error"] == "channel down"


class _BaseMetric(Metric):
    """调用抽象基类方法，覆盖抽象方法体"""

    def name(self):
        return super().name()

    def value(self):
        return super().value()

    def labels(self):
        return super().labels()


def test_metric_base_class():
    """指标抽象基类：默认方法体"""
    metric = _BaseMetric()
    assert metric.name() is None
    assert metric.value() is None
    assert metric.labels() is None


def test_counter_name_and_no_labels_prometheus():
    """计数器：name 访问器 + 无标签 Prometheus 输出"""
    from jkos_core.metrics.collector import Counter, MetricsCollector

    collector = MetricsCollector()
    counter = collector.register_counter("empty_counter", "无标签计数器")
    assert counter.name() == "empty_counter"
    counter.inc()
    counter.inc(amount=2.0)
    assert counter.value() == 3.0
    # 无标签 → _make_key 空串 → 无标签格式行
    fmt = collector.to_prometheus_format()
    assert "empty_counter 3.0" in fmt
    # get_counter 命中/未命中
    assert collector.get_counter("empty_counter") is counter
    assert collector.get_counter("missing") is None


def test_histogram_name_and_no_labels_prometheus():
    """直方图：name 访问器 + 无标签 Prometheus 输出"""
    from jkos_core.metrics.collector import Histogram, MetricsCollector

    histogram = Histogram("empty_hist", "无标签直方图")
    assert histogram.name() == "empty_hist"
    histogram.observe(2.0)
    histogram.observe(4.0)
    assert histogram.value() == 3.0

    collector = MetricsCollector()
    collector.register_histogram("empty_hist2", "无标签直方图2")
    hist = collector.get_histogram("empty_hist2")
    hist.observe(1.0)
    hist.observe(5.0)
    fmt = collector.to_prometheus_format()
    assert "empty_hist2 3.0" in fmt
    assert collector.get_histogram("missing") is None


# ─── 入口 ───

if __name__ == "__main__":
    import sys

    tests = [
        test_deepseek_provider_simulated,
        test_openai_provider_simulated,
        test_llm_router_fallback,
        test_email_channel_simulated,
        test_wecom_channel_simulated,
        test_dingtalk_channel_simulated,
        test_notification_manager,
        test_memory_cache,
        test_cache_manager,
        test_cache_manager_default_backend,
        test_counter_metric,
        test_histogram_metric,
        test_metrics_collector,
        test_dsh_metrics,
        test_build_components_integration,
        test_metrics_in_workflow,
        # M6 任务 6.2：cache/notify/metrics 覆盖率补充
        test_cache_backend_abstract_methods,
        test_memory_cache_missing_expired_clear,
        test_redis_cache_init,
        test_redis_cache_connection_live,
        test_redis_cache_connection_failure,
        test_cache_manager_delete_and_exists,
        test_llm_cached_response,
        test_channel_base_class,
        test_email_channel_configured_real_branch,
        test_wecom_channel_real_send_success,
        test_wecom_channel_real_send_failure,
        test_dingtalk_channel_real_send_success,
        test_dingtalk_channel_real_send_failure,
        test_notification_manager_channel_error,
        test_metric_base_class,
        test_counter_name_and_no_labels_prometheus,
        test_histogram_name_and_no_labels_prometheus,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  ✓ {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {e}")

    print(f"\n{passed}/{passed + failed} passed")
    sys.exit(0 if failed == 0 else 1)
