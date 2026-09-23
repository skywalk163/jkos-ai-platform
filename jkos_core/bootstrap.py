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
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional

from jkos_core.audit import AuditLogger
from jkos_core.auth.dependencies import AuthConfig, JWTManager, configure_auth
from jkos_core.cache.manager import CacheManager, MemoryCache
from jkos_core.db import ApprovalTaskRepo, Database, DatabaseConfig, LlmUsageRepo, ResourceRepo, TenantRepo, WorkflowRepo
from jkos_core.db.tenant_schema import (
    IsolationLevel,
    TenantIsolationConfig,
    TenantSchemaManager,
    create_tenant_schema_manager,
)
from jkos_core.llm import LLMRouter, build_llm_router
from jkos_core.mcp.registry import ToolRegistry, get_registry
from jkos_core.metrics.collector import DSHMetrics, get_metrics
from jkos_core.notify.channels import NotificationManager, EmailChannel, WeComChannel, DingTalkChannel

if TYPE_CHECKING:  # 仅类型标注：避免启动即耦合 harness 模块
    from jkos_core.harness.gateway import HarnessGateway

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
    resources: Optional[ResourceRepo] = None  # V3 资源配置仓储
    notify: Optional[NotificationManager] = None  # M3 通知管理器
    cache: Optional[CacheManager] = None  # M3 缓存管理器
    metrics: Optional[DSHMetrics] = None  # M3 指标收集器
    tools: Optional[ToolRegistry] = None  # M13 工具注册表
    harness: Optional["HarnessGateway"] = None  # M16 智能中枢网关（默认关闭）
    tenant_schemas: Optional[TenantSchemaManager] = None  # M18.2 L2 物理隔离（默认关闭）
    _engines: Dict[str, Any] = field(default_factory=dict, repr=False)  # 租户码 -> 租户专属引擎

    def close(self) -> None:
        if self.harness is not None:
            self.harness.close()
        if self.tenant_schemas is not None:
            self.tenant_schemas.close_all()
        self._engines.clear()
        self.db.close()

    # ─── M18.2 按租户路由 ───

    def database_for(self, tenant_code: str) -> Database:
        """按租户取数据库：L2 已隔离 → 独立 Database；否则 → 主库"""
        if self.tenant_schemas is None:
            return self.db
        config = self.tenant_schemas.get_config_by_code(tenant_code)
        if config is None:
            return self.db
        return self.tenant_schemas.get_tenant_database(config.tenant_id) or self.db

    def engine_for(self, tenant_code: str) -> Optional["WorkflowEngine"]:
        """按租户取工作流引擎（M18.2 运行时路由）

        未启用 L2 隔离（或该租户未隔离）时返回 None，调用方回落单引擎；
        启用时按租户懒建引擎（独立 Database + 仓储，共享 LLM/通知/JWT），
        并按租户码缓存复用。
        """
        if self.tenant_schemas is None:
            return None
        db = self.database_for(tenant_code)
        if db is self.db:
            return None
        engine = self._engines.get(tenant_code)
        if engine is None:
            from types import SimpleNamespace

            from jkos_core.workflow import WorkflowEngine

            tenant_comps = SimpleNamespace(
                db=db,
                tenants=TenantRepo(db),
                workflows=WorkflowRepo(db),
                approvals=ApprovalTaskRepo(db),
                resources=ResourceRepo(db),
                audit=AuditLogger(db),
                llm=self.llm,
                jwt=self.jwt,
                notify=self.notify,
            )
            engine = WorkflowEngine(tenant_comps)
            register_tenant_workflows(engine)
            self._engines[tenant_code] = engine
            logger.info("租户 %s 专属引擎已建立（独立库）", tenant_code)
        return engine


def register_tenant_workflows(engine: Any) -> None:
    """注册三案例工作流定义（幂等）

    - dev：需显式注册（`tenants.dev.workflows.register`）；
    - media / winery：由 `jkos_core.workflow.nodes` 模块级惰性挂载，无需重复注册。
    """
    try:
        from tenants.dev.workflows import register as register_dev_workflows
        register_dev_workflows(engine)
        logger.info("✓ dev 工作流定义已注册（D1 代码审查）")
    except ImportError as e:
        logger.warning("dev 工作流未注册: %s", e)


def wire_tenant_isolation(db: Database, manager: TenantSchemaManager,
                          config: TenantIsolationConfig) -> int:
    """按租户表/白名单建立 L2 独立库，返回成功接入的租户数

    单个租户建立失败即降级 L1（注销注册项并记错误日志），不阻塞启动。
    """
    wired = 0
    for row in TenantRepo(db).list_all():
        code = row["code"]
        if config.codes:
            is_l2 = code in config.codes
        else:
            is_l2 = (row.get("isolation_level") or "").upper() == IsolationLevel.L2.value
        if not is_l2:
            continue
        try:
            manager.register_tenant(row["id"], code, IsolationLevel.L2)
            manager.create_schema(row["id"])
            wired += 1
            logger.info("租户 %s 已接入 L2 物理隔离", code)
        except Exception as exc:
            logger.error("租户 %s L2 隔离建立失败，降级 L1: %s", code, exc)
            manager.unregister_tenant(row["id"])
    return wired


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

    # M16: 智能中枢（harness sidecar）—— 默认关闭（JKOS_HARNESS_ENABLED）
    harness = None
    from jkos_core.harness.config import HarnessConfig
    from jkos_core.harness.gateway import get_gateway

    harness_config = HarnessConfig.from_env()
    if harness_config.enabled:
        harness = get_gateway(harness_config)
        logger.info("智能中枢已启用：%s", harness_config.describe())

    # M18.2: 多租户 L2 物理隔离（默认关闭，由 JKOS_TENANT_L2_ENABLED 控制）
    isolation_config = TenantIsolationConfig.from_env()
    tenant_schemas = None
    if isolation_config.enabled:
        data_dir = isolation_config.data_dir or os.path.join(
            os.path.dirname(os.path.abspath(config.path)), "tenants")
        tenant_schemas = create_tenant_schema_manager(
            f"sqlite:///{config.path}", data_dir=data_dir)
        wired = wire_tenant_isolation(db, tenant_schemas, isolation_config)
        logger.info("多租户隔离已启用：%s（已接入 %d 个租户）",
                    isolation_config.describe(), wired)

    return AppComponents(
        db=db, tenants=TenantRepo(db), workflows=WorkflowRepo(db),
        audit=audit, llm=llm, jwt=jwt_manager,
        approvals=ApprovalTaskRepo(db),
        resources=ResourceRepo(db),
        notify=notify,
        cache=cache,
        metrics=metrics,
        tools=tools,
        harness=harness,
        tenant_schemas=tenant_schemas,
    )
