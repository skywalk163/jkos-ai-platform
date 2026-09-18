"""DSH API 模块

M8 可观测性新增：
- 安全中间件 (jkos_core.api.security): CORS / CSRF / 安全头
- 监控指标 (jkos_core.api.metrics): /metrics 端点 + 健康检查
"""

from jkos_core.api.metrics import (
    HealthCheckResponse,
    HealthChecker,
    MetricsMiddleware,
    create_health_router,
    create_metrics_router,
    register_default_metrics,
)
from jkos_core.api.routes import create_app, create_api_router
from jkos_core.api.security import (
    CSRFMiddleware,
    SecurityConfig,
    SecurityHeadersMiddleware,
    get_cors_middleware_config,
    get_cors_origins,
)
from jkos_core.api.tool_routes import create_tool_router, register_tool_routes

__all__ = [
    "create_app",
    "create_api_router",
    # 工具
    "create_tool_router",
    "register_tool_routes",
    # 安全
    "CSRFMiddleware",
    "SecurityConfig",
    "SecurityHeadersMiddleware",
    "get_cors_middleware_config",
    "get_cors_origins",
    # 监控
    "HealthCheckResponse",
    "HealthChecker",
    "MetricsMiddleware",
    "create_health_router",
    "create_metrics_router",
    "register_default_metrics",
]
