"""M20 四段自举闭环测试（探索 → 固化 → 模板化 → 自动化执行）

数据目录与报告目录都落在 tmp_path；D3 用 Fake 运行器，不真跑子进程。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jkos_core.selfboot import d3_testgen as d3mod
from jkos_core.selfboot.loop import (
    DEFAULT_TASK,
    LOOP_STAGES,
    LoopResult,
    PipelineSearcher,
    SelfBootstrapEngine,
    resolve_data_dir,
)

# 与 DEFAULT_TASK 逐字符相同：模板推荐按关键词覆盖率打分，改后缀会跌破阈值
SAME_TASK = DEFAULT_TASK


class TestPipelineSearcher:
    """D3 管道候选（替代会扫全仓 doc header 的 SolutionSearcher）"""

    def test_description_carries_target_and_stages(self):
        searcher = PipelineSearcher("sample_mod:label")
        lines = searcher.description().splitlines()
        assert lines[0] == "target: sample_mod:label"
        assert lines[1:] == d3mod.pipeline_steps()

    @pytest.mark.asyncio
    async def test_search_returns_single_code_candidate(self):
        candidates = await PipelineSearcher("sample_mod:label").search("任意任务")
        assert len(candidates) == 1
        assert candidates[0].source_type == "code"
        assert candidates[0].similarity == 1.0


class TestResolveDataDir:
    """数据目录解析（env 可覆盖，避免写进代码目录旁）"""

    def test_explicit_argument_wins(self, monkeypatch):
        monkeypatch.setenv("DSH_SELFBOOT_DATA_DIR", "/from/env")
        assert resolve_data_dir("/from/arg") == Path("/from/arg")

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("DSH_SELFBOOT_DATA_DIR", "/from/env")
        assert resolve_data_dir() == Path("/from/env")

    def test_default_under_repo_data(self, monkeypatch):
        monkeypatch.delenv("DSH_SELFBOOT_DATA_DIR", raising=False)
        path = resolve_data_dir()
        assert path.name == "selfboot"
        assert path.parent.name == "data"


class TestLoopHappyPath:
    """四段全部成立"""

    @pytest.mark.asyncio
    async def test_four_stages_in_order(self, make_loop):
        engine = make_loop()
        result = await engine.run_loop(SAME_TASK)
        assert [s["name"] for s in result.stages] == list(LOOP_STAGES)
        assert all(s["ok"] for s in result.stages)
        assert result.success is True
        await engine.close()

    @pytest.mark.asyncio
    async def test_result_fields_complete(self, make_loop):
        engine = make_loop()
        result = await engine.run_loop(SAME_TASK)
        assert result.task == SAME_TASK
        assert result.target == "sample_mod:label"
        assert result.process_id.startswith("p")
        assert result.template_id.startswith("t")
        assert result.run_id.startswith("r")
        assert result.subtasks >= 1
        assert set(result.first_run) >= {
            "source", "token_used", "tokens_full", "tokens_full_estimate",
            "duration_ms", "subtasks", "success", "coverage", "generation_source",
        }
        assert set(result.replay_run) >= {
            "source", "token_used", "duration_ms", "template_id", "steps", "success",
        }
        assert set(result.savings) >= {
            "cold_start_tokens", "replay_tokens", "token_saved",
            "token_saved_ratio", "duration_saved_ratio",
        }
        assert result.first_run["source"] == "exploration"
        assert result.replay_run["source"] == "template"
        assert result.replay_run["template_id"] == result.template_id
        await engine.close()

    @pytest.mark.asyncio
    async def test_explore_stage_uses_exploration_source(self, make_loop):
        engine = make_loop()
        result = await engine.run_loop(SAME_TASK)
        # 独立空知识库 → 不会早退成 knowledge 命中
        assert result.stage("explore")["source"] == "exploration"
        await engine.close()

    @pytest.mark.asyncio
    async def test_solidify_stage_records_step_count(self, make_loop):
        engine = make_loop()
        result = await engine.run_loop(SAME_TASK)
        # 5 个 D3 阶段文案（含 target 行 → 6）+ 固化器追加的 verify = 7
        assert result.stage("solidify")["steps"] == 7
        assert result.replay_run["steps"] == 7
        await engine.close()

    @pytest.mark.asyncio
    async def test_d3_evidence_and_coverage_propagated(self, make_loop):
        engine = make_loop()
        result = await engine.run_loop(SAME_TASK)
        assert result.d3["generation_source"] == "fallback"
        assert result.d3["coverage"]["percent"] == 90.0
        assert result.first_run["coverage"]["percent"] == 90.0
        assert result.first_run["generation_source"] == "fallback"
        await engine.close()

    @pytest.mark.asyncio
    async def test_llm_meta_propagated(self, make_loop, llm_json):
        engine = make_loop(llm=llm_json(provider="deepseek", total_tokens=321))
        result = await engine.run_loop(SAME_TASK)
        assert result.llm["provider"] == "deepseek"
        assert result.llm["simulated"] is False
        assert result.first_run["llm_tokens"] == 321
        assert result.first_run["generation_source"] == "llm"
        await engine.close()

    @pytest.mark.asyncio
    async def test_detail_mentions_ids_and_savings(self, make_loop):
        engine = make_loop()
        result = await engine.run_loop(SAME_TASK)
        assert "闭环成立" in result.detail
        assert result.process_id in result.detail
        assert result.template_id in result.detail
        await engine.close()

    @pytest.mark.asyncio
    async def test_default_task_within_loop_module(self, make_loop):
        engine = make_loop()
        result = await engine.run_loop()
        assert result.task == DEFAULT_TASK
        assert "selfboot" in result.task
        await engine.close()


class TestTemplateReuse:
    """模板复用语义（闭环是否真正闭合的关键）"""

    @pytest.mark.asyncio
    async def test_same_task_hits_just_created_template(self, make_loop):
        engine = make_loop()
        first = await engine.run_loop(SAME_TASK)
        assert first.template_hit is True
        recommended = await engine.optimization.templates.recommend_template(SAME_TASK)
        assert recommended is not None
        assert recommended.template_id == first.template_id
        await engine.close()

    @pytest.mark.asyncio
    async def test_replay_returns_template_source(self, make_loop):
        engine = make_loop()
        first = await engine.run_loop(SAME_TASK)
        replay = await engine.replay(SAME_TASK)
        assert replay.success is True
        assert replay.template_hit is True
        assert replay.replay_run["source"] == "template"
        assert replay.replay_run["template_id"] == first.template_id
        await engine.close()

    @pytest.mark.asyncio
    async def test_paraphrased_task_does_not_hit_our_template(self, make_loop):
        """锁住阈值语义：换个说法就不该命中刚建的模板（避免误判闭环成立）"""
        engine = make_loop()
        first = await engine.run_loop(SAME_TASK)
        other = await engine.optimization.templates.recommend_template(
            "统计季度财务报表并生成审计意见")
        assert other is None or other.template_id != first.template_id
        await engine.close()

    @pytest.mark.asyncio
    async def test_replay_without_prior_run_reports_missing_template(self, make_loop):
        engine = make_loop()
        replay = await engine.replay("一个从未跑过的任务描述 xyz")
        assert replay.success is False
        assert replay.template_hit is False
        assert "模板库中没有匹配" in replay.detail
        await engine.close()


class TestSavings:
    """量化口径：冷启动基准 vs 复现成本"""

    @pytest.mark.asyncio
    async def test_token_saved_ratio_above_90_percent(self, make_loop):
        engine = make_loop()
        result = await engine.run_loop(SAME_TASK)
        assert result.savings["cold_start_tokens"] == 1500 * len(d3mod.D3_STAGES)
        assert result.savings["token_saved_ratio"] > 0.9
        await engine.close()

    @pytest.mark.asyncio
    async def test_cold_start_uses_stage_count_when_few_subtasks(self, make_loop):
        engine = make_loop()
        result = await engine.run_loop(SAME_TASK)
        # 规则分解只有 2 个子任务，冷启动基准仍按 5 个 D3 阶段计（保守口径）
        assert result.subtasks < len(d3mod.D3_STAGES)
        assert result.savings["cold_start_tokens"] == \
            max(1500 * result.subtasks, 1500 * len(d3mod.D3_STAGES))
        await engine.close()

    @pytest.mark.asyncio
    async def test_duration_fields_are_reported(self, make_loop):
        """耗时字段如实上报；**不**断言节省比下界 —— 用 Fake 运行器时首次与复现
        都在亚毫秒量级，`first_ms` 可能等于 `replay_ms`，比值属计时噪声（0.82
        上即为 replay 2ms > first 1ms 的负值）。真正稳定的节省保证由
        `token_saved_ratio > 0.9` 承担（见 TestSavings），演示脚本另有实测断言。
        """
        engine = make_loop()
        result = await engine.run_loop(SAME_TASK)
        assert result.savings["first_duration_ms"] >= 0
        assert result.savings["replay_duration_ms"] >= 0
        assert isinstance(result.savings["duration_saved_ratio"], float)
        assert result.savings["duration_saved_ratio"] <= 1.0
        await engine.close()


class TestFailurePaths:
    """失败路径如实反映"""

    @pytest.mark.asyncio
    async def test_d3_failure_propagates(self, make_loop, runner_cls):
        """注意 Experimenter 会重试 max_attempts 次，故用 default 让每次实验都失败"""
        from jkos_core.selfboot.d3_testgen import RunOutcome
        runner = runner_cls(default=RunOutcome(passed=0, failed=1, returncode=1))
        engine = make_loop(runner=runner)
        result = await engine.run_loop(SAME_TASK)
        assert result.success is False
        assert result.stage("explore")["ok"] is False
        assert result.first_run["success"] is False
        assert "闭环未成立" in result.detail
        await engine.close()

    @pytest.mark.asyncio
    async def test_low_coverage_does_not_break_loop(self, make_loop, runner_cls):
        from jkos_core.selfboot.d3_testgen import RunOutcome
        runner = runner_cls(default=RunOutcome(passed=2, returncode=0,
                                               coverage_percent=10.0,
                                               coverage_source="real"))
        engine = make_loop(runner=runner)
        result = await engine.run_loop(SAME_TASK)
        assert result.success is False
        assert result.first_run["coverage"]["passed"] is False
        # 后续段仍然跑完，模板照样沉淀
        assert result.template_id.startswith("t")
        await engine.close()

    @pytest.mark.asyncio
    async def test_bad_target_keeps_stages_but_fails(self, make_loop):
        engine = make_loop(target="缺少冒号")
        result = await engine.run_loop(SAME_TASK)
        assert result.success is False
        assert [s["name"] for s in result.stages] == list(LOOP_STAGES)
        assert result.stage("explore")["ok"] is False
        await engine.close()


class TestLifecycle:
    """初始化 / 关闭 / 幂等"""

    @pytest.mark.asyncio
    async def test_initialize_is_idempotent(self, make_loop):
        engine = make_loop()
        await engine.initialize()
        first = engine.optimization.templates.count()
        await engine.initialize()
        assert engine.optimization.templates.count() == first
        assert engine._initialized is True
        await engine.close()

    @pytest.mark.asyncio
    async def test_close_resets_state(self, make_loop):
        engine = make_loop()
        await engine.run_loop(SAME_TASK)
        await engine.close()
        assert engine._initialized is False

    @pytest.mark.asyncio
    async def test_data_dir_created_on_initialize(self, make_loop, tmp_path):
        engine = make_loop()
        await engine.initialize()
        assert (tmp_path / "data").is_dir()
        assert (tmp_path / "data" / "templates.db").exists()
        await engine.close()

    @pytest.mark.asyncio
    async def test_comps_injection_provides_llm(self, tmp_path):
        # comps 只用于取 llm（MCP 工具侧不新增构造参数）
        engine = SelfBootstrapEngine(
            comps=SimpleNamespace(llm="LLM-FROM-COMPS"),
            data_dir=str(tmp_path / "data"), report_dir=str(tmp_path / "reports"))
        assert engine.llm == "LLM-FROM-COMPS"

    def test_explicit_llm_wins_over_comps(self, tmp_path):
        engine = SelfBootstrapEngine(
            llm="EXPLICIT", comps=SimpleNamespace(llm="FROM-COMPS"),
            data_dir=str(tmp_path / "data"), report_dir=str(tmp_path / "reports"))
        assert engine.llm == "EXPLICIT"

    def test_components_default_to_injected_data_dir(self, tmp_path):
        engine = SelfBootstrapEngine(
            data_dir=str(tmp_path / "d"), report_dir=str(tmp_path / "r"))
        assert engine.optimization.solidifier.db_path == str(tmp_path / "d" / "processes.db")
        assert engine.optimization.templates.db_path == str(tmp_path / "d" / "templates.db")
        assert engine.exploration.knowledge_base.db_path == \
            str(tmp_path / "d" / "knowledge.db")


class TestStatsAndReport:
    """统计与报告落盘"""

    @pytest.mark.asyncio
    async def test_stats_shape(self, make_loop):
        engine = make_loop()
        await engine.run_loop(SAME_TASK)
        stats = await engine.stats()
        assert stats["target"] == "sample_mod:label"
        assert stats["template_count"] >= 61  # 种子模板 + 本次新建
        assert stats["data_dir"]
        assert "usage" in stats
        assert isinstance(stats["recent_runs"], list)
        await engine.close()

    @pytest.mark.asyncio
    async def test_stats_limit_is_honoured(self, make_loop):
        engine = make_loop()
        await engine.run_loop(SAME_TASK)
        stats = await engine.stats(limit=0)
        assert len(stats["recent_runs"]) <= 1
        await engine.close()

    @pytest.mark.asyncio
    async def test_report_written_when_requested(self, make_loop, tmp_path):
        engine = make_loop()
        await engine.run_loop(SAME_TASK, save_report=True)
        files = sorted((tmp_path / "reports").glob("*-d3-selfboot.json"))
        assert len(files) == 1
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["success"] is True
        assert payload["template_hit"] is True
        assert payload["savings"]["token_saved_ratio"] > 0.9
        assert "code" not in payload["d3"]["cases"][0]
        await engine.close()

    @pytest.mark.asyncio
    async def test_no_report_when_not_requested(self, make_loop, tmp_path):
        engine = make_loop()
        await engine.run_loop(SAME_TASK)
        assert not list((tmp_path / "reports").glob("*-d3-selfboot.json"))
        await engine.close()

    @pytest.mark.asyncio
    async def test_replay_can_save_report(self, make_loop):
        engine = make_loop()
        await engine.run_loop(SAME_TASK)
        replay = await engine.replay(SAME_TASK, save_report=True)
        assert replay.success is True
        await engine.close()

    def test_report_write_failure_is_swallowed(self, make_loop, monkeypatch, tmp_path):
        engine = make_loop()

        def _boom(*args, **kwargs):
            raise OSError("磁盘满")

        monkeypatch.setattr(type(engine.report_dir), "mkdir", _boom)
        path = engine.save_report(LoopResult(task="t", target="x"))
        assert path.name == "unwritten.json"


class TestLoopResultModel:
    """结果对象的行为"""

    def test_stage_lookup_returns_none_for_missing(self):
        result = LoopResult(task="t", target="x")
        assert result.stage("explore") is None

    def test_to_dict_is_detached_copy(self):
        result = LoopResult(task="t", target="x")
        result.stages.append({"name": "explore", "ok": True})
        payload = result.to_dict()
        payload["stages"][0]["ok"] = False
        assert result.stages[0]["ok"] is True