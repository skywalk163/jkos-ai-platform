"""M12 自动化执行引擎 - 调度 / 事件 / 审批 / 统计测试（M17 覆盖补齐）。"""

import time

import pytest

from jkos_core.optimization.automation_engine import (
    EXPLORE_BASE,
    EXPLORE_SUBTASKS,
    AutomationEngine,
)
from jkos_core.optimization.base import Schedule, TokenUsage

NOVEL_TASK = "fjlz-m12-auto-20260701 雷鸟高空走钢丝特技方案"


@pytest.mark.asyncio
async def test_execute_empty_task_raises(automation):
    """M12.4 空任务直接拒绝，防止无意义调度与计费。"""
    await automation.initialize()
    try:
        with pytest.raises(ValueError, match="task 不能为空"):
            await automation.execute("   ")
    finally:
        await automation.close()


@pytest.mark.asyncio
async def test_template_execution_is_cheap_and_writes_cache(automation, optimizer):
    """M12.4 命中模板走省力模式，执行后回写 Token 缓存供门面复用。"""
    await automation.initialize()
    try:
        result = await automation.execute("生成周报")
        assert result.source == "template"
        assert result.success is True
        assert result.token_used < 1000
        opt = await optimizer.optimize("生成周报")
        assert opt.cached is True
        assert opt.token_used <= opt.meta["full_tokens"] // 10
    finally:
        await automation.close()


@pytest.mark.asyncio
async def test_exploration_execution_backfills_cache(automation, optimizer):
    """M12.4 无模板走探索模式：首次全量成本并回写缓存。"""
    await automation.initialize()
    try:
        result = await automation.execute(NOVEL_TASK)
        assert result.success is True
        assert result.source == "exploration"
        assert result.token_used == EXPLORE_BASE * EXPLORE_SUBTASKS
        assert result.meta["subtasks"] == EXPLORE_SUBTASKS
        opt = await optimizer.optimize(NOVEL_TASK)
        assert opt.cached is True
    finally:
        await automation.close()


@pytest.mark.asyncio
async def test_approve_unknown_run_id_returns_none(automation):
    """M12.4 审批不存在的挂起单 → None。"""
    await automation.initialize()
    try:
        assert await automation.approve("p-missing-001") is None
    finally:
        await automation.close()


@pytest.mark.asyncio
async def test_approve_rejected_marks_rejected(automation):
    """M12.4 人工拒绝：标记 rejected 且不执行探索。"""
    await automation.initialize()
    try:
        pending = await automation.execute(NOVEL_TASK, require_approval=True)
        assert pending.success is False
        assert pending.meta["status"] == "pending_approval"
        assert automation.pending_tasks == [pending.run_id]
        result = await automation.approve(pending.run_id, approved=False)
        assert result is not None
        assert result.success is False
        assert result.meta["status"] == "rejected"
        assert automation.pending_tasks == []
    finally:
        await automation.close()


@pytest.mark.asyncio
async def test_approve_accepted_runs_exploration(automation):
    """M12.4 审批通过后继续探索执行。"""
    await automation.initialize()
    try:
        pending = await automation.execute(NOVEL_TASK, require_approval=True)
        assert automation.pending_tasks == [pending.run_id]
        result = await automation.approve(pending.run_id)
        assert result.success is True
        assert result.source == "exploration"
        assert result.run_id == pending.run_id
        assert automation.pending_tasks == []
    finally:
        await automation.close()


@pytest.mark.asyncio
async def test_schedule_add_list_remove(automation):
    """M12.4 调度登记 / 列表 / 移除（含不存在返回 False）。"""
    await automation.initialize()
    try:
        sched = automation.add_schedule("生成周报", interval_seconds=60)
        assert isinstance(sched, Schedule)
        assert sched.interval_seconds == 60
        assert sched.trigger == "interval"
        assert sched.run_count == 0
        assert [s.schedule_id for s in automation.schedules()] == [sched.schedule_id]
        assert automation.remove_schedule("s-missing-001") is False
        assert automation.remove_schedule(sched.schedule_id) is True
        assert automation.schedules() == []
    finally:
        await automation.close()


@pytest.mark.asyncio
async def test_run_due_schedules_fires_only_when_due(automation):
    """M12.4 仅到期 interval 调度被触发，且触发后更新 last_run/run_count。"""
    await automation.initialize()
    try:
        automation.add_schedule("生成周报", interval_seconds=60)
        base = time.time()
        assert await automation.run_due_schedules(now_ts=base) == []
        r1 = await automation.run_due_schedules(now_ts=base + 120)
        assert len(r1) == 1 and r1[0].success is True
        sched = automation.schedules()[0]
        assert sched.run_count == 1 and sched.last_run
        r2 = await automation.run_due_schedules(now_ts=base + 240)
        assert len(r2) == 1 and sched.run_count == 2
        assert await automation.run_due_schedules(now_ts=base + 250) == []
    finally:
        await automation.close()


@pytest.mark.asyncio
async def test_run_due_schedules_skips_disabled_and_cron(automation):
    """M12.4 禁用或非 interval 调度不参与到期执行。"""
    await automation.initialize()
    try:
        automation.add_schedule("生成月报", interval_seconds=60, enabled=False)
        automation.add_schedule("生成周报", interval_seconds=60, trigger="cron",
                                cron_expr="*/5 * * * *")
        results = await automation.run_due_schedules(now_ts=time.time() + 10000)
        assert results == []
        assert all(s.run_count == 0 for s in automation.schedules())
    finally:
        await automation.close()


@pytest.mark.asyncio
async def test_event_register_and_trigger(automation):
    """M12.4 事件绑定后触发执行，未绑定事件返回空。"""
    await automation.initialize()
    try:
        automation.register_event("on_weekly_report", "生成周报")
        automation.register_event("on_weekly_report", NOVEL_TASK)
        results = await automation.trigger_event("on_weekly_report")
        assert len(results) == 2
        assert all(r.success for r in results)
        assert await automation.trigger_event("on_never_fired") == []
    finally:
        await automation.close()


@pytest.mark.asyncio
async def test_execution_log_and_usage_stats(automation):
    """M12.4 执行日志与 Token 用量统计可追溯。"""
    await automation.initialize()
    try:
        result = await automation.execute("生成周报")
        log = automation.execution_log()
        assert any(x.run_id == result.run_id for x in log)
        stats = await automation.usage_stats()
        assert stats["runs"] >= 1
        assert "saved_ratio" in stats and "total_tokens_full" in stats
        assert automation.cached_count() >= 1
    finally:
        await automation.close()


def test_decompose_and_render_output():
    """M12.4 探索拆解与报告渲染保持确定性。"""
    subs = AutomationEngine._decompose("生成周报")
    assert len(subs) == EXPLORE_SUBTASKS
    assert subs[0] == "分解任务目标：生成周报"
    report = AutomationEngine._render_output("X", ["a", "b"])
    assert "[1/2] a（已完成）" in report
    assert "共完成 2 个子任务" in report


def test_usage_placeholder_identity():
    """占位统一类型导出保持同一对象返回。"""
    usage = TokenUsage(usage_id="u1", task="t", module="m")
    assert AutomationEngine._usage(usage) is usage