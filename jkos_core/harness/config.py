"""极快AI操作系统 - 智能中枢 harness 配置（M16）

harness（deepseek-harness）以 SDK sidecar 形态被 JKOS 托管：凭据由 JKOS 环境变量
注入子进程，harness 自身不落密钥文件。默认关闭（JKOS_HARNESS_ENABLED=false），
保证既有功能零回归。

设计依据：.claude/artifacts/plans/m16-harness-intelligent-hub.md（ADR）
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger("dsh.harness")

_TRUE_VALUES = ("1", "true", "yes", "on")
_FALSE_VALUES = ("0", "false", "no", "off", "")

_DEFAULT_DSH_HOME = "/data/dsh/harness-home"
_DEFAULT_UI_BASE_URL = "http://127.0.0.1:3080"
# ToolBridge：harness 的 dsh-mcp-client 反向调用 JKOS 的 MCP 端点
# 注意：/mcp 由 MCP 服务提供（jkos-server mcp，默认 3000），不在 API 服务（8000）上
_DEFAULT_MCP_URL = "http://127.0.0.1:3000/mcp"

# 密钥字段名：redacted() / 日志路径一律按此集合脱敏
_SECRET_FIELDS = ("api_key", "ui_token")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    logger.warning("环境变量 %s 值非法（%s），按默认 %s 处理", name, raw, default)
    return default


def _env_float(name: str, default: Optional[float]) -> Optional[float]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("环境变量 %s 不是数字（%s），按默认 %s 处理", name, raw, default)
        return default


@dataclass
class HarnessConfig:
    """智能中枢（harness sidecar）配置

    dsh_bin 为 None 时使用 SDK 自带 bundled runtime（Windows 等有 wheel 的平台）；
    FreeBSD 无 runtime wheel，须显式指向源码构建的 launcher。
    """

    enabled: bool = False
    dsh_home: str = _DEFAULT_DSH_HOME
    dsh_bin: Optional[str] = None
    provider: str = "deepseek-official"
    model: str = "deepseek-v4-flash"
    workspace: str = ""
    api_key: str = ""
    base_url: str = ""
    sdk_source: str = "pypi"
    initialize_timeout: float = 30.0
    request_timeout: Optional[float] = None
    ui_base_url: str = _DEFAULT_UI_BASE_URL
    mcp_url: str = _DEFAULT_MCP_URL
    # harness Web UI 访问令牌：dsh web 启动时打印在 http://127.0.0.1:3080/?token=<值>
    ui_token: str = ""
    # harness LLM 接入协议：公网 api.deepseek.com 走 chat-completions；
    # harness 的 deepseek-official 默认用 messages 协议，指向公网端点会 404
    llm_protocol: str = "chat-completions"

    @classmethod
    def from_env(cls) -> "HarnessConfig":
        dsh_home = os.getenv("DSH_HOME") or _DEFAULT_DSH_HOME
        workspace = os.getenv("JKOS_HARNESS_WORKSPACE") or os.path.join(dsh_home, "workspace")
        return cls(
            enabled=_env_bool("JKOS_HARNESS_ENABLED", False),
            dsh_home=dsh_home,
            dsh_bin=os.getenv("JKOS_HARNESS_DSH_BIN") or None,
            provider=os.getenv("JKOS_HARNESS_PROVIDER", "deepseek-official"),
            model=os.getenv("JKOS_HARNESS_MODEL", "deepseek-v4-flash"),
            workspace=workspace,
            # 凭据只从 JKOS 环境读取，不接受 profile 文件承载
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", ""),
            sdk_source=os.getenv("JKOS_HARNESS_SDK_SOURCE", "pypi").strip().lower(),
            initialize_timeout=_env_float("JKOS_HARNESS_INIT_TIMEOUT", 30.0) or 30.0,
            request_timeout=_env_float("JKOS_HARNESS_REQUEST_TIMEOUT", None),
            ui_base_url=os.getenv("JKOS_HARNESS_UI_BASE_URL", _DEFAULT_UI_BASE_URL),
            mcp_url=os.getenv("JKOS_HARNESS_MCP_URL", _DEFAULT_MCP_URL),
            ui_token=os.getenv("JKOS_HARNESS_UI_TOKEN", ""),
            llm_protocol=os.getenv("JKOS_HARNESS_LLM_PROTOCOL", "chat-completions"),
        )

    def redacted(self) -> Dict[str, object]:
        """可安全打印/返回的配置视图（密钥脱敏）"""
        data: Dict[str, object] = dict(self.__dict__)
        for name in _SECRET_FIELDS:
            data[name] = "***" if data.get(name) else ""
        return data

    def describe(self) -> str:
        """单行摘要，用于日志（绝不含密钥）"""
        runtime = self.dsh_bin or "bundled"
        return (f"enabled={self.enabled} provider={self.provider} model={self.model} "
                f"dsh_home={self.dsh_home} runtime={runtime} sdk_source={self.sdk_source}")