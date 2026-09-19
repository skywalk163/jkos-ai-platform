"""M17 补充测试：ProcessSolidifier（12.1）——固化、版本管理、回滚与执行统计。"""

import pytest

from jkos_core.exploration.base import (
    ExploreResult,
    Knowledge,
    Solution,
    SolutionCandidate,
    SubTask,
)
from jkos_core.optimization.base import ProcessStep
from jkos_core.optimization.process_solidifier import VERIFY_STEP_ACTION

TASK = "fjlz-m12-季度经营复盘"


def _make_exploration(task: str = TASK, *, with_solution: bool = True) -> ExploreResult:
    """构造一次标准探索结果：solution 三步 + 候选 + 知识。"""
    return ExploreResult(
        task=task,
        success=True,
        subtasks=[
            SubTask(name="数据汇总", description="汇总该季度经营关键指标"),
            SubTask(name="差距分析", description="对比目标与实际完成情况"),
        ],
        candidates=[SolutionCandidate(solution_id="c1", title="候选方案A")],
        knowledge=[
            Knowledge(knowledge_id="k1", task=task, content="知识条目1"),
            Knowledge(knowledge_id="k2", task=task, content="知识条目2"),
        ],
        solution=(
            Solution(
                solution_id="s1",
                task=task,
                steps=["汇总该季度经营关键指标", "对比目标与实际完成情况", "生成改进建议清单"],
                source_type="exploration",
                result_summary="已完成整体探索",
            )
            if with_solution
            else None
        ),
        summary="探索完成",
    )


@pytest.mark.asyncio
async def test_initialize_and_states(solidifier):
    await solidifier.initialize()
    st = solidifier.states()
    assert st["db_path"] == solidifier.db_path
    assert st["process_count"] == 0
    assert solidifier.count() == 0


@pytest.mark.asyncio
async def test_solidify_from_solution_steps(solidifier):
    """solution.steps 优先：3 动作 + 自动追加校验步骤。"""
    process = await solidifier.solidify(_make_exploration())
    assert process.process_id.startswith("p")
    assert process.name == f"固化-{TASK}"
    assert process.task == TASK
    assert process.version == 1
    assert process.status == "active"
    assert process.description == "探索完成"
    assert len(process.steps) == 4
    assert [s.name for s in process.steps] == ["step_1", "step_2", "step_3", "verify"]
    assert process.steps[0].action == "汇总该季度经营关键指标"
    assert process.steps[0].depends_on == []
    assert process.steps[1].depends_on == ["step_1"]
    assert process.steps[2].depends_on == ["step_2"]
    assert process.steps[3].action == VERIFY_STEP_ACTION
    assert process.steps[3].depends_on == ["step_3"]
    assert process.meta["candidate_count"] == 1
    assert process.meta["knowledge_count"] == 2
    assert process.meta["source"] == ""  # ExploreResult 无 source 属性
    assert "exception_handling" in process.meta
    assert solidifier.count() == 1


@pytest.mark.asyncio
async def test_solidify_from_subtask_fallback(solidifier):
    """无 solution 时用 subtask 的 name/description 推导步骤。"""
    process = await solidifier.solidify(_make_exploration(with_solution=False))
    assert len(process.steps) == 3  # 2 个 subtask + verify
    assert process.steps[0].name == "数据汇总"
    assert process.steps[0].action == "汇总该季度经营关键指标"
    assert process.steps[0].depends_on == []
    assert process.steps[1].name == "差距分析"
    assert process.steps[1].depends_on == ["数据汇总"]
    assert process.steps[2].name == "verify"
    assert process.steps[2].depends_on == ["差距分析"]
    assert process.meta["source"] == ""


@pytest.mark.asyncio
async def test_solidify_empty_task_raises(solidifier):
    with pytest.raises(ValueError, match="exploration.task 不能为空"):
        await solidifier.solidify(_make_exploration(task="  "))


@pytest.mark.asyncio
async def test_solidify_without_steps_yields_empty_process(solidifier):
    exp = ExploreResult(task="无步骤任务", success=True)
    process = await solidifier.solidify(exp)
    assert process.steps == []


@pytest.mark.asyncio
async def test_upgrade_creates_version_and_archives_old(solidifier):
    p1 = await solidifier.solidify(_make_exploration())
    p2 = await solidifier.upgrade(
        p1.process_id,
        steps=[ProcessStep(name="新步骤", action="新动作")],
        name="升级版",
        description="升级后的流程",
    )
    assert p2.version == 2
    assert p2.base_version == 1
    assert p2.status == "active"
    assert p2.name == "升级版"
    assert p2.description == "升级后的流程"
    assert len(p2.steps) == 1
    assert p2.task == TASK
    got = await solidifier.get(p1.process_id)
    assert got.version == 2
    v1 = await solidifier.get(p1.process_id, 1)
    assert v1.version == 1
    assert v1.status == "archived"
    assert solidifier.count() == 2


@pytest.mark.asyncio
async def test_upgrade_missing_raises_keyerror(solidifier):
    with pytest.raises(KeyError, match="流程不存在"):
        await solidifier.upgrade("no-such-process")


@pytest.mark.asyncio
async def test_get_returns_none_for_missing(solidifier):
    assert await solidifier.get("no-such-process") is None


@pytest.mark.asyncio
async def test_rollback_restores_previous_steps(solidifier):
    p1 = await solidifier.solidify(_make_exploration())
    p2 = await solidifier.upgrade(
        p1.process_id,
        steps=[ProcessStep(name="精简", action="只做一件事")],
    )
    p3 = await solidifier.rollback(p1.process_id)
    assert p3.version == 3
    assert p3.base_version == 2
    assert p3.status == "active"
    assert [s.action for s in p3.steps] == [
        "汇总该季度经营关键指标",
        "对比目标与实际完成情况",
        "生成改进建议清单",
        VERIFY_STEP_ACTION,
    ]
    assert p3.description == "探索完成"
    assert p3.name == p2.name  # 回滚保留当前名称与任务


@pytest.mark.asyncio
async def test_rollback_without_history_raises(solidifier):
    p1 = await solidifier.solidify(_make_exploration())
    with pytest.raises(ValueError, match="没有可回滚的历史版本"):
        await solidifier.rollback(p1.process_id)


@pytest.mark.asyncio
async def test_rollback_missing_raises_keyerror(solidifier):
    with pytest.raises(KeyError, match="流程不存在"):
        await solidifier.rollback("no-such-process")


@pytest.mark.asyncio
async def test_record_run_updates_stats(solidifier):
    process = await solidifier.solidify(_make_exploration())
    r1 = await solidifier.record_run(process.process_id, success=True, duration_ms=100)
    assert r1.runs == 1
    assert r1.success_count == 1
    assert r1.success_rate == 1.0
    assert r1.avg_duration_ms == 100
    r2 = await solidifier.record_run(process.process_id, success=False, duration_ms=200)
    assert r2.runs == 2
    assert r2.success_count == 1
    assert r2.success_rate == 0.5
    assert r2.avg_duration_ms == 150  # (100*1 + 200) // 2
    assert await solidifier.record_run("no-such-process") is None


@pytest.mark.asyncio
async def test_delete(solidifier):
    process = await solidifier.solidify(_make_exploration())
    assert await solidifier.delete(process.process_id) is True
    assert await solidifier.get(process.process_id) is None
    assert await solidifier.delete(process.process_id) is False


@pytest.mark.asyncio
async def test_delete_after_upgrade_keeps_history(solidifier):
    p1 = await solidifier.solidify(_make_exploration())
    await solidifier.upgrade(p1.process_id)
    assert solidifier.count() == 2
    assert await solidifier.delete(p1.process_id) is True
    assert await solidifier.get(p1.process_id) is None
    # delete 仅移除当前 active 版本，v1 历史仍保留
    assert solidifier.count() == 1


@pytest.mark.asyncio
async def test_list_processes_active_only(solidifier):
    p1 = await solidifier.solidify(_make_exploration(task="fjlz-m12-任务A"))
    await solidifier.solidify(_make_exploration(task="fjlz-m12-任务B"))
    await solidifier.upgrade(p1.process_id)
    processes = await solidifier.list_processes()
    assert len(processes) == 2
    assert {p.task for p in processes} == {"fjlz-m12-任务A", "fjlz-m12-任务B"}


@pytest.mark.asyncio
async def test_clear(solidifier):
    await solidifier.solidify(_make_exploration(task="fjlz-m12-任务A"))
    await solidifier.solidify(_make_exploration(task="fjlz-m12-任务B"))
    assert await solidifier.clear() == 2
    assert solidifier.count() == 0