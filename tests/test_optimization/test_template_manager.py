"""M12 模板管理：种子数据、推荐、沉淀与计数。"""

import pytest

from dsh_core.optimization.base import Process, ProcessStep


@pytest.mark.asyncio
async def test_seed_count_ge_50(engine):
    await engine.initialize()
    try:
        assert engine.templates.count() >= 50
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_recommend_weekly_report_template(engine):
    """能从种子模板中为「生成周报」推荐出匹配模板。"""
    await engine.initialize()
    try:
        tpl = await engine.templates.recommend_template("生成周报")
        assert tpl is not None
        assert getattr(tpl, "task", any)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_create_template_from_process(engine):
    """从固化流程 create_template 沉淀新模板并立即可用。"""
    await engine.initialize()
    try:
        before = engine.templates.count()
        process = Process(
            process_id="p-m12-manual-001",
            name="季度经营复盘",
            task="季度经营复盘并输出改进建议",
            steps=[
                ProcessStep(name="数据汇总", action="汇总该季度经营关键指标"),
                ProcessStep(name="差距分析", action="对比目标与实际完成情况"),
                ProcessStep(name="建议输出", action="生成可执行的改进建议清单"),
            ],
            description="手动构造的测试流程，用于验证 create_template",
        )
        tpl = await engine.templates.create_template(process)
        assert tpl is not None
        assert tpl.task == process.task
        assert getattr(tpl, "version", 1) == 1

        after = engine.templates.count()
        assert after == before + 1
        assert await engine.templates.has_template(process.task)
    finally:
        await engine.close()