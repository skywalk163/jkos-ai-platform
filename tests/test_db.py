"""数据层单元测试（任务 0.1/0.4）— 纯 assert，兼容 pytest 与 m0_selftest 直跑"""
import os
import tempfile

from dsh_core.db import (
    Database,
    DatabaseConfig,
    ApprovalTaskRepo,
    AuditRepo,
    LlmUsageRepo,
    MIGRATIONS,
    TenantRepo,
    WorkflowRepo,
    is_ulid,
    new_ulid,
)
from dsh_core.db.repos import _loads


def make_db() -> Database:
    tmp = tempfile.mkdtemp(prefix="dsh_db_")
    db = Database(DatabaseConfig(path=os.path.join(tmp, "test.db"))).connect()
    db.migrate()
    return db


def test_ulid_format_and_uniqueness():
    ids = {new_ulid() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(is_ulid(i) for i in ids)


def test_migrate_idempotent():
    db = make_db()
    try:
        version = db.version()
        assert version == len(MIGRATIONS), f"当前 schema 版本应与迁移脚本数一致: {version}"
        db.migrate()  # 重复执行不报错
        assert db.version() == version
    finally:
        db.close()


def test_tenant_seeds():
    db = make_db()
    try:
        repo = TenantRepo(db)
        codes = {t["code"] for t in repo.list_all()}
        assert {"dev", "media", "winery"} <= codes
        # ensure 幂等：重复 ensure 返回同一条
        t1 = repo.ensure("dev", "dup")
        t2 = repo.ensure("dev", "dup")
        assert t1["id"] == t2["id"]
    finally:
        db.close()


def test_workflow_roundtrip():
    db = make_db()
    try:
        tenants, wf = TenantRepo(db), WorkflowRepo(db)
        t = tenants.get_by_code("dev")
        inst = wf.create_instance(t["id"], "D1", {"repo": "dsh-ai-platform"}, created_by="tester")
        assert inst["status"] == "PENDING"
        assert inst["context"]["repo"] == "dsh-ai-platform"

        s1 = wf.append_step(inst["id"], 1, "fetch_diff", "tool", {"pr": 42})
        wf.start_step(s1["id"])
        wf.finish_step(s1["id"], "SUCCEEDED", output={"files": 3})
        wf.update_status(inst["id"], "RUNNING", current_step="fetch_diff")

        got = wf.get_instance(inst["id"])
        assert got["status"] == "RUNNING" and got["current_step"] == "fetch_diff"
        steps = wf.get_steps(inst["id"])
        assert len(steps) == 1
        assert steps[0]["output"]["files"] == 3
        assert steps[0]["attempts"] == 1

        # 崩溃恢复：RUNNING → PENDING
        s2 = wf.append_step(inst["id"], 2, "llm_review", "llm", {})
        wf.start_step(s2["id"])
        assert wf.requeue_running_steps(inst["id"]) == 1
        assert wf.get_step_by_seq(inst["id"], 2)["status"] == "PENDING"

        # (instance_id, seq) 唯一约束：重复 seq 必须被拒绝
        raised = False
        try:
            wf.append_step(inst["id"], 1, "dup", "tool")
        except Exception:
            raised = True
        assert raised, "重复 seq 应当违反唯一约束"
    finally:
        db.close()


def test_audit_append_only():
    db = make_db()
    try:
        repo = AuditRepo(db)
        event_id = repo.append(
            tenant_id="dev", actor_type="AGENT", actor_id="agent-01",
            action="tool.invoke", resource_type="tool",
            resource_id="dsh_text_generate",
            before={"a": 1}, after={"b": 2}, reason="test", trace_id="tr-1",
        )
        assert is_ulid(event_id)
        events = repo.list_events(tenant_id="dev")
        assert len(events) == 1
        assert events[0]["before"] == {"a": 1}
        assert events[0]["after"] == {"b": 2}

        # UPDATE / DELETE 必须被触发器拒绝（§8.2.4 append-only）
        for sql in ("UPDATE audit_event SET action = 'tampered' WHERE id = ?",
                    "DELETE FROM audit_event WHERE id = ?"):
            raised = False
            try:
                db.execute(sql, (event_id,))
            except Exception as e:
                raised = True
                assert "append-only" in str(e)
            assert raised, f"应当拒绝: {sql}"
    finally:
        db.close()


def test_llm_usage_record_and_summary():
    db = make_db()
    try:
        repo = LlmUsageRepo(db)
        repo.record(tenant_id="dev", provider="deepseek", model="deepseek-chat",
                    purpose="review", prompt_tokens=100, completion_tokens=50,
                    duration_ms=800)
        repo.record(tenant_id="dev", provider="simulated", model="simulated",
                    purpose="review", prompt_tokens=10, completion_tokens=5,
                    status="simulated")
        s = repo.summary(tenant_id="dev")
        assert {r["provider"] for r in s} == {"deepseek", "simulated"}
        row = next(r for r in s if r["provider"] == "deepseek")
        assert row["calls"] == 1 and row["tokens"] == 150
    finally:
        db.close()


def test_transaction_rollback():
    db = make_db()
    try:
        raised = False
        try:
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO tenants (id, code, name, created_at, updated_at)"
                    " VALUES ('x','x','x','t','t')"
                )
                raise RuntimeError("boom")
        except RuntimeError:
            raised = True
        assert raised
        assert db.query_one("SELECT COUNT(*) c FROM tenants WHERE code='x'")["c"] == 0
    finally:
        db.close()


def test_execute_autocommit_persists_across_connections():
    """回归（0.2 联调 bug）：repo 单条写入必须立即持久化，
    不得悬挂在隐式事务上随进程退出回滚——写后用全新连接验证可见性"""
    tmp = tempfile.mkdtemp(prefix="dsh_db_")
    path = os.path.join(tmp, "test.db")

    db = Database(DatabaseConfig(path=path)).connect()
    try:
        db.migrate()
        wf = WorkflowRepo(db)
        t = TenantRepo(db).get_by_code("dev")
        wf.create_instance(t["id"], "D1", {"persist": True}, created_by="t")
    finally:
        db.close()

    db2 = Database(DatabaseConfig(path=path)).connect()
    try:
        row = db2.query_one("SELECT COUNT(*) c FROM workflow_instance")["c"]
        assert row == 1, "跨连接不可见：写入被隐式事务回滚（autocommit 回归）"
    finally:
        db2.close()


def test_transaction_commits_on_success():
    """autocommit 改动后，显式事务成功路径仍应完整提交多条写入"""
    db = make_db()
    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO tenants (id, code, name, created_at, updated_at)"
                " VALUES ('y1','y1','y1','t','t')"
            )
            conn.execute(
                "INSERT INTO tenants (id, code, name, created_at, updated_at)"
                " VALUES ('y2','y2','y2','t','t')"
            )
        assert db.query_one("SELECT COUNT(*) c FROM tenants WHERE code LIKE 'y%'")["c"] == 2
    finally:
        db.close()


def test_loads_invalid_json_returns_raw():
    """_loads 遇到非法 JSON 时原样返回，不丢数据（覆盖 repos.py 63-64 except 分支）"""
    assert _loads("not-json") == "not-json"
    assert _loads("") is None
    assert _loads(None) is None


def test_tenant_ensure_creates_new_tenant():
    """TenantRepo.ensure 在租户不存在时走 INSERT 新建路径（覆盖 repos.py 84-90）"""
    db = make_db()
    try:
        row = TenantRepo(db).ensure("n1", "新租户")
        assert row["code"] == "n1"
        assert row["name"] == "新租户"
        assert row["isolation_level"] == "L3"
        assert row["status"] == "active"

        # 幂等：再次 ensure 返回同一行，不重复插入
        again = TenantRepo(db).ensure("n1", "新租户")
        assert again["code"] == "n1"
        rows = db.query("SELECT COUNT(*) c FROM tenants WHERE code = 'n1'")
        assert rows[0]["c"] == 1
    finally:
        db.close()


def test_audit_list_events_filter_resource_type():
    """list_events 带 resource_type 过滤（覆盖 repos.py 313-314）"""
    db = make_db()
    try:
        AuditRepo(db).append(
            tenant_id="dev", actor_type="AGENT", actor_id="agent-01",
            action="tool.invoke", resource_type="tool",
            resource_id="dsh_text_generate",
            before=None, after={"b": 1}, reason="test", trace_id="tr-2",
        )
        AuditRepo(db).append(
            tenant_id="dev", actor_type="AGENT", actor_id="agent-01",
            action="workflow.start", resource_type="workflow",
            resource_id="wf-01",
            before=None, after={"b": 2}, reason="test", trace_id="tr-3",
        )
        events = AuditRepo(db).list_events(tenant_id="dev", resource_type="tool", limit=10)
        assert len(events) == 1
        assert events[0]["resource_type"] == "tool"
        assert events[0]["resource_id"] == "dsh_text_generate"
        assert events[0]["before"] is None
        assert events[0]["after"] == {"b": 1}
    finally:
        db.close()


def test_audit_list_events_filter_resource_id():
    """list_events 带 resource_id 过滤（覆盖 repos.py 316-317）"""
    db = make_db()
    try:
        AuditRepo(db).append(
            tenant_id="dev", actor_type="AGENT", actor_id="agent-01",
            action="tool.invoke", resource_type="tool",
            resource_id="dsh_text_generate",
            before=None, after={"b": 1}, reason="test", trace_id="tr-4",
        )
        AuditRepo(db).append(
            tenant_id="dev", actor_type="AGENT", actor_id="agent-01",
            action="tool.invoke", resource_type="tool",
            resource_id="other_tool",
            before=None, after={"b": 2}, reason="test", trace_id="tr-5",
        )
        events = AuditRepo(db).list_events(tenant_id="dev", resource_id="dsh_text_generate", limit=10)
        assert len(events) == 1
        assert events[0]["resource_id"] == "dsh_text_generate"
        assert events[0]["after"] == {"b": 1}
    finally:
        db.close()


def test_record_decision_missing_task_is_noop():
    """record_decision 对不存在的审批任务直接返回，不抛错（覆盖 repos.py 454）"""
    db = make_db()
    try:
        # 不存在的 task_id：应静默无操作
        ApprovalTaskRepo(db).record_decision(
            "nonexistent-task-id", approver="u-01", decision="approve"
        )
        rows = db.query("SELECT COUNT(*) c FROM approval_task")
        assert rows[0]["c"] == 0
    finally:
        db.close()
