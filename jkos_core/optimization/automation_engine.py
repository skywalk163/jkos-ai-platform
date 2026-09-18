"""DSH 优化引擎 - 自动化执行引擎（12.4）

自动化执行引擎 - 执行自动化流程。调度化、事件化地把「固化流程」变为
一键/定时/事件触发的自动化执行：

1. 有模板：使用模板执行（省力模式，复用确定性产出）；
2. 无模板：探索执行（探索模式），按 10 个子任务估算全量成本；
3. 支持定时执行（interval / cron）与事件触发执行；
4. 支持人工审批环节（探索类任务先挂起等待审批）；
5. 每次执行写完整审计 JSON 日志（reports/automation/），并回写
   Token 用量（reports/token_usage/），命中缓存后回放成本 ≤ 全量 10%。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from jkos_core.optimization.base import AutomationResult, Schedule, TokenUsage, now_iso
from jkos_core.optimization.template_manager import TemplateManager
from jkos_core.optimization.token_optimizer import TokenOptimizer, estimate_full

# 探索模式下单个子任务的全量成本（模拟 分解/搜索/实验 各阶段提示词用量）
EXPLORE_BASE = 1500
# 默认探索拆解的子任务数 => 首次全量成本 = 10 * 1500 = 15000
EXPLORE_SUBTASKS = 10

# 自动执行审计日志目录
DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "reports" / "automation"


def _now_ms_id(prefix: str) -> str:
    """生成带前缀的唯一 ID。"""
    return f"{prefix}{int(time.time() * 1000)}{uuid.uuid4().hex[:4]}"


class AutomationEngine:
    """自动化执行引擎 - 执行自动化流程

    面向调度/事件触发的人工智能自动化执行器。用法（对应开发计划 M12.4 快速开始）：
        from jkos_core.optimization import AutomationEngine
        engine = AutomationEngine()
        await engine.initialize()
        result = await engine.execute("生成周报")
    """

    def __init__(
        self,
        *,
        templates: Optional[TemplateManager] = None,
        optimizer: Optional[TokenOptimizer] = None,
        log_dir: str | Path = DEFAULT_LOG_DIR,
    ):
        self._templates = templates or TemplateManager()
        self._optimizer = optimizer or TokenOptimizer()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 调度登记 / 事件触发登记
        self._schedules: Dict[str, Schedule] = {}
        self._sched_last_ts: Dict[str, float] = {}
        self._event_triggers: Dict[str, List[str]] = {}

        # 人工审批挂起表：run_id -> {task, meta, created_at}
        self._pending: Dict[str, Dict[str, Any]] = {}

        # 执行日志（内存 + 落盘 JSON）
        self.executions: List[AutomationResult] = []

    # ---------- 生命周期 ----------

    async def initialize(self) -> None:
        """初始化依赖（建库建表 / 注入种子模板）。"""
        await self._templates.initialize()
        await self._optimizer.initialize()

    async def close(self) -> None:
        """关闭依赖连接。"""
        await self._templates.close()
        await self._optimizer.close()

    # ---------- 核心执行 ----------

    async def has_template(self, task: str) -> bool:
        """是否存在语义匹配的模板（分支判断）。"""
        return await self._templates.has_template(task)

    async def execute(
        self,
        task: str,
        *,
        require_approval: bool = False,
        meta: Optional[Dict[str, Any]] = None,
    ) -> AutomationResult:
        """执行一次自动化流程。

        - 有模板：走省力模式（模板执行）；
        - 无模板 + require_approval=True：先挂起等待人工审批；
        - 无模板：走探索模式（全量成本，命中缓存后自动复用）。
        """
        task = (task or "").strip()
        if not task:
            raise ValueError("task 不能为空")
        if await self._templates.has_template(task):
            return await self.execute_with_template(task, meta=meta)
        if require_approval:
            return await self._create_pending(task, meta=meta)
        return await self.execute_with_exploration(task, meta=meta)

    async def execute_with_template(
        self,
        task: str,
        *,
        meta: Optional[Dict[str, Any]] = None,
    ) -> AutomationResult:
        """省力模式：推荐最相关模板并按其步骤确定性执行。"""
        template = await self._templates.recommend_template(task)
        result = await self._templates.execute_template(template)
        tokens_full = estimate_full(task)
        result.meta = dict(meta or {})
        result.meta.update({
            "tokens_full": tokens_full,
            "template_id": template.template_id,
            "template_name": template.name,
            "template_version": getattr(template, "version", 1),
        })
        if result.success:
            # 回写缓存：下次直接命中缓存，Token 节省 ≥90%
            await self._optimizer.store_cache(task, result.output, full_tokens=tokens_full)
        await self._optimizer.record_usage(
            task, "automate", result.token_used, tokens_full, cached=False
        )
        self._log(result)
        return result

    async def execute_with_exploration(
        self,
        task: str,
        *,
        meta: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
    ) -> AutomationResult:
        """探索模式：把任务拆解为 EXPLORE_SUBTASKS 个子任务并执行（首次全量成本）。"""
        subtasks = self._decompose(task)
        token_used = EXPLORE_BASE * max(1, len(subtasks))  # 默认 10 * 1500 = 15000
        tokens_full = token_used
        output = self._render_output(task, subtasks)
        duration_ms = max(1, int(len(subtasks) * 12))  # 模拟串行执行耗时
        result = AutomationResult(
            run_id=run_id or _now_ms_id("r"),
            task=task,
            success=True,
            source="exploration",
            token_used=token_used,
            tokens_saved=0,
            duration_ms=duration_ms,
            summary=f"探索执行：拆解为 {len(subtasks)} 个子任务并串行完成",
            output=output,
            meta={
                **(meta or {}),
                "tokens_full": tokens_full,
                "subtasks": len(subtasks),
                "approach": "exploration",
            },
        )
        await self._optimizer.store_cache(task, output, full_tokens=tokens_full)
        await self._optimizer.record_usage(
            task, "automate", token_used, tokens_full, cached=False
        )
        self._log(result)
        return result

    # ---------- 人工审批 ----------

    async def _create_pending(
        self,
        task: str,
        *,
        meta: Optional[Dict[str, Any]] = None,
    ) -> AutomationResult:
        """探索类任务要求人工审批时先挂起，等待 approve()。"""
        run_id = _now_ms_id("p")
        self._pending[run_id] = {
            "task": task,
            "meta": dict(meta or {}),
            "created_at": now_iso(),
        }
        result = AutomationResult(
            run_id=run_id,
            task=task,
            success=False,
            source="exploration",
            token_used=0,
            duration_ms=0,
            summary="等待人工审批后执行",
            meta={"status": "pending_approval", "approval_required": True, **dict(meta or {})},
        )
        self._log(result)
        return result

    @property
    def pending_tasks(self) -> List[str]:
        """当前挂起等待审批的任务。"""
        return list(self._pending.keys())

    async def approve(
        self,
        run_id: str,
        *,
        approved: bool = True,
    ) -> Optional[AutomationResult]:
        """人工审批：通过则继续探索执行，拒绝则标记 rejected。"""
        info = self._pending.pop(run_id, None)
        if info is None:
            return None
        task: str = info["task"]
        if not approved:
            result = AutomationResult(
                run_id=run_id,
                task=task,
                success=False,
                source="exploration",
                duration_ms=0,
                summary="已被人工拒绝，未执行",
                meta={"status": "rejected", **info["meta"]},
            )
            self._log(result)
            return result
        return await self.execute_with_exploration(task, meta=info["meta"], run_id=run_id)

    # ---------- 定时调度 ----------

    def add_schedule(
        self,
        task: str,
        *,
        interval_seconds: int = 3600,
        cron_expr: str = "",
        trigger: str = "interval",
        enabled: bool = True,
    ) -> Schedule:
        """登记一个定时调度（interval 秒级 / cron 表达式 / event）。"""
        schedule = Schedule(
            schedule_id=_now_ms_id("s"),
            task=task,
            interval_seconds=int(interval_seconds),
            cron_expr=cron_expr or "",
            trigger=trigger or "interval",
            enabled=enabled,
        )
        self._schedules[schedule.schedule_id] = schedule
        self._sched_last_ts[schedule.schedule_id] = time.time()
        return schedule

    def remove_schedule(self, schedule_id: str) -> bool:
        """移除一个调度。"""
        if schedule_id in self._schedules:
            self._schedules.pop(schedule_id, None)
            self._sched_last_ts.pop(schedule_id, None)
            return True
        return False

    def schedules(self) -> List[Schedule]:
        """当前所有调度登记。"""
        return list(self._schedules.values())

    async def run_due_schedules(
        self,
        *,
        now_ts: Optional[float] = None,
    ) -> List[AutomationResult]:
        """执行所有到期的 interval 调度，返回本次触发的结果列表。"""
        now_ts = now_ts if now_ts is not None else time.time()
        results: List[AutomationResult] = []
        for sid, schedule in list(self._schedules.items()):
            if not schedule.enabled or schedule.trigger != "interval":
                continue
            last = self._sched_last_ts.get(sid, 0.0)
            if now_ts - last >= schedule.interval_seconds:
                result = await self.execute(schedule.task)
                self._sched_last_ts[sid] = now_ts
                schedule.last_run = now_iso()
                schedule.run_count += 1
                results.append(result)
        return results

    # ---------- 事件触发 ----------

    def register_event(self, event: str, task: str) -> None:
        """把任务绑定到某个事件名，事件触发时自动执行。"""
        self._event_triggers.setdefault((event or "").strip(), []).append(task)

    async def trigger_event(
        self,
        event: str,
        *,
        require_approval: bool = False,
    ) -> List[AutomationResult]:
        """触发事件：执行所有绑定到该事件的任务。"""
        results: List[AutomationResult] = []
        for task in list(self._event_triggers.get((event or "").strip(), [])):
            results.append(await self.execute(task, require_approval=require_approval))
        return results

    # ---------- 日志 / 统计 ----------

    def _log(self, result: AutomationResult) -> None:
        """完整执行日志：内存记录 + 落盘 JSON。"""
        self.executions.append(result)
        path = self.log_dir / f"{result.run_id}.json"
        path.write_text(
            json.dumps({**result.to_dict(), "logged_at": now_iso()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def execution_log(self) -> List[AutomationResult]:
        """最近全部执行记录。"""
        return list(self.executions)

    async def usage_stats(self) -> Dict[str, Any]:
        """Token 用量汇总（委托给 TokenOptimizer）。"""
        return await self._optimizer.usage_stats()

    # ---------- 探索拆解工具 ----------

    @staticmethod
    def _decompose(task: str) -> List[str]:
        """把任务拆解为一组探索子任务（确定性、可审计）。"""
        return [
            f"分解任务目标：{task}",
            f"检索与该任务相关的最佳实践",
            f"评估候选解决方案",
            "选定最优执行方案",
            "设计具体执行步骤",
            "准备所需数据与依赖",
            "执行核心处理步骤",
            "校验中间结果并纠错",
            "整理最终交付物",
            "撰写执行总结报告",
        ][:EXPLORE_SUBTASKS]

    @staticmethod
    def _render_output(task: str, subtasks: List[str]) -> str:
        lines = [f"#{task} —— 探索执行报告", ""]
        for i, sub in enumerate(subtasks, 1):
            lines.append(f"[{i}/{len(subtasks)}] {sub}（已完成）")
        lines.append("")
        lines.append(f"共完成 {len(subtasks)} 个子任务，预执行结果已就绪。")
        return "\n".join(lines)

    # ---------- 便捷查询 ----------

    def cached_count(self) -> int:
        """当前 Token 缓存条目数。"""
        return self._optimizer.count_cache()

    @staticmethod
    def _usage(param: TokenUsage):
        """占位统一类型导出，避免未使用告警。"""
        return param