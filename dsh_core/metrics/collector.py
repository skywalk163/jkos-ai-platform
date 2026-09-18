"""DSH 监控指标 - Prometheus 指标收集（M3 任务 3.4）

实现 Prometheus 指标收集，用于监控工作流执行、LLM 调用、审批决策等。
"""
from __future__ import annotations

import abc
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dsh.metrics")


# ─── 指标基类 ───

class Metric(abc.ABC):
    """指标基类"""

    @abc.abstractmethod
    def name(self) -> str:
        """指标名称"""
        pass

    @abc.abstractmethod
    def value(self) -> float:
        """指标值"""
        pass

    @abc.abstractmethod
    def labels(self) -> Dict[str, str]:
        """指标标签"""
        pass


# ─── 计数器 ───

class Counter(Metric):
    """计数器：只增不减"""

    def __init__(self, name: str, description: str = "", label_names: Optional[List[str]] = None):
        self._name = name
        self._description = description
        self._label_names = label_names or []
        self._values: Dict[str, float] = {}

    def name(self) -> str:
        return self._name

    def value(self, labels: Optional[Dict[str, str]] = None) -> float:
        key = self._make_key(labels)
        return self._values.get(key, 0.0)

    def labels(self) -> Dict[str, str]:
        return {"description": self._description}

    def inc(self, labels: Optional[Dict[str, str]] = None, amount: float = 1.0) -> None:
        key = self._make_key(labels)
        self._values[key] = self._values.get(key, 0.0) + amount

    def _make_key(self, labels: Optional[Dict[str, str]]) -> str:
        if not labels:
            return ""
        return "|".join(f"{k}={labels[k]}" for k in sorted(labels.keys()))


# ─── 直方图 ───

class Histogram(Metric):
    """直方图：用于统计分布"""

    def __init__(self, name: str, description: str = "", label_names: Optional[List[str]] = None):
        self._name = name
        self._description = description
        self._label_names = label_names or []
        self._values: Dict[str, List[float]] = {}

    def name(self) -> str:
        return self._name

    def value(self, labels: Optional[Dict[str, str]] = None) -> float:
        key = self._make_key(labels)
        values = self._values.get(key, [])
        return sum(values) / len(values) if values else 0.0

    def labels(self) -> Dict[str, str]:
        return {"description": self._description}

    def observe(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        key = self._make_key(labels)
        if key not in self._values:
            self._values[key] = []
        self._values[key].append(value)

    def _make_key(self, labels: Optional[Dict[str, str]]) -> str:
        if not labels:
            return ""
        return "|".join(f"{k}={labels[k]}" for k in sorted(labels.keys()))


# ─── 指标收集器 ───

class MetricsCollector:
    """指标收集器：收集和管理所有指标"""

    def __init__(self):
        self.counters: Dict[str, Counter] = {}
        self.histograms: Dict[str, Histogram] = {}

    def register_counter(self, name: str, description: str = "", label_names: Optional[List[str]] = None) -> Counter:
        """注册计数器"""
        counter = Counter(name, description, label_names)
        self.counters[name] = counter
        return counter

    def register_histogram(self, name: str, description: str = "", label_names: Optional[List[str]] = None) -> Histogram:
        """注册直方图"""
        histogram = Histogram(name, description, label_names)
        self.histograms[name] = histogram
        return histogram

    def get_counter(self, name: str) -> Optional[Counter]:
        """获取计数器"""
        return self.counters.get(name)

    def get_histogram(self, name: str) -> Optional[Histogram]:
        """获取直方图"""
        return self.histograms.get(name)

    def to_prometheus_format(self) -> str:
        """转换为 Prometheus 格式"""
        lines = []

        for name, counter in self.counters.items():
            lines.append(f"# HELP {name} {counter.labels().get('description', '')}")
            lines.append(f"# TYPE {name} counter")
            for key, value in counter._values.items():
                if key:
                    labels = dict(item.split("=") for item in key.split("|"))
                    label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
                    lines.append(f"{name}{{{label_str}}} {value}")
                else:
                    lines.append(f"{name} {value}")

        for name, histogram in self.histograms.items():
            lines.append(f"# HELP {name} {histogram.labels().get('description', '')}")
            lines.append(f"# TYPE {name} histogram")
            for key, values in histogram._values.items():
                if values:
                    avg = sum(values) / len(values)
                    if key:
                        labels = dict(item.split("=") for item in key.split("|"))
                        label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
                        lines.append(f"{name}{{{label_str}}} {avg}")
                    else:
                        lines.append(f"{name} {avg}")

        return "\n".join(lines)


# ─── 预定义指标 ───

class DSHMetrics:
    """DSH 预定义指标"""

    def __init__(self, collector: Optional[MetricsCollector] = None):
        self.collector = collector or MetricsCollector()

        # 工作流指标
        self.workflow_executions = self.collector.register_counter(
            "dsh_workflow_executions_total",
            "工作流执行总数",
            ["workflow_code", "status"]
        )
        self.workflow_duration = self.collector.register_histogram(
            "dsh_workflow_duration_seconds",
            "工作流执行耗时",
            ["workflow_code"]
        )

        # LLM 指标
        self.llm_calls = self.collector.register_counter(
            "dsh_llm_calls_total",
            "LLM 调用总数",
            ["provider", "model"]
        )
        self.llm_tokens = self.collector.register_counter(
            "dsh_llm_tokens_total",
            "LLM Token 总数",
            ["provider", "type"]
        )

        # 审批指标
        self.approval_decisions = self.collector.register_counter(
            "dsh_approval_decisions_total",
            "审批决策总数",
            ["mode", "decision"]
        )
        self.approval_timeouts = self.collector.register_counter(
            "dsh_approval_timeouts_total",
            "审批超时总数",
            ["risk"]
        )

    def record_workflow(self, workflow_code: str, status: str, duration: float) -> None:
        """记录工作流执行"""
        self.workflow_executions.inc(
            labels={"workflow_code": workflow_code, "status": status}
        )
        self.workflow_duration.observe(duration, labels={"workflow_code": workflow_code})

    def record_llm_call(self, provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        """记录 LLM 调用"""
        self.llm_calls.inc(labels={"provider": provider, "model": model})
        self.llm_tokens.inc(labels={"provider": provider, "type": "prompt"}, amount=prompt_tokens)
        self.llm_tokens.inc(labels={"provider": provider, "type": "completion"}, amount=completion_tokens)

    def record_approval(self, mode: str, decision: str) -> None:
        """记录审批决策"""
        self.approval_decisions.inc(labels={"mode": mode, "decision": decision})

    def record_timeout(self, risk: str) -> None:
        """记录审批超时"""
        self.approval_timeouts.inc(labels={"risk": risk})


# ─── 全局指标实例 ───

_metrics: Optional[DSHMetrics] = None


def get_metrics() -> DSHMetrics:
    """获取全局指标实例"""
    global _metrics
    if _metrics is None:
        _metrics = DSHMetrics()
    return _metrics
