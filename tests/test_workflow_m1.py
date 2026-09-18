"""M1 工作流扩展测试（任务 1.3/1.4）— 黑板并行、写冲突校验、审批协作模式、超时策略

纯 assert 风格，兼容 pytest 与 m0_selftest 直跑。
"""
import asyncio
import os
import tempfile
from types import SimpleNamespace

from dsh_core.audit import AuditLogger
from dsh_core.db import ApprovalTaskRepo, Database, DatabaseConfig, TenantRepo, WorkflowRepo
from dsh_core.llm import build_llm_router
from dsh_core.workflow import WorkflowEngine, WorkflowError
from dsh_core.workflow.base import ApprovalSpec, NodeSpec, WorkflowDef
from dsh_core.workflow.nodes import WORKFLOW_REGISTRY
from tenants.dev.analyzers.rules import scan_patch, summarize_findings
from tenants.dev import workflows as dev_workflows


def make_engine():
    tmp = tempfile.mkdtemp(prefix="dsh_m1_")
    db = Database(DatabaseConfig(path=os.path.join(tmp, "test.db"))).connect()
    db.migrate()
    comps = SimpleNamespace(
        db=db,
        tenants=TenantRepo(db),
        workflows=WorkflowRepo(db),
        approvals=ApprovalTaskRepo(db),
        audit=AuditLogger(db),
        llm=build_llm_router(),
    )
    return db, WorkflowEngine(comps)


def run(coro):
    return asyncio.run(coro)


def _register_mini(code, nodes):
    WORKFLOW_REGISTRY[code] = WorkflowDef(code=code, description="mini 测试工作流", nodes=nodes)


# ─── 黑板式并行（任务 1.3）───

def test_blackboard_parallel_merges_partitions():
    db, engine = make_engine()
    try:
        run_count = {"n": 0}

        async def node_a(ctx):
            run_count["n"] += 1
            return {"a": "A1"}

        async def node_b(ctx):
            run_count["n"] += 1
            return {"b": "B2"}

        async def node_join(ctx):
            return {"joined": dict(ctx.blackboard or {})}

        engine.register_node("mb_a", node_a)
        engine.register_node("mb_b", node_b)
        engine.register_node("mb_join", node_join)
        _register_mini("mini_blackboard", [
            NodeSpec("split_a", "mb_a", parallel_group="p", write_keys=["a"]),
            NodeSpec("split_b", "mb_b", parallel_group="p", write_keys=["b"]),
            NodeSpec("join", "mb_join", read_keys=["a", "b"]),
        ])
        status = run(engine.start("mini_blackboard", "dev"))
        inst, steps = status["instance"], status["steps"]
        assert inst["status"] == "COMPLETED"
        assert all(s["status"] == "SUCCEEDED" for s in steps)
        # 黑板分区合并 + 持久化到 context.blackboard
        assert inst["context"]["blackboard"] == {"a": "A1", "b": "B2"}
        join_output = next(s for s in steps if s["node_code"] == "join")["output"]
        assert join_output["joined"] == {"a": "A1", "b": "B2"}
        # 并行组审计
        actions = [e["action"] for e in db.query(
            "SELECT action FROM audit_event WHERE trace_id = ?", (inst["id"],))]
        assert "blackboard.completed" in actions
    finally:
        db.close()


def test_write_conflict_rejected_at_definition():
    db, engine = make_engine()
    try:
        engine.register_node("mb_a", lambda ctx: None)
        engine.register_node("mb_b", lambda ctx: None)
        WORKFLOW_REGISTRY["mini_conflict"] = WorkflowDef(
            code="mini_conflict", description="写冲突", nodes=[
                NodeSpec("n1", "mb_a", parallel_group="p", write_keys=["same"]),
                NodeSpec("n2", "mb_b", parallel_group="p", write_keys=["same"]),
            ])
        raised = False
        try:
            run(engine.start("mini_conflict", "dev"))
        except WorkflowError as exc:
            raised = True
            assert "写冲突" in str(exc) and "same" in str(exc)
        assert raised, "同并行组 write_keys 互斥校验应阻止启动"
    finally:
        db.close()


# ─── 审批协作模式（任务 1.4，§8.2.3）───

def test_approval_any_mode_single_approver_converges():
    db, engine = make_engine()
    try:
        WORKFLOW_REGISTRY["mini_approval_any"] = WorkflowDef(
            code="mini_approval_any", description="或签", nodes=[
                NodeSpec("gate", "noop", node_type="approval",
                         approval=ApprovalSpec(approvers=["u1", "u2"], mode="any", risk="low")),
                NodeSpec("after", "echo"),
            ])
        status = run(engine.start("mini_approval_any", "dev"))
        assert status["instance"]["status"] == "WAITING_APPROVAL"
        tasks = engine.list_pending_approvals("dev")
        assert len(tasks) == 1 and tasks[0]["mode"] == "any"
        # 或签：任一人通过即收敛，后续节点继续执行
        status = run(engine.approve_task(tasks[0]["id"], True, "u2"))
        assert status["instance"]["status"] == "COMPLETED"
        task = engine.approvals.get_task(tasks[0]["id"])
        assert task["status"] == "APPROVED"
        decisions = [d["approver"] for d in task["decisions"]]
        assert decisions == ["u2"]
    finally:
        db.close()


def test_approval_all_mode_requires_unanimous():
    db, engine = make_engine()
    try:
        WORKFLOW_REGISTRY["mini_approval_all"] = WorkflowDef(
            code="mini_approval_all", description="会签", nodes=[
                NodeSpec("gate", "noop", node_type="approval",
                         approval=ApprovalSpec(approvers=["u1", "u2"], mode="all", risk="medium")),
            ])
        status = run(engine.start("mini_approval_all", "dev"))
        task = engine.list_pending_approvals("dev")[0]
        # 第一人通过 → 未收敛，实例仍等待
        status = run(engine.approve_task(task["id"], True, "u1"))
        assert status["instance"]["status"] == "WAITING_APPROVAL"
        assert engine.approvals.get_task(task["id"])["status"] == "PENDING"
        # 第二人通过 → 会签收敛
        status = run(engine.approve_task(task["id"], True, "u2"))
        assert status["instance"]["status"] == "COMPLETED"
        assert engine.approvals.get_task(task["id"])["status"] == "APPROVED"
    finally:
        db.close()


def test_approval_all_mode_any_rejection_rejects():
    db, engine = make_engine()
    try:
        WORKFLOW_REGISTRY["mini_approval_all_rej"] = WorkflowDef(
            code="mini_approval_all_rej", description="会签驳回", nodes=[
                NodeSpec("gate", "noop", node_type="approval",
                         approval=ApprovalSpec(approvers=["u1", "u2"], mode="all", risk="medium")),
            ])
        run(engine.start("mini_approval_all_rej", "dev"))
        task = engine.list_pending_approvals("dev")[0]
        status = run(engine.approve_task(task["id"], False, "u2", reason="方案不可行"))
        assert status["instance"]["status"] == "CANCELLED"
        assert engine.approvals.get_task(task["id"])["status"] == "REJECTED"
    finally:
        db.close()


def test_approval_serial_enforces_order():
    db, engine = make_engine()
    try:
        WORKFLOW_REGISTRY["mini_approval_serial"] = WorkflowDef(
            code="mini_approval_serial", description="串行", nodes=[
                NodeSpec("gate", "noop", node_type="approval",
                         approval=ApprovalSpec(approvers=["u1", "u2"], mode="serial", risk="low")),
            ])
        run(engine.start("mini_approval_serial", "dev"))
        task = engine.list_pending_approvals("dev")[0]
        # 未轮到 u2 → 拒绝
        raised = False
        try:
            run(engine.approve_task(task["id"], True, "u2"))
        except WorkflowError as exc:
            raised = True
            assert "串行" in str(exc)
        assert raised
        # 按序决策 → 收敛
        run(engine.approve_task(task["id"], True, "u1"))
        status = run(engine.approve_task(task["id"], True, "u2"))
        assert status["instance"]["status"] == "COMPLETED"
    finally:
        db.close()


def test_approver_not_in_list_rejected():
    db, engine = make_engine()
    try:
        WORKFLOW_REGISTRY["mini_approval_auth"] = WorkflowDef(
            code="mini_approval_auth", description="越权", nodes=[
                NodeSpec("gate", "noop", node_type="approval",
                         approval=ApprovalSpec(approvers=["u1"], mode="any", risk="low")),
            ])
        run(engine.start("mini_approval_auth", "dev"))
        task = engine.list_pending_approvals("dev")[0]
        raised = False
        try:
            run(engine.approve_task(task["id"], True, "mallory"))
        except WorkflowError as exc:
            raised = True
            assert "mallory" in str(exc)
        assert raised, "不在审批人列表的用户不得决策（越权防护）"
    finally:
        db.close()


# ─── 超时策略（任务 1.4，§8.2.2）───

def _make_due_task(engine, workflow_code, risk):
    WORKFLOW_REGISTRY[workflow_code] = WorkflowDef(
        code=workflow_code, description="超时测试", nodes=[
            NodeSpec("gate", "noop", node_type="approval",
                     approval=ApprovalSpec(approvers=["u1"], mode="any", risk=risk)),
        ])
    status = run(engine.start(workflow_code, "dev"))
    task = engine.list_pending_approvals("dev")[0]
    # 把超时时间拨到过去 → due
    engine.approvals.extend_timeout(task["id"], "2020-01-01T00:00:00Z")
    return task


def test_timeout_low_risk_auto_passes():
    db, engine = make_engine()
    try:
        task = _make_due_task(engine, "mini_to_low", "low")
        handled = run(engine.sweep_timeouts())
        assert task["id"] in handled["auto_passed"]
        assert engine.approvals.get_task(task["id"])["status"] == "APPROVED"
        assert engine.status(task["instance_id"])["instance"]["status"] == "COMPLETED"
        actions = [e["action"] for e in db.query(
            "SELECT action FROM audit_event WHERE trace_id = ?", (task["instance_id"],))]
        assert "approval.timeout_auto_pass" in actions
    finally:
        db.close()


def test_timeout_high_risk_auto_rejects():
    db, engine = make_engine()
    try:
        task = _make_due_task(engine, "mini_to_high", "high")
        handled = run(engine.sweep_timeouts())
        assert task["id"] in handled["auto_rejected"]
        assert engine.approvals.get_task(task["id"])["status"] == "REJECTED"
        assert engine.status(task["instance_id"])["instance"]["status"] == "CANCELLED"
        actions = [e["action"] for e in db.query(
            "SELECT action FROM audit_event WHERE trace_id = ?", (task["instance_id"],))]
        assert "approval.timeout_auto_reject" in actions
    finally:
        db.close()


def test_timeout_medium_risk_escalates_and_stays_pending():
    db, engine = make_engine()
    try:
        task = _make_due_task(engine, "mini_to_med", "medium")
        handled = run(engine.sweep_timeouts())
        assert task["id"] in handled["escalated"]
        fresh = engine.approvals.get_task(task["id"])
        assert fresh["status"] == "PENDING"                       # 保持挂起
        assert fresh["escalated_to"]                              # 升级链前移
        assert fresh["timeout_at"] > "2020-01-02"                 # 顺延了窗口
        actions = [e["action"] for e in db.query(
            "SELECT action FROM audit_event WHERE trace_id = ?", (task["instance_id"],))]
        assert "approval.escalated" in actions
    finally:
        db.close()


# ─── 规则扫描器（任务 1.2）───

def test_rule_scanner_hits_and_line_numbers():
    patch = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,2 +1,4 @@\n"
        " import os\n"
        "+password = \"supersecret123\"\n"
        "+result = eval(user_input)\n"
        " x = 1\n"
        "+except :\n"
        "+# TODO fix later\n"
    )
    findings = scan_patch([{"path": "app.py", "additions": 4, "deletions": 0, "patch": patch}])
    codes = {f["code"] for f in findings}
    assert {"HARDCODED_SECRET", "PY_EVAL_EXEC", "BARE_EXCEPT", "TODO_FIXME"} <= codes
    by_code = {f["code"]: f for f in findings}
    assert by_code["HARDCODED_SECRET"]["line"] == 2   # hunk 新文件行号推算
    assert by_code["PY_EVAL_EXEC"]["severity"] == "high"
    summary = summarize_findings(findings)
    assert summary["total"] == len(findings) and summary["high"] >= 2


def test_rule_scanner_clean_diff_no_findings():
    findings = scan_patch([{"path": "a.py", "additions": 1, "deletions": 0,
                            "patch": "diff --git a/a.py b/a.py\n@@ -1 +1,2 @@\n import os\n+value = compute()\n"}])
    assert findings == []


# ─── D1 端到端（任务 1.6 自举工作流，simulated LLM）───

def test_d1_code_review_end_to_end():
    db, engine = make_engine()
    try:
        dev_workflows.register(engine)
        status = run(engine.start("d1_code_review", "dev", {
            "repo_path": dev_workflows.DEFAULT_REPO,
            # git 空树对象：任意仓库可解析，与提交历史无关（本仓库可能只有 root commit）
            "base": "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        }))
        inst = status["instance"]
        # D1 终点是低风险审批 → 停在 WAITING_APPROVAL
        assert inst["status"] == "WAITING_APPROVAL", inst.get("error")
        board = inst["context"]["blackboard"]
        assert "diff" in board and board["diff"]["files"]
        assert isinstance(board.get("rule_findings"), list)
        assert isinstance(board.get("llm_findings"), list)
        assert board["report"]["path"] and os.path.exists(board["report"]["path"])
        # 并行组两个成员都成功
        steps = status["steps"]
        for code in ("rule_scan", "llm_review"):
            step = next(s for s in steps if s["node_code"] == code)
            assert step["status"] == "SUCCEEDED"
        # 审批通过 → 实例完成
        task = engine.list_pending_approvals("dev")[0]
        status = run(engine.approve_task(task["id"], True, "admin"))
        assert status["instance"]["status"] == "COMPLETED"
    finally:
        db.close()
