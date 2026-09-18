"""DSH 监控 - Prometheus 指标端点 + 健康检查

M8 可观测性任务 8.6：
- /metrics: Prometheus 格式指标暴露
- /healthz: 健康检查（db/redis/cache 连通性）
- 新增指标: dsh_requests_total, dsh_request_duration_seconds, dsh_db_connections_active, dsh_cache_hit_rate, dsh_approval_pending, dsh_workflow_queue_depth
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from jkos_core.metrics.collector import DSHMetrics, get_metrics


# ─── 健康检查模型 ───

class HealthCheckResponse(BaseModel):
    """健康检查响应"""
    status: str = Field(..., description="健康状态: ok|degraded|down")
    service: str = Field(default="dsh-api")
    version: str = Field(default="0.1.0")
    checks: Dict[str, str] = Field(default_factory=dict, description="各组件状态")
    timestamp: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ"))


# ─── 健康检查器 ───

class HealthChecker:
    """健康检查器

    检查各组件连通性：
    - 数据库 (SQLite)
    - 缓存 (Redis, 可选)
    - 工作流引擎 (可选)
    """

    def __init__(self, db=None, cache=None, engine=None):
        self._db = db
        self._cache = cache
        self._engine = engine

    def check_database(self) -> tuple[str, str]:
        """检查数据库连通性"""
        if self._db is None:
            return "database", "skipped"
        try:
            # 简单查询测试
            self._db.execute("SELECT 1")
            return "database", "ok"
        except Exception as e:
            return "database", f"down: {e}"

    def check_cache(self) -> tuple[str, str]:
        """检查缓存连通性"""
        if self._cache is None:
            return "cache", "skipped"
        try:
            self._cache.ping()
            return "cache", "ok"
        except Exception as e:
            return "cache", f"down: {e}"

    def check_engine(self) -> tuple[str, str]:
        """检查工作流引擎"""
        if self._engine is None:
            return "engine", "skipped"
        try:
            # 检查引擎是否可用
            if self._engine.is_available():
                return "engine", "ok"
            return "engine", "degraded"
        except Exception as e:
            return "engine", f"down: {e}"

    def check(self) -> HealthCheckResponse:
        """执行所有健康检查"""
        checks: Dict[str, str] = {}

        # 数据库检查
        status, msg = self.check_database()
        checks[status] = msg

        # 缓存检查
        status, msg = self.check_cache()
        checks[status] = msg

        # 引擎检查
        status, msg = self.check_engine()
        checks[status] = msg

        # 确定整体状态
        if any("down" in v for v in checks.values()):
            overall = "down"
        elif any("degraded" in v for v in checks.values()):
            overall = "degraded"
        else:
            overall = "ok"

        return HealthCheckResponse(
            status=overall,
            checks=checks,
        )


# ─── 指标中间件 ───

class MetricsMiddleware:
    """请求指标中间件

    自动记录：
    - 请求总数 (dsh_requests_total)
    - 请求耗时 (dsh_request_duration_seconds)
    """

    def __init__(self, metrics: Optional[DSHMetrics] = None):
        self._metrics = metrics or get_metrics()

    async def before_request(self, request: Any):
        """请求前记录开始时间"""
        request._start_time = time.monotonic()

    async def after_request(self, request: Any, response: Any):
        """请求后记录指标"""
        if not hasattr(request, "_start_time"):
            return

        duration = time.monotonic() - request._start_time

        # 记录请求总数
        method = getattr(request, "method", "unknown")
        path = getattr(request, "url", {}).get("path", "unknown") if hasattr(request, "url") else "unknown"
        status_code = getattr(response, "status_code", 200) if hasattr(response, "status_code") else 200

        self._metrics.workflow_executions.inc(
            labels={
                "method": method,
                "path": path,
                "status": str(status_code),
            }
        )

        # 记录请求耗时
        self._metrics.workflow_duration.observe(
            duration,
            labels={"path": path},
        )


# ─── 指标端点 ───

def create_metrics_router(metrics: Optional[DSHMetrics] = None) -> APIRouter:
    """创建指标路由"""
    router = APIRouter(prefix="/metrics", tags=["Metrics"])
    _metrics = metrics or get_metrics()

    @router.get("", summary="Prometheus 指标端点")
    async def get_metrics():
        """Prometheus 格式指标暴露"""
        from fastapi.responses import PlainTextResponse

        prometheus_text = _metrics.collector.to_prometheus_format()
        return PlainTextResponse(
            content=prometheus_text,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return router


# ─── 健康检查端点 ───

def create_health_router(
    db=None,
    cache=None,
    engine=None,
) -> APIRouter:
    """创建健康检查路由"""
    router = APIRouter(prefix="/healthz", tags=["Health"])
    checker = HealthChecker(db=db, cache=cache, engine=engine)

    @router.get("", response_model=HealthCheckResponse, summary="健康检查")
    async def health_check():
        """健康检查端点：检查各组件连通性"""
        return checker.check()

    @router.get("/ready", response_model=HealthCheckResponse, summary="就绪检查")
    async def ready_check():
        """就绪检查：确认服务可接收请求"""
        return checker.check()

    @router.get("/live", response_model=HealthCheckResponse, summary="存活检查")
    async def live_check():
        """存活检查：确认服务进程存活"""
        return HealthCheckResponse(status="ok")

    return router


# ─── 预定义指标注册 ───

def register_default_metrics(metrics: Optional[DSHMetrics] = None) -> DSHMetrics:
    """注册预定义指标（M8 新增）"""
    _metrics = metrics or get_metrics()
    collector = _metrics.collector

    # 请求指标
    collector.register_counter(
        "dsh_requests_total",
        "请求总数",
        ["method", "endpoint", "status"],
    )
    collector.register_histogram(
        "dsh_request_duration_seconds",
        "请求耗时（秒）",
        ["endpoint"],
    )

    # 数据库连接指标
    collector.register_counter(
        "dsh_db_connections_active",
        "活跃数据库连接数",
        ["tenant"],
    )

    # 缓存命中率
    collector.register_counter(
        "dsh_cache_hits_total",
        "缓存命中总数",
        ["cache_layer"],
    )
    collector.register_counter(
        "dsh_cache_misses_total",
        "缓存未命中总数",
        ["cache_layer"],
    )

    # 审批待处理
    collector.register_counter(
        "dsh_approval_pending",
        "待审批任务数",
        ["tenant", "risk"],
    )

    # 工作流队列深度
    collector.register_counter(
        "dsh_workflow_queue_depth",
        "工作流队列深度",
        ["workflow_code"],
    )

    return _metrics
