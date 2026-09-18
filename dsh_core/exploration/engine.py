"""DSH 探索引擎 - 端到端编排（M11 快速开始）

流水线：问题分解 → 知识检索 → 方案搜索 → 试错实验 → 知识沉淀。
命中历史知识时直接复用（探索成功）；有候选时先实验再采纳；
全部失败沉淀失败教训供后续规避。可选写入 JSON 探索报告。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, List, Optional

from dsh_core.exploration.base import (
    ExploreResult, Hypothesis, Result, Solution, SolutionCandidate,
)
from dsh_core.exploration.decomposer import TaskDecomposer
from dsh_core.exploration.experimenter import Experimenter
from dsh_core.exploration.knowledge_base import KnowledgeBase
from dsh_core.exploration.solution_searcher import SolutionSearcher

logger = logging.getLogger("dsh.exploration")

DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[2] / "reports" / "exploration"


class ExplorationEngine:
    """探索引擎：explore(task) 一键完成 分解→搜索→实验→沉淀

    通过 comps（M2 组合装配）可注入 llm；也支持直接构造。
    """

    def __init__(self, llm: Optional[Any] = None,
                 knowledge_base: Optional[KnowledgeBase] = None,
                 decomposer: Optional[TaskDecomposer] = None,
                 searcher: Optional[SolutionSearcher] = None,
                 experimenter: Optional[Experimenter] = None,
                 comps: Optional[Any] = None,
                 report_dir: Optional[str] = None):
        # M2 组合装配：外部注入 comps.llm
        if llm is None and comps is not None:
            llm = getattr(comps, "llm", None)
        self.llm = llm
        self.knowledge_base = knowledge_base or KnowledgeBase()
        self.decomposer = decomposer or TaskDecomposer(llm)
        self.searcher = searcher or SolutionSearcher(self.knowledge_base, llm)
        self.experimenter = experimenter or Experimenter(llm)
        self.report_dir = Path(report_dir) if report_dir else DEFAULT_REPORT_DIR
        self._initialized = False

    async def initialize(self) -> None:
        """初始化知识库（幂等）"""
        if not self._initialized:
            await self.knowledge_base.initialize()
            self._initialized = True

    async def close(self) -> None:
        """关闭引擎（知识库按操作开连接，无长期资源，仅复位状态）"""
        await self.knowledge_base.close()
        self._initialized = False

    async def explore(self, task: str, *, executor: Optional[Any] = None,
                      dataset_size: int = 0,
                      save_report: bool = False) -> ExploreResult:
        """端到端探索：分解 → 知识检索 → 方案搜索 → 实验 → 沉淀"""
        await self.initialize()
        task = (task or "").strip()
        started = time.monotonic()
        result = ExploreResult(task=task)

        # 1. 问题分解
        result.subtasks = await self.decomposer.decompose(task)
        if not result.subtasks:
            result.summary = "任务为空，未开始探索"
            return result

        # 2. 知识库历史检索：命中即复用 → 探索成功
        result.knowledge = await self.knowledge_base.search(task, limit=5)
        if result.knowledge:
            best = result.knowledge[0]
            result.solution = Solution(
                solution_id=uuid.uuid4().hex[:16],
                task=task,
                steps=self._content_to_steps(best.content),
                source_type="knowledge",
                result_summary=f"复用历史方案（知识条目 {best.knowledge_id}）",
            )
            result.success = True
            result.summary = f"命中历史方案，直接复用（知识条目 {best.knowledge_id}）"
            await self._save_report(result, started, save_report)
            return result

        # 3. 方案搜索
        result.candidates = await self.searcher.search(task, limit=5)
        best_candidate = result.candidates[0] if result.candidates else None

        # 4. 试错实验
        hypothesis = self._build_hypothesis(task, best_candidate)
        experiment = await self.experimenter.experiment(
            hypothesis, executor=executor, dataset_size=dataset_size)
        result.experiment = experiment

        if experiment.success:
            result.solution = Solution(
                solution_id=uuid.uuid4().hex[:16],
                task=task,
                steps=(self._content_to_steps(best_candidate.description)
                       if best_candidate else [task]),
                source_type="exploration",
                result_summary=experiment.details,
            )
            await self.knowledge_base.store(
                result.solution,
                Result(task=task, success=True, summary=experiment.details,
                       metric=experiment.metric),
            )
            result.success = True
            result.summary = f"实验通过（{experiment.attempts} 次尝试），方案已沉淀"
        else:
            failed = Solution(solution_id=uuid.uuid4().hex[:16], task=task,
                              steps=[task], source_type="exploration")
            await self.knowledge_base.store_failure(failed, experiment.details)
            result.success = False
            result.summary = f"实验未通过（{experiment.attempts} 次尝试），教训已沉淀"

        await self._save_report(result, started, save_report)
        return result

    # ---------- 组装辅助 ----------
    @staticmethod
    def _build_hypothesis(task: str,
                          candidate: Optional[SolutionCandidate]) -> Hypothesis:
        if candidate is not None:
            return Hypothesis(
                statement=candidate.title,
                approach=candidate.description,
                expected_result=f"完成「{task}」",
                confidence=min(0.9, 0.4 + candidate.similarity),
            )
        return Hypothesis(
            statement=f"完成「{task}」",
            approach=task,
            expected_result=f"完成「{task}」",
            confidence=0.5,
        )

    @staticmethod
    def _content_to_steps(content: str) -> List[str]:
        return [line.strip() for line in (content or "").splitlines()
                if line.strip()]

    async def _save_report(self, result: ExploreResult, started: float,
                           enabled: bool) -> None:
        """探索报告：写入 reports/exploration/explore_<时间戳>_<随机>.json"""
        if not enabled:
            return
        try:
            self.report_dir.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            payload = result.to_dict()
            payload["duration_ms"] = int((time.monotonic() - started) * 1000)
            fname = f"explore_{timestamp}_{uuid.uuid4().hex[:6]}.json"
            (self.report_dir / fname).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError as exc:
            logger.warning("探索报告写入失败: %s", exc)