"""DSH AI 中台 - 优化引擎（M12 自动化优化引擎）

面向自动化优化的一组能力：流程固化（12.1）、Token 优化（12.2）、
模板管理（12.3）、自动化执行（12.4）。统一入口为 :class:`OptimizationEngine`。

快速开始（对应开发计划 M12.4 快速开始示例）：

    from dsh_core.optimization import OptimizationEngine
    engine = OptimizationEngine()
    await engine.initialize()
    result = await engine.execute("生成周报")
    print(result.token_used)  # 首次全量 15000，命中缓存后 1000 以内
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dsh_core.optimization.automation_engine import AutomationEngine
from dsh_core.optimization.base import (
    AutomationResult,
    OptimizedTask,
    Process,
    ProcessStep,
    Schedule,
    Template,
    TokenUsage,
    now_iso,
)
from dsh_core.optimization.process_solidifier import ProcessSolidifier
from dsh_core.optimization.template_manager import TemplateManager
from dsh_core.optimization.token_optimizer import TokenOptimizer, estimate_full

__all__ = [
    "AutomationEngine",
    "AutomationResult",
    "OptimizationEngine",
    "OptimizedTask",
    "Process",
    "ProcessSolidifier",
    "ProcessStep",
    "Schedule",
    "Template",
    "TemplateManager",
    "TokenOptimizer",
    "TokenUsage",
    "now_iso",
]


class OptimizationEngine:
    """优化引擎统一门面。

    组装流程固化器 + Token 优化器 + 模板管理器 + 自动化执行引擎，
    对外提供「一次执行」入口：

    - 命中 Token 缓存：直接返回缓存结果（cost ≈ 全量 10%）；
    - 语义匹配模板：按模板确定性执行；
    - 否则：探索执行（首次全量成本，执行后回写缓存）。

    另转发常用运维/调度能力：has_template / approve / add_schedule /
    register_event / run_due_schedules / execution_log / usage_stats。
    """

    def __init__(
        self,
        *,
        solidifier: Optional[ProcessSolidifier] = None,
        optimizer: Optional[TokenOptimizer] = None,
        templates: Optional[TemplateManager] = None,
        automation: Optional[AutomationEngine] = None,
    ):
        self.solidifier = solidifier or ProcessSolidifier()
        self.optimizer = optimizer or TokenOptimizer()
        self.templates = templates or TemplateManager()
        self.automation = automation or AutomationEngine(
            templates=self.templates,
            optimizer=self.optimizer,
        )

    async def initialize(self) -> None:
        """初始化全部组件（建库建表 / 注入种子模板）。"""
        await self.solidifier.initialize()
        await self.optimizer.initialize()
        await self.templates.initialize()
        await self.automation.initialize()

    async def close(self) -> None:
        """关闭全部组件连接。"""
        await self.automation.close()
        await self.templates.close()
        await self.optimizer.close()
        await self.solidifier.close()

    # ---------- 核心执行 ----------

    async def execute(
        self,
        task: str,
        *,
        require_approval: bool = False,
        meta: Optional[Dict[str, Any]] = None,
    ) -> AutomationResult:
        """执行一次自动化流程，自动走 缓存 / 模板 / 探索 三层路径。"""
        task = (task or "").strip()
        if not task:
            raise ValueError("task 不能为空")

        # 第一层：Token 缓存命中 => 直接复用
        opt = await self.optimizer.optimize(task)
        if opt.cached:
            return AutomationResult(
                run_id=f"c{int(__import__('time').time() * 1000)}",
                task=task,
                success=True,
                source="cache",
                token_used=opt.token_used,
                tokens_saved=max(0, opt.meta.get("full_tokens", opt.token_used) - opt.token_used),
                duration_ms=1,
                summary="复用 Token 缓存（回放成本 ≤ 全量 10%）",
                output=opt.cached_content or "",
                meta={
                    **(meta or {}),
                    "hit_count": opt.meta.get("hit_count", 1),
                    "cached": True,
                },
            )

        # 第二层：交给自动化执行引擎（模板 / 探索，执行后回写缓存）
        return await self.automation.execute(task, require_approval=require_approval, meta=meta)

    # ---------- 转发能力 ----------

    async def has_template(self, task: str) -> bool:
        """是否存在语义匹配的模板。"""
        return await self.automation.has_template(task)

    async def approve(self, run_id: str, *, approved: bool = True) -> Optional[AutomationResult]:
        """人工审批入口。"""
        return await self.automation.approve(run_id, approved=approved)

    @property
    def pending_tasks(self) -> List[str]:
        """挂起等待审批的任务。"""
        return self.automation.pending_tasks

    def add_schedule(
        self,
        task: str,
        *,
        interval_seconds: int = 3600,
        cron_expr: str = "",
        trigger: str = "interval",
        enabled: bool = True,
    ) -> Schedule:
        """登记定时调度。"""
        return self.automation.add_schedule(
            task,
            interval_seconds=interval_seconds,
            cron_expr=cron_expr,
            trigger=trigger,
            enabled=enabled,
        )

    def remove_schedule(self, schedule_id: str) -> bool:
        """移除定时调度。"""
        return self.automation.remove_schedule(schedule_id)

    def schedules(self) -> List[Schedule]:
        """当前定时调度登记。"""
        return self.automation.schedules()

    async def run_due_schedules(self) -> List[AutomationResult]:
        """执行到期的定时调度。"""
        return await self.automation.run_due_schedules()

    def register_event(self, event: str, task: str) -> None:
        """事件绑定。"""
        self.automation.register_event(event, task)

    async def trigger_event(self, event: str) -> List[AutomationResult]:
        """触发事件执行。"""
        return await self.automation.trigger_event(event)

    def execution_log(self) -> List[AutomationResult]:
        """全部执行记录。"""
        return self.automation.execution_log()

    async def usage_stats(self) -> Dict[str, Any]:
        """Token 用量汇总。"""
        return await self.automation.usage_stats()

    def cached_count(self) -> int:
        """Token 缓存条目数。"""
        return self.automation.cached_count()