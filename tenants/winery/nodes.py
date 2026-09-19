"""酒厂（案例C）工作流节点处理器（M18.1 引擎集成）

20 个 winery 节点处理器，语义与 POC（tenants/winery/workflows/production.py
中 register() 的内联处理器）一一对应。全部幂等、输出确定，可安全重跑。
由 jkos_core/workflow/nodes.py 通过 WINERY_NODES 惰性挂载进引擎节点表
BUILTIN_NODES / engine._nodes。

节点键 = 工作流 NodeSpec.node_code，与 POC 注册的处理器名一致。
"""
from __future__ import annotations

from typing import Dict

from jkos_core.workflow.base import NodeContext, NodeHandler


def _prev(ctx: NodeContext) -> Dict:
    """上一个节点的黑板输出（首节点为空 dict）。"""
    return ctx.prev_output or {}


def _params(ctx: NodeContext) -> Dict:
    """工作流启动参数（start 的入参，存于 instance['context']）。"""
    return ctx.instance.get("context") or {}


# ───────────────────────── W2.1 智能生产调度链路 ─────────────────────────

async def winery_sales_forecaster(ctx: NodeContext) -> Dict:
    region = _params(ctx).get("region", "华东-上海")
    return {
        "node": ctx.step["node_code"],
        "region": region,
        "window": _params(ctx).get("window", "2026-Q4"),
        "forecast": {
            "赤霞珠干红·750ml": 5200,
            "精酿啤酒·20L桶": 3200,
            "气泡白葡萄酒·750ml": 1800,
        },
        "confidence": 0.87,
    }


async def winery_demand_aggregator(ctx: NodeContext) -> Dict:
    prev = _prev(ctx)
    forecast = prev.get("forecast", {})
    return {
        "node": ctx.step["node_code"],
        "region": prev.get("region", "华东-上海"),
        "aggregated_demand": forecast,
        "total_demand": sum(forecast.values()),
    }


async def winery_production_scheduler(ctx: NodeContext) -> Dict:
    prev = _prev(ctx)
    total = prev.get("total_demand", 10200)
    return {
        "node": ctx.step["node_code"],
        "produce_plan": {
            "灌装线A": int(total * 0.6),
            "灌装线B": int(total * 0.4),
        },
        "total_plan": total,
    }


async def winery_material_procurement(ctx: NodeContext) -> Dict:
    prev = _prev(ctx)
    total = prev.get("total_plan", 10200)
    return {
        "node": ctx.step["node_code"],
        "bom": {
            "葡萄原浆(kg)": total,
            "酒瓶(只)": total,
            "软木塞(只)": total,
        },
        "po_status": "READY",
    }


async def winery_logistics_dispatcher(ctx: NodeContext) -> Dict:
    return {
        "node": ctx.step["node_code"],
        "carrier": "自营冷链",
        "lanes": ["上海→北京", "上海→广州", "上海→成都"],
        "departure_at": "2026-10-03 08:00",
    }


async def winery_execution_monitor(ctx: NodeContext) -> Dict:
    return {
        "node": ctx.step["node_code"],
        "review": "投产计划就绪，待人工审批放行",
        "decision": "pending",
    }


# ───────────────────────── W2.3 质量追溯链路 ─────────────────────────

async def winery_batch_creator(ctx: NodeContext) -> Dict:
    return {
        "node": ctx.step["node_code"],
        "batch_id": _params(ctx).get("batch_code", "BT-20260919-001"),
        "product": _params(ctx).get("product", "赤霞珠干红·750ml"),
        "quantity": _params(ctx).get("quantity", 1200),
        "created_at": "2026-09-19T08:00:00Z",
    }


async def winery_production_recorder(ctx: NodeContext) -> Dict:
    return {
        "node": ctx.step["node_code"],
        "batch_id": _prev(ctx).get("batch_id", "BT-UNKNOWN"),
        "records": [
            {"工序": "破碎/压榨", "result": "OK"},
            {"工序": "发酵", "result": "OK"},
            {"工序": "陈酿", "result": "OK"},
        ],
    }


async def winery_quality_inspector(ctx: NodeContext) -> Dict:
    return {
        "node": ctx.step["node_code"],
        "batch_id": _prev(ctx).get("batch_id", "BT-UNKNOWN"),
        "inspector": "质检科-张工",
        "items": {
            "酒精度": "12.5%vol（标准 12.0-13.0 OK）",
            "总二氧化硫": "88mg/L（<=150 OK）",
            "菌落总数": "未检出（<=50 OK）",
        },
        "verdict": "PASS",
    }


async def winery_trace_query(ctx: NodeContext) -> Dict:
    insp = _prev(ctx)
    batch = insp.get("batch_id", "BT-UNKNOWN")
    return {
        "node": ctx.step["node_code"],
        "batch_id": batch,
        "trace": {
            "原料": "宁夏贺兰山东麓·赤霞珠",
            "产线": "灌装线A",
            "质检结论": insp.get("verdict", "PASS"),
            "检验员": insp.get("inspector", "质检科"),
        },
        "qrcode": f"QR-{batch}",
    }


# ───────────────────────── W2.2 营销决策链路 ─────────────────────────

async def winery_campaign_planner(ctx: NodeContext) -> Dict:
    return {
        "node": ctx.step["node_code"],
        "campaign": _params(ctx).get("campaign", "秋季品鉴会"),
        "budget": _params(ctx).get("budget", 120000),
        "channels": ["小程序", "抖音本地生活", "门店快闪"],
    }


async def winery_content_generator(ctx: NodeContext) -> Dict:
    prev = _prev(ctx)
    return {
        "node": ctx.step["node_code"],
        "campaign": prev.get("campaign", "秋季品鉴会"),
        "copy": "贺兰山东麓干红，一口秋意入喉。",
        "visual": "酒窖黄昏主视觉.jpg",
        "assets": ["banner_1080x1920.png", "edm.html", "短视频脚本_v2.docx"],
    }


async def winery_content_reviewer(ctx: NodeContext) -> Dict:
    prev = _prev(ctx)
    return {
        "node": ctx.step["node_code"],
        "campaign": prev.get("campaign", "秋季品鉴会"),
        "assets": prev.get("assets", []),
        "compliance": {
            "禁用词扫描": "0 命中",
            "国家级获奖指代": "OK",
            "未成年饮酒提示": "已加",
        },
        "verdict": "APPROVED",
    }


async def winery_channel_adapter(ctx: NodeContext) -> Dict:
    return {
        "node": ctx.step["node_code"],
        "channel_configs": {
            "小程序": {"slot": "首页轮播", "schedule": "10-01 08:00"},
            "抖音本地生活": {"slot": "团购卡", "schedule": "10-01 10:00"},
        },
    }


async def winery_campaign_executor(ctx: NodeContext) -> Dict:
    prev = _prev(ctx)
    return {
        "node": ctx.step["node_code"],
        "campaign": prev.get("campaign", "秋季品鉴会"),
        "published": ["小程序", "抖音本地生活", "门店快闪"],
        "url": "https://activity.example.com/autumn-tasting",
    }


# ───────────────────────── W2.5 危机预警链路 ─────────────────────────

async def winery_sentiment_monitor(ctx: NodeContext) -> Dict:
    return {
        "node": ctx.step["node_code"],
        "window": _params(ctx).get("window", "2026-09-19"),
        "posts": {"微博": 3421, "小红书": 1507, "抖音": 2210},
        "negative_rate": 0.032,
    }


async def winery_sentiment_analyzer(ctx: NodeContext) -> Dict:
    return {
        "node": ctx.step["node_code"],
        "negative_rate": _prev(ctx).get("negative_rate", 0.032),
        "risk_level": "L2",
    }


async def winery_crisis_alerter(ctx: NodeContext) -> Dict:
    return {
        "node": ctx.step["node_code"],
        "alert": "负面舆情达 L2，触发预警联动",
        "to": ["品牌部", "质量部", "市场部"],
    }


async def winery_crisis_responder(ctx: NodeContext) -> Dict:
    return {
        "node": ctx.step["node_code"],
        "response": "官方说明口径 + 涉事批次内部核查",
        "contact_channels": ["客服专线", "官方微博"],
    }


async def winery_marketing_pausor(ctx: NodeContext) -> Dict:
    return {
        "node": ctx.step["node_code"],
        "paused_campaigns": _params(ctx).get("paused_campaigns", ["秋季品鉴会"]),
        "resume_rule": "risk_level 回落至 L1 后自动恢复",
    }


WINERY_NODES: Dict[str, NodeHandler] = {
    # W2.1 智能生产调度
    "sales_forecaster": winery_sales_forecaster,
    "demand_aggregator": winery_demand_aggregator,
    "production_scheduler": winery_production_scheduler,
    "material_procurement": winery_material_procurement,
    "logistics_dispatcher": winery_logistics_dispatcher,
    "execution_monitor": winery_execution_monitor,
    # W2.3 质量追溯
    "batch_creator": winery_batch_creator,
    "production_recorder": winery_production_recorder,
    "quality_inspector": winery_quality_inspector,
    "trace_query": winery_trace_query,
    # W2.2 营销决策
    "campaign_planner": winery_campaign_planner,
    "content_generator": winery_content_generator,
    "content_reviewer": winery_content_reviewer,
    "channel_adapter": winery_channel_adapter,
    "campaign_executor": winery_campaign_executor,
    # W2.5 危机预警
    "sentiment_monitor": winery_sentiment_monitor,
    "sentiment_analyzer": winery_sentiment_analyzer,
    "crisis_alerter": winery_crisis_alerter,
    "crisis_responder": winery_crisis_responder,
    "marketing_pausor": winery_marketing_pausor,
}

__all__ = ["WINERY_NODES"]