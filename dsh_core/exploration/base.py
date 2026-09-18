"""DSH 探索引擎 - 数据模型（M11）

流水线四阶段的共享数据结构：
分解（SubTask）→ 搜索（SolutionCandidate）→ 实验（Hypothesis/ExperimentResult）
→ 沉淀（Knowledge），端到端输出 ExploreResult。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def now_iso() -> str:
    """UTC ISO 时间戳，供知识条目/结果记录使用"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SubTask:
    """子任务：问题分解产物，可执行工作单元"""
    name: str
    description: str = ""
    depends_on: List[str] = field(default_factory=list)
    depth: int = 1                    # 层级（1 ~ 5）
    status: str = "pending"           # pending | running | done | failed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "depth": self.depth,
            "status": self.status,
        }


@dataclass
class SolutionCandidate:
    """方案候选：搜索与匹配的一个命中"""
    solution_id: str
    title: str
    description: str = ""
    source_type: str = "knowledge"    # knowledge | code | doc
    source_ref: str = ""
    similarity: float = 0.0           # 0.0-1.0 相关度
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "solution_id": self.solution_id,
            "title": self.title,
            "description": self.description,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "similarity": self.similarity,
            "meta": dict(self.meta),
        }


@dataclass
class Hypothesis:
    """实验假说：试错学习的输入"""
    statement: str
    approach: str = ""
    expected_result: str = ""
    confidence: float = 0.5           # 0.0-1.0


@dataclass
class ExperimentResult:
    """实验结果：试错学习的一次完整输出"""
    hypothesis: Hypothesis
    success: bool
    metric: Optional[float] = None
    metric_name: str = ""
    details: str = ""
    sample_size: int = 0
    total_size: int = 0
    duration_ms: int = 0
    attempts: int = 1
    scale_up: bool = False            # 是否放大到全量
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "metric": self.metric,
            "metric_name": self.metric_name,
            "details": self.details,
            "sample_size": self.sample_size,
            "total_size": self.total_size,
            "duration_ms": self.duration_ms,
            "attempts": self.attempts,
            "scale_up": self.scale_up,
            "hypothesis": {
                "statement": self.hypothesis.statement,
                "approach": self.hypothesis.approach,
                "confidence": self.hypothesis.confidence,
            },
            "meta": dict(self.meta),
        }


@dataclass
class Solution:
    """解决方案：实验成功后沉淀为知识的内容"""
    solution_id: str
    task: str
    steps: List[str] = field(default_factory=list)
    source_type: str = "exploration"  # exploration | knowledge（历史复用）
    result_summary: str = ""


@dataclass
class Result:
    """任务执行结果：方案被采纳后的落地结果"""
    task: str
    success: bool
    summary: str = ""
    metric: Optional[float] = None
    created_at: str = field(default_factory=now_iso)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Knowledge:
    """知识条目：知识库中的一个沉淀记录"""
    knowledge_id: str
    task: str
    content: str
    category: str = "solution"        # solution | failure
    outcome: str = "success"          # success | failure
    created_at: str = field(default_factory=now_iso)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "knowledge_id": self.knowledge_id,
            "task": self.task,
            "content": self.content,
            "category": self.category,
            "outcome": self.outcome,
            "created_at": self.created_at,
            "meta": dict(self.meta),
        }


@dataclass
class ExploreResult:
    """探索结果：ExplorationEngine.explore 的端到端输出"""
    task: str
    success: bool = False
    subtasks: List[SubTask] = field(default_factory=list)
    candidates: List[SolutionCandidate] = field(default_factory=list)
    knowledge: List[Knowledge] = field(default_factory=list)
    experiment: Optional[ExperimentResult] = None
    solution: Optional[Solution] = None
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "success": self.success,
            "summary": self.summary,
            "subtasks": [s.to_dict() for s in self.subtasks],
            "candidates": [c.to_dict() for c in self.candidates],
            "knowledge": [k.to_dict() for k in self.knowledge],
            "experiment": self.experiment.to_dict() if self.experiment else None,
            "solution": {
                "solution_id": self.solution.solution_id,
                "task": self.solution.task,
                "steps": list(self.solution.steps),
                "source_type": self.solution.source_type,
            } if self.solution else None,
        }