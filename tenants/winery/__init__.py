"""酒厂中台（案例C）- 租户包

M3 任务 3.1/3.2/3.4/3.5：
- W2.1 智能生产调度
- W2.3 质量追溯（§2.6 数据模型）
- W2.2 营销决策（复用 H1 内容管线）
- W2.5 危机预警（复用 H2 舆情组件）
- 联动事件：SENTIMENT.LEVEL_RAISED → 营销暂停
"""
from __future__ import annotations

from tenants.winery.workflows.production import (
    WINERY_WORKFLOWS,
    BRAND_COMPLIANCE,
    CHANNELS,
    RISK_LEVELS,
    get_winery_workflow,
    register,
)

__all__ = [
    "WINERY_WORKFLOWS",
    "BRAND_COMPLIANCE",
    "CHANNELS",
    "RISK_LEVELS",
    "get_winery_workflow",
    "register",
]
