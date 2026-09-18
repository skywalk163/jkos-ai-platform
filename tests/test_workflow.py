"""工作流引擎单元测试（任务 0.2）— 纯 assert，兼容 pytest 与 m0_selftest 直跑"""
import asyncio
import os
import tempfile
from types import SimpleNamespace

from jkos_core.audit import AuditLogger
from jkos_core.db import Database, DatabaseConfig, TenantRepo, WorkflowRepo
from jkos_core.llm import build_llm_router
from jkos_core.workflow import WorkflowEngine, WorkflowError, get_workflow


def make_engine():
    tmp = tempfile.mkdtemp(prefix="dsh_wf_")
    db = Database(DatabaseConfig(path=os.path.join(tmp, "test.db"))).connect()
    db.migrate()
    comps = SimpleNamespace(
        db=db,
        tenants=TenantRepo(db),
        workflows=WorkflowRepo(db),
        audit=AuditLogger(db),
        llm=build_llm_router(),   # 无 API Key → 纯模拟供应商，结果确定
    )
    return db, WorkflowEngine(comps)


def run(coro):
    return asyncio.run(coro)


def test_unknown_workflow_rejected():
    raised = False
    try:
        get_workflow("nope")
    except WorkflowError as e:
        raised = True
        assert "nope" in str(e)
    assert raised


def test_hello_workflow_completes():
    db, engine = make_engine()
    try:
        status = run(engine.start("hello", "dev", {"prompt": "用一句话介绍 SQLite"}))
        inst, steps = status["instance"], status["steps"]
        assert inst["status"] == "COMPLETED"
        assert inst["finished_at"] is not None
        assert len(steps) == 3
        assert all(s["status"] == "SUCCEEDED" for s in steps)
        # LLM 节点产出内容（模拟供应商）
        llm_step = next(s for s in steps if s["node_code"] == "ask_llm")
        assert llm_step["output"]["content"]
        assert llm_step["output"]["provider"] == "simulated"
        # 实例 result = 末节点输出（echo 链）
        assert inst["result"]["echo"]["content"] == llm_step["output"]["content"]
        # 审计埋点齐全：started / step_succeeded×3 / completed
        events = [e["action"] for e in db.query(
            "SELECT action FROM audit_event WHERE trace_id = ? ORDER BY occurred_at", (inst["id"],)
        )]
        assert events[0] == "workflow.started"
        assert events.count("workflow.step_succeeded") == 3
        assert events[-1] == "workflow.completed"
    finally:
        db.close()


def test_fail_node_marks_failed():
    db, engine = make_engine()
    try:
        status = run(engine.start("hello_fail", "dev", {"fail_reason": "测试注入失败"}))
        inst, steps = status["instance"], status["steps"]
        assert inst["status"] == "FAILED"
        assert inst["finished_at"] is not None
        boom = next(s for s in steps if s["node_code"] == "boom")
        assert boom["status"] == "FAILED"
        assert boom["error"]["type"] == "WorkflowStepError"
        assert "测试注入失败" in boom["error"]["message"]
        greet = next(s for s in steps if s["node_code"] == "greet")
        assert greet["status"] == "SUCCEEDED"   # 失败前的步骤已完成
        actions = [e["action"] for e in db.query(
            "SELECT action FROM audit_event WHERE trace_id = ?", (inst["id"],)
        )]
        assert "workflow.step_failed" in actions and "workflow.failed" in actions
    finally:
        db.close()


def test_crash_and_resume():
    db, engine = make_engine()
    try:
        # 首次运行：悬挂在 crash 点（等价 kill -9 遗留态）
        status = run(engine.start("hello_crash", "dev", {}))
        inst = status["instance"]
        assert inst["status"] == "RUNNING"
        assert inst["current_step"] == "danger"
        danger = next(s for s in status["steps"] if s["node_code"] == "danger")
        assert danger["status"] == "RUNNING" and danger["attempts"] == 1
        # resume：RUNNING 步骤重置 PENDING → 重跑成功 → 完成
        status2 = run(engine.resume(inst["id"]))
        inst2, steps2 = status2["instance"], status2["steps"]
        assert inst2["status"] == "COMPLETED"
        assert len(steps2) == 3   # 无重复 seq（幂等续跑）
        danger2 = next(s for s in steps2 if s["node_code"] == "danger")
        assert danger2["status"] == "SUCCEEDED" and danger2["attempts"] == 2
        assert danger2["output"]["recovered"] is True
        actions = [e["action"] for e in db.query(
            "SELECT action FROM audit_event WHERE trace_id = ? ORDER BY occurred_at", (inst["id"],)
        )]
        assert "workflow.crashed" in actions and "workflow.resumed" in actions
    finally:
        db.close()


def test_terminal_instance_cannot_resume_or_cancel():
    db, engine = make_engine()
    try:
        status = run(engine.start("hello", "dev", {}))
        inst_id = status["instance"]["id"]
        for op in (engine.resume(inst_id), engine.cancel(inst_id)):
            raised = False
            try:
                run(op)
            except WorkflowError as e:
                raised = True
                assert "终态" in str(e)
            assert raised
    finally:
        db.close()


def test_approval_flow_approve_and_reject():
    db, engine = make_engine()
    try:
        # 同意路径
        s1 = run(engine.start("hello_approval", "dev", {}))
        assert s1["instance"]["status"] == "WAITING_APPROVAL"
        review = next(s for s in s1["steps"] if s["node_code"] == "manual_review")
        assert review["status"] == "WAITING"
        done = run(engine.approve(s1["instance"]["id"], True, decided_by="boss", reason="同意"))
        assert done["instance"]["status"] == "COMPLETED"
        review2 = next(s for s in done["steps"] if s["node_code"] == "manual_review")
        assert review2["status"] == "SUCCEEDED"
        assert review2["output"]["decided_by"] == "boss"
        # 驳回路径
        s2 = run(engine.start("hello_approval", "dev", {}))
        rejected = run(engine.approve(s2["instance"]["id"], False, decided_by="boss"))
        assert rejected["instance"]["status"] == "CANCELLED"
        review3 = next(s for s in rejected["steps"] if s["node_code"] == "manual_review")
        assert review3["status"] == "SKIPPED"
        actions = [e["action"] for e in db.query(
            "SELECT action FROM audit_event WHERE trace_id = ?", (s2["instance"]["id"],)
        )]
        assert "workflow.rejected" in actions and "workflow.cancelled" in actions
    finally:
        db.close()


def test_list_instances_filters():
    db, engine = make_engine()
    try:
        run(engine.start("hello", "dev", {"tag": "list-1"}))
        run(engine.start("hello_fail", "dev", {"tag": "list-2"}))
        rows = engine.list_instances(tenant_code="dev")
        assert len(rows) >= 2
        failed = engine.list_instances(tenant_code="dev", status="FAILED")
        assert all(r["status"] == "FAILED" for r in failed)
        assert any(r["context"].get("tag") == "list-2" for r in failed)
    finally:
        db.close()
