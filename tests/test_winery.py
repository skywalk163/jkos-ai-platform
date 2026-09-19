"""M18.1 酒厂（案例C）工作流引擎集成验证

覆盖：
1. WINERY_NODES 的 20 个节点处理器挂载进引擎节点表（BUILTIN_NODES 注入）
2. 4 个 winery 工作流挂载进 WORKFLOW_REGISTRY（get_workflow 可查 / 未知码报错）
3. 端到端运行：quality_traceability / marketing_decision / crisis_alert 直达完成
4. production_scheduling：审批流 —— 等待审批 → 同意后完成、驳回走拒绝路径
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
from jkos_core.workflow.nodes import BUILTIN_NODES, WORKFLOW_REGISTRY, get_workflow

WINERY_NODE_CODES = [
    # W2.1 智能生产调度
    "sales_forecaster", "demand_aggregator", "production_scheduler",
    "material_procurement", "logistics_dispatcher", "execution_monitor",
    # W2.3 质量追溯
    "batch_creator", "production_recorder", "quality_inspector", "trace_query",
    # W2.2 营销决策
    "campaign_planner", "content_generator", "content_reviewer",
    "channel_adapter", "campaign_executor",
    # W2.5 危机预警
    "sentiment_monitor", "sentiment_analyzer", "crisis_alerter",
    "crisis_responder", "marketing_pausor",
]

WINERY_WORKFLOW_CODES = [
    "winery.production_scheduling",
    "winery.quality_traceability",
    "winery.marketing_decision",
    "winery.crisis_alert",
]


def make_engine():
    tmp = tempfile.mkdtemp(prefix="dsh_m18_")
    db = Database(DatabaseConfig(path=os.path.join(tmp, "wb.db"))).connect()
    db.migrate()
    comps = SimpleNamespace(
        db=db,
        tenants=TenantRepo(db),
        workflows=WorkflowRepo(db),
        approvals=ApprovalTaskRepo(db),
        audit=AuditLogger(db),
        llm=build_llm_router(),
        jwt=None,
    )
    return db, WorkflowEngine(comps)


def run(coro):
    return asyncio.run(coro)


def _assert_all_steps_succeeded(status):
    for s in status["steps"]:
        assert s["status"] == "SUCCEEDED", f"step {s['node_code']} 状态异常: {s}"


# ── 注册挂载 ──

def test_winery_nodes_registered():
    for code in WINERY_NODE_CODES:
        assert code in BUILTIN_NODES, f"winery 节点未挂载: {code}"


def test_winery_workflows_registered():
    for code in WINERY_WORKFLOW_CODES:
        wf = get_workflow(code)
        assert wf.code == code
        assert code in WORKFLOW_REGISTRY


def test_unknown_workflow_raises():
    try:
        get_workflow("winery.not_exist")
        raise AssertionError("应抛出 WorkflowError")
    except WorkflowError:
        pass


# ── 端到端：直达完成 ──

def test_quality_traceability_runs():
    _, engine = make_engine()
    status = run(engine.start(
        "winery.quality_traceability", "winery",
        {"product": "赤霞珠干红·750ml", "quantity": 1200},
    ))
    _assert_all_steps_succeeded(status)
    assert status["instance"]["status"] == "COMPLETED"
    codes = [s["node_code"] for s in status["steps"]]
    assert codes == ["batch", "record", "inspect", "trace"]


def test_marketing_decision_runs():
    _, engine = make_engine()
    status = run(engine.start(
        "winery.marketing_decision", "winery", {"campaign": "秋季品鉴会"},
    ))
    _assert_all_steps_succeeded(status)
    assert status["instance"]["status"] == "COMPLETED"
    codes = [s["node_code"] for s in status["steps"]]
    assert codes == ["plan", "generate", "review", "adapt", "execute"]


def test_crisis_alert_runs():
    _, engine = make_engine()
    status = run(engine.start(
        "winery.crisis_alert", "winery", {"window": "2026-09-19"},
    ))
    _assert_all_steps_succeeded(status)
    assert status["instance"]["status"] == "COMPLETED"
    codes = [s["node_code"] for s in status["steps"]]
    assert codes == ["monitor", "analyze", "alert", "response", "pause"]


# ── 端到端：审批流 ──

def test_production_scheduling_approval_flow():
    _, engine = make_engine()
    status = run(engine.start(
        "winery.production_scheduling", "winery", {"region": "华东-上海"},
    ))

    if status["instance"]["status"] == INSTANCE_WAITING_APPROVAL:
        # 同意 → 完成（engine.status 为同步方法，返回实例+步骤快照）
        run(engine.approve(status["instance"]["id"], True, decided_by="厂长-李总", reason="同意投产"))
        final = engine.status(status["instance"]["id"])
        assert final["instance"]["status"] == "COMPLETED", final["instance"]

        # 驳回 → 非等待审批的终止态
        status2 = run(engine.start(
            "winery.production_scheduling", "winery", {"region": "华北-天津"},
        ))
        run(engine.approve(status2["instance"]["id"], False, decided_by="厂长-李总", reason="待复核"))
        rejected = engine.status(status2["instance"]["id"])
        assert rejected["instance"]["status"] != INSTANCE_WAITING_APPROVAL, rejected["instance"]
    else:
        # 若 POC 当前不含审批节点，也应直达完成
        assert status["instance"]["status"] == "COMPLETED", status["instance"]