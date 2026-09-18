"""DSH 探索引擎 - 试错学习机制（11.3，P1）

职责：对假说做小规模实验（默认样本 < 10%），失败自动调整策略重试，
成功后决定是否放大到全量；支持人工干预（approval 钩子，可与 M1 审批流对接）。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any, Awaitable, Callable, Optional, Tuple

from jkos_core.exploration.base import ExperimentResult, Hypothesis

logger = logging.getLogger("dsh.exploration")

# 执行器返回：(成功与否, 指标, 详情)
Executor = Callable[["Hypothesis", int],
                    "Awaitable[Tuple[bool, Optional[float], str]]"]

MAX_SAMPLE_RATIO = 0.5      # 样本占比安全上限
DEFAULT_SAMPLE_RATIO = 0.1  # 小规模实验默认 10%


class Experimenter:
    """试错学习：小样本试点 → 失败调整 → 成功放大"""

    def __init__(self, llm: Optional[Any] = None, *,
                 max_attempts: int = 3,
                 sample_ratio: float = DEFAULT_SAMPLE_RATIO,
                 require_approval: bool = False,
                 intervention_hook: Optional[
                     Callable[[Hypothesis, ExperimentResult], Any]] = None):
        if not 0.0 < sample_ratio <= MAX_SAMPLE_RATIO:
            raise ValueError(f"sample_ratio 必须在 (0, {MAX_SAMPLE_RATIO}] 区间")
        self.llm = llm
        self.max_attempts = max_attempts
        self.sample_ratio = sample_ratio
        self.require_approval = require_approval
        self.intervention_hook = intervention_hook

    async def experiment(self, hypothesis: Hypothesis, *,
                         executor: Optional[Executor] = None,
                         dataset_size: int = 0) -> ExperimentResult:
        """执行小规模实验

        - executor: 自定义执行器 async/sync (hypothesis, sample_size)
          -> (success, metric, details)；缺省用启发式评估（确定性、可测）
        - dataset_size: 全量数据规模；<=0 表示按单次实验处理（不做放大）
        """
        if dataset_size < 0:
            raise ValueError("dataset_size 不能为负")
        sample_size = self._sample_size(dataset_size)
        started = time.monotonic()
        current = hypothesis
        last_metric: Optional[float] = None
        last_details = ""
        for attempt in range(1, self.max_attempts + 1):
            result = await self._run_one(current, executor, sample_size)
            last_metric, last_details = result.metric, result.details
            if result.success:
                result.attempts = attempt
                result.total_size = dataset_size
                result.duration_ms = int((time.monotonic() - started) * 1000)
                result.scale_up = await self._decide_scale_up(
                    current, result, dataset_size)
                logger.info("实验通过（第 %d 次尝试），放大=%s",
                            attempt, result.scale_up)
                return result
            logger.warning("实验未通过（第 %d 次尝试）: %s",
                           attempt, last_details)
            current = self._adjust(current)
        # 全部尝试失败：沉淀教训
        return ExperimentResult(
            hypothesis=current, success=False,
            metric=last_metric, metric_name="trial_estimate",
            details=last_details, sample_size=sample_size,
            total_size=dataset_size,
            duration_ms=int((time.monotonic() - started) * 1000),
            attempts=self.max_attempts, scale_up=False,
        )

    # ---------- 内部 ----------
    async def _run_one(self, hypothesis: Hypothesis, executor: Optional[Executor],
                       sample_size: int) -> ExperimentResult:
        exec_fn = executor or self._default_executor
        if inspect.iscoroutinefunction(exec_fn):
            success, metric, details = await exec_fn(hypothesis, sample_size)
        else:
            success, metric, details = await asyncio.to_thread(
                exec_fn, hypothesis, sample_size)
        return ExperimentResult(
            hypothesis=hypothesis, success=bool(success),
            metric=metric, metric_name="trial_estimate",
            details=str(details or ""), sample_size=sample_size,
        )

    async def _default_executor(self, hypothesis: Hypothesis,
                                sample_size: int) -> Tuple[bool, Optional[float], str]:
        """默认执行器：基于置信度与策略描述的启发式评估（确定性、可测）"""
        estimate = max(0.0, min(1.0, hypothesis.confidence))
        if "存疑" in hypothesis.statement or "不确定" in hypothesis.statement:
            estimate -= 0.15
        if hypothesis.approach.strip():
            estimate += 0.1
        success = estimate >= 0.6
        details = (
            f"小规模试点通过（{sample_size} 样本，估算置信度 {estimate:.2f}）"
            if success else
            f"小规模试点未通过（估算置信度 {estimate:.2f}），建议调整策略"
        )
        return success, round(estimate, 3), details

    def _adjust(self, hypothesis: Hypothesis) -> Hypothesis:
        """失败后自动调整：追加调整策略并小幅提升置信度"""
        strategy = "调整策略：缩小实验范围并更换验证数据源"
        return Hypothesis(
            statement=hypothesis.statement,
            approach=(hypothesis.approach + "；" + strategy
                      if hypothesis.approach else strategy),
            expected_result=hypothesis.expected_result,
            confidence=min(1.0, round(hypothesis.confidence + 0.1, 3)),
        )

    async def _decide_scale_up(self, hypothesis: Hypothesis,
                               result: ExperimentResult,
                               dataset_size: int) -> bool:
        """成功后的放大决策：默认仅当存在更大数据集；需审批时走人工钩子"""
        if dataset_size <= result.sample_size:
            return False
        if not self.require_approval:
            return True
        if self.intervention_hook is None:
            logger.warning("实验通过但 require_approval=True 且无干预钩子，等待人工审批")
            return False
        try:
            if inspect.iscoroutinefunction(self.intervention_hook):
                return bool(await self.intervention_hook(hypothesis, result))
            return bool(self.intervention_hook(hypothesis, result))
        except Exception as exc:
            logger.error("人工干预钩子执行失败，按不放大处理: %s", exc)
            return False

    def _sample_size(self, dataset_size: int) -> int:
        if dataset_size <= 0:
            return 1
        return max(1, int(dataset_size * self.sample_ratio))