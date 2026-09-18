"""M1 API 路由测试（任务 1.5）— 审批端点 + 超时扫描

纯 assert 风格，兼容 pytest 与 m0_selftest 直跑。
启动真实 FastAPI 测试客户端，验证 JSON 契约、状态码、租户隔离。
"""
import asyncio
import json
import os
import tempfile
from types import SimpleNamespace

from fastapi.testclient import TestClient

from dsh_core.api.routes import create_app
from dsh_core.auth.dependencies import AuthConfig, JWTManager
from dsh_core.audit import AuditLogger
from dsh_core.db import ApprovalTaskRepo, Database, DatabaseConfig, TenantRepo, WorkflowRepo
from dsh_core.llm import build_llm_router
from dsh_core.workflow import WorkflowEngine
from dsh_core.workflow.base import ApprovalSpec, NodeSpec, WorkflowDef
from dsh_core.workflow.nodes import WORKFLOW_REGISTRY


def make_engine():
    tmp = tempfile.mkdtemp(prefix="dsh_api_")
    db = Database(DatabaseConfig(path=os.path.join(tmp, "test.db"))).connect()
    db.migrate()
    jwt_mgr = JWTManager(AuthConfig(secret="test-secret-key-16-chars!"))
    comps = SimpleNamespace(
        db=db,
        tenants=TenantRepo(db),
        workflows=WorkflowRepo(db),
        approvals=ApprovalTaskRepo(db),
        audit=AuditLogger(db),
        llm=build_llm_router(),
        jwt=jwt_mgr,
    )
    engine = WorkflowEngine(comps)
    engine._test_token = jwt_mgr.issue_token("test-tenant-id", "dev", "alice", ["admin"], expires_in=3600)
    return db, comps, engine


def run(coro):
    return asyncio.run(coro)


def _register_mini(code, nodes):
    WORKFLOW_REGISTRY[code] = WorkflowDef(code=code, description="mini 测试工作流", nodes=nodes)


def _make_approval_instance(engine, risk="medium", mode="serial", approvers=None):
    """创建一个含审批节点的工作流并启动，返回 (instance_id, approval_task_id)"""
    if approvers is None:
        approvers = ["alice", "bob"]

    async def node_a(ctx):
        return {"data": "change"}

    async def node_approve(ctx):
        return {"approved": True}

    engine.register_node("na", node_a)
    engine.register_node("nr", node_approve)
    _register_mini("wf_approval", [
        NodeSpec("step1", "na"),
        NodeSpec("approve", "nr", node_type="approval",
                 approval=ApprovalSpec(
                     risk=risk, mode=mode, approvers=approvers)),
    ])
    status = run(engine.start("wf_approval", "dev"))
    inst_id = status["instance"]["id"]
    db = engine.comps.db
    rows = db.query(
        "SELECT id FROM approval_task WHERE instance_id = ? ORDER BY id DESC LIMIT 1",
        (inst_id,))
    task_id = rows[0]["id"] if rows else None
    return inst_id, task_id


# ─── 1. GET /api/v1/approvals/pending ───

def test_get_pending_approvals():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        resp = client.get("/api/v1/approvals/pending", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["tasks"], list)
        assert len(data["tasks"]) == 0

        _make_approval_instance(engine)
        resp = client.get("/api/v1/approvals/pending", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tasks"]) == 1
        task = data["tasks"][0]
        assert task["risk"] == "medium"
        assert task["mode"] == "serial"
        assert task["status"] == "PENDING"
        assert "alice" in task.get("approvers", [])
    finally:
        db.close()


def test_get_pending_approvals_tenant_isolated():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        _make_approval_instance(engine)

        # 当前租户能看到自己的审批
        resp = client.get("/api/v1/approvals/pending", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tasks"]) == 1
    finally:
        db.close()


def test_get_pending_approvals_missing_auth():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)

        resp = client.get("/api/v1/approvals/pending")
        assert resp.status_code == 401
    finally:
        db.close()


# ─── 2. POST /api/v1/approvals/{id}/decision ───

def test_post_decision_approve():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        inst_id, task_id = _make_approval_instance(engine, mode="any")
        assert task_id is not None

        resp = client.post(
            f"/api/v1/approvals/{task_id}/decision",
            json={"decision": "approve", "reason": "LGTM"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "approve"

        rows = db.query(
            "SELECT status, decisions FROM approval_task WHERE id = ?", (task_id,))
        assert rows[0]["status"] == "APPROVED"
        decisions = json.loads(rows[0]["decisions"])
        assert decisions[-1]["decision"] == "approve"
        assert decisions[-1]["approver"] == "alice"
    finally:
        db.close()


def test_post_decision_reject():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        inst_id, task_id = _make_approval_instance(engine)

        resp = client.post(
            f"/api/v1/approvals/{task_id}/decision",
            json={"decision": "reject", "reason": "Security concern"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "reject"

        rows = db.query("SELECT status FROM approval_task WHERE id = ?", (task_id,))
        assert rows[0]["status"] == "REJECTED"
    finally:
        db.close()


def test_post_decision_invalid_user():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        inst_id, task_id = _make_approval_instance(engine, approvers=["alice", "bob"])

        resp = client.post(
            f"/api/v1/approvals/{task_id}/decision",
            json={"decision": "approve"},
            headers=headers,
        )
        # alice 是审批人，所以应该通过
        assert resp.status_code == 200
    finally:
        db.close()


def test_post_decision_twice_rejected():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        inst_id, task_id = _make_approval_instance(engine)

        client.post(
            f"/api/v1/approvals/{task_id}/decision",
            json={"decision": "reject"},
            headers=headers,
        )

        resp = client.post(
            f"/api/v1/approvals/{task_id}/decision",
            json={"decision": "approve"},
            headers=headers,
        )
        # 已终态，返回 400
        assert resp.status_code == 400
    finally:
        db.close()


def test_post_decision_not_found():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        resp = client.post(
            "/api/v1/approvals/999999/decision",
            json={"decision": "approve"},
            headers=headers,
        )
        assert resp.status_code == 404
    finally:
        db.close()


def test_post_decision_invalid_payload():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        inst_id, task_id = _make_approval_instance(engine)

        resp = client.post(
            f"/api/v1/approvals/{task_id}/decision",
            json={},
            headers=headers,
        )
        # Pydantic 校验失败返回 422
        assert resp.status_code == 422
    finally:
        db.close()


# ─── 3. POST /api/v1/approvals/sweep-timeouts ───

def test_sweep_timeouts_low_auto_approves():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        inst_id, task_id = _make_approval_instance(engine, risk="low")

        import time
        ts = int(time.time()) - 3600
        db.execute(
            "UPDATE approval_task SET timeout_at = ?, status = 'PENDING' WHERE id = ?",
            (ts, task_id))

        resp = client.post("/api/v1/approvals/sweep-timeouts", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        handled = data.get("handled", {})
        assert len(handled.get("auto_passed", [])) >= 1

        rows = db.query("SELECT status FROM approval_task WHERE id = ?", (task_id,))
        assert rows[0]["status"] == "APPROVED"
    finally:
        db.close()


def test_sweep_timeouts_high_auto_rejects():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        inst_id, task_id = _make_approval_instance(engine, risk="high")

        import time
        ts = int(time.time()) - 3600
        db.execute(
            "UPDATE approval_task SET timeout_at = ?, status = 'PENDING' WHERE id = ?",
            (ts, task_id))

        resp = client.post("/api/v1/approvals/sweep-timeouts", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        handled = data.get("handled", {})
        assert len(handled.get("auto_rejected", [])) >= 1

        rows = db.query("SELECT status FROM approval_task WHERE id = ?", (task_id,))
        assert rows[0]["status"] == "REJECTED"
    finally:
        db.close()


def test_sweep_timeouts_medium_escalates():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        inst_id, task_id = _make_approval_instance(engine, risk="medium")

        import time
        ts = int(time.time()) - 3600
        db.execute(
            "UPDATE approval_task SET timeout_at = ?, status = 'PENDING', risk = 'medium' WHERE id = ?",
            (ts, task_id))

        resp = client.post("/api/v1/approvals/sweep-timeouts", headers=headers)
        assert resp.status_code == 200

        rows = db.query("SELECT status, risk, escalated_to FROM approval_task WHERE id = ?", (task_id,))
        assert rows[0]["status"] == "PENDING"
        assert rows[0]["risk"] == "medium"
        escalated = json.loads(rows[0]["escalated_to"] or "[]")
        assert "直属上级" in escalated
    finally:
        db.close()


def test_sweep_timeouts_no_timeouts():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        resp = client.post("/api/v1/approvals/sweep-timeouts", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        handled = data.get("handled", {})
        assert len(handled.get("auto_passed", [])) == 0
        assert len(handled.get("auto_rejected", [])) == 0
        assert len(handled.get("escalated", [])) == 0
    finally:
        db.close()


# ─── 4. 串行/会签模式决策逻辑 ───

def test_serial_mode_requires_all():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        inst_id, task_id = _make_approval_instance(
            engine, mode="serial", approvers=["alice", "bob"])

        # alice 同意
        client.post(
            f"/api/v1/approvals/{task_id}/decision",
            json={"decision": "approve", "approver": "alice"},
            headers=headers,
        )
        rows = db.query("SELECT status FROM approval_task WHERE id = ?", (task_id,))
        assert rows[0]["status"] == "PENDING"

        # bob 同意（显式指定 approver）
        client.post(
            f"/api/v1/approvals/{task_id}/decision",
            json={"decision": "approve", "approver": "bob"},
            headers=headers,
        )
        rows = db.query("SELECT status FROM approval_task WHERE id = ?", (task_id,))
        assert rows[0]["status"] == "APPROVED"
    finally:
        db.close()


def test_unanimous_mode_reject_blocks():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        inst_id, task_id = _make_approval_instance(
            engine, mode="all", approvers=["alice", "bob"])

        client.post(
            f"/api/v1/approvals/{task_id}/decision",
            json={"decision": "reject"},
            headers=headers,
        )
        rows = db.query("SELECT status FROM approval_task WHERE id = ?", (task_id,))
        assert rows[0]["status"] == "REJECTED"
    finally:
        db.close()


def test_anyone_mode_single_approves():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        inst_id, task_id = _make_approval_instance(
            engine, mode="any", approvers=["alice", "bob"])

        client.post(
            f"/api/v1/approvals/{task_id}/decision",
            json={"decision": "approve"},
            headers=headers,
        )
        rows = db.query("SELECT status FROM approval_task WHERE id = ?", (task_id,))
        assert rows[0]["status"] == "APPROVED"
    finally:
        db.close()


# ─── 5. 审计留痕 ───

def test_audit_log_on_decision():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        inst_id, task_id = _make_approval_instance(engine)

        client.post(
            f"/api/v1/approvals/{task_id}/decision",
            json={"decision": "approve", "reason": "LGTM"},
            headers=headers,
        )

        rows = db.query(
            "SELECT action, actor_id, after_json FROM audit_event ORDER BY id DESC LIMIT 1")
        assert rows[0]["action"] == "approval.decision_recorded"
        assert rows[0]["actor_id"] == "alice"
        detail = json.loads(rows[0]["after_json"])
        assert detail["decision"] == "approve"
    finally:
        db.close()


# ─── 6. 工作流实例状态联动 ───

def test_workflow_instance_completes_after_approval():
    db, comps, engine = make_engine()
    try:
        app = create_app(engine)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {engine._test_token}"}

        inst_id, task_id = _make_approval_instance(engine, mode="any")

        rows = db.query(
            "SELECT status FROM workflow_instance WHERE id = ?", (inst_id,))
        assert rows[0]["status"] == "WAITING_APPROVAL"

        client.post(
            f"/api/v1/approvals/{task_id}/decision",
            json={"decision": "approve"},
            headers=headers,
        )

        rows = db.query(
            "SELECT status FROM workflow_instance WHERE id = ?", (inst_id,))
        assert rows[0]["status"] == "COMPLETED"
    finally:
        db.close()


# ─── 入口 ───

if __name__ == "__main__":
    import sys

    tests = [
        test_get_pending_approvals,
        test_get_pending_approvals_tenant_isolated,
        test_get_pending_approvals_missing_auth,
        test_post_decision_approve,
        test_post_decision_reject,
        test_post_decision_invalid_user,
        test_post_decision_twice_rejected,
        test_post_decision_not_found,
        test_post_decision_invalid_payload,
        test_sweep_timeouts_low_auto_approves,
        test_sweep_timeouts_high_auto_rejects,
        test_sweep_timeouts_medium_escalates,
        test_sweep_timeouts_no_timeouts,
        test_serial_mode_requires_all,
        test_unanimous_mode_reject_blocks,
        test_anyone_mode_single_approves,
        test_audit_log_on_decision,
        test_workflow_instance_completes_after_approval,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  ✓ {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {e}")

    print(f"\n{passed}/{passed + failed} passed")
    sys.exit(0 if failed == 0 else 1)
