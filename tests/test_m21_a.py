"""M21 跨执行边界审批闭环与外部 Agent 互联（任务 21.1 executor SPI）测试。

覆盖：
- engine._enter_executor 派发路径（注册表缺失 / 派发异常 / 租户白名单 / 并行组校验）
- resume_agent 恢复路径（成功合并黑板 / 错误终态 / 未知 task 幂等）
- 事件总线 EXEC.* 契约闭环（RESULT / FAILED / TIMEOUT 事件驱动恢复、DISPATCHED 发布）
- SLA 超时补偿（_sweep_agent_timeouts）
- 状态机（resume 保持 WAITING / cancel 取消）
- 跨执行边界链式：黑板跨边界传递 + 执行器后接审批链闭环

风格对齐 tests/test_workflow_m3.py：纯 assert + asyncio.run + SimpleNamespace
注入 comps + 动态注册 WORKFLOW_REGISTRY + try/finally db.close()。
"""
import asyncio
import os
import tempfile
import time
from types import SimpleNamespace

import pytest

from jkos_core.audit import AuditLogger
from jkos_core.bus import Event, EventTypes, NatsEventBus
from jkos_core.db import (
    INSTANCE_CANCELLED,
    INSTANCE_COMPLETED,
    INSTANCE_FAILED,
    INSTANCE_WAITING_AGENT,
    INSTANCE_WAITING_APPROVAL,
    STEP_SUCCEEDED,
    STEP_WAITING,
    ApprovalTaskRepo,
    Database,
    DatabaseConfig,
    TenantRepo,
    WorkflowRepo,
)
from jkos_core.executors import ExecutorRegistry, MockExecutor
from jkos_core.llm import build_llm_router
from jkos_core.workflow import WorkflowEngine, WorkflowError
from jkos_core.workflow.base import ApprovalSpec, NodeSpec, WorkflowDef
from jkos_core.workflow.nodes import WORKFLOW_REGISTRY


# ─── 辅助设施 ───

def run(coro):
    """运行协程（m3 同款模式）"""
    return asyncio.run(coro)


def make_engine(*, executors=None, bus=None):
    """构造带 M21 可选注入（bus/executors）的引擎"""
    tmp = tempfile.mkdtemp(prefix="dsh_m21_")
    db = Database(DatabaseConfig(path=os.path.join(tmp, "test.db"))).connect()
    db.migrate()
    comps = SimpleNamespace(
        db=db,
        tenants=TenantRepo(db),
        workflows=WorkflowRepo(db),
        approvals=ApprovalTaskRepo(db),
        audit=AuditLogger(db),
        llm=build_llm_router(),
        jwt=None,
        bus=bus,
        executors=executors,
    )
    return db, WorkflowEngine(comps)


def make_bus():
    """内嵌降级事件总线（NATS 不可达 → embedded 模式，_connected=True）"""
    bus = NatsEventBus(nats_url="nats://127.0.0.1:1")
    run(bus.connect())
    return bus


def _register_workflow(code, *nodes):
    WORKFLOW_REGISTRY[code] = WorkflowDef(
        code=code, description="m21 test: " + code, nodes=list(nodes))


def _exec_node(code="delegate", task=None, write_keys=None, sla_hours=1.0,
               parallel_group=None):
    """标准执行器节点（executor="mock"）"""
    return NodeSpec(
        code, "noop", node_type="agent", executor="mock",
        task=task or {"goal": "say_hi"}, sla_hours=sla_hours,
        write_keys=write_keys or ["agent_out"], parallel_group=parallel_group,
    )


def _audit_actions(db, trace_id):
    """读取审计动作序列（兼容 dict / Row / tuple 查询结果）"""
    rows = db.query("SELECT action FROM audit_event WHERE trace_id = ?", (trace_id,))
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return [r["action"] for r in rows]
    return [r[0] for r in rows]


# ─── A. 派发路径（_enter_executor）───

def test_executor_dispatch_enters_waiting_agent():
    registry = ExecutorRegistry()
    mock = MockExecutor()
    registry.register("mock", mock)
    db, engine = make_engine(executors=registry)
    try:
        _register_workflow("m21_exec_basic", _exec_node())
        result = run(engine.start("m21_exec_basic"))
        inst_id = result["instance"]["id"]
        repo = engine.repo

        # 实例挂起等待 Agent
        inst = repo.get_instance(inst_id)
        assert inst["status"] == INSTANCE_WAITING_AGENT
        assert inst["current_step"] == "delegate"

        # 步骤进入 WAITING
        steps = repo.get_steps(inst_id)
        assert len(steps) == 1
        assert steps[0]["node_code"] == "delegate"
        assert steps[0]["status"] == STEP_WAITING

        # 派发记录：payload 透传 task，context 携带 blackboard/sla_hours
        assert len(mock.dispatches) == 1
        dispatch = mock.dispatches[0]
        assert dispatch["payload"]["task"] == {"goal": "say_hi"}
        assert dispatch["payload"]["instance_id"] == inst_id
        assert dispatch["payload"]["node_code"] == "delegate"
        assert "blackboard" in dispatch["context"]
        assert dispatch["context"]["sla_hours"] == 1.0

        # task_id 格式：mock-{n}-{node_code}
        task_id = dispatch["task_id"]
        assert task_id == "mock-1-delegate"

        # 审计：workflow.waiting_agent
        assert "workflow.waiting_agent" in _audit_actions(db, inst_id)
    finally:
        db.close()


def test_executor_without_registry_fails_instance():
    # 不注入 comps.executors → 实例失败
    db, engine = make_engine(executors=None)
    try:
        _register_workflow("m21_exec_no_registry", _exec_node())
        result = run(engine.start("m21_exec_no_registry"))
        inst_id = result["instance"]["id"]
        inst = engine.repo.get_instance(inst_id)
        assert inst["status"] == INSTANCE_FAILED
        assert inst["error"]["type"] == "RuntimeError"
        assert "执行器注册表未注入" in inst["error"]["message"]
        # 步骤失败且 error 含节点
        steps = engine.repo.get_steps(inst_id)
        assert steps[0]["status"] == "FAILED"
        assert steps[0]["error"]["step"] == "delegate"
    finally:
        db.close()


def test_executor_dispatch_error_fails_instance():
    registry = ExecutorRegistry()
    registry.register("mock", MockExecutor(fail=True))
    db, engine = make_engine(executors=registry)
    try:
        _register_workflow("m21_exec_dispatch_fail", _exec_node())
        result = run(engine.start("m21_exec_dispatch_fail"))
        inst_id = result["instance"]["id"]
        inst = engine.repo.get_instance(inst_id)
        assert inst["status"] == INSTANCE_FAILED
        assert inst["error"]["type"] == "ExecutorError"
        assert "mock 派发失败" in inst["error"]["message"]
    finally:
        db.close()


def test_executor_tenant_unauthorized_fails_instance():
    # 白名单 ["acme"]，租户 dev 派发 → 超权终止
    registry = ExecutorRegistry()
    registry.register("mock", MockExecutor(), tenant_codes=["acme"])
    db, engine = make_engine(executors=registry)
    try:
        _register_workflow("m21_exec_unauth", _exec_node())
        result = run(engine.start("m21_exec_unauth"))
        inst_id = result["instance"]["id"]
        inst = engine.repo.get_instance(inst_id)
        assert inst["status"] == INSTANCE_FAILED
        assert inst["error"]["type"] == "ExecutorNotAuthorizedError"
        assert "无权使用" in inst["error"]["message"]
    finally:
        db.close()


def test_executor_whitelisted_tenant_ok():
    registry = ExecutorRegistry()
    registry.register("mock", MockExecutor(), tenant_codes=["dev"])
    db, engine = make_engine(executors=registry)
    try:
        _register_workflow("m21_exec_whitelist", _exec_node())
        result = run(engine.start("m21_exec_whitelist"))
        inst_id = result["instance"]["id"]
        inst = engine.repo.get_instance(inst_id)
        assert inst["status"] == INSTANCE_WAITING_AGENT
    finally:
        db.close()


def test_executor_in_parallel_group_rejected():
    registry = ExecutorRegistry()
    registry.register("mock", MockExecutor())
    db, engine = make_engine(executors=registry)
    try:
        _register_workflow("m21_exec_parallel",
                           _exec_node(parallel_group="g1"))
        with pytest.raises(WorkflowError, match="不可加入并行组"):
            run(engine.start("m21_exec_parallel"))
    finally:
        db.close()


# ─── B. 恢复路径（resume_agent）───

def test_resume_agent_success_completes():
    registry = ExecutorRegistry()
    mock = MockExecutor()
    registry.register("mock", mock)
    db, engine = make_engine(executors=registry)
    try:
        _register_workflow("m21_exec_resume_ok", _exec_node())
        result = run(engine.start("m21_exec_resume_ok"))
        inst_id = result["instance"]["id"]
        task_id = mock.dispatches[0]["task_id"]

        result = run(engine.resume_agent(inst_id, task_id,
                                         result={"agent_out": "hi"}))
        assert result["instance"]["status"] == INSTANCE_COMPLETED

        inst = engine.repo.get_instance(inst_id)
        assert inst["status"] == INSTANCE_COMPLETED
        # 黑板合并：write_keys=["agent_out"] → result 并入黑板
        assert inst["context"]["blackboard"]["agent_out"] == "hi"

        # 步骤 SUCCEEDED 且 output={task_id, result}
        steps = engine.repo.get_steps(inst_id)
        assert steps[0]["status"] == STEP_SUCCEEDED
        assert steps[0]["output"]["task_id"] == task_id
        assert steps[0]["output"]["result"] == {"agent_out": "hi"}

        # 审计：agent_completed
        assert "workflow.agent_completed" in _audit_actions(db, inst_id)
    finally:
        db.close()


def test_resume_agent_error_fails_instance():
    registry = ExecutorRegistry()
    mock = MockExecutor()
    registry.register("mock", mock)
    db, engine = make_engine(executors=registry)
    try:
        _register_workflow("m21_exec_resume_fail", _exec_node())
        result = run(engine.start("m21_exec_resume_fail"))
        inst_id = result["instance"]["id"]
        task_id = mock.dispatches[0]["task_id"]

        result = run(engine.resume_agent(
            inst_id, task_id, error={"type": "executor", "message": "boom"}))
        assert result["instance"]["status"] == INSTANCE_FAILED

        inst = engine.repo.get_instance(inst_id)
        assert inst["status"] == INSTANCE_FAILED
        assert inst["error"]["message"] == "boom"

        steps = engine.repo.get_steps(inst_id)
        assert steps[0]["status"] == "FAILED"
        assert steps[0]["error"]["message"] == "boom"

        actions = _audit_actions(db, inst_id)
        assert "workflow.agent_failed" in actions
        assert "workflow.failed" in actions
    finally:
        db.close()


def test_resume_agent_unknown_task_idempotent():
    registry = ExecutorRegistry()
    mock = MockExecutor()
    registry.register("mock", mock)
    db, engine = make_engine(executors=registry)
    try:
        _register_workflow("m21_exec_resume_ghost", _exec_node())
        result = run(engine.start("m21_exec_resume_ghost"))
        inst_id = result["instance"]["id"]

        # 未知 task_id：不抛错、状态不变（幂等）
        result = run(engine.resume_agent(inst_id, "ghost-task",
                                         result={"agent_out": "x"}))
        assert result["instance"]["status"] == INSTANCE_WAITING_AGENT
        inst = engine.repo.get_instance(inst_id)
        assert inst["status"] == INSTANCE_WAITING_AGENT
        assert mock.dispatches[0]["task_id"] == "mock-1-delegate"
    finally:
        db.close()


# ─── C. 事件总线闭环（外部 Agent 互联）───

def test_exec_result_event_resumes_instance():
    registry = ExecutorRegistry()
    mock = MockExecutor()
    registry.register("mock", mock)
    bus = make_bus()
    db, engine = make_engine(executors=registry, bus=bus)
    try:
        _register_workflow("m21_exec_evt_result", _exec_node())
        result = run(engine.start("m21_exec_evt_result"))
        inst_id = result["instance"]["id"]
        assert engine.repo.get_instance(inst_id)["status"] == INSTANCE_WAITING_AGENT
        task_id = mock.dispatches[0]["task_id"]
        inst = engine.repo.get_instance(inst_id)

        # 外部 Agent 通过总线回传结果 → _on_exec_result → resume_agent
        ok = run(bus.handle_event(Event(
            id="m21-ev-result-1",
            type=EventTypes.EXEC_RESULT,
            tenant_id=inst["tenant_id"],
            payload={"instance_id": inst_id, "task_id": task_id,
                     "result": {"agent_out": "from-bus"}},
        )))
        assert ok is True
        final = engine.repo.get_instance(inst_id)
        assert final["status"] == INSTANCE_COMPLETED
        assert final["context"]["blackboard"]["agent_out"] == "from-bus"
    finally:
        db.close()


def test_exec_failed_event_fails_instance():
    registry = ExecutorRegistry()
    mock = MockExecutor()
    registry.register("mock", mock)
    bus = make_bus()
    db, engine = make_engine(executors=registry, bus=bus)
    try:
        _register_workflow("m21_exec_evt_failed", _exec_node())
        result = run(engine.start("m21_exec_evt_failed"))
        inst_id = result["instance"]["id"]
        task_id = mock.dispatches[0]["task_id"]
        inst = engine.repo.get_instance(inst_id)

        ok = run(bus.handle_event(Event(
            id="m21-ev-failed-1",
            type=EventTypes.EXEC_FAILED,
            tenant_id=inst["tenant_id"],
            payload={"instance_id": inst_id, "task_id": task_id,
                     "error": {"type": "executor", "message": "executor boom"}},
        )))
        assert ok is True
        final = engine.repo.get_instance(inst_id)
        assert final["status"] == INSTANCE_FAILED
        assert final["error"]["message"] == "executor boom"
    finally:
        db.close()


def test_exec_timeout_event_fails_instance():
    registry = ExecutorRegistry()
    mock = MockExecutor()
    registry.register("mock", mock)
    bus = make_bus()
    db, engine = make_engine(executors=registry, bus=bus)
    try:
        _register_workflow("m21_exec_evt_timeout", _exec_node())
        result = run(engine.start("m21_exec_evt_timeout"))
        inst_id = result["instance"]["id"]
        task_id = mock.dispatches[0]["task_id"]
        inst = engine.repo.get_instance(inst_id)

        ok = run(bus.handle_event(Event(
            id="m21-ev-timeout-1",
            type=EventTypes.EXEC_TIMEOUT,
            tenant_id=inst["tenant_id"],
            payload={"instance_id": inst_id, "task_id": task_id,
                     "error": "agent exceeded sla"},
        )))
        assert ok is True
        final = engine.repo.get_instance(inst_id)
        assert final["status"] == INSTANCE_FAILED
        assert final["error"]["type"] == "timeout"
    finally:
        db.close()


def test_dispatched_event_published():
    registry = ExecutorRegistry()
    mock = MockExecutor()
    registry.register("mock", mock)
    bus = make_bus()
    db, engine = make_engine(executors=registry, bus=bus)
    try:
        _register_workflow("m21_exec_evt_dispatch", _exec_node())
        result = run(engine.start("m21_exec_evt_dispatch"))
        inst_id = result["instance"]["id"]
        task_id = mock.dispatches[0]["task_id"]

        # _enter_executor 发布 EXEC.DISPATCHED（id=exec-dispatched-{task_id}）
        ev = bus.get_event("exec-dispatched-" + task_id)
        assert ev is not None
        assert ev.type == EventTypes.EXEC_DISPATCHED
        assert ev.payload["task_id"] == task_id
        assert ev.payload["instance_id"] == inst_id
        assert ev.payload["executor"] == "mock"
        assert ev.payload["node_code"] == "delegate"
    finally:
        db.close()


# ─── D. SLA 补偿与状态机 ───

def test_sweep_agent_timeout_compensates():
    registry = ExecutorRegistry()
    mock = MockExecutor()
    registry.register("mock", mock)
    db, engine = make_engine(executors=registry)
    try:
        _register_workflow("m21_exec_sweep", _exec_node(sla_hours=1.0))
        result = run(engine.start("m21_exec_sweep"))
        inst_id = result["instance"]["id"]
        task_id = mock.dispatches[0]["task_id"]

        # 人为推进派发时间（已过 2h > SLA 1h）
        engine._pending_agents[task_id]["dispatched_at_ts"] = time.time() - 7200

        handled = run(engine.sweep_timeouts())
        assert handled["agent_timed_out"] == [task_id]

        final = engine.repo.get_instance(inst_id)
        assert final["status"] == INSTANCE_FAILED
        assert final["error"]["type"] == "timeout"
        assert "超过 SLA" in final["error"]["message"]
        # 补偿后 pending 清空
        assert task_id not in engine._pending_agents
    finally:
        db.close()


def test_resume_waiting_agent_stays_waiting():
    registry = ExecutorRegistry()
    mock = MockExecutor()
    registry.register("mock", mock)
    db, engine = make_engine(executors=registry)
    try:
        _register_workflow("m21_exec_resume_hold", _exec_node())
        result = run(engine.start("m21_exec_resume_hold"))
        inst_id = result["instance"]["id"]

        run(engine.resume(inst_id))
        inst = engine.repo.get_instance(inst_id)
        assert inst["status"] == INSTANCE_WAITING_AGENT
        assert inst["current_step"] == "delegate"
        steps = engine.repo.get_steps(inst_id)
        assert steps[0]["status"] == STEP_WAITING
    finally:
        db.close()


def test_cancel_waiting_agent_instance():
    registry = ExecutorRegistry()
    mock = MockExecutor()
    registry.register("mock", mock)
    db, engine = make_engine(executors=registry)
    try:
        _register_workflow("m21_exec_cancel", _exec_node())
        result = run(engine.start("m21_exec_cancel"))
        inst_id = result["instance"]["id"]
        assert engine.repo.get_instance(inst_id)["status"] == INSTANCE_WAITING_AGENT

        run(engine.cancel(inst_id))
        final = engine.repo.get_instance(inst_id)
        assert final["status"] == INSTANCE_CANCELLED
    finally:
        db.close()


# ─── E. 跨执行边界链式（M21 主题）───

def test_executor_blackboard_handoff_chain():
    """前节点写入黑板 → 派发携带 → 恢复合并 → 尾节点读取"""
    registry = ExecutorRegistry()
    mock = MockExecutor()
    registry.register("mock", mock)
    db, engine = make_engine(executors=registry)

    async def write_seed(ctx):
        return {"seed": "from-node"}

    async def read_blackboard(ctx):
        return {"final": ctx.blackboard.get("agent_out")}

    try:
        engine.register_node("m21_write_seed", write_seed)
        engine.register_node("m21_read_bb", read_blackboard)
        _register_workflow(
            "m21_exec_handoff",
            NodeSpec("produce", "m21_write_seed", node_type="tool",
                     write_keys=["seed"]),
            _exec_node(write_keys=["agent_out"]),
            NodeSpec("consume", "m21_read_bb", node_type="tool",
                     write_keys=["final"]),
        )
        result = run(engine.start("m21_exec_handoff"))
        inst_id = result["instance"]["id"]

        # 派发 context.blackboard 已携带前节点输出
        dispatch = mock.dispatches[0]
        assert dispatch["context"]["blackboard"]["seed"] == "from-node"

        # 恢复后黑板合并 agent_out → 尾节点读取并产出 final
        task_id = dispatch["task_id"]
        run(engine.resume_agent(inst_id, task_id, result={"agent_out": "hi"}))

        final = engine.repo.get_instance(inst_id)
        assert final["status"] == INSTANCE_COMPLETED
        blackboard = final["context"]["blackboard"]
        assert blackboard["seed"] == "from-node"
        assert blackboard["agent_out"] == "hi"
        assert blackboard["final"] == "hi"
    finally:
        db.close()


def test_executor_then_approval_chain():
    """执行器恢复后进入审批环节 → 跨执行边界审批闭环"""
    registry = ExecutorRegistry()
    mock = MockExecutor()
    registry.register("mock", mock)
    db, engine = make_engine(executors=registry)
    try:
        _register_workflow(
            "m21_exec_approval",
            _exec_node(),
            NodeSpec("gate", "noop", node_type="approval",
                     approval=ApprovalSpec(approvers=["admin"], mode="any",
                                           risk="low")),
        )
        result = run(engine.start("m21_exec_approval"))
        inst_id = result["instance"]["id"]
        task_id = mock.dispatches[0]["task_id"]

        # Agent 恢复 → 续跑至审批节点挂起
        run(engine.resume_agent(inst_id, task_id, result={"agent_out": "hi"}))
        inst = engine.repo.get_instance(inst_id)
        assert inst["status"] == INSTANCE_WAITING_APPROVAL
        assert inst["current_step"] == "gate"
        steps = engine.repo.get_steps(inst_id)
        assert steps[1]["node_code"] == "gate"
        assert steps[1]["status"] == STEP_WAITING

        # 审批通过 → 闭环完成
        run(engine.approve(inst_id, True))
        final = engine.repo.get_instance(inst_id)
        assert final["status"] == INSTANCE_COMPLETED
        assert final["context"]["blackboard"]["agent_out"] == "hi"
    finally:
        db.close()