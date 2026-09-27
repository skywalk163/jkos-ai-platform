"""工作流引擎补充覆盖测试（M6 任务 6.1）— 纯 assert，兼容 pytest 直跑

目标：jkos_core/workflow/engine.py 覆盖率 88% → 95%（M6 任务 6.1）。
本文件覆盖 dedupe 后仍漏测的分支（engine.py 当前 323 stmts / 38 missed）：

  - start 租户不存在（91）
  - approve 前置校验：非等待审批态（123）、WAITING 步骤缺失（128）
  - approve → approve_task 任务化转发路径（130-132）
  - approve_task 前置校验：仓储未注入（145）/ 任务不存在（148）/ 任务已终态（150）
  - sweep_timeouts：仓储未注入（194）、实例已终态任务取消（199-200）、
    升级链耗尽顺延（244-249）
  - cancel 未决审批任务取消循环（261）
  - list_instances / list_pending_approvals 租户不存在（277 / 290）、
    list_pending_approvals 仓储未注入（285）
  - 定义期校验：审批节点入并行组（303）
  - 并行批次：含失败节点中止（338/393/413）、全部崩溃悬挂（337-338/396/411-412/
    508-509 空黑板合并）、末尾批次失败（364-366）
  - resume 遇 STEP_WAITING 回到等待审批（351-353）
  - 未注册节点处理器 → FAILED（433）
  - _notify 旁路：发送成功（547-549）/ 发送异常仅告警（550-551）
  - _must_get 实例不存在（566）
"""
import asyncio
import os
import tempfile
from types import SimpleNamespace

from jkos_core.audit import AuditLogger
from jkos_core.db import (
    INSTANCE_CANCELLED,
    INSTANCE_WAITING_APPROVAL,
    ApprovalTaskRepo,
    Database,
    DatabaseConfig,
    TenantRepo,
    WorkflowRepo,
)
from jkos_core.llm import build_llm_router
from jkos_core.workflow import WorkflowEngine, WorkflowError
from jkos_core.workflow.base import ApprovalSpec, NodeSpec, WorkflowDef
from jkos_core.workflow.nodes import WORKFLOW_REGISTRY


def make_engine():
    """标准引擎（注入 approvals，不注入 notify）"""
    tmp = tempfile.mkdtemp(prefix="dsh_m3_")
    db = Database(DatabaseConfig(path=os.path.join(tmp, "test.db"))).connect()
    db.migrate()
    comps = SimpleNamespace(
        db=db,
        tenants=TenantRepo(db),
        workflows=WorkflowRepo(db),
        approvals=ApprovalTaskRepo(db),
        audit=AuditLogger(db),
        llm=build_llm_router(),   # 无 API Key → 纯模拟供应商，结果确定
        jwt=None,
    )
    return db, WorkflowEngine(comps)


def make_engine_no_approvals():
    """兼容 M0 的引擎：未注入 approvals/notify（approval 节点走旧数据兜底）"""
    db, engine = make_engine()
    engine.approvals = None
    return db, engine


def make_engine_notify(notify_impl):
    """注入通知后端（_notify 旁路测试）"""
    db, engine = make_engine()
    engine.notify = notify_impl
    return db, engine


def run(coro):
    return asyncio.run(coro)


class FakeNotify:
    """记录调用的通知后端；raise_on_send=True 模拟通道故障"""

    def __init__(self, raise_on_send=False):
        self.raise_on_send = raise_on_send
        self.calls = []

    def send(self, event, title="", body="", tenant_id=None, ref_type=None, ref_id=None):
        if self.raise_on_send:
            raise RuntimeError("通知通道故障（模拟）")
        self.calls.append({"event": event, "title": title, "body": body,
                           "tenant_id": tenant_id, "ref_type": ref_type, "ref_id": ref_id})


def _register_approval(code, risk="medium", mode="any", approvers=None, timeout_hours=None):
    """注册单审批节点 mini 工作流（WAITING_APPROVAL 后无后续节点）"""
    WORKFLOW_REGISTRY[code] = WorkflowDef(
        code=code,
        description="审批 mini",
        nodes=[NodeSpec("approve_gate", "noop", node_type="approval",
                        approval=ApprovalSpec(approvers=approvers or [], mode=mode,
                                              risk=risk, timeout_hours=timeout_hours))],
    )


def _due_approval_task(engine, code, risk="medium"):
    """启动审批工作流 → 过期的审批任务"""
    status = run(engine.start(code, "dev", {}))
    task = engine.list_pending_approvals("dev")[0]
    engine.approvals.extend_timeout(task["id"], "2020-01-01T00:00:00Z")
    return status["instance"]["id"], task


# ── 分支覆盖 1：start 前置校验 ──

def test_start_unknown_tenant_rejected():
    db, engine = make_engine()
    try:
        raised = False
        try:
            run(engine.start("hello", "nope_tenant", {}))
        except WorkflowError as e:
            raised = True
            assert "租户不存在: nope_tenant" in str(e)
        assert raised
    finally:
        db.close()


# ── 分支覆盖 2：approve 前置校验 ──

def test_approve_non_waiting_instance_rejected():
    db, engine = make_engine()
    try:
        status = run(engine.start("hello", "dev", {}))
        raised = False
        try:
            run(engine.approve(status["instance"]["id"], True, decided_by="admin"))
        except WorkflowError as e:
            raised = True
            assert "不在等待审批状态" in str(e)
        assert raised
    finally:
        db.close()


def test_approve_no_waiting_step_rejected():
    # 实例状态被置为 WAITING_APPROVAL 但没有任何 WAITING 步骤（脏数据兜底）
    db, engine = make_engine()
    try:
        status = run(engine.start("hello", "dev", {}))
        inst_id = status["instance"]["id"]
        engine.repo.update_status(inst_id, INSTANCE_WAITING_APPROVAL, current_step="x")
        raised = False
        try:
            run(engine.approve(inst_id, True, decided_by="admin"))
        except WorkflowError as e:
            raised = True
            assert "找不到 WAITING 状态的审批步骤" in str(e)
        assert raised
    finally:
        db.close()


def test_approve_forwards_to_approval_task():
    # M0 兼容入口转发到任务化审批（130-132）
    db, engine = make_engine()
    try:
        _register_approval("m3_approval_any", risk="low")
        status = run(engine.start("m3_approval_any", "dev", {}))
        inst_id = status["instance"]["id"]
        assert status["instance"]["status"] == "WAITING_APPROVAL"
        done = run(engine.approve(inst_id, True, decided_by="admin", reason="同意"))
        assert done["instance"]["status"] == "COMPLETED"
        gate = next(s for s in done["steps"] if s["node_code"] == "approve_gate")
        assert gate["status"] == "SUCCEEDED"
        assert gate["output"]["decision"] == "approved"
        assert gate["output"]["decided_by"] == "admin"
    finally:
        db.close()


# ── 分支覆盖 3：approve_task 前置校验 ──

def test_approve_task_without_repo_rejected():
    db, engine = make_engine_no_approvals()
    try:
        raised = False
        try:
            run(engine.approve_task("whatever", True, approver="admin"))
        except WorkflowError as e:
            raised = True
            assert "审批仓储未注入" in str(e)
        assert raised
    finally:
        db.close()


def test_approve_task_unknown_task_rejected():
    db, engine = make_engine()
    try:
        raised = False
        try:
            run(engine.approve_task("no-such-task", True, approver="admin"))
        except WorkflowError as e:
            raised = True
            assert "审批任务不存在: no-such-task" in str(e)
        assert raised
    finally:
        db.close()


def test_approve_task_terminal_task_rejected():
    db, engine = make_engine()
    try:
        _register_approval("m3_approval_once", risk="low")
        status = run(engine.start("m3_approval_once", "dev", {}))
        task = engine.list_pending_approvals("dev")[0]
        done = run(engine.approve_task(task["id"], True, approver="admin"))
        assert done["instance"]["status"] == "COMPLETED"
        raised = False
        try:
            run(engine.approve_task(task["id"], False, approver="admin"))
        except WorkflowError as e:
            raised = True
            assert "审批任务已终态" in str(e)
        assert raised
    finally:
        db.close()


# ── 分支覆盖 4：sweep_timeouts 边角 ──

def test_sweep_timeouts_without_repo_returns_empty():
    db, engine = make_engine_no_approvals()
    try:
        handled = run(engine.sweep_timeouts())
        assert handled == {"auto_passed": [], "auto_rejected": [], "escalated": [], "agent_timed_out": []}
    finally:
        db.close()


def test_sweep_timeouts_terminal_instance_cancels_task():
    # 实例已终态（外部取消）但审批任务仍 PENDING → 任务补取消，不计入 handled
    db, engine = make_engine()
    try:
        _register_approval("m3_approval_stale", risk="medium")
        inst_id, task = _due_approval_task(engine, "m3_approval_stale")
        engine.repo.update_status(inst_id, INSTANCE_CANCELLED, error={"reason": "外部取消"})
        handled = run(engine.sweep_timeouts())
        assert handled == {"auto_passed": [], "auto_rejected": [], "escalated": [], "agent_timed_out": []}
        assert engine.approvals.get_task(task["id"])["status"] == "CANCELLED"
    finally:
        db.close()


def test_sweep_timeouts_escalation_chain_exhausted():
    # medium 升级链 [直属上级, 部门总监] 两级全部升级仍无响应 → 顺延窗口 + 审计
    db, engine = make_engine()
    try:
        _register_approval("m3_approval_exhaust", risk="medium")
        inst_id, task = _due_approval_task(engine, "m3_approval_exhaust")
        engine.approvals.escalate(task["id"], ["直属上级", "部门总监"])
        handled = run(engine.sweep_timeouts())
        assert handled["escalated"] == [task["id"]]
        assert handled["auto_passed"] == [] and handled["auto_rejected"] == []
        fresh = engine.approvals.get_task(task["id"])
        assert fresh["status"] == "PENDING"
        assert fresh["escalated_to"] == ["直属上级", "部门总监"]
        actions = [e["action"] for e in db.query(
            "SELECT action FROM audit_event WHERE trace_id = ?", (inst_id,)
        )]
        assert "approval.escalation_exhausted" in actions
    finally:
        db.close()


# ── 分支覆盖 5：cancel 未决审批任务 ──

def test_cancel_resolves_pending_approval_task():
    db, engine = make_engine()
    try:
        _register_approval("m3_approval_cancel", risk="high")
        status = run(engine.start("m3_approval_cancel", "dev", {}))
        inst_id = status["instance"]["id"]
        task = engine.list_pending_approvals("dev")[0]
        done = run(engine.cancel(inst_id, actor="boss", reason="重新走审批流程"))
        assert done["instance"]["status"] == "CANCELLED"
        assert engine.approvals.get_task(task["id"])["status"] == "CANCELLED"
    finally:
        db.close()


# ── 分支覆盖 6：列表接口租户/仓储校验 ──

def test_list_instances_unknown_tenant_rejected():
    db, engine = make_engine()
    try:
        raised = False
        try:
            engine.list_instances(tenant_code="nope_tenant")
        except WorkflowError as e:
            raised = True
            assert "租户不存在: nope_tenant" in str(e)
        assert raised
    finally:
        db.close()


def test_list_pending_approvals_without_repo_empty():
    db, engine = make_engine_no_approvals()
    try:
        assert engine.list_pending_approvals() == []
    finally:
        db.close()


def test_list_pending_approvals_unknown_tenant_rejected():
    db, engine = make_engine()
    try:
        raised = False
        try:
            engine.list_pending_approvals(tenant_code="nope_tenant")
        except WorkflowError as e:
            raised = True
            assert "租户不存在: nope_tenant" in str(e)
        assert raised
    finally:
        db.close()


# ── 分支覆盖 7：定义期校验（审批节点入并行组）──

def test_definition_rejects_approval_in_parallel_group():
    db, engine = make_engine()
    try:
        WORKFLOW_REGISTRY["m3_bad_parallel_approval"] = WorkflowDef(
            code="m3_bad_parallel_approval",
            description="非法：审批节点加入并行组",
            nodes=[NodeSpec("gate", "noop", node_type="approval", parallel_group="g1"),
                   NodeSpec("peer", "noop", parallel_group="g1")],
        )
        raised = False
        try:
            run(engine.start("m3_bad_parallel_approval", "dev", {}))
        except WorkflowError as e:
            raised = True
            assert "不可加入并行组" in str(e)
        assert raised
    finally:
        db.close()


# ── 分支覆盖 8：并行批次执行路径 ──

def test_parallel_batch_fail_aborts_midflow():
    # 并行批次含失败节点：收敛时 FAILED 快照直接返回（338/393/413）
    db, engine = make_engine()
    try:
        WORKFLOW_REGISTRY["m3_batch_fail_mid"] = WorkflowDef(
            code="m3_batch_fail_mid",
            description="并行批次失败且后面还有节点",
            nodes=[NodeSpec("a1", "noop", parallel_group="g1"),
                   NodeSpec("bad", "fail", parallel_group="g1"),
                   NodeSpec("after", "noop")],
        )
        status = run(engine.start("m3_batch_fail_mid", "dev", {"fail_reason": "并行注入失败"}))
        assert status["instance"]["status"] == "FAILED"
        bad = next(s for s in status["steps"] if s["node_code"] == "bad")
        assert bad["status"] == "FAILED"
        assert "并行注入失败" in bad["error"]["message"]
        # 后续节点未被追加
        assert all(s["node_code"] != "after" for s in status["steps"])
    finally:
        db.close()


def test_parallel_batch_trailing_fail():
    # 末尾并行批次失败：循环结束后收敛返回（364-366）
    db, engine = make_engine()
    try:
        WORKFLOW_REGISTRY["m3_batch_fail_tail"] = WorkflowDef(
            code="m3_batch_fail_tail",
            description="末尾并行批次失败",
            nodes=[NodeSpec("pre", "noop"),
                   NodeSpec("a2", "noop", parallel_group="g2"),
                   NodeSpec("bad2", "fail", parallel_group="g2")],
        )
        status = run(engine.start("m3_batch_fail_tail", "dev", {"fail_reason": "末尾失败"}))
        assert status["instance"]["status"] == "FAILED"
        pre = next(s for s in status["steps"] if s["node_code"] == "pre")
        assert pre["status"] == "SUCCEEDED"
    finally:
        db.close()


def test_parallel_batch_all_crash_then_resume():
    # 并行批次全部模拟崩溃：悬挂 RUNNING（338/396/411-412/508-509 空合并）→ resume 恢复
    db, engine = make_engine()
    try:
        WORKFLOW_REGISTRY["m3_batch_all_crash"] = WorkflowDef(
            code="m3_batch_all_crash",
            description="并行批次全部崩溃",
            nodes=[NodeSpec("c1", "crash", parallel_group="g3"),
                   NodeSpec("c2", "crash", parallel_group="g3"),
                   NodeSpec("after2", "noop")],
        )
        status = run(engine.start("m3_batch_all_crash", "dev", {}))
        inst = status["instance"]
        assert inst["status"] == "RUNNING"
        assert set(s["node_code"] for s in status["steps"]) == {"c1", "c2"}
        assert all(s["status"] == "RUNNING" for s in status["steps"])
        # 崩溃后续跑：attempts 2 → 恢复成功 → 整体完成
        status2 = run(engine.resume(inst["id"]))
        assert status2["instance"]["status"] == "COMPLETED"
        assert all(s["status"] == "SUCCEEDED" for s in status2["steps"])
        assert any(s["node_code"] == "c1" and s["output"]["recovered"] is True for s in status2["steps"])
    finally:
        db.close()


# ── 分支覆盖 9：resume 遇等待步骤 ──

def test_resume_waiting_approval_stays_waiting():
    db, engine = make_engine()
    try:
        status = run(engine.start("hello_approval", "dev", {}))
        inst_id = status["instance"]["id"]
        assert status["instance"]["status"] == "WAITING_APPROVAL"
        again = run(engine.resume(inst_id))
        assert again["instance"]["status"] == "WAITING_APPROVAL"
        assert again["instance"]["current_step"] == "manual_review"
        review = next(s for s in again["steps"] if s["node_code"] == "manual_review")
        assert review["status"] == "WAITING"
    finally:
        db.close()


# ── 分支覆盖 10：未注册处理器 ──

def test_unregistered_handler_fails_instance():
    db, engine = make_engine()
    try:
        WORKFLOW_REGISTRY["m3_ghost_handler"] = WorkflowDef(
            code="m3_ghost_handler",
            description="未注册处理器",
            nodes=[NodeSpec("ghost", "ghost_processor_xyz")],
        )
        status = run(engine.start("m3_ghost_handler", "dev", {}))
        assert status["instance"]["status"] == "FAILED"
        assert "未注册的节点处理器: ghost_processor_xyz" in status["instance"]["error"]["message"]
    finally:
        db.close()


# ── 分支覆盖 11：_notify 旁路 ──

def test_notify_send_success():
    notify = FakeNotify()
    db, engine = make_engine_notify(notify)
    try:
        _register_approval("m3_approval_notify_ok", risk="low")
        status = run(engine.start("m3_approval_notify_ok", "dev", {}))
        assert status["instance"]["status"] == "WAITING_APPROVAL"
        assert len(notify.calls) == 1
        call = notify.calls[0]
        assert call["event"] == "approval.created"
        assert call["ref_type"] == "workflow_instance"
        assert call["ref_id"] == status["instance"]["id"]
        assert call["tenant_id"] is not None
    finally:
        db.close()


def test_notify_send_failure_is_non_fatal():
    notify = FakeNotify(raise_on_send=True)
    db, engine = make_engine_notify(notify)
    try:
        _register_approval("m3_approval_notify_fail", risk="low")
        status = run(engine.start("m3_approval_notify_fail", "dev", {}))
        # 通知失败不影响主流程
        assert status["instance"]["status"] == "WAITING_APPROVAL"
        done = run(engine.approve(status["instance"]["id"], True, decided_by="admin"))
        assert done["instance"]["status"] == "COMPLETED"
    finally:
        db.close()


# ── 分支覆盖 12：_must_get 实例不存在 ──

def test_status_unknown_instance_rejected():
    db, engine = make_engine()
    try:
        raised = False
        try:
            engine.status("ghost-instance")
        except WorkflowError as e:
            raised = True
            assert "工作流实例不存在: ghost-instance" in str(e)
        assert raised
    finally:
        db.close()