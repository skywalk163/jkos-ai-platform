"""极快AI操作系统 - 智能中枢（M16）

以官方 deepseek-harness Python SDK 的 sidecar 形态嵌入，提供 Agent 会话、
工具桥接与 Web UI 反代。默认关闭（JKOS_HARNESS_ENABLED=false）。

设计依据：.claude/artifacts/plans/m16-harness-intelligent-hub.md
"""
from jkos_core.harness.config import HarnessConfig
from jkos_core.harness.gateway import (
    HarnessGateway,
    HubSession,
    get_gateway,
    identity_preamble,
    reset_gateway,
)
from jkos_core.harness.proxy import create_ui_proxy_router
from jkos_core.harness.routes import create_harness_router
from jkos_core.harness.runtime import (
    HarnessUnavailable,
    SidecarManager,
    sdk_available,
)
from jkos_core.harness.toolbridge import (
    ToolBridgeConfigError,
    apply_profile_patch,
    llm_provider_row,
    mcp_client_row,
    render_profile_patch,
)

__all__ = [
    "HarnessConfig",
    "HarnessGateway",
    "HarnessUnavailable",
    "HubSession",
    "SidecarManager",
    "ToolBridgeConfigError",
    "apply_profile_patch",
    "create_harness_router",
    "create_ui_proxy_router",
    "get_gateway",
    "identity_preamble",
    "llm_provider_row",
    "mcp_client_row",
    "render_profile_patch",
    "reset_gateway",
    "sdk_available",
]