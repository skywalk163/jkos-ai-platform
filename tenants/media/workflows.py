"""自媒体中台 - 工作流定义（M2 任务 2.1/2.2）

H1 内容生产管线：内容策划 → 内容生成 → 内容审核 → 内容发布
H2 舆情情感管线：舆情监控 → 情感分析 → 危机预警 → 互动回复
"""
from __future__ import annotations

from typing import Any, Dict, List

from dsh_core.workflow.base import ApprovalSpec, NodeSpec, WorkflowDef

# ─── H1 内容生产管线（M2 任务 2.1）───

def build_content_production_workflow() -> WorkflowDef:
    """内容生产管线（复用酒厂营销决策 + 自媒体内容生产）

    节点：
    1. content_planning：内容策划（基于热点话题）
    2. content_generation：内容生成（LLM 生成正文）
    3. content_review：内容审核（自动审核，无人工审批）
    4. content_publish：内容发布（多平台分发）
    """
    return WorkflowDef(
        code="media.content_production",
        description="自媒体内容生产管线：策划 → 生成 → 审核 → 发布",
        nodes=[
            NodeSpec("plan", "content_planner", node_type="llm"),
            NodeSpec("generate", "content_generator", node_type="llm"),
            NodeSpec("review", "content_reviewer", node_type="tool"),
            NodeSpec("publish", "content_publisher", node_type="tool"),
        ],
    )


# ─── H2 舆情情感管线（M2 任务 2.2）───

def build_sentiment_monitoring_workflow() -> WorkflowDef:
    """舆情情感管线（复用酒厂危机预警 + 自媒体评论互动）

    节点：
    1. monitor：舆情监控（收集评论/提及）
    2. analyze：情感分析（识别情感倾向）
    3. alert：危机预警（自动预警）
    4. respond：互动回复（生成回复内容）
    """
    return WorkflowDef(
        code="media.sentiment_monitoring",
        description="舆情情感管线：监控 → 分析 → 预警 → 回复",
        nodes=[
            NodeSpec("monitor", "sentiment_monitor", node_type="tool"),
            NodeSpec("analyze", "sentiment_analyzer", node_type="llm"),
            NodeSpec("alert", "crisis_alerter", node_type="tool"),
            NodeSpec("respond", "reply_generator", node_type="llm"),
        ],
    )


# ─── 内容审核管线（简化版，无人工审批）───

def build_content_review_workflow() -> WorkflowDef:
    """内容审核管线（自动审核 + 人工复审）

    节点：
    1. auto_review：自动审核（敏感词/合规检查）
    2. human_review：人工复审（高风险内容）
    3. approve：审批决策
    """
    return WorkflowDef(
        code="media.content_review",
        description="内容审核管线：自动审核 → 人工复审 → 审批决策",
        nodes=[
            NodeSpec("auto", "content_reviewer", node_type="tool"),
            NodeSpec("human", "content_reviewer", node_type="tool"),
            NodeSpec("decision", "content_publisher", node_type="tool"),
        ],
    )


# ─── 工作流注册表 ───

MEDIA_WORKFLOWS: Dict[str, WorkflowDef] = {
    "media.content_production": build_content_production_workflow(),
    "media.sentiment_monitoring": build_sentiment_monitoring_workflow(),
    "media.content_review": build_content_review_workflow(),
}


def get_media_workflow(code: str) -> WorkflowDef:
    """获取自媒体工作流定义"""
    if code not in MEDIA_WORKFLOWS:
        raise ValueError(f"未知的自媒体工作流: {code}")
    return MEDIA_WORKFLOWS[code]
