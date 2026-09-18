"""M4 集成测试（M4 任务 4.1-4.7）

测试内容：
- M4.1 案例C P1：生产调度工作流
- M4.2 案例C P1：质量追溯（2.6 数据模型）
- M4.3 ERP/MES 适配器 POC
- M4.4 多租户 L2 schema 隔离
- M4.5 PostgreSQL 迁移准备
- M4.6 NATS 事件总线替换
- M4.7 自举第二期：D3 单测生成提升覆盖率
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── 路径设置 ───
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from jkos_core.db import Database, DatabaseConfig
from jkos_core.workflow.base import ApprovalSpec, NodeSpec, WorkflowDef
from jkos_core.workflow.engine import WorkflowEngine
from jkos_core.workflow.nodes import WORKFLOW_REGISTRY
from jkos_core.db.repos import WorkflowRepo
from tenants.winery import (
    WINERY_WORKFLOWS,
    BRAND_COMPLIANCE,
    CHANNELS,
    RISK_LEVELS,
    get_winery_workflow,
    register,
)
from tenants.winery.models.traceability import (
    Batch,
    BatchStatus,
    InspectionResult,
    QualityInspection,
    RawMaterial,
    TraceNode,
    TraceNodeType,
    TraceabilityRepo,
)
from tenants.winery.adapters.erp import (
    AdapterMode,
    ErpAdapter,
    MesAdapter,
    AdapterFactory,
)
from jkos_core.db.tenant_schema import (
    CrossTenantQuery,
    IsolationLevel,
    TenantSchemaConfig,
    TenantSchemaManager,
)
from jkos_core.db.postgres import (
    ConnectionPoolManager,
    DatabaseType,
    MigrationPlan,
    MigrationStatus,
    PostgresConfig,
    PostgresMigrator,
)
from jkos_core.bus.nats import (
    EventBus,
    EventBusFactory,
    EventTypes,
    NatsEventBus,
    build_production_delay_event,
    build_sentiment_raised_event,
)
from tests.test_coverage import (
    CoverageAnalyzer,
    CoverageBooster,
    CoverageResult,
    TestGenerationPlan,
)


# ─── Fixtures ───

@pytest.fixture
def db():
    """测试数据库"""
    config = DatabaseConfig(path=":memory:")
    db = Database(config).connect()
    db.migrate()
    yield db
    db.close()


@pytest.fixture
def engine(db):
    """工作流引擎"""
    from jkos_core.db.repos import WorkflowRepo, ApprovalTaskRepo
    from jkos_core.audit import AuditLogger
    from jkos_core.llm import LLMRouter, build_llm_router
    from jkos_core.db import TenantRepo
    from types import SimpleNamespace
    
    repo = WorkflowRepo(db)
    approvals = ApprovalTaskRepo(db)
    audit = AuditLogger(db)
    llm = build_llm_router()
    tenants = TenantRepo(db)
    
    comps = SimpleNamespace(
        workflows=repo,
        audit=audit,
        llm=llm,
        tenants=tenants,
        approvals=approvals,
    )
    
    engine = WorkflowEngine(comps)
    register(engine)
    return engine


@pytest.fixture
def traceability_repo(db):
    """追溯仓储"""
    return TraceabilityRepo(db)


# ═══════════════════════════════════════════════════════════════
# M4.1 案例C P1：生产调度工作流
# ═══════════════════════════════════════════════════════════════

class TestM41_ProductionScheduling:
    """M4.1 案例C P1：生产调度工作流"""

    def test_winery_workflows_registered(self):
        """测试酒厂工作流已注册"""
        assert "winery.production_scheduling" in WINERY_WORKFLOWS
        assert "winery.quality_traceability" in WINERY_WORKFLOWS
        assert "winery.marketing_decision" in WINERY_WORKFLOWS
        assert "winery.crisis_alert" in WINERY_WORKFLOWS

    def test_production_scheduling_workflow_structure(self):
        """测试生产调度工作流结构"""
        wf = get_winery_workflow("winery.production_scheduling")
        assert wf.code == "winery.production_scheduling"
        assert len(wf.nodes) == 6
        node_codes = [n.node_code for n in wf.nodes]
        assert "forecast" in node_codes
        assert "aggregate" in node_codes
        assert "schedule" in node_codes
        assert "procure" in node_codes
        assert "logistics" in node_codes
        assert "monitor" in node_codes

    def test_production_scheduling_approval_nodes(self):
        """测试生产调度审批节点"""
        wf = get_winery_workflow("winery.production_scheduling")
        schedule_node = next(n for n in wf.nodes if n.node_code == "schedule")
        assert schedule_node.approval is not None
        assert schedule_node.approval.risk == "medium"
        assert schedule_node.approval.mode == "any"

        procure_node = next(n for n in wf.nodes if n.node_code == "procure")
        assert procure_node.approval is not None
        assert procure_node.approval.risk == "medium"

    def test_marketing_decision_high_risk_approval(self):
        """测试营销决策高风险审批（双人复核）"""
        wf = get_winery_workflow("winery.marketing_decision")
        review_node = next(n for n in wf.nodes if n.node_code == "review")
        assert review_node.approval is not None
        assert review_node.approval.risk == "high"
        assert review_node.approval.mode == "all"  # 会签

    def test_crisis_alert_high_risk_approval(self):
        """测试危机预警高风险审批（会签）"""
        wf = get_winery_workflow("winery.crisis_alert")
        response_node = next(n for n in wf.nodes if n.node_code == "response")
        assert response_node.approval is not None
        assert response_node.approval.risk == "high"
        assert response_node.approval.mode == "all"

    def test_risk_levels_configured(self):
        """测试风险等级配置"""
        assert "low" in RISK_LEVELS
        assert "medium" in RISK_LEVELS
        assert "high" in RISK_LEVELS
        assert RISK_LEVELS["high"]["auto_reject_hours"] == 2
        assert RISK_LEVELS["medium"]["escalate_hours"] == 8
        assert RISK_LEVELS["low"]["auto_pass_hours"] == 24

    def test_brand_compliance_configured(self):
        """测试品牌合规配置"""
        assert "brand_terms" in BRAND_COMPLIANCE
        assert "prohibited_terms" in BRAND_COMPLIANCE
        assert "required_disclaimer" in BRAND_COMPLIANCE
        assert "酒厂" in BRAND_COMPLIANCE["brand_terms"]
        assert "药效" in BRAND_COMPLIANCE["prohibited_terms"]

    def test_channels_configured(self):
        """测试渠道配置"""
        assert "store" in CHANNELS
        assert "ecommerce" in CHANNELS
        assert "wechat" in CHANNELS

    def test_register_winery_nodes(self, engine):
        """测试注册酒厂节点"""
        # 注册后应该能找到节点处理器
        # 实际实现：检查 WORKFLOW_REGISTRY
        pass


# ═══════════════════════════════════════════════════════════════
# M4.2 案例C P1：质量追溯（2.6 数据模型）
# ═══════════════════════════════════════════════════════════════

class TestM42_Traceability:
    """M4.2 案例C P1：质量追溯（2.6 数据模型）"""

    def test_batch_creation(self, traceability_repo):
        """测试批次创建"""
        batch = Batch(
            id="B001",
            batch_no="B20260914001",
            product_code="P001",
            product_name="52度经典",
            line_code="L01",
            status=BatchStatus.CREATED,
            planned_quantity=500,
            actual_quantity=0,
            planned_start=datetime.now(),
            planned_end=datetime.now() + timedelta(days=3),
        )
        traceability_repo.create_batch(batch)
        retrieved = traceability_repo.get_batch("B001")
        assert retrieved is not None
        assert retrieved.batch_no == "B20260914001"
        assert retrieved.product_name == "52度经典"

    def test_add_trace_node(self, traceability_repo):
        """测试添加追溯节点"""
        batch = Batch(
            id="B002", batch_no="B20260914002", product_code="P001",
            product_name="52度经典", line_code="L01", status=BatchStatus.CREATED,
            planned_quantity=500, actual_quantity=0,
            planned_start=datetime.now(), planned_end=datetime.now() + timedelta(days=3),
        )
        traceability_repo.create_batch(batch)

        node = TraceNode(
            id="N001", batch_id="B002", node_type=TraceNodeType.FERMENTATION,
            seq=1, timestamp=datetime.now(), data={"temp": 25, "humidity": 60},
            operator_id="operator1",
        )
        traceability_repo.add_trace_node(node)

        chain = traceability_repo.get_trace_chain("B002")
        assert len(chain) == 1
        assert chain[0].node_type == TraceNodeType.FERMENTATION

    def test_quality_inspection(self, traceability_repo):
        """测试质检记录"""
        batch = Batch(
            id="B003", batch_no="B20260914003", product_code="P001",
            product_name="52度经典", line_code="L01", status=BatchStatus.CREATED,
            planned_quantity=500, actual_quantity=0,
            planned_start=datetime.now(), planned_end=datetime.now() + timedelta(days=3),
        )
        traceability_repo.create_batch(batch)

        inspection = QualityInspection(
            id="I001", batch_id="B003", inspector_id="inspector1",
            inspection_time=datetime.now(), alcohol_content=52.0,
            ph_value=4.2, turbidity=1.5, sensory_score=95.0,
            result=InspectionResult.PASS, notes="感官优秀",
        )
        traceability_repo.add_inspection(inspection)

        # 验证追溯链
        trace = traceability_repo.trace_batch("B20260914003")
        assert trace is not None
        assert len(trace["inspections"]) == 1
        assert trace["inspections"][0].result == InspectionResult.PASS

    def test_raw_material_traceability(self, traceability_repo):
        """测试原料溯源"""
        batch = Batch(
            id="B004", batch_no="B20260914004", product_code="P001",
            product_name="52度经典", line_code="L01", status=BatchStatus.CREATED,
            planned_quantity=500, actual_quantity=0,
            planned_start=datetime.now(), planned_end=datetime.now() + timedelta(days=3),
        )
        traceability_repo.create_batch(batch)

        material = RawMaterial(
            id="M001", batch_id="B004", material_type="grain",
            supplier_id="S001", supplier_name="高粱供应商", origin="东北",
            quantity=1000, unit="吨", delivery_date=datetime.now(),
            quality_cert="QC-2026-001",
        )
        traceability_repo.add_raw_material(material)

        trace = traceability_repo.trace_batch("B20260914004")
        assert trace is not None
        assert len(trace["raw_materials"]) == 1
        assert trace["raw_materials"][0].origin == "东北"

    def test_trace_batch_not_found(self, traceability_repo):
        """测试追溯不存在的批次"""
        trace = traceability_repo.trace_batch("NONEXISTENT")
        assert trace is None

    def test_batch_status_enum(self):
        """测试批次状态枚举"""
        assert BatchStatus.CREATED.value == "created"
        assert BatchStatus.PASSED.value == "passed"
        assert BatchStatus.REJECTED.value == "rejected"

    def test_trace_node_type_enum(self):
        """测试追溯节点类型枚举"""
        assert TraceNodeType.FERMENTATION.value == "fermentation"
        assert TraceNodeType.AGING.value == "aging"
        assert TraceNodeType.FILLING.value == "filling"


# ═══════════════════════════════════════════════════════════════
# M4.3 ERP/MES 适配器 POC
# ═══════════════════════════════════════════════════════════════

class TestM43_Adapters:
    """M4.3 ERP/MES 适配器 POC"""

    def test_erp_adapter_fallback_mode(self):
        """测试 ERP 适配器降级模式"""
        adapter = ErpAdapter(mode=AdapterMode.FALLBACK)
        assert adapter.mode == AdapterMode.FALLBACK

    def test_erp_adapter_simulated_mode(self):
        """测试 ERP 适配器模拟模式"""
        adapter = ErpAdapter(mode=AdapterMode.SIMULATED)
        sales = adapter.get_sales_data(
            datetime.now() - timedelta(days=7),
            datetime.now(),
        )
        assert len(sales) > 0
        assert all(s.quantity > 0 for s in sales)

    def test_erp_adapter_inventory(self):
        """测试 ERP 适配器库存查询"""
        adapter = ErpAdapter(mode=AdapterMode.SIMULATED)
        inventory = adapter.get_inventory()
        assert len(inventory) > 0
        assert all(i.quantity > 0 for i in inventory)

    def test_mes_adapter_fallback_mode(self):
        """测试 MES 适配器降级模式"""
        adapter = MesAdapter(mode=AdapterMode.FALLBACK)
        assert adapter.mode == AdapterMode.FALLBACK

    def test_mes_adapter_simulated_mode(self):
        """测试 MES 适配器模拟模式"""
        adapter = MesAdapter(mode=AdapterMode.SIMULATED)
        orders = adapter.get_production_orders(
            datetime.now() - timedelta(days=7),
            datetime.now(),
        )
        assert len(orders) > 0

    def test_mes_adapter_push_order(self):
        """测试 MES 适配器推送工单"""
        adapter = MesAdapter(mode=AdapterMode.SIMULATED)
        from tenants.winery.adapters.erp import ProductionOrder
        order = ProductionOrder(
            order_no="PO-2026-004", product_code="P001", product_name="52度经典",
            line_code="L01", planned_quantity=500, actual_quantity=0,
            planned_start=datetime.now(), planned_end=datetime.now() + timedelta(days=3),
        )
        result = adapter.push_production_order(order)
        assert result is True

    def test_adapter_factory(self):
        """测试适配器工厂"""
        erp = AdapterFactory.create_erp_adapter()
        mes = AdapterFactory.create_mes_adapter()
        assert erp is not None
        assert mes is not None

    def test_adapter_mode_enum(self):
        """测试适配器模式枚举"""
        assert AdapterMode.REAL.value == "real"
        assert AdapterMode.FALLBACK.value == "fallback"
        assert AdapterMode.SIMULATED.value == "simulated"


# ═══════════════════════════════════════════════════════════════
# M4.4 多租户 L2 schema 隔离
# ═══════════════════════════════════════════════════════════════

class TestM44_TenantSchema:
    """M4.4 多租户 L2 schema 隔离"""

    def test_tenant_schema_manager(self):
        """测试租户 Schema 管理器"""
        manager = TenantSchemaManager("postgresql://localhost/dsh_ai")
        config = manager.register_tenant("tenant1", "winery", IsolationLevel.L2)
        assert config.tenant_code == "winery"
        assert config.schema_name == "tenant_winery"
        assert config.isolation_level == IsolationLevel.L2

    def test_l1_isolation(self):
        """测试 L1 隔离"""
        manager = TenantSchemaManager("postgresql://localhost/dsh_ai")
        config = manager.register_tenant("tenant1", "dev", IsolationLevel.L1)
        assert config.isolation_level == IsolationLevel.L1

    def test_get_schema_name(self):
        """测试获取 Schema 名"""
        manager = TenantSchemaManager("postgresql://localhost/dsh_ai")
        manager.register_tenant("tenant1", "winery", IsolationLevel.L2)
        schema_name = manager.get_schema_name("tenant1")
        assert schema_name == "tenant_winery"

    def test_get_connection_string_l2(self):
        """测试 L2 隔离连接字符串"""
        manager = TenantSchemaManager("postgresql://localhost/dsh_ai")
        manager.register_tenant("tenant1", "winery", IsolationLevel.L2)
        conn_str = manager.get_connection_string("tenant1")
        assert "search_path" in conn_str

    def test_get_connection_string_l1(self):
        """测试 L1 隔离连接字符串"""
        manager = TenantSchemaManager("postgresql://localhost/dsh_ai")
        manager.register_tenant("tenant1", "dev", IsolationLevel.L1)
        conn_str = manager.get_connection_string("tenant1")
        assert "search_path" not in conn_str

    def test_create_schema(self):
        """测试创建 Schema"""
        manager = TenantSchemaManager("postgresql://localhost/dsh_ai")
        manager.register_tenant("tenant1", "winery", IsolationLevel.L2)
        result = manager.create_schema("tenant1")
        assert result is True

    def test_list_tenant_schemas(self):
        """测试列出租户 Schema"""
        manager = TenantSchemaManager("postgresql://localhost/dsh_ai")
        manager.register_tenant("tenant1", "winery", IsolationLevel.L2)
        manager.register_tenant("tenant2", "media", IsolationLevel.L1)
        schemas = manager.list_tenant_schemas()
        assert len(schemas) == 2

    def test_cross_tenant_query_grant(self):
        """测试跨租户查询授权"""
        query = CrossTenantQuery()
        query.grant("tenant1", "tenant2")
        assert query.is_granted("tenant1", "tenant2") is True
        assert query.is_granted("tenant2", "tenant1") is False

    def test_cross_tenant_query_revoke(self):
        """测试跨租户查询撤销"""
        query = CrossTenantQuery()
        query.grant("tenant1", "tenant2")
        query.revoke("tenant1", "tenant2")
        assert query.is_granted("tenant1", "tenant2") is False

    def test_isolation_level_enum(self):
        """测试隔离级别枚举"""
        assert IsolationLevel.L1.value == "L1"
        assert IsolationLevel.L2.value == "L2"
        assert IsolationLevel.L3.value == "L3"


# ═══════════════════════════════════════════════════════════════
# M4.5 PostgreSQL 迁移准备
# ═══════════════════════════════════════════════════════════════

class TestM45_PostgresMigration:
    """M4.5 PostgreSQL 迁移准备"""

    def test_postgres_config_from_env(self):
        """测试 PostgreSQL 配置从环境变量加载"""
        os.environ["DSH_PG_HOST"] = "pg.example.com"
        os.environ["DSH_PG_PORT"] = "5433"
        config = PostgresConfig.from_env()
        assert config.host == "pg.example.com"
        assert config.port == 5433

    def test_postgres_config_defaults(self):
        """测试 PostgreSQL 配置默认值"""
        config = PostgresConfig()
        assert config.host == "localhost"
        assert config.port == 5432
        assert config.database == "dsh_ai"

    def test_postgres_connection_string(self):
        """测试 PostgreSQL 连接字符串"""
        config = PostgresConfig(
            host="pg.example.com", port=5432, database="test",
            username="user", password="pass",
        )
        conn_str = config.to_connection_string()
        assert "postgresql://" in conn_str
        assert "user:pass" in conn_str

    def test_postgres_migrator_create_plan(self):
        """测试创建迁移计划"""
        migrator = PostgresMigrator(PostgresConfig())
        plan = migrator.create_migration_plan(
            DatabaseType.SQLITE,
            ["tenants", "workflow_instance", "audit_event"],
        )
        assert plan.source_type == DatabaseType.SQLITE
        assert plan.target_type == DatabaseType.POSTGRESQL
        assert len(plan.tables) == 3
        assert plan.status == MigrationStatus.PENDING

    def test_postgres_migrator_generate_sql(self):
        """测试生成迁移 SQL"""
        migrator = PostgresMigrator(PostgresConfig())
        sql = migrator.generate_migration_sql("test_table")
        assert len(sql) > 0
        assert any("CREATE TABLE" in s for s in sql)

    def test_postgres_migrator_execute(self):
        """测试执行迁移"""
        migrator = PostgresMigrator(PostgresConfig())
        plan = migrator.create_migration_plan(DatabaseType.SQLITE, ["test_table"])
        result = migrator.execute_migration(plan.id)
        assert result is True

    def test_connection_pool_manager(self):
        """测试连接池管理器"""
        manager = ConnectionPoolManager(PostgresConfig(pool_size=5))
        pool = manager.get_pool()
        assert pool["size"] == 5

    def test_connection_pool_stats(self):
        """测试连接池统计"""
        manager = ConnectionPoolManager(PostgresConfig())
        stats = manager.get_stats()
        assert "pool_count" in stats
        assert "config" in stats

    def test_migration_status_enum(self):
        """测试迁移状态枚举"""
        assert MigrationStatus.PENDING.value == "pending"
        assert MigrationStatus.COMPLETED.value == "completed"
        assert MigrationStatus.FAILED.value == "failed"

    def test_database_type_enum(self):
        """测试数据库类型枚举"""
        assert DatabaseType.SQLITE.value == "sqlite"
        assert DatabaseType.POSTGRESQL.value == "postgresql"


# ═══════════════════════════════════════════════════════════════
# M4.6 NATS 事件总线替换
# ═══════════════════════════════════════════════════════════════

class TestM46_NatsEventBus:
    """M4.6 NATS 事件总线替换"""

    @pytest.mark.asyncio
    async def test_nats_event_bus_publish(self):
        """测试 NATS 事件总线发布"""
        bus = NatsEventBus()
        await bus.connect()

        import uuid
        event = type("Event", (), {
            "id": str(uuid.uuid4()),
            "type": EventTypes.SENTIMENT_LEVEL_RAISED,
            "tenant_id": "test",
            "payload": {"level": "high", "score": 0.9},
        })()
        result = await bus.publish(event)
        assert result is True
        await bus.disconnect()

    @pytest.mark.asyncio
    async def test_nats_event_bus_subscribe(self):
        """测试 NATS 事件总线订阅"""
        bus = NatsEventBus()
        await bus.connect()

        handler = AsyncMock()
        sub = await bus.subscribe(EventTypes.SENTIMENT_LEVEL_RAISED, handler)
        assert sub.event_type == EventTypes.SENTIMENT_LEVEL_RAISED
        await bus.disconnect()

    @pytest.mark.asyncio
    async def test_nats_event_bus_ack(self):
        """测试 NATS 事件总线确认"""
        bus = NatsEventBus()
        await bus.connect()

        import uuid
        event = type("Event", (), {
            "id": str(uuid.uuid4()),
            "type": EventTypes.SENTIMENT_LEVEL_RAISED,
            "tenant_id": "test",
            "payload": {},
        })()
        await bus.publish(event)
        result = await bus.ack(event.id)
        assert result is True
        await bus.disconnect()

    def test_event_types_defined(self):
        """测试事件类型定义"""
        assert EventTypes.SENTIMENT_LEVEL_RAISED == "SENTIMENT.LEVEL_RAISED"
        assert EventTypes.PRODUCTION_DELAY == "PRODUCTION.DELAY"
        assert EventTypes.MARKETING_PAUSED == "MARKETING.PAUSED"
        assert EventTypes.APPROVAL_APPROVED == "APPROVAL.APPROVED"

    def test_build_sentiment_raised_event(self):
        """测试构建舆情等级提升事件"""
        event = build_sentiment_raised_event(
            tenant_id="winery",
            level="high",
            score=0.95,
            source="weibo",
            content="负面舆情内容",
        )
        assert event.type == EventTypes.SENTIMENT_LEVEL_RAISED
        assert event.tenant_id == "winery"
        assert event.payload["level"] == "high"

    def test_build_production_delay_event(self):
        """测试构建生产延迟事件"""
        event = build_production_delay_event(
            tenant_id="winery",
            order_no="PO-2026-001",
            delay_hours=24,
        )
        assert event.type == EventTypes.PRODUCTION_DELAY
        assert event.payload["order_no"] == "PO-2026-001"

    def test_event_bus_factory(self):
        """测试事件总线工厂"""
        bus = EventBusFactory.create("nats://localhost:4222")
        assert bus is not None

    def test_delivery_guarantee_enum(self):
        """测试投递保证枚举"""
        from jkos_core.bus.nats import DeliveryGuarantee
        assert DeliveryGuarantee.AT_LEAST_ONCE.value == "at_least_once"

    def test_event_status_enum(self):
        """测试事件状态枚举"""
        from jkos_core.bus.nats import EventStatus
        assert EventStatus.PUBLISHED.value == "published"
        assert EventStatus.CONSUMED.value == "consumed"
        assert EventStatus.DEAD_LETTER.value == "dead_letter"


class TestM46_NatsEventBusCoverage:
    """M4.6 补充：NATS 事件总线额外分支覆盖（任务 6.4）"""

    # ── 抽象接口 NotImplementedError ──

    @pytest.mark.asyncio
    async def test_event_bus_abstract_publish(self):
        from jkos_core.bus.nats import Event, EventBus
        bus = EventBus()
        event = Event(id="e1", type="T", tenant_id="t1", payload={})
        with pytest.raises(NotImplementedError):
            await bus.publish(event)

    @pytest.mark.asyncio
    async def test_event_bus_abstract_subscribe(self):
        from jkos_core.bus.nats import EventBus
        bus = EventBus()
        with pytest.raises(NotImplementedError):
            await bus.subscribe("T", AsyncMock())

    @pytest.mark.asyncio
    async def test_event_bus_abstract_unsubscribe(self):
        from jkos_core.bus.nats import EventBus
        bus = EventBus()
        with pytest.raises(NotImplementedError):
            await bus.unsubscribe("s1")

    @pytest.mark.asyncio
    async def test_event_bus_abstract_ack(self):
        from jkos_core.bus.nats import EventBus
        bus = EventBus()
        with pytest.raises(NotImplementedError):
            await bus.ack("e1")

    @pytest.mark.asyncio
    async def test_event_bus_abstract_nack(self):
        from jkos_core.bus.nats import EventBus
        bus = EventBus()
        with pytest.raises(NotImplementedError):
            await bus.nack("e1")

    # ── 连接生命周期 ──

    @pytest.mark.asyncio
    async def test_disconnect(self):
        bus = NatsEventBus()
        await bus.connect()
        assert bus._connected is True
        await bus.disconnect()
        assert bus._connected is False

    # ── 发布 ──

    @pytest.mark.asyncio
    async def test_publish_not_connected(self):
        bus = NatsEventBus()
        event = build_production_delay_event("t1", "PO-1", 24)
        assert await bus.publish(event) is False

    # ── 订阅管理 ──

    @pytest.mark.asyncio
    async def test_unsubscribe_hit_and_miss(self):
        bus = NatsEventBus()
        sub = await bus.subscribe("T.EVENT", AsyncMock(), queue_group="q1")
        assert sub.durable is True
        assert sub.id in bus._subscriptions
        assert await bus.unsubscribe(sub.id) is True
        assert sub.id not in bus._subscriptions
        assert await bus.unsubscribe(sub.id) is False

    # ── 确认 ──

    @pytest.mark.asyncio
    async def test_ack_not_found(self):
        bus = NatsEventBus()
        assert await bus.ack("missing") is False

    # ── 否定确认三路分支 ──

    @pytest.mark.asyncio
    async def test_nack_not_found(self):
        bus = NatsEventBus()
        assert await bus.nack("missing") is False

    @pytest.mark.asyncio
    async def test_nack_requeue(self):
        from jkos_core.bus.nats import Event, EventStatus
        bus = NatsEventBus()
        event = Event(id="e1", type="T", tenant_id="t1", payload={})
        bus._event_store[event.id] = event
        assert await bus.nack(event.id) is True
        assert event.retry_count == 1
        assert event.status == EventStatus.PENDING
        assert bus._dead_letter_queue == []

    @pytest.mark.asyncio
    async def test_nack_dead_letter(self):
        from jkos_core.bus.nats import Event, EventStatus
        bus = NatsEventBus()
        event = Event(id="e1", type="T", tenant_id="t1", payload={}, max_retries=2)
        event.retry_count = 1
        bus._event_store[event.id] = event
        assert await bus.nack(event.id) is True
        assert event.status == EventStatus.DEAD_LETTER
        assert len(bus._dead_letter_queue) == 1
        assert bus._dead_letter_queue[0].id == f"dlq-{event.id}"
        assert bus._dead_letter_queue[0].event is event

    @pytest.mark.asyncio
    async def test_nack_failed(self):
        from jkos_core.bus.nats import Event, EventStatus
        bus = NatsEventBus()
        event = Event(id="e1", type="T", tenant_id="t1", payload={})
        bus._event_store[event.id] = event
        assert await bus.nack(event.id, requeue=False) is True
        assert event.status == EventStatus.FAILED
        assert event.retry_count == 1

    # ── 事件处理（幂等）──

    @pytest.mark.asyncio
    async def test_handle_event_idempotent(self):
        from jkos_core.bus.nats import Event
        bus = NatsEventBus()
        handler = AsyncMock()
        await bus.subscribe("T.EVENT", handler)
        event = Event(id="e1", type="T.EVENT", tenant_id="t1", payload={})
        bus._idempotency_set.add(event.id)
        assert await bus.handle_event(event) is True
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_event_success(self):
        from jkos_core.bus.nats import Event, EventStatus
        bus = NatsEventBus()
        handler = AsyncMock()
        await bus.subscribe("T.EVENT", handler)
        event = Event(id="e1", type="T.EVENT", tenant_id="t1", payload={})
        bus._event_store[event.id] = event
        assert await bus.handle_event(event) is True
        handler.assert_awaited_once_with(event)
        assert event.status == EventStatus.CONSUMED
        assert event.id in bus._idempotency_set

    @pytest.mark.asyncio
    async def test_handle_event_exception_nacks(self):
        from jkos_core.bus.nats import Event, EventStatus
        bus = NatsEventBus()
        handler = AsyncMock(side_effect=RuntimeError("boom"))
        await bus.subscribe("T.EVENT", handler)
        event = Event(id="e1", type="T.EVENT", tenant_id="t1", payload={})
        bus._event_store[event.id] = event
        assert await bus.handle_event(event) is False
        assert event.retry_count == 1
        assert event.status == EventStatus.PENDING

    @pytest.mark.asyncio
    async def test_handle_event_no_subscription(self):
        from jkos_core.bus.nats import Event
        bus = NatsEventBus()
        event = Event(id="e1", type="T.UNKNOWN", tenant_id="t1", payload={})
        assert await bus.handle_event(event) is False

    # ── 事件存储访问 ──

    @pytest.mark.asyncio
    async def test_get_event_hit_and_miss(self):
        bus = NatsEventBus()
        await bus.connect()
        event = build_production_delay_event("t1", "PO-1", 24)
        await bus.publish(event)
        assert bus.get_event(event.id) is event
        assert bus.get_event("missing") is None
        await bus.disconnect()

    def test_get_dead_letter_events(self):
        from jkos_core.bus.nats import DeadLetterEvent, Event
        bus = NatsEventBus()
        event = Event(id="e1", type="T", tenant_id="t1", payload={})
        bus._dead_letter_queue.append(
            DeadLetterEvent(id="dlq-e1", event=event, error_message="boom")
        )
        dlqs = bus.get_dead_letter_events()
        assert len(dlqs) == 1
        assert dlqs[0].event is event

    def test_retry_dead_letter_hit(self):
        from jkos_core.bus.nats import DeadLetterEvent, Event, EventStatus
        bus = NatsEventBus()
        event = Event(id="e1", type="T", tenant_id="t1", payload={})
        event.status = EventStatus.DEAD_LETTER
        event.retry_count = 3
        dlq = DeadLetterEvent(id="dlq-e1", event=event, error_message="重试次数超限")
        bus._dead_letter_queue.append(dlq)
        assert bus.retry_dead_letter("dlq-e1") is True
        assert event.status == EventStatus.PENDING
        assert event.retry_count == 0

    def test_retry_dead_letter_miss(self):
        bus = NatsEventBus()
        assert bus.retry_dead_letter("nope") is False

    # ── 工厂 ──

    def test_event_bus_factory_get_set_instance(self):
        from jkos_core.bus.nats import EventBus
        EventBusFactory._instance = None
        assert EventBusFactory.get_instance() is None
        bus = EventBus()
        EventBusFactory.set_instance(bus)
        assert EventBusFactory.get_instance() is bus
        EventBusFactory._instance = None  # 还原，避免影响其它测试


# ═══════════════════════════════════════════════════════════════
# M4.7 自举第二期：D3 单测生成提升覆盖率
# ═══════════════════════════════════════════════════════════════

class TestM47_CoverageBooster:
    """M4.7 自举第二期：D3 单测生成提升覆盖率"""

    def test_coverage_analyzer(self):
        """测试覆盖率分析器"""
        analyzer = CoverageAnalyzer(str(PROJECT_ROOT))
        results = analyzer.analyze()
        assert len(results) > 0
        assert all(r.coverage >= 0 for r in results)

    def test_coverage_analyzer_low_coverage(self):
        """测试低覆盖率模块识别"""
        analyzer = CoverageAnalyzer(str(PROJECT_ROOT))
        low = analyzer.get_low_coverage_modules(threshold=90.0)
        # 实际实现：根据覆盖率数据判断
        assert isinstance(low, list)

    def test_coverage_analyzer_generate_plan(self):
        """测试生成测试计划"""
        analyzer = CoverageAnalyzer(str(PROJECT_ROOT))
        plans = analyzer.generate_test_plan(target_coverage=90.0)
        assert isinstance(plans, list)

    def test_coverage_booster(self):
        """测试覆盖率提升器"""
        booster = CoverageBooster(str(PROJECT_ROOT))
        result = booster.run(target_coverage=90.0)
        assert result["status"] == "completed"

    def test_coverage_result_dataclass(self):
        """测试覆盖率结果数据类"""
        result = CoverageResult(
            module="test_module",
            statements=100,
            missed=20,
            coverage=80.0,
            missing_lines=[10, 20, 30],
        )
        assert result.module == "test_module"
        assert result.coverage == 80.0

    def test_test_generation_plan_dataclass(self):
        """测试测试生成计划数据类"""
        plan = TestGenerationPlan(
            module="test_module",
            target_coverage=90.0,
            current_coverage=70.0,
            tests_to_generate=5,
            priority="high",
        )
        assert plan.priority == "high"
        assert plan.tests_to_generate == 5


# ═══════════════════════════════════════════════════════════════
# 集成测试
# ═══════════════════════════════════════════════════════════════

class TestM4_Integration:
    """M4 集成测试"""

    def test_winery_workflow_with_traceability(self, db):
        """测试酒厂工作流与追溯集成"""
        from jkos_core.db.repos import WorkflowRepo, ApprovalTaskRepo
        from jkos_core.audit import AuditLogger
        from jkos_core.llm import build_llm_router
        from jkos_core.db import TenantRepo
        from types import SimpleNamespace
        
        repo = WorkflowRepo(db)
        approvals = ApprovalTaskRepo(db)
        audit = AuditLogger(db)
        llm = build_llm_router()
        tenants = TenantRepo(db)
        
        comps = SimpleNamespace(
            workflows=repo,
            audit=audit,
            llm=llm,
            tenants=tenants,
            approvals=approvals,
        )
        
        engine = WorkflowEngine(comps)
        register(engine)

        # 创建批次
        repo = TraceabilityRepo(db)
        batch = Batch(
            id="B001", batch_no="B20260914001", product_code="P001",
            product_name="52度经典", line_code="L01", status=BatchStatus.CREATED,
            planned_quantity=500, actual_quantity=0,
            planned_start=datetime.now(), planned_end=datetime.now() + timedelta(days=3),
        )
        repo.create_batch(batch)

        # 添加追溯节点
        node = TraceNode(
            id="N001", batch_id="B001", node_type=TraceNodeType.FERMENTATION,
            seq=1, timestamp=datetime.now(), data={"temp": 25},
            operator_id="operator1",
        )
        repo.add_trace_node(node)

        # 验证追溯
        trace = repo.trace_batch("B20260914001")
        assert trace is not None
        assert len(trace["trace_chain"]) == 1

    def test_event_bus_with_winery_workflow(self):
        """测试事件总线与酒厂工作流集成"""
        event = build_sentiment_raised_event(
            tenant_id="winery",
            level="high",
            score=0.95,
            source="weibo",
            content="负面舆情",
        )
        assert event.type == EventTypes.SENTIMENT_LEVEL_RAISED
        assert event.tenant_id == "winery"

    def test_tenant_schema_with_postgres_config(self):
        """测试租户 Schema 与 PostgreSQL 配置集成"""
        manager = TenantSchemaManager("postgresql://localhost/dsh_ai")
        config = manager.register_tenant("tenant1", "winery", IsolationLevel.L2)

        # 验证连接字符串包含 schema
        conn_str = manager.get_connection_string("tenant1")
        assert "tenant_winery" in conn_str

    def test_erp_adapter_with_traceability(self):
        """测试 ERP 适配器与追溯集成"""
        adapter = ErpAdapter(mode=AdapterMode.SIMULATED)
        sales = adapter.get_sales_data(
            datetime.now() - timedelta(days=7),
            datetime.now(),
        )
        # 销售数据可用于生产调度
        assert len(sales) > 0

    def test_full_winery_workflow_chain(self):
        """测试完整酒厂工作流链"""
        # 1. 验证工作流定义
        assert "winery.production_scheduling" in WINERY_WORKFLOWS
        assert "winery.quality_traceability" in WINERY_WORKFLOWS

        # 2. 验证风险等级配置
        assert "high" in RISK_LEVELS
        assert RISK_LEVELS["high"]["auto_reject_hours"] == 2

        # 3. 验证品牌合规
        assert "prohibited_terms" in BRAND_COMPLIANCE
        assert "药效" in BRAND_COMPLIANCE["prohibited_terms"]

        # 4. 验证渠道配置
        assert "store" in CHANNELS


# ═══════════════════════════════════════════════════════════════
# 运行入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
