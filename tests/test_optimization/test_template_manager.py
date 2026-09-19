"""M12 模板管理：种子数据、推荐、沉淀与计数。"""

import pytest

from jkos_core.optimization.base import Process, ProcessStep


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


# ---------- M17 覆盖补齐：版本管理 / 回滚 / 删除 / 搜索 / 统计 ----------

@pytest.mark.asyncio
async def test_states_reports_db_and_count(engine):
    """states() 暴露 db_path 与模板总数。"""
    await engine.initialize()
    try:
        st = engine.templates.states()
        assert st["db_path"] == engine.templates.db_path
        assert st["template_count"] == engine.templates.count()
        assert st["template_count"] >= 50
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_update_template_bumps_version_and_archives_old(engine):
    """编辑模板生成新版本，旧版本归档不再参与列出。"""
    await engine.initialize()
    try:
        process = Process(
            process_id="p-m17-upd-001",
            name="更新测试",
            task="更新测试任务并输出说明",
            steps=[ProcessStep(name="s1", action="动作一")],
            description="v1 描述",
        )
        tpl = await engine.templates.create_template(process)
        v2 = await engine.templates.update_template(
            tpl.template_id,
            name="更新后",
            description="v2 描述",
            steps=[ProcessStep(name="s1", action="动作一"),
                   ProcessStep(name="s2", action="动作二")],
        )
        assert v2.version == 2
        assert v2.name == "更新后"
        assert v2.description == "v2 描述"
        assert len(v2.steps) == 2
        cur = await engine.templates.get(tpl.template_id)
        assert cur.version == 2
        listed = [t.template_id for t in await engine.templates.list_templates()]
        assert listed.count(tpl.template_id) == 1
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_update_missing_template_raises_keyerror(engine):
    """编辑不存在的模板抛 KeyError。"""
    await engine.initialize()
    try:
        with pytest.raises(KeyError):
            await engine.templates.update_template("t-missing-upd", name="x")
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_rollback_restores_previous_steps(engine):
    """回滚恢复最近历史版本的步骤与描述，版本继续递增。"""
    await engine.initialize()
    try:
        process = Process(
            process_id="p-m17-rb-001",
            name="回滚测试",
            task="回滚测试任务场景",
            steps=[ProcessStep(name="s1", action="原始动作")],
            description="原始描述",
        )
        tpl = await engine.templates.create_template(process)
        await engine.templates.update_template(
            tpl.template_id,
            name="改坏版",
            steps=[ProcessStep(name="s1", action="错误动作")],
        )
        rolled = await engine.templates.rollback(tpl.template_id)
        assert rolled.version == 3
        assert rolled.steps[0].action == "原始动作"
        assert rolled.description == "原始描述"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_rollback_without_history_raises(engine):
    """v1（无历史版本）回滚抛 ValueError。"""
    await engine.initialize()
    try:
        process = Process(
            process_id="p-m17-rb2-001",
            name="无历史",
            task="无历史回滚场景",
            steps=[ProcessStep(name="s1", action="动作")],
        )
        tpl = await engine.templates.create_template(process)
        with pytest.raises(ValueError, match="没有可回滚"):
            await engine.templates.rollback(tpl.template_id)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_rollback_missing_template_raises(engine):
    """回滚不存在的模板抛 KeyError。"""
    await engine.initialize()
    try:
        with pytest.raises(KeyError):
            await engine.templates.rollback("t-missing-rb")
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_delete_template_active_only(engine):
    """删除当前 active 版本；不存在时返回 False。"""
    await engine.initialize()
    try:
        process = Process(
            process_id="p-m17-del-001",
            name="删除测试",
            task="删除测试任务描述",
            steps=[ProcessStep(name="s1", action="动作")],
        )
        tpl = await engine.templates.create_template(process)
        assert await engine.templates.delete_template(tpl.template_id) is True
        assert await engine.templates.get(tpl.template_id) is None
        assert await engine.templates.delete_template(tpl.template_id) is False
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_create_template_empty_task_raises(engine):
    """无 task 的流程不允许沉淀为模板。"""
    await engine.initialize()
    try:
        with pytest.raises(ValueError, match="process.task"):
            await engine.templates.create_template(
                Process(process_id="p-m17-empty", name="空", task="")
            )
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_recommend_empty_task_raises(engine):
    """空任务推荐抛 ValueError。"""
    await engine.initialize()
    try:
        with pytest.raises(ValueError, match="task 不能为空"):
            await engine.templates.recommend_template("   ")
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_recommend_empty_library_raises(engine):
    """清空模板库后推荐抛 ValueError。"""
    await engine.initialize()
    try:
        await engine.templates.clear()
        with pytest.raises(ValueError, match="模板库为空"):
            await engine.templates.recommend_template("生成周报")
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_recommend_semantic_miss_returns_none(engine):
    """语义未命中（低于 MIN_TEMPLATE_MATCH）返回 None。"""
    await engine.initialize()
    try:
        tpl = await engine.templates.recommend_template("zzzfjlzmissing20260701qq")
        assert tpl is None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_get_with_explicit_version(engine):
    """get 可按版本号取回历史版本。"""
    await engine.initialize()
    try:
        process = Process(
            process_id="p-m17-ver-001",
            name="版本取回",
            task="版本取回测试任务",
            steps=[ProcessStep(name="s1", action="v1 动作")],
        )
        tpl = await engine.templates.create_template(process)
        await engine.templates.update_template(tpl.template_id, name="v2名")
        v1 = await engine.templates.get(tpl.template_id, version=1)
        assert v1 is not None
        assert v1.version == 1
        assert v1.name != "v2名"
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_search_templates_by_keyword(engine):
    """关键字搜索命中 / 空关键字返回全部 / 未命中返回空。"""
    await engine.initialize()
    try:
        hits = await engine.templates.search_templates("周报")
        assert any(t.task == "生成周报" for t in hits)
        all_tpl = await engine.templates.search_templates("")
        assert len(all_tpl) == engine.templates.count()
        assert await engine.templates.search_templates("zzzfjlznomatch20260701qq") == []
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_record_run_updates_stats(engine):
    """record_run 累计使用次数与成功率；不存在返回 None。"""
    await engine.initialize()
    try:
        process = Process(
            process_id="p-m17-run-001",
            name="统计测试",
            task="统计测试任务场景",
            steps=[ProcessStep(name="s1", action="动作")],
        )
        tpl = await engine.templates.create_template(process)
        after_ok = await engine.templates.record_run(tpl.template_id, success=True)
        assert after_ok.use_count == 1 and after_ok.success_count == 1
        assert after_ok.success_rate == 1.0
        after_fail = await engine.templates.record_run(tpl.template_id, success=False)
        assert after_fail.use_count == 2 and after_fail.success_count == 1
        assert after_fail.success_rate == 0.5
        assert await engine.templates.record_run("t-missing-run") is None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_execute_template_returns_result_and_stats(engine):
    """execute_template 确定性执行并回写统计；空模板抛 ValueError。"""
    await engine.initialize()
    try:
        process = Process(
            process_id="p-m17-exe-001",
            name="执行测试",
            task="执行测试任务场景",
            steps=[
                ProcessStep(name="s1", action="动作一"),
                ProcessStep(name="s2", action="动作二"),
            ],
        )
        tpl = await engine.templates.create_template(process)
        res = await engine.templates.execute_template(tpl)
        assert res.success is True
        assert res.source == "template"
        assert res.task == process.task
        assert "2 个步骤" in res.summary
        back = await engine.templates.get(tpl.template_id)
        assert back.use_count == 1 and back.success_count == 1
        with pytest.raises(ValueError, match="template 不能为空"):
            await engine.templates.execute_template(None)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_clear_wipes_templates(engine):
    """clear() 清空模板库并返回删除条数。"""
    await engine.initialize()
    try:
        n = await engine.templates.clear()
        assert n >= 50
        assert engine.templates.count() == 0
    finally:
        await engine.close()


def test_steps_from_actions_skips_empty():
    """空动作文案被跳过，步骤保持链式依赖。"""
    from jkos_core.optimization.template_manager import _steps_from_actions

    steps = _steps_from_actions(["第一步", "", "   ", "第三步"])
    assert len(steps) == 2
    assert steps[0].name == "step_1" and steps[0].depends_on == []
    assert steps[1].name == "step_2" and steps[1].depends_on == ["step_1"]