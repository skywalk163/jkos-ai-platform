"""极快AI操作系统 - 工具桥接 ToolBridge（M16）

把 JKOS 已注册的工具经 harness 的 dsh-mcp-client 暴露给 agent，使中枢可直接
调用 JKOS 现有工具（复用 M14 的租户可见性与配额，不重建一套）。

形态选择（ADR）：优先 A′ —— `streamable-http` 直连 JKOS `POST /mcp`，
零新增协议代码；协议不兼容时再落 B′（stdio shim）。

安全要点（Critic 保留意见）：jkos_core/mcp/server.py 的 _visible_tools(ctx)
在 ctx 为 None（匿名）时返回**全部**工具，因此桥接配置必须带 Bearer token；
本模块在缺少 token 时**拒绝构建**，避免 harness 匿名连接导致可见性退化。
token 经环境变量在 harness 侧读取，不写入配置文件（密钥铁律）。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

from jkos_core.harness.config import HarnessConfig

logger = logging.getLogger("dsh.harness")

MCP_SERVER_NAME = "jkos"
MCP_TRANSPORT = "streamable-http"
TOKEN_ENV_VAR = "JKOS_MCP_TOKEN"

# harness 侧 profile patch 的相对位置（$DSH_HOME/profiles/<profile>/cordis.patch.yml）
PATCH_RELATIVE_PATH = os.path.join("profiles", "sdk", "cordis.patch.yml")


class ToolBridgeConfigError(ValueError):
    """桥接配置非法（例如缺少鉴权 token）"""


def mcp_client_row(config: HarnessConfig, token: str,
                   server_name: str = MCP_SERVER_NAME) -> Dict[str, Any]:
    """构造 harness dsh-mcp-client 配置行

    token 为 JKOS 签发的访问令牌凭证；为空则拒绝构建（防止匿名可见性退化）。
    """
    if not token or not token.strip():
        raise ToolBridgeConfigError(
            "ToolBridge 必须有鉴权 token：JKOS /mcp 在匿名上下文下会返回全部工具，"
            "缺少 token 会导致跨租户可见性退化"
        )
    return {
        "id": f"mcp-{server_name}",
        "name": "@deepseek-ai/dsh-mcp-client",
        "config": {
            "serverName": server_name,
            "transport": MCP_TRANSPORT,
            "url": config.mcp_url,
            # token 由 harness 侧读环境变量，不落盘
            "headers": {"Authorization": f"${{process.env.{TOKEN_ENV_VAR}}}"},
            "failOnStartupError": True,
        },
    }


# harness 已由 bundle 挂载的行 id（patch 必须带 id 才能覆盖既有行；
# 无 id 的行会被当作新增项而静默失效 —— 见 deepseek-harness 合成配置快照）
LLM_ROW_ID = "llm-deepseek"


def llm_provider_row(config: HarnessConfig) -> Dict[str, Any]:
    """构造 harness LLM 接入行（deepseek-official）

    公网 api.deepseek.com 只支持 Chat Completions，而 harness 的 deepseek-official
    默认走 messages 协议（会 404），因此这里显式选择 protocol。
    密钥经 apiKeyEnv 引用环境变量，不写入配置文件。
    """
    row: Dict[str, Any] = {
        "id": LLM_ROW_ID,
        "name": "@deepseek-ai/dsh-llm-deepseek",
        "config": {
            "apiKeyEnv": "DEEPSEEK_API_KEY",
            "protocol": config.llm_protocol,
        },
    }
    if config.base_url:
        row["config"]["baseURL"] = config.base_url
    return row


def render_profile_patch(config: HarnessConfig,
                         server_name: str = MCP_SERVER_NAME) -> str:
    """渲染 cordis.patch.yml 内容（LLM 接入行 + ToolBridge 行）

    使用 harness 支持的 !!js / apiKeyEnv 引用环境变量，
    因此 patch 文件中**不含任何密钥**。
    """
    base_url_line = f"    baseURL: {config.base_url}\n" if config.base_url else ""
    return (
        "# 由 JKOS 生成（M16 智能中枢）——请勿手工编辑；\n"
        f"# 密钥经环境变量注入（DEEPSEEK_API_KEY / {TOKEN_ENV_VAR}），本文件不含密钥。\n"
        "- id: " + LLM_ROW_ID + "\n"
        "  name: '@deepseek-ai/dsh-llm-deepseek'\n"
        "  config:\n"
        "    apiKeyEnv: DEEPSEEK_API_KEY\n"
        f"    protocol: {config.llm_protocol}\n"
        f"{base_url_line}"
        f"- id: mcp-{server_name}\n"
        "  name: '@deepseek-ai/dsh-mcp-client'\n"
        "  config:\n"
        f"    serverName: {server_name}\n"
        f"    transport: {MCP_TRANSPORT}\n"
        f"    url: {config.mcp_url}\n"
        "    headers:\n"
        "      Authorization: !!js '`Bearer ${process.env." + TOKEN_ENV_VAR + "}`'\n"
        "    failOnStartupError: true\n"
    )


def apply_profile_patch(config: HarnessConfig,
                        server_name: str = MCP_SERVER_NAME) -> Path:
    """把桥接配置写入 harness profile patch（幂等覆盖）"""
    target = Path(config.dsh_home) / PATCH_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_profile_patch(config, server_name), encoding="utf-8")
    logger.info("ToolBridge 配置已写入 %s（serverName=%s）", target, server_name)
    return target