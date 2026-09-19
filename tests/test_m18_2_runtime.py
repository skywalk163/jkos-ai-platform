"""M18.2 运行时接线回归：L2 物理隔离接入 bootstrap 与 API

覆盖：
- 默认关闭（JKOS_TENANT_L2_ENABLED 未设置）时零退化：
  tenant_schemas 为 None、database_for 回落主库、engine_for 返回 None；
- 开启后：按白名单/租户表建立独立 .db，租户引擎与实例只落独立库（主库无痕）；
- L1 租户仍走主库；建立失败自动降级 L1（不阻塞启动）；
- close() 收敛租户连接；
- API 层：按租户路由引擎，L2 模式下跨租户查询 403。
"""
import asyncio
import sys
import types
from types import SimpleNamespace

from fastapi.testclient import TestClient

from jkos_core.api.routes import _engine_provider_of, create_app
from jkos_core.audit import AuditLogger
from jkos_core.auth.dependencies import AuthConfig, JWTManager
from jkos_core.bootstrap import (
    AppComponents,
    build_components,
    register_tenant_workflows,
    wire_tenant_isolation,
)
from jkos_core.db import (
    ApprovalTaskRepo,
    Database,
    DatabaseConfig,
    IsolationLevel,
    TenantIsolationConfig,
    TenantRepo,
    WorkflowRepo,
    create_tenant_schema_manager,
)
from jkos_core.llm import build_llm_router
from jkos_core.workflow import WorkflowEngine

ENV_KEYS = (
    "JKOS_TENANT_L2_ENABLED",
    "JKOS_TENANT_L2_CODES",
    "DSH_TENANT_DATA_DIR",
    "DSH_DB_PATH",
    "JKOS_HARNESS_ENABLED",
)


def run(coro):
    return asyncio.run(coro)


def make_components(tmp_path, *, isolation=False, codes=None, db_name="main.db"):
    """构造 AppComponents（可选开启 L2 隔离），返回组件"""
    main_path = tmp_path / db_name
    db = Database(DatabaseConfig(path=str(main_path))).connect()
    db.migrate()
    comps = AppComponents(
        db=db,
        tenants=TenantRepo(db),
        workflows=WorkflowRepo(db),
        audit=AuditLogger(db),
        llm=build_llm_router(),
        jwt=JWTManager(AuthConfig(secret="test-secret-key-16-chars!")),
        approvals=ApprovalTaskRepo(db),
    )
    if isolation:
        manager = create_tenant_schema_manager(
            f"sqlite:///{main_path}", data_dir=str(tmp_path / "tenants"))
        wire_tenant_isolation(
            db, manager, TenantIsolationConfig(enabled=True, codes=codes or []))
        comps.tenant_schemas = manager
    return comps


# ─── 默认关闭：零退化 ───

class TestM182RuntimeDefaultOff:

    def test_build_components_keeps_isolation_off(self, monkeypatch, tmp_path):
        for key in ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        comps = build_components(db_path=str(tmp_path / "main.db"), init_auth=False)
        try:
            assert comps.tenant_schemas is None
            assert comps.database_for("winery") is comps.db
            assert comps.engine_for("winery") is None
        finally:
            comps.close()

    def test_engine_provider_ignores_non_appcomponents(self):
        # Mock / SimpleNamespace 不应被误判为租户路由提供者（既有用例零退化）
        fake_engine = SimpleNamespace(comps=SimpleNamespace(engine_for=lambda code: "X"))
        assert _engine_provider_of(fake_engine, None) is None
        assert _engine_provider_of(None, None) is None
        assert _engine_provider_of(fake_engine, "P") == "P"


# ─── 开启隔离：按租户落地 ───

class TestM182RuntimeIsolated:

    def test_engine_for_creates_tenant_db_and_isolates_instances(self, tmp_path):
        comps = make_components(tmp_path, isolation=True, codes=["winery"])
        try:
            assert (tmp_path / "tenants" / "tenant_winery.db").exists()
            assert comps.database_for("winery") is not comps.db
            assert comps.database_for("media") is comps.db  # 未列入白名单 -> 主库

            engine = comps.engine_for("winery")
            assert engine is not None
            assert comps.engine_for("winery") is engine  # 缓存复用
            assert comps.engine_for("media") is None     # L1 -> 回落单引擎

            status = run(engine.start(
                "winery.quality_traceability", "winery",
                {"product": "赤霞珠干红·750ml", "quantity": 500},
            ))
            assert status["instance"]["status"] == "COMPLETED"

            tenant_db = comps.database_for("winery")
            assert tenant_db.query("SELECT COUNT(*) AS c FROM workflow_instance")[0]["c"] == 1
            assert comps.db.query(
                "SELECT COUNT(*) AS c FROM workflow_instance")[0]["c"] == 0  # 主库无痕
        finally:
            comps.close()

    def test_wire_uses_tenant_table_isolation_level(self, tmp_path):
        comps = make_components(tmp_path, isolation=False)
        try:
            comps.db.execute("UPDATE tenants SET isolation_level = ? WHERE code = ?",
                             (IsolationLevel.L2.value, "winery"))
            manager = create_tenant_schema_manager(
                f"sqlite:///{tmp_path / 'main.db'}", data_dir=str(tmp_path / "tenants"))
            wired = wire_tenant_isolation(
                comps.db, manager, TenantIsolationConfig(enabled=True))
            assert wired == 1  # 白名单为空 -> 按租户表 isolation_level 判定
            assert comps.db.query(
                "SELECT COUNT(*) AS c FROM workflow_instance")[0]["c"] == 0
            manager.close_all()
        finally:
            comps.close()

    def test_wire_degrades_to_l1_on_failure(self, monkeypatch, tmp_path):
        comps = make_components(tmp_path, isolation=False)
        try:
            def _boom(self, tenant_id):
                raise RuntimeError("模拟建库失败")

            monkeypatch.setattr(
                "jkos_core.db.tenant_schema.TenantSchemaManager.create_schema", _boom)
            manager = create_tenant_schema_manager(
                f"sqlite:///{tmp_path / 'main.db'}", data_dir=str(tmp_path / "tenants"))
            wired = wire_tenant_isolation(
                comps.db, manager, TenantIsolationConfig(enabled=True, codes=["winery"]))
            assert wired == 0
            assert manager.list_tenant_schemas() == []  # 失败即注销（降级 L1）
        finally:
            comps.close()

    def test_close_releases_tenant_connections(self, tmp_path):
        comps = make_components(tmp_path, isolation=True, codes=["winery"])
        comps.engine_for("winery")
        assert comps.tenant_schemas.get_tenant_database(
            comps.tenant_schemas.get_config_by_code("winery").tenant_id) is not None
        comps.close()
        assert comps.tenant_schemas.get_tenant_database(
            comps.tenant_schemas.get_config_by_code("winery").tenant_id) is None
        assert comps.engine_for("winery") is None  # 主库已关，路由回落

    def test_register_tenant_workflows_handles_missing_dev(self, monkeypatch):
        fake = types.ModuleType("tenants.dev.workflows")

        def _boom(engine):
            raise ImportError("模拟 tenants.dev 缺失")

        fake.register = _boom
        monkeypatch.setitem(sys.modules, "tenants.dev.workflows", fake)
        register_tenant_workflows(SimpleNamespace())  # 静默降级，不抛异常


# ─── API 层按租户路由 ───

class TestM182RuntimeApi:

    def test_api_routes_to_tenant_engine_and_denies_cross_tenant(self, tmp_path):
        comps = make_components(tmp_path, isolation=True, codes=["winery"])
        engine = WorkflowEngine(comps)
        register_tenant_workflows(engine)
        try:
            client = TestClient(create_app(engine))
            token = comps.jwt.issue_token(
                "winery-id", "winery", "alice", ["admin"], expires_in=3600)
            headers = {"Authorization": f"Bearer {token}"}

            ok = client.get("/api/v1/approvals/pending", headers=headers)
            assert ok.status_code == 200
            assert ok.json()["total"] == 0  # 落在租户独立库（空）

            denied = client.get("/api/v1/approvals/pending?tenant_code=media",
                                headers=headers)
            assert denied.status_code == 403  # L2 隔离：不支持跨租户查询
        finally:
            comps.close()

    def test_api_single_engine_when_isolation_off(self, tmp_path):
        comps = make_components(tmp_path, isolation=False)
        engine = WorkflowEngine(comps)
        register_tenant_workflows(engine)
        try:
            client = TestClient(create_app(engine))
            token = comps.jwt.issue_token(
                "dev-id", "dev", "alice", ["admin"], expires_in=3600)
            resp = client.get("/api/v1/approvals/pending",
                              headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200  # 与接线前行为一致
        finally:
            comps.close()
