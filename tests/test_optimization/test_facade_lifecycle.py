"""M12 门面 OptimizationEngine：生命周期 / 三层路径 / 审批流。"""

import pytest

from dsh_core.optimization.base import AutomationResult


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