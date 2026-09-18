"""M8 任务 8.4/8.6：安全配置 + 监控告警 — 速率限制 / CSRF / 安全头 / 健康检查 / 指标

测试覆盖：
- 速率限制器（令牌桶算法）
- 多租户速率限制
- CSRF Token 生成与校验
- 安全头中间件
- 健康检查
- Prometheus 指标端点
"""

import time
import pytest
from fastapi import HTTPException, Request, Response
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Route

from dsh_core.utils import RateLimiter, MultiTenantRateLimiter, rate_limit
from dsh_core.api import (
    CSRFMiddleware,
    SecurityConfig,
    SecurityHeadersMiddleware,
    HealthChecker,
    HealthCheckResponse,
    create_metrics_router,
    create_health_router,
    register_default_metrics,
    get_cors_middleware_config,
    get_cors_origins,
)
from dsh_core.metrics.collector import get_metrics


# ─── 速率限制器测试 ───


class TestRateLimiter:
    """令牌桶速率限制器测试"""

    def test_acquire_within_limit(self):
        """在限制范围内获取令牌"""
        limiter = RateLimiter(rps=10, burst=20)
        for _ in range(10):
            assert limiter.acquire()

    def test_acquire_beyond_burst(self):
        """超过突发容量后拒绝"""
        limiter = RateLimiter(rps=1, burst=2)
        assert limiter.acquire()  # 1
        assert limiter.acquire()  # 2
        assert not limiter.acquire()  # 桶空，拒绝

    def test_refill_over_time(self):
        """时间流逝后令牌补充"""
        limiter = RateLimiter(rps=1, burst=1)
        assert limiter.acquire()
        assert not limiter.acquire()
        time.sleep(1.1)  # 等待令牌补充
        assert limiter.acquire()

    def test_disabled_limiter(self):
        """禁用的限流器始终放行"""
        limiter = RateLimiter(rps=1000, burst=1000)  # 高限制 = 等效禁用
        assert limiter.acquire()
        assert limiter.acquire()

    def test_remaining_tokens(self):
        """剩余令牌数"""
        limiter = RateLimiter(rps=10, burst=5)
        assert limiter.remaining == 5.0
        limiter.acquire(2)
        assert limiter.remaining == 3.0


class TestMultiTenantRateLimiter:
    """多租户速率限制器测试"""

    def test_isolate_tenants(self):
        """租户间限流隔离"""
        limiter = MultiTenantRateLimiter(rps=1, burst=1)
        assert limiter.acquire_by_tenant("tenant1")
        assert not limiter.acquire_by_tenant("tenant1")
        assert limiter.acquire_by_tenant("tenant2")  # 不同租户，不受影响

    def test_isolate_users(self):
        """用户间限流隔离"""
        limiter = MultiTenantRateLimiter(rps=1, burst=1)
        assert limiter.acquire_by_user("user1")
        assert not limiter.acquire_by_user("user1")
        assert limiter.acquire_by_user("user2")

    def test_isolate_ips(self):
        """IP 间限流隔离"""
        limiter = MultiTenantRateLimiter(rps=1, burst=1)
        assert limiter.acquire_by_ip("192.168.1.1")
        assert not limiter.acquire_by_ip("192.168.1.1")
        assert limiter.acquire_by_ip("192.168.1.2")


# ─── CSRF 中间件测试 ───


class TestCSRFMiddleware:
    """CSRF 保护测试"""

    def _create_test_app(self, secret="test-secret-key"):
        """创建测试 FastAPI 应用"""
        app = Starlette(routes=[
            Route("/test", endpoint=lambda r: Response("ok"), methods=["POST"]),
            Route("/get", endpoint=lambda r: Response("ok"), methods=["GET"]),
        ])
        app.add_middleware(CSRFMiddleware, secret=secret)
        return app

    def test_get_request_sets_cookie(self):
        """GET 请求设置 CSRF Cookie"""
        app = self._create_test_app()
        client = TestClient(app)
        response = client.get("/get")
        assert "csrf_token" in response.cookies
        assert "X-CSRF-Token" in response.headers

    def test_post_without_token_fails(self):
        """POST 请求无 Token 时拒绝"""
        app = self._create_test_app()
        client = TestClient(app)
        response = client.post("/test")
        assert response.status_code == 403

    def test_post_with_valid_token_succeeds(self):
        """POST 请求带有效 Token 时通过"""
        app = self._create_test_app()
        client = TestClient(app)
        # 先 GET 获取 token
        get_resp = client.get("/get")
        token = get_resp.headers.get("X-CSRF-Token", "")
        # 用 token POST
        post_resp = client.post("/test", headers={"X-CSRF-Token": token})
        assert post_resp.status_code == 200

    def test_post_with_invalid_token_fails(self):
        """POST 请求带无效 Token 时拒绝"""
        app = self._create_test_app()
        client = TestClient(app)
        response = client.post("/test", headers={"X-CSRF-Token": "invalid-token"})
        assert response.status_code == 403

    def test_token_expiry(self):
        """过期 Token 被拒绝"""
        app = self._create_test_app()
        client = TestClient(app)
        get_resp = client.get("/get")
        token = get_resp.headers.get("X-CSRF-Token", "")
        # 模拟过期（修改 token 中的时间戳）
        parts = token.split(":")
        if len(parts) == 3:
            expired_token = f"{parts[0]}:0:{parts[2]}"
            response = client.post("/test", headers={"X-CSRF-Token": expired_token})
            assert response.status_code == 403


# ─── 安全头中间件测试 ───


class TestSecurityHeadersMiddleware:
    """安全头中间件测试"""

    def _create_test_app(self):
        app = Starlette(routes=[
            Route("/test", endpoint=lambda r: Response("ok")),
        ])
        app.add_middleware(SecurityHeadersMiddleware)
        return app

    def test_security_headers_present(self):
        """安全头存在"""
        app = self._create_test_app()
        client = TestClient(app)
        response = client.get("/test")
        assert "Strict-Transport-Security" in response.headers
        assert "X-Content-Type-Options" in response.headers
        assert "X-Frame-Options" in response.headers
        assert "Content-Security-Policy" in response.headers
        assert "Referrer-Policy" in response.headers

    def test_hsts_header(self):
        """HSTS 头内容"""
        app = self._create_test_app()
        client = TestClient(app)
        response = client.get("/test")
        hsts = response.headers["Strict-Transport-Security"]
        assert "max-age=31536000" in hsts
        assert "includeSubDomains" in hsts

    def test_x_frame_options(self):
        """X-Frame-Options 头内容"""
        app = self._create_test_app()
        client = TestClient(app)
        response = client.get("/test")
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_x_content_type_options(self):
        """X-Content-Type-Options 头内容"""
        app = self._create_test_app()
        client = TestClient(app)
        response = client.get("/test")
        assert response.headers["X-Content-Type-Options"] == "nosniff"


# ─── 健康检查测试 ───


class TestHealthChecker:
    """健康检查器测试"""

    def test_check_without_components(self):
        """无组件时返回 skipped"""
        checker = HealthChecker()
        result = checker.check()
        assert result.status == "ok"
        assert "database" in result.checks
        assert result.checks["database"] == "skipped"

    def test_check_with_mock_db(self):
        """模拟数据库检查"""
        class MockDB:
            def execute(self, query):
                pass

        checker = HealthChecker(db=MockDB())
        result = checker.check()
        assert result.checks["database"] == "ok"

    def test_check_with_failing_db(self):
        """失败的数据库检查"""
        class FailingDB:
            def execute(self, query):
                raise Exception("Connection failed")

        checker = HealthChecker(db=FailingDB())
        result = checker.check()
        assert result.checks["database"].startswith("down:")
        assert result.status == "down"

    def test_health_check_response_model(self):
        """健康检查响应模型"""
        response = HealthCheckResponse(
            status="ok",
            checks={"database": "ok"},
        )
        assert response.status == "ok"
        assert response.checks == {"database": "ok"}


class TestHealthRouter:
    """健康检查路由测试"""

    def test_health_endpoint(self):
        """健康检查端点"""
        from fastapi import FastAPI
        router = create_health_router()
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get("/healthz")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "checks" in data

    def test_ready_endpoint(self):
        """就绪检查端点"""
        from fastapi import FastAPI
        router = create_health_router()
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get("/healthz/ready")
        assert response.status_code == 200

    def test_live_endpoint(self):
        """存活检查端点"""
        from fastapi import FastAPI
        router = create_health_router()
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get("/healthz/live")
        assert response.status_code == 200


# ─── Prometheus 指标测试 ───


class TestMetricsEndpoint:
    """Prometheus 指标端点测试"""

    def test_metrics_endpoint(self):
        """指标端点返回 Prometheus 格式"""
        from fastapi import FastAPI
        from dsh_core.metrics.collector import get_metrics as get_global_metrics
        router = create_metrics_router(metrics=get_global_metrics())
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

    def test_metrics_content(self):
        """指标内容包含预定义指标"""
        from fastapi import FastAPI
        from dsh_core.metrics.collector import get_metrics as get_global_metrics
        router = create_metrics_router(metrics=get_global_metrics())
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get("/metrics")
        text = response.text
        # 检查预定义指标
        assert "dsh_workflow_executions_total" in text
        assert "dsh_workflow_duration_seconds" in text
        assert "dsh_llm_calls_total" in text
        assert "dsh_llm_tokens_total" in text

    def test_register_default_metrics(self):
        """注册默认指标"""
        metrics = register_default_metrics()
        assert metrics.collector.get_counter("dsh_requests_total") is not None
        assert metrics.collector.get_histogram("dsh_request_duration_seconds") is not None

    def test_metrics_counter_inc(self):
        """计数器递增"""
        metrics = get_metrics()
        before = metrics.llm_calls.value(labels={"provider": "deepseek", "model": "deepseek-chat"})
        metrics.llm_calls.inc(labels={"provider": "deepseek", "model": "deepseek-chat"})
        after = metrics.llm_calls.value(labels={"provider": "deepseek", "model": "deepseek-chat"})
        assert after == before + 1.0

    def test_metrics_histogram_observe(self):
        """直方图观测"""
        metrics = get_metrics()
        metrics.workflow_duration.observe(0.5, labels={"workflow_code": "test"})
        assert metrics.workflow_duration.value(labels={"workflow_code": "test"}) == 0.5


# ─── CORS 配置测试 ───


class TestCORSConfig:
    """CORS 配置测试"""

    def test_get_cors_origins_default(self):
        """默认 CORS 源"""
        origins = get_cors_origins()
        assert len(origins) > 0
        assert "http://localhost:3000" in origins

    def test_get_cors_middleware_config(self):
        """CORS 中间件配置"""
        config = get_cors_middleware_config()
        assert "allow_origins" in config
        assert "allow_credentials" in config
        assert config["allow_credentials"] is True


# ─── 安全配置测试 ───


class TestSecurityConfig:
    """安全配置测试"""

    def test_default_config(self):
        """默认安全配置"""
        config = SecurityConfig()
        assert len(config.cors_origins) > 0
        assert config.csrf_expiry == 3600
        assert config.rate_limit_rps == 10.0
        assert config.rate_limit_burst == 20

    def test_from_env(self):
        """从环境变量创建配置"""
        import os
        os.environ["DSH_CORS_ORIGINS"] = "http://example.com,http://test.com"
        os.environ["DSH_CSRF_SECRET"] = "test-secret"
        os.environ["DSH_RATE_LIMIT_RPS"] = "5"
        os.environ["DSH_RATE_LIMIT_BURST"] = "10"

        config = SecurityConfig.from_env()
        assert "http://example.com" in config.cors_origins
        assert "http://test.com" in config.cors_origins
        assert config.csrf_secret == "test-secret"
        assert config.rate_limit_rps == 5.0
        assert config.rate_limit_burst == 10

        # 清理
        del os.environ["DSH_CORS_ORIGINS"]
        del os.environ["DSH_CSRF_SECRET"]
        del os.environ["DSH_RATE_LIMIT_RPS"]
        del os.environ["DSH_RATE_LIMIT_BURST"]
