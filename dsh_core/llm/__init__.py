"""DSH LLM 路由 - 多供应商故障转移（M0-M3）+ purpose 路由/熔断（AI 中台控制层）

M10 任务 10.1 新增：通义千问 (Qwen)、Claude (Anthropic) 供应商。

默认供应商链：deepseek（真实）→ openai（真实）→ qwen（真实）→ claude（真实）→ simulated（兜底）。
环境变量约定（DSH_* 前缀）：
  DSH_LLM_API_KEY / DSH_LLM_BASE_URL / DSH_LLM_MODEL
  DSH_DEEPSEEK_API_KEY / DSH_DEEPSEEK_BASE_URL / DSH_DEEPSEEK_MODEL
  DSH_OPENAI_API_KEY / DSH_OPENAI_BASE_URL / DSH_OPENAI_MODEL
  DSH_QWEN_API_KEY / DSH_QWEN_BASE_URL / DSH_QWEN_MODEL
  DSH_CLAUDE_API_KEY / DSH_CLAUDE_BASE_URL / DSH_CLAUDE_MODEL
purpose 路由（JSON，可选）：
  DSH_LLM_ROUTES = {"chat": ["deepseek", "openai"], "code": ["qwen", "deepseek"]}
熔断（可选）：
  DSH_LLM_CIRCUIT_MAX_FAILURES（连续失败次数，>0 启用）
  DSH_LLM_CIRCUIT_COOLDOWN（冷却秒数）
"""
import json
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from dsh_core.llm.base import LLMError, LLMMessage, LLMProvider, LLMResult
from dsh_core.llm.deepseek import DeepSeekProvider
from dsh_core.llm.openai import OpenAIProvider
from dsh_core.llm.router import CircuitConfig, LLMRouter, UsageRecorder
from dsh_core.llm.simulated import SimulatedProvider

# M10 新增
from dsh_core.llm.claude import ClaudeProvider, build_claude_provider
from dsh_core.llm.qwen import QwenProvider, build_qwen_provider

__all__ = [
    "LLMError",
    "LLMMessage",
    "LLMProvider",
    "LLMResult",
    "LLMRouter",
    "LLMConfig",
    "CircuitConfig",
    "DeepSeekProvider",
    "OpenAIProvider",
    "SimulatedProvider",
    "QwenProvider",
    "ClaudeProvider",
    "build_llm_router",
    "build_qwen_provider",
    "build_claude_provider",
]


@dataclass
class LLMConfig:
    """LLM 配置"""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com"
    openai_model: str = "gpt-4o-mini"
    # AI 中台控制层：purpose 路由（purpose -> [primary, fallback...]）
    llm_routes: Dict[str, List[str]] = field(default_factory=dict)
    # 熔断：连续失败次数 >0 启用
    circuit_max_failures: int = 0
    circuit_cooldown_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "LLMConfig":
        routes: Dict[str, List[str]] = {}
        raw = os.getenv("DSH_LLM_ROUTES")
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    routes = {str(k): [str(x) for x in v] for k, v in parsed.items()}
            except Exception:
                pass  # 配置非法时降级为不配置路由
        max_failures = int(os.getenv("DSH_LLM_CIRCUIT_MAX_FAILURES", "0") or "0")
        cooldown = float(os.getenv("DSH_LLM_CIRCUIT_COOLDOWN", "30.0") or "30.0")
        return cls(
            deepseek_api_key=os.getenv("DSH_DEEPSEEK_API_KEY") or os.getenv("DSH_LLM_API_KEY", ""),
            deepseek_base_url=os.getenv("DSH_DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            deepseek_model=os.getenv("DSH_DEEPSEEK_MODEL", "deepseek-chat"),
            openai_api_key=os.getenv("DSH_OPENAI_API_KEY", ""),
            openai_base_url=os.getenv("DSH_OPENAI_BASE_URL", "https://api.openai.com"),
            openai_model=os.getenv("DSH_OPENAI_MODEL", "gpt-4o-mini"),
            llm_routes=routes,
            circuit_max_failures=max_failures,
            circuit_cooldown_seconds=cooldown,
        )


def build_llm_router(config: Optional[LLMConfig] = None,
                     usage_recorder: Optional[UsageRecorder] = None) -> LLMRouter:
    """构建默认供应商链：deepseek → openai → qwen → claude → simulated

    支持 purpose 路由（LLMConfig.llm_routes）与熔断（circuit_* 配置）。
    """
    cfg = config or LLMConfig.from_env()
    providers: List[LLMProvider] = [
        DeepSeekProvider({
            "api_key": cfg.deepseek_api_key,
            "base_url": cfg.deepseek_base_url,
            "model": cfg.deepseek_model,
        }),
        OpenAIProvider({
            "api_key": cfg.openai_api_key,
            "base_url": cfg.openai_base_url,
            "model": cfg.openai_model,
        }),
        QwenProvider({
            "api_key": os.getenv("DSH_QWEN_API_KEY") or os.getenv("DSH_LLM_API_KEY", ""),
            "base_url": os.getenv("DSH_QWEN_BASE_URL", "https://dashscope.aliyuncs.com"),
            "model": os.getenv("DSH_QWEN_MODEL", "qwen-turbo"),
        }),
        ClaudeProvider({
            "api_key": os.getenv("DSH_CLAUDE_API_KEY") or os.getenv("DSH_LLM_API_KEY", ""),
            "base_url": os.getenv("DSH_CLAUDE_BASE_URL", "https://api.anthropic.com"),
            "model": os.getenv("DSH_CLAUDE_MODEL", "claude-3-5-sonnet-20241022"),
        }),
        SimulatedProvider(),
    ]
    circuit = None
    if cfg.circuit_max_failures > 0:
        circuit = CircuitConfig(
            max_failures=cfg.circuit_max_failures,
            cooldown_seconds=cfg.circuit_cooldown_seconds,
        )
    return LLMRouter(providers, usage_recorder=usage_recorder,
                     routes=cfg.llm_routes or None, circuit=circuit)