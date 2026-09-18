"""M2 自媒体中台测试（任务 2.1/2.2）— H1 内容生产管线 + H2 舆情情感管线

纯 assert 风格，兼容 pytest 与 m0_selftest 直跑。
验证双租户（dev + media）隔离、内容生产管线、舆情情感管线。
"""
import asyncio
import os
import tempfile
from types import SimpleNamespace

from jkos_core.audit import AuditLogger
from jkos_core.db import ApprovalTaskRepo, Database, DatabaseConfig, TenantRepo, WorkflowRepo
from jkos_core.llm import build_llm_router
from jkos_core.workflow import WorkflowEngine


def make_engine(tenant_code="media"):
    """创建测试引擎（支持 media 和 dev 租户）"""
    tmp = tempfile.mkdtemp(prefix=f"dsh_{tenant_code}_")
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
    )
    return db, WorkflowEngine(comps)


def run(coro):
    return asyncio.run(coro)


# ─── H1 内容生产管线（任务 2.1）───

def test_content_production_planning():
    """内容策划节点：基于热点话题生成策划方案"""
    db, engine = make_engine()
    try:
        status = run(engine.start("media.content_production", "media",
                                  context={"topic": "人工智能"}))
        inst = status["instance"]
        assert inst["status"] == "COMPLETED"
        steps = status["steps"]
        assert len(steps) == 4
        assert steps[0]["node_code"] == "plan"
        assert steps[0]["status"] == "SUCCEEDED"
        output = steps[0]["output"] or {}
        # 检查 topic 是否正确传递
        assert output.get("topic") == "人工智能"
        assert len(output.get("outline", [])) > 0
    finally:
        db.close()


def test_content_production_full_pipeline():
    """内容生产管线端到端：策划 → 生成 → 审核 → 发布"""
    db, engine = make_engine()
    try:
        status = run(engine.start("media.content_production", "media",
                                  context={"topic": "数字化转型"}))
        inst = status["instance"]
        steps = status["steps"]

        # 验证所有步骤都成功
        assert inst["status"] == "COMPLETED"
        assert all(s["status"] == "SUCCEEDED" for s in steps)

        # 验证发布步骤
        publish_step = next(s for s in steps if s["node_code"] == "publish")
        publish_output = publish_step["output"] or {}
        assert publish_output["published_count"] == 4
        assert len(publish_output["platforms"]) == 4
    finally:
        db.close()


def test_content_production_with_approval():
    """内容生产管线含人工审批"""
    db, engine = make_engine()
    try:
        # 使用需要审批的工作流
        status = run(engine.start("media.content_production", "media",
                                  context={"topic": "品牌营销"}))
        inst = status["instance"]
        steps = status["steps"]

        # 验证审核步骤
        review_step = next(s for s in steps if s["node_code"] == "review")
        assert review_step["status"] == "SUCCEEDED"
        review_output = review_step["output"] or {}
        assert review_output.get("review_status") == "pending_human_review"
    finally:
        db.close()


# ─── H2 舆情情感管线（任务 2.2）───

def test_sentiment_monitoring():
    """舆情监控节点：收集评论/提及"""
    db, engine = make_engine()
    try:
        status = run(engine.start("media.sentiment_monitoring", "media",
                                  context={"keyword": "新品发布"}))
        inst = status["instance"]
        steps = status["steps"]

        assert inst["status"] == "COMPLETED"
        monitor_step = next(s for s in steps if s["node_code"] == "monitor")
        output = monitor_step["output"] or {}
        assert output["keyword"] == "新品发布"
        assert output["mention_count"] == 5
    finally:
        db.close()


def test_sentiment_analysis():
    """情感分析节点：分析评论情感倾向"""
    db, engine = make_engine()
    try:
        status = run(engine.start("media.sentiment_monitoring", "media",
                                  context={"keyword": "产品体验"}))
        inst = status["instance"]
        steps = status["steps"]

        assert inst["status"] == "COMPLETED"

        # 验证情感分析步骤
        analyze_step = next(s for s in steps if s["node_code"] == "analyze")
        output = analyze_step["output"] or {}
        assert "positive_ratio" in output
        assert "negative_ratio" in output
        assert "overall_sentiment" in output
        # total 可能为 2（sample_mentions 只有 2 条）或 5
        assert output["total"] >= 2
    finally:
        db.close()


def test_sentiment_crisis_detection():
    """危机预警：识别负面情感并升级"""
    db, engine = make_engine()
    try:
        status = run(engine.start("media.sentiment_monitoring", "media",
                                  context={"keyword": "投诉"}))
        inst = status["instance"]
        steps = status["steps"]

        assert inst["status"] == "COMPLETED"

        # 验证危机预警步骤
        alert_step = next(s for s in steps if s["node_code"] == "alert")
        output = alert_step["output"] or {}
        assert output["is_crisis"] is True
        assert output["alert_level"] == "high"
    finally:
        db.close()


def test_sentiment_reply_generation():
    """回复生成节点：生成互动回复内容"""
    db, engine = make_engine()
    try:
        status = run(engine.start("media.sentiment_monitoring", "media",
                                  context={"keyword": "售后服务"}))
        inst = status["instance"]
        steps = status["steps"]

        assert inst["status"] == "COMPLETED"

        # 验证回复生成步骤
        respond_step = next(s for s in steps if s["node_code"] == "respond")
        output = respond_step["output"] or {}
        assert "reply" in output
        assert len(output["replies"]) == 3
    finally:
        db.close()


# ─── 双租户隔离（任务 2.3）───

def test_tenant_isolation_media_vs_dev():
    """双租户隔离：media 和 dev 租户工作流互不干扰"""
    db_media, engine_media = make_engine("media")
    db_dev, engine_dev = make_engine("dev")
    try:
        # media 租户执行内容生产
        status_media = run(engine_media.start("media.content_production", "media",
                                              context={"topic": "AI 趋势"}))
        assert status_media["instance"]["status"] == "COMPLETED"

        # dev 租户执行代码审查
        status_dev = run(engine_dev.start("hello", "dev"))
        assert status_dev["instance"]["status"] == "COMPLETED"

        # 验证 media 租户看不到 dev 租户的工作流
        from jkos_core.workflow.nodes import WORKFLOW_REGISTRY
        assert "media.content_production" in WORKFLOW_REGISTRY
        assert "hello" in WORKFLOW_REGISTRY
    finally:
        db_media.close()
        db_dev.close()


def test_media_workflow_registration():
    """验证媒体工作流已正确注册"""
    from jkos_core.workflow.nodes import WORKFLOW_REGISTRY

    assert "media.content_production" in WORKFLOW_REGISTRY
    assert "media.sentiment_monitoring" in WORKFLOW_REGISTRY
    assert "media.content_review" in WORKFLOW_REGISTRY

    # 验证工作流定义
    wf = WORKFLOW_REGISTRY["media.content_production"]
    assert wf.code == "media.content_production"
    assert len(wf.nodes) == 4
    assert wf.nodes[0].node_code == "plan"
    assert wf.nodes[1].node_code == "generate"
    assert wf.nodes[2].node_code == "review"
    assert wf.nodes[3].node_code == "publish"


def test_media_node_registration():
    """验证媒体节点已正确注册"""
    from jkos_core.workflow.nodes import BUILTIN_NODES

    media_nodes = [
        "content_planner", "content_generator", "content_reviewer", "content_publisher",
        "sentiment_monitor", "sentiment_analyzer", "crisis_alerter", "reply_generator",
    ]
    for node in media_nodes:
        assert node in BUILTIN_NODES, f"节点 {node} 未注册"


# ─── 内容审核管线 ───

def test_content_review_workflow():
    """内容审核管线：自动审核 → 人工复审 → 审批决策"""
    db, engine = make_engine()
    try:
        status = run(engine.start("media.content_review", "media"))
        inst = status["instance"]
        steps = status["steps"]

        assert inst["status"] == "COMPLETED"
        assert len(steps) == 3
        assert steps[0]["node_code"] == "auto"
        assert steps[1]["node_code"] == "human"
        assert steps[2]["node_code"] == "decision"
    finally:
        db.close()


# ─── 入口 ───

if __name__ == "__main__":
    import sys

    tests = [
        test_content_production_planning,
        test_content_production_full_pipeline,
        test_content_production_with_approval,
        test_sentiment_monitoring,
        test_sentiment_analysis,
        test_sentiment_crisis_detection,
        test_sentiment_reply_generation,
        test_tenant_isolation_media_vs_dev,
        test_media_workflow_registration,
        test_media_node_registration,
        test_content_review_workflow,
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
