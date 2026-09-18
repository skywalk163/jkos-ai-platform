"""酒厂中台 - 工作流定义（M3 任务 3.1/3.2/3.4/3.5）

案例C P1 工作流：
- W2.1 智能生产调度：销售预测 → 需求聚合 → 生产排程 → 原料采购 → 物流调度 → 执行监控
- W2.3 质量追溯：批次创建 → 生产记录 → 质检录入 → 追溯查询

联动事件（§2.9）：
- SENTIMENT.LEVEL_RAISED → 营销暂停（危机预警联动）
- PRODUCTION.DELAY → 物流调度重排
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from jkos_core.workflow.base import ApprovalSpec, NodeSpec, WorkflowDef
from jkos_core.workflow.nodes import WORKFLOW_REGISTRY

logger = logging.getLogger("dsh.tenants.winery")

# ─── 酒厂配置 ───

# 风险等级表（§2.5 危机预警联动）
RISK_LEVELS = {
    "low": {"auto_pass_hours": 24, "auto_reject_hours": None, "escalate_hours": None},
    "medium": {"auto_pass_hours": None, "auto_reject_hours": None, "escalate_hours": 8},
    "high": {"auto_pass_hours": None, "auto_reject_hours": 2, "escalate_hours": None},
}

# 品牌规范/合规词库（§2.2 营销决策）
BRAND_COMPLIANCE = {
    "brand_terms": ["酒厂", "酿造", "传统工艺", "精选", "年份"],
    "prohibited_terms": ["特效", "药效", "治疗", "治愈"],
    "required_disclaimer": "适量饮酒，有益健康",
}

# 门店/电商渠道列表
CHANNELS = {
    "store": {"type": "门店", "priority": 1},
    "ecommerce": {"type": "电商", "priority": 2},
    "wechat": {"type": "微信小程序", "priority": 3},
}


# ─── W2.1 智能生产调度工作流 ───

def build_production_scheduling_workflow() -> WorkflowDef:
    """智能生产调度工作流（§2.1）

    节点：
    1. sales_forecast：销售预测（LLM 基于历史销售 + 促销 + 季节）
    2. demand_aggregation：需求聚合（汇总各门店预测）
    3. production_scheduling：生产排程（人工确认/调整，中风险）
    4. material_procurement：原料采购（人工审批，中风险）
    5. logistics_dispatch：物流调度（自动生成配送计划）
    6. execution_monitor：执行监控（生产看板 + 异常告警）
    """
    return WorkflowDef(
        code="winery.production_scheduling",
        description="智能生产调度：销售预测 → 需求聚合 → 生产排程 → 原料采购 → 物流调度 → 执行监控",
        nodes=[
            NodeSpec("forecast", "sales_forecaster", node_type="llm"),
            NodeSpec("aggregate", "demand_aggregator", node_type="tool"),
            NodeSpec("schedule", "production_scheduler", node_type="tool",
                     approval=ApprovalSpec(
                         approvers=["production_manager"],
                         mode="any",
                         risk="medium"
                     )),
            NodeSpec("procure", "material_procurement", node_type="tool",
                     approval=ApprovalSpec(
                         approvers=["procurement_manager"],
                         mode="any",
                         risk="medium"
                     )),
            NodeSpec("logistics", "logistics_dispatcher", node_type="tool"),
            NodeSpec("monitor", "execution_monitor", node_type="tool"),
        ],
    )


# ─── W2.3 质量追溯工作流 ───

def build_quality_traceability_workflow() -> WorkflowDef:
    """质量追溯工作流（§2.3 + §2.6 数据模型）

    节点：
    1. batch_create：批次创建（生成批次号 + 原料溯源）
    2. production_record：生产记录（温度/湿度/时间传感器数据）
    3. quality_inspection：质检录入（理化指标 + 感官评审）
    4. trace_query：追溯查询（批次号 → 全链路信息）
    """
    return WorkflowDef(
        code="winery.quality_traceability",
        description="质量追溯：批次创建 → 生产记录 → 质检录入 → 追溯查询",
        nodes=[
            NodeSpec("batch", "batch_creator", node_type="tool"),
            NodeSpec("record", "production_recorder", node_type="tool"),
            NodeSpec("inspect", "quality_inspector", node_type="tool"),
            NodeSpec("trace", "trace_query", node_type="tool"),
        ],
    )


# ─── W2.2 营销决策工作流（复用 H1 内容管线）───

def build_marketing_decision_workflow() -> WorkflowDef:
    """营销决策工作流（§2.2，复用 H1 内容生产管线）

    节点：
    1. campaign_planning：营销策划（基于品牌规范）
    2. content_generation：内容生成（LLM + 合规词库过滤）
    3. content_review：内容审核（高风险，双人复核）
    4. channel_adaptation：渠道适配（门店/电商/微信）
    5. campaign_execute：营销执行（投放 + 监控）
    """
    return WorkflowDef(
        code="winery.marketing_decision",
        description="营销决策：营销策划 → 内容生成 → 内容审核 → 渠道适配 → 营销执行",
        nodes=[
            NodeSpec("plan", "campaign_planner", node_type="llm"),
            NodeSpec("generate", "content_generator", node_type="llm"),
            NodeSpec("review", "content_reviewer", node_type="tool",
                     approval=ApprovalSpec(
                         approvers=["marketing_manager", "compliance_officer"],
                         mode="all",
                         risk="high"
                     )),
            NodeSpec("adapt", "channel_adapter", node_type="tool"),
            NodeSpec("execute", "campaign_executor", node_type="tool"),
        ],
    )


# ─── W2.5 危机预警工作流（复用 H2 舆情组件）───

def build_crisis_alert_workflow() -> WorkflowDef:
    """危机预警工作流（§2.5，复用 H2 舆情组件）

    节点：
    1. monitor：舆情监控（新闻/微博/投诉多源抓取）
    2. analyze：情感分析（识别负面舆情）
    3. alert：危机预警（自动分级 + 通知升级链）
    4. response：应对方案（人工审批，高风险会签）
    5. pause_marketing：联动事件（暂停相关投放）
    """
    return WorkflowDef(
        code="winery.crisis_alert",
        description="危机预警：舆情监控 → 情感分析 → 危机预警 → 应对方案 → 营销暂停",
        nodes=[
            NodeSpec("monitor", "sentiment_monitor", node_type="tool"),
            NodeSpec("analyze", "sentiment_analyzer", node_type="llm"),
            NodeSpec("alert", "crisis_alerter", node_type="tool"),
            NodeSpec("response", "crisis_responder", node_type="tool",
                     approval=ApprovalSpec(
                         approvers=["crisis_team"],
                         mode="all",
                         risk="high"
                     )),
            NodeSpec("pause", "marketing_pausor", node_type="tool"),
        ],
    )


# ─── 工作流注册表 ───

WINERY_WORKFLOWS: Dict[str, WorkflowDef] = {
    "winery.production_scheduling": build_production_scheduling_workflow(),
    "winery.quality_traceability": build_quality_traceability_workflow(),
    "winery.marketing_decision": build_marketing_decision_workflow(),
    "winery.crisis_alert": build_crisis_alert_workflow(),
}


def get_winery_workflow(code: str) -> WorkflowDef:
    """获取酒厂工作流定义"""
    if code not in WINERY_WORKFLOWS:
        raise ValueError(f"未知的酒厂工作流: {code}")
    return WINERY_WORKFLOWS[code]


def register(engine) -> None:
    """向引擎注册酒厂节点处理器（POC 阶段使用模拟处理器）"""
    for name, handler in {
        "sales_forecaster": lambda ctx: {"forecast": {"week1": 100, "week2": 120, "week3": 150, "week4": 130}},
        "demand_aggregator": lambda ctx: {"demand": {"total": 500, "by_store": {"store1": 200, "store2": 300}}},
        "production_scheduler": lambda ctx: {"schedule": {"batch": "B20260914001", "lines": ["line1", "line2"]}},
        "material_procurement": lambda ctx: {"procurement": {"items": ["grain", "yeast", "oak_barrel"]}},
        "logistics_dispatcher": lambda ctx: {"dispatch": {"routes": ["store1", "store2"]}},
        "execution_monitor": lambda ctx: {"monitor": {"status": "running", "progress": 0.5}},
        "batch_creator": lambda ctx: {"batch": {"id": "B20260914001", "created_at": datetime.now().isoformat()}},
        "production_recorder": lambda ctx: {"record": {"temp": 25, "humidity": 60, "duration": 72}},
        "quality_inspector": lambda ctx: {"inspect": {"alcohol": 52, "ph": 4.2, "result": "pass"}},
        "trace_query": lambda ctx: {"trace": {"batch": "B20260914001", "full_chain": True}},
        "campaign_planner": lambda ctx: {"plan": {"theme": "秋季新品发布", "budget": 50000}},
        "content_generator": lambda ctx: {"content": {"title": "秋季新品", "body": "精选原料，传统工艺"}},
        "channel_adapter": lambda ctx: {"adapt": {"channels": ["store", "ecommerce"]}},
        "campaign_executor": lambda ctx: {"execute": {"status": "scheduled", "start_at": "2026-09-20T00:00:00"}},
        "crisis_responder": lambda ctx: {"response": {"plan": "发布声明", "contact": "media_team"}},
        "marketing_pausor": lambda ctx: {"pause": {"campaigns": ["winery.marketing_decision"], "status": "paused"}},
    }.items():
        engine.register_node(name, handler)
    WINERY_WORKFLOWS[build_production_scheduling_workflow().code] = build_production_scheduling_workflow()
    WINERY_WORKFLOWS[build_quality_traceability_workflow().code] = build_quality_traceability_workflow()
    WINERY_WORKFLOWS[build_marketing_decision_workflow().code] = build_marketing_decision_workflow()
    WINERY_WORKFLOWS[build_crisis_alert_workflow().code] = build_crisis_alert_workflow()
