"""DSH 优化引擎 - 数据模型（M12）

覆盖四类产物：
  固化（Process）→ 模板（Template）→ 优化后任务（OptimizedTask）→ 自动执行（AutomationResult），
以及 Token 用量（TokenUsage）与定时调度（Schedule）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def now_iso() -> str:
    """UTC ISO 时间戳，供各记录使用"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ProcessStep:
    """固化流程中的一步：可执行动作（LLM 指令或系统命令描述）"""
    name: str
    action: str = ""                                  # 执行动作/提示词
    params: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    status: str = "pending"                           # pending | running | done | failed
    avg_duration_ms: int = 0                          # 历史平均耗时
    success_rate: float = 1.0                         # 历史成功率 0-1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "action": self.action,
            "params": dict(self.params),
            "depends_on": list(self.depends_on),
            "status": self.status,
            "avg_duration_ms": self.avg_duration_ms,
            "success_rate": self.success_rate,
        }


@dataclass
class Process:
    """固化流程：探索结果落盘为可重复执行的流程（12.1 产物）"""
    process_id: str
    name: str
    task: str
    steps: List[ProcessStep] = field(default_factory=list)
    description: str = ""
    version: int = 1
    base_version: Optional[int] = None                # 升级/回滚来源版本
    status: str = "active"                            # active | archived
    runs: int = 0
    success_count: int = 0
    success_rate: float = 1.0
    avg_duration_ms: int = 0
    created_at: str = field(default_factory=now_iso)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "process_id": self.process_id,
            "name": self.name,
            "task": self.task,
            "steps": [s.to_dict() for s in self.steps],
            "description": self.description,
            "version": self.version,
            "base_version": self.base_version,
            "status": self.status,
            "runs": self.runs,
            "success_count": self.success_count,
            "success_rate": self.success_rate,
            "avg_duration_ms": self.avg_duration_ms,
            "created_at": self.created_at,
            "meta": dict(self.meta),
        }


@dataclass
class OptimizedTask:
    """Token 优化结果：缓存命中即低成本复用（12.2 产物）"""
    task: str
    status: str                                       # cached | planned
    token_used: int = 0                               # 本次实际消耗
    tokens_saved: int = 0                             # 较首次全量节省
    saved_ratio: float = 0.0                          # 0-1 节省比例
    cached: bool = False
    cached_content: str = ""                          # 缓存命中时直接复用
    plan: List[str] = field(default_factory=list)     # 未命中时给出的执行计划
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "status": self.status,
            "token_used": self.token_used,
            "tokens_saved": self.tokens_saved,
            "saved_ratio": self.saved_ratio,
            "cached": self.cached,
            "plan": list(self.plan),
            "meta": dict(self.meta),
        }


@dataclass
class Template:
    """流程模板：固化流程的模板化，供推荐与复用（12.3 产物）"""
    template_id: str
    name: str
    task: str
    steps: List[ProcessStep] = field(default_factory=list)
    description: str = ""
    version: int = 1
    use_count: int = 0
    success_count: int = 0
    success_rate: float = 1.0
    created_at: str = field(default_factory=now_iso)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "template_id": self.template_id,
            "name": self.name,
            "task": self.task,
            "steps": [s.to_dict() for s in self.steps],
            "description": self.description,
            "version": self.version,
            "use_count": self.use_count,
            "success_count": self.success_count,
            "success_rate": self.success_rate,
            "created_at": self.created_at,
            "meta": dict(self.meta),
        }


@dataclass
class TokenUsage:
    """Token 用量记录：写往 reports/token_usage/ 的审计条目"""
    usage_id: str
    task: str
    module: str           # explore | optimize | template | automate
    token_used: int = 0
    tokens_full: int = 0  # 若走全量探索的估算成本
    cached: bool = False
    created_at: str = field(default_factory=now_iso)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "usage_id": self.usage_id,
            "task": self.task,
            "module": self.module,
            "token_used": self.token_used,
            "tokens_full": self.tokens_full,
            "cached": self.cached,
            "saved_ratio": (1 - self.token_used / self.tokens_full)
                           if self.tokens_full and self.token_used <= self.tokens_full
                           else 0.0,
            "created_at": self.created_at,
            "meta": dict(self.meta),
        }


@dataclass
class AutomationResult:
    """自动执行结果：AutomationEngine.execute 的输出（快速开始）"""
    run_id: str
    task: str
    success: bool = False
    source: str = "exploration"   # cache | template | exploration
    token_used: int = 0
    tokens_saved: int = 0
    duration_ms: int = 0
    summary: str = ""
    output: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task": self.task,
            "success": self.success,
            "source": self.source,
            "token_used": self.token_used,
            "tokens_saved": self.tokens_saved,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "output": self.output,
            "meta": dict(self.meta),
        }


@dataclass
class Schedule:
    """定时调度：周期/complex 时间/事件触发执行的登记（12.4）"""
    schedule_id: str
    task: str
    interval_seconds: int = 0                 # interval 触发：间隔秒数
    cron_expr: str = ""                       # cron 触发："*/5 * * * *"
    trigger: str = "interval"                 # interval | cron | event
    enabled: bool = True
    last_run: str = ""
    run_count: int = 0
    created_at: str = field(default_factory=now_iso)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "task": self.task,
            "interval_seconds": self.interval_seconds,
            "cron_expr": self.cron_expr,
            "trigger": self.trigger,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "run_count": self.run_count,
            "created_at": self.created_at,
            "meta": dict(self.meta),
        }