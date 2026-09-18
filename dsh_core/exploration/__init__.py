"""DSH 探索引擎（M11）

通过「问题分解 → 方案搜索 → 试错实验 → 知识沉淀」的流水线探索新领域任务。
知识库已沉淀的相似任务会直接复用，避免重复探索。

用法示例（对应开发计划 M11 快速开始）：
    from dsh_core.exploration import ExplorationEngine

    engine = ExplorationEngine()
    result = await engine.explore("分析2026年Q1销售数据")
    print(result.solution)
"""
from dsh_core.exploration.base import (
    ExploreResult,
    ExperimentResult,
    Hypothesis,
    Knowledge,
    Result,
    Solution,
    SolutionCandidate,
    SubTask,
)
from dsh_core.exploration.decomposer import TaskDecomposer
from dsh_core.exploration.engine import ExplorationEngine
from dsh_core.exploration.experimenter import Experimenter
from dsh_core.exploration.knowledge_base import KnowledgeBase
from dsh_core.exploration.solution_searcher import SolutionSearcher

__all__ = [
    "ExploreResult",
    "ExperimentResult",
    "Hypothesis",
    "Knowledge",
    "Result",
    "Solution",
    "SolutionCandidate",
    "SubTask",
    "TaskDecomposer",
    "ExplorationEngine",
    "Experimenter",
    "KnowledgeBase",
    "SolutionSearcher",
]