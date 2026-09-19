"""M18.2 多租户 L2 Schema 隔离——SQLite 真实隔离落地回归（数据层）

覆盖（M18.2 里程碑验收）：
- SQLite 模式：create_schema / migrate_schema / drop_schema 执行真实 SQL
  （每租户独立 .db 文件，schema_version 幂等迁移，MIGRATIONS 种子可用）；
- PostgreSQL 模式：仅渲染可再生脚本，假连接串全程零连接（M4.4 契约延续）；
- 新 API：get_tenant_database / render_pg_schema_sql；
- 加固项：租户码校验、close/close_all/unregister_tenant、迁移失败不泄漏连接、
  未注册租户的降级分支、data_dir 与工厂配置。
"""
import pytest

from jkos_core.db import tenant_schema as tenant_schema_mod
from jkos_core.db.schema import MIGRATIONS
from jkos_core.db.tenant_schema import (
    IsolationLevel,
    TenantIsolationConfig,
    TenantSchemaManager,
    create_cross_tenant_query,
    create_tenant_schema_manager,
)


@pytest.fixture
def manager(tmp_path):
    return TenantSchemaManager("sqlite:///main.db", data_dir=str(tmp_path))


# ─── SQLite 模式：真实隔离 ───

class TestM182_SqliteRealIsolation:

    def test_create_schema_creates_real_tenant_db(self, manager, tmp_path):
        manager.register_tenant("t1", "winery", IsolationLevel.L2)
        assert manager.create_schema("t1") is True
        db_file = tmp_path / "tenant_winery.db"
        assert db_file.exists()
        db = manager.get_tenant_database("t1")
        assert db is not None
        tables = {r["name"] for r in db.query(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"tenants", "workflow_instance", "workflow_step",
                "audit_event", "llm_usage", "approval_task",
                "schema_version"} <= tables
        db.close()

    def test_tenant_db_seeded_and_isolated(self, manager, tmp_path):
        manager.register_tenant("t1", "winery", IsolationLevel.L2)
        manager.create_schema("t1")
        db = manager.get_tenant_database("t1")
        codes = [r["code"] for r in db.query(
            "SELECT code FROM tenants ORDER BY code")]
        assert codes == ["dev", "media", "winery"]  # MIGRATION_V1 幂等种子
        db.close()
        # 另一租户：独立文件，互不影响
        m2 = TenantSchemaManager("sqlite:///main.db", data_dir=str(tmp_path))
        m2.register_tenant("t2", "winery2", IsolationLevel.L2)
        m2.create_schema("t2")
        assert (tmp_path / "tenant_winery2.db").exists()
        m2.get_tenant_database("t2").close()

    def test_migrate_schema_custom_sql(self, manager, tmp_path):
        manager.register_tenant("t1", "winery", IsolationLevel.L2)
        manager.create_schema("t1")
        assert manager.migrate_schema(
            "t1", ["CREATE TABLE tenant_extra (id INTEGER PRIMARY KEY)"]) is True
        db = manager.get_tenant_database("t1")
        assert len(db.query(
            "SELECT name FROM sqlite_master WHERE name='tenant_extra'")) == 1
        db.close()

    def test_migrate_schema_default_applies_migrations(self, manager, tmp_path):
        manager.register_tenant("t1", "winery", IsolationLevel.L2)
        assert manager.migrate_schema("t1") is True
        db = manager.get_tenant_database("t1")
        assert db.version() == len(MIGRATIONS)
        db.close()

    def test_drop_schema_removes_db_file(self, manager, tmp_path):
        manager.register_tenant("t1", "winery", IsolationLevel.L2)
        manager.create_schema("t1")
        db_file = tmp_path / "tenant_winery.db"
        assert db_file.exists()
        assert manager.drop_schema("t1") is True
        assert not db_file.exists()
        assert not (tmp_path / "tenant_winery.db-wal").exists()
        assert manager.get_tenant_database("t1") is None

    def test_get_connection_string_l2_sqlite(self, manager, tmp_path):
        manager.register_tenant("t1", "winery", IsolationLevel.L2)
        cs = manager.get_connection_string("t1")
        assert cs == f"sqlite:///{tmp_path / 'tenant_winery.db'}"

    def test_get_connection_string_default(self, manager):
        manager.register_tenant("t1", "winery")  # 默认 L1
        assert manager.get_connection_string("t1") == "sqlite:///main.db"

    def test_create_schema_l1_skips_physical_isolation(self, manager, tmp_path):
        manager.register_tenant("t1", "winery")  # L1 不建物理隔离
        assert manager.create_schema("t1") is True
        assert not (tmp_path / "tenant_winery.db").exists()


# ─── PostgreSQL 模式：渲染就绪（M19），假连接串零连接 ───

class TestM182_PostgresRenderOnly:

    def test_create_schema_pg_no_connection(self):
        m = TenantSchemaManager("postgresql://localhost/dsh_ai")
        m.register_tenant("t1", "winery")
        assert m.create_schema("t1") is True  # 假连接串零连接（M4.4 契约）
        assert len(m.list_tenant_schemas()) == 1

    def test_render_pg_schema_sql(self):
        m = TenantSchemaManager("postgresql://localhost/dsh_ai")
        m.register_tenant("t1", "winery", IsolationLevel.L2)
        sql = m.render_pg_schema_sql("t1")
        joined = "\n".join(sql)
        assert "CREATE SCHEMA IF NOT EXISTS tenant_winery" in joined
        assert "SET search_path TO tenant_winery" in joined
        assert "CREATE TABLE tenants" in joined
        assert "CREATE TABLE approval_task" in joined  # V2 DDL 可再生
        assert all(stmt.endswith(";") for stmt in sql)

    def test_migrate_schema_pg_no_connection(self):
        m = TenantSchemaManager("postgresql://localhost/dsh_ai")
        m.register_tenant("t1", "winery")
        assert m.migrate_schema("t1", ["CREATE TABLE x (id int)"]) is True
        assert m.drop_schema("t1") is True

    def test_drop_schema_pg_l2_no_connection(self):
        m = TenantSchemaManager("postgresql://localhost/dsh_ai")
        m.register_tenant("t1", "winery", IsolationLevel.L2)
        assert m.drop_schema("t1") is True  # PG 模式仅记日志，不连库
        assert m.get_tenant_database("t1") is None

    def test_list_tenant_schemas_l2(self):
        m = TenantSchemaManager("postgresql://localhost/dsh_ai")
        m.register_tenant("t1", "winery", IsolationLevel.L2)
        m.register_tenant("t2", "media", IsolationLevel.L2)
        cfgs = m.list_tenant_schemas()
        assert len(cfgs) == 2
        assert {c.schema_name for c in cfgs} == {"tenant_winery", "tenant_media"}

    def test_pg_mode_has_no_physical_path(self):
        m = TenantSchemaManager("postgresql://localhost/dsh_ai")
        m.register_tenant("t1", "winery", IsolationLevel.L2)
        # PG 模式无独立文件载体（_tenant_db_path 契约）
        assert m._tenant_db_path("t1") is None
        assert m.get_connection_string("t1").endswith("search_path%3Dtenant_winery")

    def test_render_pg_schema_sql_unregistered_raises(self):
        m = TenantSchemaManager("postgresql://localhost/dsh_ai")
        with pytest.raises(KeyError):
            m.render_pg_schema_sql("nope")


# ─── 加固：租户码校验与连接生命周期（M18.2 收口）───

class TestM182_Hardening:

    def test_register_tenant_rejects_illegal_code(self, manager):
        for bad in ("../etc", "Winery", "a" * 33, "", "te nant", "win/ery", "-lead"):
            with pytest.raises(ValueError):
                manager.register_tenant("t1", bad)

    def test_register_tenant_accepts_safe_codes(self, manager):
        for code in ("dev", "winery2", "case-c", "a_b", "0abc"):
            assert manager.register_tenant(code, code).schema_name == f"tenant_{code}"

    def test_close_single_tenant_is_idempotent(self, manager):
        manager.register_tenant("t1", "winery", IsolationLevel.L2)
        manager.create_schema("t1")
        db = manager.get_tenant_database("t1")
        assert manager.close("t1") is True
        assert manager.close("t1") is False  # 幂等：已关闭返回 False
        assert manager.get_tenant_database("t1") is None
        with pytest.raises(RuntimeError):  # 连接确实已关闭
            db.query("SELECT 1")

    def test_close_all_closes_every_tenant(self, manager):
        manager.register_tenant("t1", "winery", IsolationLevel.L2)
        manager.register_tenant("t2", "media", IsolationLevel.L2)
        manager.create_schema("t1")
        manager.create_schema("t2")
        assert manager.close_all() == 2
        assert manager.close_all() == 0

    def test_unregister_tenant_removes_and_closes(self, manager):
        manager.register_tenant("t1", "winery", IsolationLevel.L2)
        manager.create_schema("t1")
        assert manager.unregister_tenant("t1") is True
        assert manager.list_tenant_schemas() == []
        assert manager.get_tenant_database("t1") is None
        assert manager.unregister_tenant("t1") is False

    def test_create_schema_migrate_failure_closes_connection(self, monkeypatch, tmp_path):
        class _FakeDB:
            instances: list = []

            def __init__(self, config):
                self.config = config
                self.closed = False
                _FakeDB.instances.append(self)

            def connect(self):
                return self

            def migrate(self):
                raise RuntimeError("迁移失败（模拟）")

            def close(self):
                self.closed = True

        monkeypatch.setattr(tenant_schema_mod, "Database", _FakeDB)
        mgr = TenantSchemaManager("sqlite:///main.db", data_dir=str(tmp_path))
        mgr.register_tenant("t1", "winery", IsolationLevel.L2)
        with pytest.raises(RuntimeError):
            mgr.create_schema("t1")
        assert mgr.get_tenant_database("t1") is None  # 失败不入 _dbs
        assert _FakeDB.instances and all(db.closed for db in _FakeDB.instances)

    def test_create_schema_is_idempotent(self, manager):
        manager.register_tenant("t1", "winery", IsolationLevel.L2)
        assert manager.create_schema("t1") is True
        first = manager.get_tenant_database("t1")
        assert manager.create_schema("t1") is True
        assert manager.get_tenant_database("t1") is first  # 复用同一连接

    def test_data_dir_env_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DSH_TENANT_DATA_DIR", str(tmp_path / "envdir"))
        mgr = TenantSchemaManager("sqlite:///main.db")
        mgr.register_tenant("t1", "winery", IsolationLevel.L2)
        mgr.create_schema("t1")
        assert (tmp_path / "envdir" / "tenant_winery.db").exists()
        mgr.close_all()

    def test_factories(self, tmp_path):
        mgr = create_tenant_schema_manager("sqlite:///main.db", data_dir=str(tmp_path))
        assert isinstance(mgr, TenantSchemaManager)
        assert mgr.get_connection_string("nope") == "sqlite:///main.db"  # 未注册回落默认
        assert create_cross_tenant_query().is_granted("a", "b") is False

    def test_unregistered_tenant_branches(self, manager):
        assert manager.create_schema("nope") is False
        assert manager.drop_schema("nope") is False
        assert manager.migrate_schema("nope") is False
        assert manager.get_schema_name("nope") is None
        assert manager.close("nope") is False
        assert manager.unregister_tenant("nope") is False
        assert manager._tenant_db_path("nope") is None  # sqlite 模式 + 未注册

    def test_sqlite_l1_skip_migration_and_drop(self, manager, tmp_path):
        manager.register_tenant("t1", "winery")  # 默认 L1
        assert manager.migrate_schema("t1") is True   # L1 跳过独立迁移
        assert manager.drop_schema("t1") is True      # L1 无独立文件可删
        assert not (tmp_path / "tenant_winery.db").exists()

    def test_isolation_config_from_env(self, monkeypatch):
        monkeypatch.setenv("JKOS_TENANT_L2_ENABLED", "true")
        monkeypatch.setenv("DSH_TENANT_DATA_DIR", "/tmp/tenants")
        monkeypatch.setenv("JKOS_TENANT_L2_CODES", "winery, media ,")
        cfg = TenantIsolationConfig.from_env()
        assert cfg.enabled is True
        assert cfg.data_dir == "/tmp/tenants"
        assert cfg.codes == ["winery", "media"]
        assert "启用" in cfg.describe()

    def test_isolation_config_defaults_off(self, monkeypatch):
        for var in ("JKOS_TENANT_L2_ENABLED", "DSH_TENANT_DATA_DIR", "JKOS_TENANT_L2_CODES"):
            monkeypatch.delenv(var, raising=False)
        cfg = TenantIsolationConfig.from_env()
        assert cfg.enabled is False
        assert cfg.codes == []
        assert "关闭" in cfg.describe()