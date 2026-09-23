"""数据层/API - 资源配置与数据库连通管理（V3）测试

覆盖：
- V3 迁移落库（resource / connection_check 表）
- ResourceRepo 增删改查 / 幂等 ensure / 租户隔离 / 连通记录 / 统计
- DatabaseConnector 参数校验与分类型连通探测（sqlite / http / socket）
- API 资源配置与连通检测端点（单引擎模式，无需认证初始化）
"""
import http.server
import os
import shutil
import socket
import tempfile
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient

from jkos_core.api.routes import create_app
from jkos_core.db import (
    RESOURCE_ACTIVE,
    RESOURCE_DISABLED,
    RESOURCE_KIND_HTTP,
    RESOURCE_KIND_REDIS,
    RESOURCE_KIND_SQLITE,
    Database,
    DatabaseConfig,
    ResourceRepo,
    is_ulid,
)
from jkos_core.db.connectivity import ConnectorError, DatabaseConnector, redact_config


# ─── 脚手架 ───

def _make_db():
    tmp = tempfile.mkdtemp(prefix="dsh_db_")
    db = Database(DatabaseConfig(path=os.path.join(tmp, "test.db")))
    db.connect()
    db.migrate()
    return db, tmp


def _close(db, tmp):
    try:
        db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _make_engine():
    """单引擎模式（不 init 认证）：资源路由走 get_optional_context，匿名回落 system 租户"""
    db, tmp = _make_db()
    comps = SimpleNamespace(db=db, resources=ResourceRepo(db))
    engine = SimpleNamespace(comps=comps)
    app = create_app(engine)
    return engine, db, tmp, TestClient(app)


def _raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    raise AssertionError(f"应抛出 {exc_type.__name__}")


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


# ─── V3 迁移 ───

def test_v3_tables_created():
    db, tmp = _make_db()
    try:
        assert db.version() >= 3
        tables = {r["name"] for r in db.query(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"resource", "connection_check"} <= tables
    finally:
        _close(db, tmp)


# ─── ResourceRepo ───

def test_repo_create_roundtrip():
    db, tmp = _make_db()
    try:
        repo = ResourceRepo(db)
        r = repo.create("dev", "postgres-orders", "postgres",
                        config={"host": "127.0.0.1", "port": 5432, "password": "x"})
        assert is_ulid(r["id"])
        assert r["tenant_id"] == "dev"
        assert r["kind"] == "postgres"
        assert r["category"] == "general"
        assert r["status"] == RESOURCE_ACTIVE
        assert r["config"] == {"host": "127.0.0.1", "port": 5432, "password": "x"}
        got = repo.get(r["id"])
        assert got["name"] == "postgres-orders"
        assert repo.get("nope") is None
    finally:
        _close(db, tmp)


def test_repo_ensure_idempotent():
    db, tmp = _make_db()
    try:
        repo = ResourceRepo(db)
        a = repo.ensure("dev", "pg-main", "postgres", config={"host": "h"})
        b = repo.ensure("dev", "pg-main", "postgres", config={"host": "h"})
        assert a["id"] == b["id"]
        cnt = db.query_one("SELECT COUNT(*) AS c FROM resource")["c"]
        assert cnt == 1
    finally:
        _close(db, tmp)


def test_repo_tenant_scoping():
    db, tmp = _make_db()
    try:
        repo = ResourceRepo(db)
        repo.create("dev", "a", "postgres", config={"host": "h1"})
        repo.create("media", "b", "redis", config={"host": "h2"})
        dev_only = repo.list_all(tenant_id="dev")
        assert len(dev_only) == 1 and dev_only[0]["tenant_id"] == "dev"
        assert len(repo.list_all()) == 2
        by_kind = repo.list_all(kind="redis")
        assert len(by_kind) == 1 and by_kind[0]["name"] == "b"
    finally:
        _close(db, tmp)


def test_repo_update_and_status():
    db, tmp = _make_db()
    try:
        repo = ResourceRepo(db)
        r = repo.create("dev", "x", "sqlite", config={"path": "a.db"})
        after = repo.update_config(r["id"], {"path": "b.db"})
        assert after["config"] == {"path": "b.db"}
        disabled = repo.set_status(r["id"], RESOURCE_DISABLED)
        assert disabled["status"] == RESOURCE_DISABLED
    finally:
        _close(db, tmp)


def test_repo_delete_cascades_checks():
    db, tmp = _make_db()
    try:
        repo = ResourceRepo(db)
        r = repo.create("dev", "x", "sqlite", config={"path": "a.db"})
        repo.record_check("dev", r["id"], "sqlite", True, 1, {"a": 1})
        repo.record_check("dev", r["id"], "sqlite", False, 2, {"a": 2})
        assert repo.delete(r["id"]) is True
        assert repo.get(r["id"]) is None
        assert repo.list_checks(r["id"]) == []
        cnt = db.query_one("SELECT COUNT(*) AS c FROM connection_check")["c"]
        assert cnt == 0
    finally:
        _close(db, tmp)


def test_repo_checks_roundtrip():
    db, tmp = _make_db()
    try:
        repo = ResourceRepo(db)
        r = repo.create("dev", "x", "postgres", config={"host": "h"})
        repo.record_check("dev", r["id"], "postgres", False, 5, {"error": "refused"})
        repo.record_check("dev", r["id"], "postgres", True, 3, {"ok": True})
        checks = repo.list_checks(r["id"])
        assert len(checks) == 2
        assert checks[0]["connected"] == 1   # 最新在前
        assert checks[0]["detail"] == {"ok": True}
        assert checks[1]["connected"] == 0
    finally:
        _close(db, tmp)


def test_repo_stats():
    db, tmp = _make_db()
    try:
        repo = ResourceRepo(db)
        r1 = repo.create("dev", "a", "postgres", config={"host": "h"})
        r2 = repo.create("dev", "b", "redis", config={"host": "h"})
        r3 = repo.create("dev", "c", "sqlite", config={"path": "a.db"})
        repo.set_status(r3["id"], RESOURCE_DISABLED)
        repo.record_check("dev", r1["id"], "postgres", True, 1, None)
        repo.record_check("dev", r2["id"], "redis", False, 2, None)
        repo.record_check("dev", r2["id"], "redis", True, 1, None)
        stats = repo.stats("dev")
        assert stats["total"] == 3
        assert stats["by_status"] == {RESOURCE_ACTIVE: 2, RESOURCE_DISABLED: 1}
        assert stats["checks_total"] == 3
        assert stats["checks_ok"] == 2
    finally:
        _close(db, tmp)


# ─── DatabaseConnector ───

def test_connector_validate_sqlite_missing_path():
    _raises(ConnectorError, DatabaseConnector().validate, RESOURCE_KIND_SQLITE, {})


def test_connector_validate_bad_port():
    _raises(ConnectorError, DatabaseConnector().validate, RESOURCE_KIND_REDIS,
            {"host": "h", "port": 70000})


def test_connector_validate_unknown_kind():
    _raises(ConnectorError, DatabaseConnector().validate, "oracle", {})


def test_connector_validate_http_bad_url():
    _raises(ConnectorError, DatabaseConnector().validate, RESOURCE_KIND_HTTP, {"url": "ftp://x"})


def test_connector_sqlite_file_ok():
    tmp = tempfile.mkdtemp(prefix="dsh_conn_")
    try:
        path = os.path.join(tmp, "a.db")
        result = DatabaseConnector().check(RESOURCE_KIND_SQLITE, {"path": path}, timeout=2.0)
        assert result["connected"] is True
        assert result["latency_ms"] >= 0
        assert os.path.exists(path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_connector_sqlite_bad_path():
    tmp = tempfile.mkdtemp(prefix="dsh_conn_")
    try:
        result = DatabaseConnector().check(
            RESOURCE_KIND_SQLITE, {"path": os.path.join(tmp, "no_such_dir", "a.db")},
            timeout=2.0,
        )
        assert result["connected"] is False
        assert "连接失败" in result["detail"]["error"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_connector_http_ok():
    server = http.server.HTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        result = DatabaseConnector().check(
            RESOURCE_KIND_HTTP, {"url": f"http://127.0.0.1:{port}/health"}, timeout=5.0)
        assert result["connected"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_connector_socket_closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    result = DatabaseConnector().check(
        RESOURCE_KIND_REDIS, {"host": "127.0.0.1", "port": port}, timeout=2.0)
    assert result["connected"] is False
    assert "无法连接" in result["detail"]["error"]


def test_redact_config_hides_secrets():
    out = redact_config({"host": "db", "password": "s3cr3t", "token": "x"})
    assert out["password"] == "***"
    assert out["token"] == "***"
    assert out["host"] == "db"


# ─── API ───

def test_api_list_empty_and_create_ok():
    engine, db, tmp, client = _make_engine()
    try:
        r = client.get("/api/v1/resources")
        assert r.status_code == 200
        assert r.json() == {"resources": [], "total": 0}

        res = client.post("/api/v1/resources", json={
            "name": "pg-orders", "kind": "postgres",
            "config": {"host": "127.0.0.1", "port": 5432},
        })
        assert res.status_code == 200
        body = res.json()["resource"]
        assert is_ulid(body["id"])
        assert body["name"] == "pg-orders"
        assert body["status"] == RESOURCE_ACTIVE
        assert body["config"]["host"] == "127.0.0.1"

        lst = client.get("/api/v1/resources").json()
        assert lst["total"] == 1
    finally:
        _close(db, tmp)


def test_api_create_duplicate_409():
    engine, db, tmp, client = _make_engine()
    try:
        payload = {"name": "pg", "kind": "postgres", "config": {"host": "h"}}
        assert client.post("/api/v1/resources", json=payload).status_code == 200
        assert client.post("/api/v1/resources", json=payload).status_code == 409
    finally:
        _close(db, tmp)


def test_api_create_unknown_kind_400():
    engine, db, tmp, client = _make_engine()
    try:
        r = client.post("/api/v1/resources", json={"name": "x", "kind": "oracle", "config": {}})
        assert r.status_code == 400
    finally:
        _close(db, tmp)


def test_api_get_missing_404():
    engine, db, tmp, client = _make_engine()
    try:
        r = client.get("/api/v1/resources/00000000000000000000000000")
        assert r.status_code == 404
    finally:
        _close(db, tmp)


def test_api_update_and_delete():
    engine, db, tmp, client = _make_engine()
    try:
        rid = client.post("/api/v1/resources", json={
            "name": "sqlite-a", "kind": "sqlite", "config": {"path": "a.db"}}).json()["resource"]["id"]
        up = client.put(f"/api/v1/resources/{rid}", json={
            "config": {"path": "b.db"}, "status": RESOURCE_DISABLED})
        assert up.status_code == 200
        assert up.json()["resource"]["config"] == {"path": "b.db"}
        assert up.json()["resource"]["status"] == RESOURCE_DISABLED

        d = client.delete(f"/api/v1/resources/{rid}")
        assert d.status_code == 200
        assert d.json()["status"] == "deleted"
        assert client.get(f"/api/v1/resources/{rid}").status_code == 404
    finally:
        _close(db, tmp)


def test_api_check_sqlite_ok():
    engine, db, tmp_dir, client = _make_engine()
    try:
        db_path = os.path.join(tmp_dir, "conn_a.db")
        rid = client.post("/api/v1/resources", json={
            "name": "sqlite-conn", "kind": "sqlite", "config": {"path": db_path}}).json()["resource"]["id"]
        r = client.post(f"/api/v1/resources/{rid}/check")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is True
        assert body["latency_ms"] >= 0
        assert is_ulid(body["check_id"])
        assert os.path.exists(db_path)
    finally:
        _close(db, tmp_dir)


def test_api_check_sqlite_badpath():
    engine, db, tmp_dir, client = _make_engine()
    try:
        rid = client.post("/api/v1/resources", json={
            "name": "sqlite-bad", "kind": "sqlite",
            "config": {"path": os.path.join(tmp_dir, "no_dir", "x.db")}}).json()["resource"]["id"]
        r = client.post(f"/api/v1/resources/{rid}/check")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is False
        assert body["error"]
    finally:
        _close(db, tmp_dir)


def test_api_checks_history():
    engine, db, tmp_dir, client = _make_engine()
    try:
        rid = client.post("/api/v1/resources", json={
            "name": "sqlite-hist", "kind": "sqlite",
            "config": {"path": os.path.join(tmp_dir, "h.db")}}).json()["resource"]["id"]
        client.post(f"/api/v1/resources/{rid}/check")
        client.post(f"/api/v1/resources/{rid}/check")
        r = client.get(f"/api/v1/resources/{rid}/checks")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        assert body["checks"][0]["detail"]["kind"] == RESOURCE_KIND_SQLITE
    finally:
        _close(db, tmp_dir)


def test_api_stats_endpoint():
    engine, db, tmp, client = _make_engine()
    try:
        client.post("/api/v1/resources", json={"name": "a", "kind": "postgres", "config": {"host": "h"}})
        client.post("/api/v1/resources", json={"name": "b", "kind": "redis", "config": {"host": "h"}})
        r = client.get("/api/v1/resources/stats")
        assert r.status_code == 200
        stats = r.json()["stats"]
        assert stats["total"] == 2
        assert stats["by_status"] == {RESOURCE_ACTIVE: 2}
        assert stats["checks_total"] == 0
    finally:
        _close(db, tmp)


def test_api_check_missing_resource_404():
    engine, db, tmp, client = _make_engine()
    try:
        r = client.post("/api/v1/resources/00000000000000000000000000/check")
        assert r.status_code == 404
    finally:
        _close(db, tmp)