"""酒厂（案例C）工作流包（M18.1 引擎集成）

从 tenants.winery.workflows.production 转发导出，使核心引擎节点表可以
`from tenants.winery.workflows import WINERY_WORKFLOWS` 惰性挂载
（与 `from tenants.media.workflows import MEDIA_WORKFLOWS` 对齐）。
"""
from __future__ import annotations

from tenants.winery.workflows.production import (
    BRAND_COMPLIANCE,
    CHANNELS,
    RISK_LEVELS,
    WINERY_WORKFLOWS,
    get_winery_workflow,
)

__all__ = [
    "BRAND_COMPLIANCE",
    "CHANNELS",
    "RISK_LEVELS",
    "WINERY_WORKFLOWS",
    "get_winery_workflow",
]