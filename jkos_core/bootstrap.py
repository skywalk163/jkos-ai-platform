"""DSH 组件装配 - 数据层 / 审计 / LLM 路由 / 认证一站式构建（M0-M3）

usage：build_components() 一次拿到全套组件；
0.2 工作流引擎将以 AppComponents 为唯一依赖入口。

M3 新增：
  - LLM 供应商：DeepSeek、OpenAI
  - 通知渠道：邮件、企业微信、钉钉
  - 缓存层：Redis、内存缓存
  - 监控指标：Prometheus 指标收集
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from jkos_core.audit import AuditLogger
from jkos_core.auth.dependencies import AuthConfig, JWTManager, configure_auth
from jkos_core.cache.manager import CacheManager, MemoryCache
from jkos_core.db import ApprovalTaskRepo, Database, DatabaseConfig, LlmUsageRepo, TenantRepo, WorkflowRepo
from jkos_core.llm import LLMRouter, build_llm_router
from jkos_core.mcp.registry import ToolRegistry, get_registry
from jkos_core.mcp.registry import ToolRegistry, get_registry
from jkos_core.metrics.collector import DSHMetrics, get_metrics
from jkos_core.notify.channels import NotificationManager, EmailChannel, WeComChannel, DingTalkChannel

logger = logging.getLogger("dsh.bootstrap")


@dataclass
class AppComponents:
    """M0-M3 基础层组件集合"""
    db: Database
    tenants: TenantRepo
    workflows: WorkflowRepo
    audit: AuditLogger
    llm: LLMRouter
    jwt: JWTManager
    approvals: Optional[ApprovalTaskRepo] = None  # M1 审批仓储
    notify: Optional[NotificationManager] = None  # M3 通知管理器
    cache: Optional[CacheManager] = None  # M3 缓存管理器
    metrics: Optional[DSHMetrics] = None  # M3 指标收集器
    tools: Optional[ToolRegistry] = None  # M13 工具注册表
    tools: Optional[ToolRegistry] = None  # M13 工具注册表

    def close(self) -> None:
        self.db.close()


def build_components(db_path: Optional[str] = None, init_auth: bool = True) -> AppComponents:
    """构建并初始化全部基础组件（迁移自动执行，幂等）"""
    config = DatabaseConfig.from_env()
    if db_path:
        config.path = db_path
    db = Database(config).connect()
    applied = db.migrate()
    if applied:
        logger.info("数据层迁移完成: v%d（应用 %s）", db.version(), applied)

    audit = AuditLogger(db)
    usage_repo = LlmUsageRepo(db)

    def record_usage(result, meta):  # LLM 计量 → llm_usage 表（§8.5.4）
        m = meta or {}
        usage_repo.record(
            tenant_id=m.get("tenant_id") or "system",
            provider=result.provider,
            model=result.model,
            purpose=m.get("purpose"),
            ref_type=m.get("ref_type"),
            ref_id=m.get("ref_id"),
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            duration_ms=result.duration_ms,
            status="simulated" if result.simulated else "ok",
        )

    llm = build_llm_router(usage_recorder=record_usage)
    jwt_manager = JWTManager(AuthConfig.from_env())
    if init_auth:
        configure_auth(jwt_manager)

    # M3: 通知管理器
    notify = NotificationManager()
    notify.register(EmailChannel())
    notify.register(WeComChannel())
    notify.register(DingTalkChannel())

    # M3: 缓存管理器（默认内存缓存）
    cache = CacheManager()

    # M3: 指标收集器
    metrics = get_metrics()

    # M13: 工具注册表（全局单例，供 MCPServer 与工具路由共享）
    tools = get_registry()

    return AppComponents(
        db=db, tenants=TenantRepo(db), workflows=WorkflowRepo(db),
        audit=audit, llm=llm, jwt=jwt_manager,
        approvals=ApprovalTaskRepo(db),
        notify=notify,
        cache=cache,
        metrics=metrics,
        tools=tools,
    )
