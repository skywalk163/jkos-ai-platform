"""M12 门面 OptimizationEngine：生命周期 / 三层路径 / 审批流。"""

import pytest

from jkos_core.optimization.base import AutomationResult


@pytest.mark.asyncio
async def test_empty_task_raises(engine):
    await engine.initialize()
    try:
        with pytest.raises(ValueError):
            await engine.execute("   ")
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_initialize_close_idempotent(engine):
    """initialize / close 均可重复调用，不抛错。"""
    await engine.initialize()
    await engine.initialize()
    await engine.close()
    await engine.close()
    assert True


@pytest.mark.asyncio
async def test_seed_templates_from_fresh_db(engine):
    """全新数据库自动装载种子模板（>= 50），可命中「生成周报」。"""
    await engine.initialize()
    try:
        assert engine.templates.count() >= 50
        assert await engine.has_template("生成周报") is True
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_execute_known_template_returns_result(engine):
    """已知种子任务走模板路径并返回规范结果。"""
    await engine.initialize()
    try:
        res = await engine.execute("生成周报")
        assert isinstance(res, AutomationResult)
        assert res.success is True
        assert res.source in ("template", "cache")
        assert res.task == "生成周报"
        assert res.token_used <= 1000
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_approve_flow_for_novel_task(engine):
    """require_approval=True 时先进入待办，审批通过后执行。"""
    await engine.initialize()
    try:
        task = "fjlz-m12-approve-20240612 新立项：设计工业园零碳改造方案"
        res = await engine.execute(task, require_approval=True)
        assert res.success is False
        assert res.source == "exploration"
        assert res.output == ""  # 待审批，未真正执行

        pending = engine.pending_tasks
        assert pending, "require_approval=True 后应至少存在一条待办"

        approved = await engine.approve(res.run_id)
        assert approved.success is True
    finally:
        await engine.close()


# ---------- M17 覆盖补齐：调度 / 事件 / 日志 / 统计转发 ----------

@pytest.mark.asyncio
async def test_schedule_forwarders(engine):
    """add_schedule / schedules / remove_schedule 转发到自动化引擎。"""
    await engine.initialize()
    try:
        sched = engine.add_schedule("生成周报", interval_seconds=3600)
        assert sched.interval_seconds == 3600
        assert [s.schedule_id for s in engine.schedules()] == [sched.schedule_id]
        assert engine.remove_schedule("s-missing-001") is False
        assert engine.remove_schedule(sched.schedule_id) is True
        assert engine.schedules() == []
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_run_due_schedules_forwarder(engine):
    """刚登记的调度未到期时不触发执行。"""
    await engine.initialize()
    try:
        engine.add_schedule("生成周报", interval_seconds=3600)
        results = await engine.run_due_schedules()
        assert results == []
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_event_forwarders(engine):
    """register_event / trigger_event 转发；未绑定事件返回空。"""
    await engine.initialize()
    try:
        engine.register_event("on_deploy", "生成周报")
        results = await engine.trigger_event("on_deploy")
        assert len(results) == 1 and results[0].success is True
        assert await engine.trigger_event("on_missing") == []
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_log_stats_forwarders(engine):
    """execution_log / usage_stats / cached_count 可追溯。"""
    await engine.initialize()
    try:
        res = await engine.execute("生成周报")
        log = engine.execution_log()
        assert any(x.run_id == res.run_id for x in log)
        stats = await engine.usage_stats()
        assert stats["runs"] >= 1
        assert engine.cached_count() >= 1
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_has_template_negative_branch(engine):
    """语义未命中时 has_template 返回 False。"""
    await engine.initialize()
    try:
        assert await engine.has_template("zzzfjlzmissing20260701qq") is False
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_approve_rejected_through_facade(engine):
    """门面层审批拒绝：状态 rejected 且待办清空。"""
    await engine.initialize()
    try:
        task = "fjlz-m12-reject-20260701 全息投影数据可视化方案"
        res = await engine.execute(task, require_approval=True)
        assert res.success is False
        rejected = await engine.approve(res.run_id, approved=False)
        assert rejected is not None
        assert rejected.success is False
        assert engine.pending_tasks == []
    finally:
        await engine.close()