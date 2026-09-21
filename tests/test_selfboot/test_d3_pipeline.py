"""M20 D3 执行器 - 五阶段流水线测试（LLM 真实路径 + 确定性兜底 + 安全校验）

全部用 FakePytestRunner / FakeLLM 注入，不真跑子进程、不触网。
"""
from __future__ import annotations

import json

import pytest

from jkos_core.selfboot import d3_testgen as mod
from jkos_core.selfboot.d3_testgen import (
    D3Executor,
    RunOutcome,
    deterministic_cases,
    locate_function,
    parse_generated_cases,
    safe_value,
    validate_generated_code,
)

VALID_CODE = (
    "from sample_mod import label\n"
    "\n"
    "\n"
    "def test_label_empty():\n"
    "    assert label('') == 'empty'\n"
)


class TestValidateGeneratedCode:
    """生成代码安全校验（语法 + 导入白名单 + 危险名拒绝 + 必须含 assert）"""

    def test_accepts_valid_code(self):
        assert validate_generated_code(VALID_CODE, allowed_module="sample_mod") == []

    def test_rejects_empty_code(self):
        assert validate_generated_code("   ") == ["代码为空"]

    def test_rejects_syntax_error(self):
        violations = validate_generated_code("def broken(:\n    pass\n")
        assert any("语法错误" in v for v in violations)

    def test_rejects_missing_assert(self):
        violations = validate_generated_code(
            "from sample_mod import add\n\n\ndef test_x():\n    add(1, 2)\n",
            allowed_module="sample_mod")
        assert "未包含 assert 断言" in violations

    @pytest.mark.parametrize("module_name", ["os", "subprocess", "shutil", "requests"])
    def test_rejects_denied_module_import(self, module_name):
        code = f"import {module_name}\n\n\ndef test_x():\n    assert True\n"
        violations = validate_generated_code(code, allowed_module="sample_mod")
        assert any("禁止的模块" in v for v in violations)

    def test_rejects_import_outside_allowlist(self):
        code = "import numpy\n\n\ndef test_x():\n    assert True\n"
        violations = validate_generated_code(code, allowed_module="sample_mod")
        assert any("白名单外" in v for v in violations)

    def test_allows_pytest_and_repo_packages(self):
        for module_name in ("pytest", "jkos_core.optimization", "tenants.dev"):
            code = f"import {module_name}\n\n\ndef test_x():\n    assert True\n"
            assert validate_generated_code(code, allowed_module="sample_mod") == []

    @pytest.mark.parametrize("name", ["eval", "exec", "open", "__import__", "system"])
    def test_rejects_denied_name_usage(self, name):
        code = (
            "from sample_mod import add\n\n\n"
            f"def test_x():\n    assert {name} is not None\n"
        )
        violations = validate_generated_code(code, allowed_module="sample_mod")
        assert any(f"禁止的名字 `{name}`" in v for v in violations)

    def test_rejects_denied_attribute_usage(self):
        code = (
            "from sample_mod import Calc\n\n\n"
            "def test_x():\n    assert Calc.rmtree is not None or True\n"
        )
        violations = validate_generated_code(code, allowed_module="sample_mod")
        assert any("禁止的属性 `rmtree`" in v for v in violations)

    def test_rejects_unresolvable_relative_import(self):
        code = "from . import x\n\n\ndef test_x():\n    assert x is not None\n"
        violations = validate_generated_code(code, allowed_module="sample_mod")
        assert any("无法解析的导入" in v or "白名单外" in v for v in violations)


class TestParseGeneratedCases:
    """LLM 输出容错解析"""

    def test_parses_plain_json_array(self):
        cases = parse_generated_cases(json.dumps([{"name": "t", "code": "assert 1"}]))
        assert cases == [{"name": "t", "code": "assert 1", "rationale": ""}]

    def test_strips_markdown_fence(self):
        text = '```json\n[{"name": "t", "code": "assert 1"}]\n```'
        assert len(parse_generated_cases(text)) == 1

    def test_extracts_array_from_prose(self):
        text = '好的，以下是用例：[{"name": "t", "code": "assert 1"}] 请查收。'
        assert len(parse_generated_cases(text)) == 1

    def test_returns_empty_for_garbage(self):
        assert parse_generated_cases("抱歉，我无法完成该请求。") == []

    def test_returns_empty_for_json_object(self):
        assert parse_generated_cases('{"name": "t", "code": "assert 1"}') == []

    def test_skips_non_dict_and_codeless_items(self):
        text = json.dumps(["x", {"name": "a"}, {"name": "b", "code": "assert 1"}])
        assert [c["name"] for c in parse_generated_cases(text)] == ["b"]

    def test_returns_empty_for_empty_input(self):
        assert parse_generated_cases("") == []

    def test_caps_case_count(self):
        text = json.dumps([{"name": f"t{i}", "code": "assert 1"} for i in range(9)])
        assert len(parse_generated_cases(text)) == mod.MAX_CASES


class TestSafeValue:
    """安全入参构造"""

    @pytest.mark.parametrize("annotation,expected", [
        ("str", "''"), ("int", "0"), ("float", "0.0"), ("bool", "False"),
        ("bytes", "b''"), ("list", "[]"), ("dict", "{}"), ("tuple", "()"),
        ("set", "set()"), ("None", "None"), ("Any", "''"), ("Optional[str]", "''"),
    ])
    def test_uses_annotation(self, annotation, expected):
        assert safe_value(mod.FunctionParam(name="x", annotation=annotation)) == expected

    def test_text_like_name_without_annotation(self):
        assert safe_value(mod.FunctionParam(name="query")) == "''"

    def test_unknown_param_falls_back_to_empty_string(self):
        assert safe_value(mod.FunctionParam(name="payload")) == "''"


class TestDeterministicCases:
    """确定性兜底用例生成"""

    def test_module_function_gets_two_cases(self, tmp_path, sample_module):
        target = locate_function(sample_module, "label", root=tmp_path)
        cases = deterministic_cases(target)
        assert [c["name"] for c in cases] == [
            "test_label_callable", "test_label_deterministic",
        ]
        assert "assert label('') is not None or True" in cases[0]["code"]
        assert "assert label('') == label('')" in cases[1]["code"]

    def test_int_params_get_zero(self, tmp_path, sample_module):
        target = locate_function(sample_module, "add", root=tmp_path)
        assert "assert add(0, 0) == add(0, 0)" in deterministic_cases(target)[1]["code"]

    def test_async_function_uses_await(self, tmp_path, sample_module):
        target = locate_function(sample_module, "afetch", root=tmp_path)
        cases = deterministic_cases(target)
        assert "async def test_afetch_callable():" in cases[0]["code"]
        assert "await afetch('')" in cases[0]["code"]

    def test_method_gets_importable_only(self, tmp_path, sample_module):
        target = locate_function(sample_module, "Calc.mul", root=tmp_path)
        cases = deterministic_cases(target)
        assert len(cases) == 1
        assert "assert callable(getattr(Calc, 'mul'))" in cases[0]["code"]

    def test_identifier_is_sanitized(self, tmp_path, sample_module):
        target = locate_function(sample_module, "label", root=tmp_path)
        target.qualname = "label-x"
        assert "test_label_x_callable" in deterministic_cases(target)[0]["name"]


class TestPipelineHappyPath:
    """五阶段全部通过的基线"""

    @pytest.mark.asyncio
    async def test_stages_evidence_and_success(self, d3_executor, fake_runner):
        outcome = await d3_executor.run("为 label 生成单元测试")
        assert [s["stage"] for s in outcome.stages] == list(mod.D3_STAGES)
        assert all(s["ok"] for s in outcome.stages)
        assert outcome.success is True
        assert outcome.generation_source == "fallback"
        assert outcome.metric == 0.9
        assert outcome.target.qualname == "label"
        assert outcome.coverage == {
            "percent": 90.0, "source": "real", "threshold": 50.0, "passed": True,
        }
        assert "覆盖率 90.0%" in outcome.details

    @pytest.mark.asyncio
    async def test_runner_receives_counts_and_file(self, d3_executor, fake_runner):
        await d3_executor.run("为 label 生成单元测试")
        call = fake_runner.calls[0]
        assert call["target_module"] == "sample_mod"
        assert call["case_count"] == 2
        assert call["assert_count"] == 2
        assert call["files"] == ["test_label.py"]

    @pytest.mark.asyncio
    async def test_line_range_matches_located_function(self, tmp_path, sample_module,
                                                      make_executor, runner_cls):
        runner = runner_cls()
        await make_executor(runner=runner).run("任务")
        target = locate_function(sample_module, "label", root=tmp_path)
        assert runner.calls[0]["line_range"] == (target.lineno, target.end_lineno)

    @pytest.mark.asyncio
    async def test_written_file_has_header_and_cases(self, d3_executor, fake_runner):
        await d3_executor.run("为 label 生成单元测试")
        source = fake_runner.written_source()
        assert "D3 自举生成的单元测试（M20）" in source
        assert "目标: sample_mod:label" in source
        assert "def test_label_callable():" in source

    @pytest.mark.asyncio
    async def test_to_dict_hides_code_body(self, d3_executor):
        payload = (await d3_executor.run("为 label 生成单元测试")).to_dict()
        assert "code" not in payload["cases"][0]
        assert payload["cases"][0]["name"] == "test_label_callable"
        assert payload["success"] is True
        assert payload["llm"]["used"] is False
        assert payload["target"]["module"] == "sample_mod"


class TestSelectStageFailure:
    """第 1 段失败：只剩一条阶段证据并如实返回"""

    @pytest.mark.asyncio
    async def test_bad_target_spec(self, d3_executor):
        outcome = await d3_executor.run("任务", target="没有冒号的目标")
        assert [s["stage"] for s in outcome.stages] == [mod.STAGE_SELECT]
        assert outcome.stages[0]["ok"] is False
        assert outcome.success is False
        assert "目标定位失败" in outcome.details

    @pytest.mark.asyncio
    async def test_missing_function(self, d3_executor):
        outcome = await d3_executor.run("任务", target="sample_mod:nope")
        assert outcome.success is False
        assert "nope" in outcome.details
        assert outcome.cases == []


class TestDesignStage:
    """第 2 段：LLM 真实路径与各类回退"""

    @pytest.mark.asyncio
    async def test_llm_valid_cases_accepted(self, d3_executor, fake_runner, llm_json):
        d3_executor.llm = llm_json(provider="deepseek", total_tokens=123)
        outcome = await d3_executor.run("为 label 生成单元测试")
        assert outcome.generation_source == "llm"
        assert outcome.llm == {
            "used": True, "simulated": False, "provider": "deepseek",
            "tokens": 123, "parsed": 1, "accepted": 1,
        }
        assert outcome.success is True
        assert outcome.cases[0]["name"] == "test_label_empty"
        assert "test_label_empty" in fake_runner.written_source()

    @pytest.mark.asyncio
    async def test_llm_prompt_and_kwargs(self, d3_executor, llm_json):
        llm = llm_json()
        d3_executor.llm = llm
        await d3_executor.run("为 label 生成单元测试")
        assert "被测模块：sample_mod" in llm.prompts[0]
        assert "被测函数：label" in llm.prompts[0]
        assert "只输出 JSON 数组" in llm.prompts[0]
        assert llm.kwargs[0]["purpose"] == "selfboot"
        assert llm.kwargs[0]["tenant_id"] == "system"
        assert llm.kwargs[0]["ref_id"] == "sample_mod:label"

    @pytest.mark.asyncio
    async def test_simulated_llm_falls_back(self, d3_executor, llm_json):
        d3_executor.llm = llm_json(simulated=True, provider="simulated")
        outcome = await d3_executor.run("为 label 生成单元测试")
        assert outcome.generation_source == "fallback"
        assert outcome.llm["simulated"] is True
        assert outcome.llm["provider"] == "simulated"

    @pytest.mark.asyncio
    async def test_garbage_output_falls_back(self, d3_executor, fake_llm_cls):
        d3_executor.llm = fake_llm_cls("抱歉，我无法完成该请求。")
        outcome = await d3_executor.run("为 label 生成单元测试")
        assert outcome.generation_source == "fallback"
        assert outcome.llm["parsed"] == 0
        assert outcome.llm["accepted"] == 0

    @pytest.mark.asyncio
    async def test_invalid_codes_rejected_but_valid_kept(self, d3_executor, llm_json):
        d3_executor.llm = llm_json([
            {"name": "bad", "code": "def broken(:\n    pass\n"},
            {"name": "evil", "code": "import os\n\n\ndef t():\n    assert os.getcwd()\n"},
            {"name": "ok", "code": VALID_CODE, "rationale": "唯一合规用例"},
        ])
        outcome = await d3_executor.run("为 label 生成单元测试")
        assert outcome.llm["parsed"] == 3
        assert outcome.llm["accepted"] == 1
        assert outcome.generation_source == "llm"

    @pytest.mark.asyncio
    async def test_all_invalid_falls_back(self, d3_executor, llm_json):
        d3_executor.llm = llm_json([
            {"name": "evil", "code": "import os\n\n\ndef t():\n    assert True\n"},
        ])
        outcome = await d3_executor.run("为 label 生成单元测试")
        assert outcome.generation_source == "fallback"
        assert outcome.llm["accepted"] == 0

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back(self, d3_executor, fake_llm_cls):
        d3_executor.llm = fake_llm_cls(error=RuntimeError("供应商全挂"))
        outcome = await d3_executor.run("为 label 生成单元测试")
        assert outcome.generation_source == "fallback"
        assert outcome.llm["error"] == "供应商全挂"
        assert outcome.success is True

    @pytest.mark.asyncio
    async def test_empty_design_short_circuits(self, d3_executor, monkeypatch):
        async def _empty(target):
            return [], "fallback", {"used": False}

        monkeypatch.setattr(d3_executor, "design_cases", _empty)
        outcome = await d3_executor.run("为 label 生成单元测试")
        assert [s["stage"] for s in outcome.stages] == [
            mod.STAGE_SELECT, mod.STAGE_DESIGN,
        ]
        assert outcome.stages[-1]["ok"] is False
        assert outcome.details == "用例设计为空"
        assert outcome.success is False


class TestRunStageFailure:
    """第 4 段：运行失败与 LLM → 兜底重跑"""

    @pytest.mark.asyncio
    async def test_run_failure_marks_stage_failed(self, make_executor, runner_cls):
        runner = runner_cls([RunOutcome(passed=0, failed=1, returncode=1)])
        outcome = await make_executor(runner=runner).run("为 label 生成单元测试")
        assert outcome.success is False
        assert outcome.stages[3]["stage"] == mod.STAGE_RUN
        assert outcome.stages[3]["ok"] is False
        assert outcome.attempts == [{
            "source": "fallback", "passed": 0, "failed": 1, "errors": 0,
            "returncode": 1, "timed_out": False, "coverage_percent": None,
            "coverage_source": "none",
        }]

    @pytest.mark.asyncio
    async def test_llm_run_failure_retries_with_fallback(self, make_executor,
                                                        runner_cls, llm_json):
        runner = runner_cls([
            RunOutcome(passed=0, failed=1, returncode=1),
            RunOutcome(passed=2, returncode=0, coverage_percent=88.0,
                       coverage_source="real"),
        ])
        executor = make_executor(runner=runner, llm=llm_json())
        outcome = await executor.run("为 label 生成单元测试")
        assert [a["source"] for a in outcome.attempts] == ["llm", "fallback"]
        assert outcome.generation_source == "fallback"
        assert outcome.success is True
        assert outcome.cases[0]["name"] == "test_label_callable"
        assert len(runner.calls) == 2

    @pytest.mark.asyncio
    async def test_llm_run_failure_without_recovery_keeps_llm_source(
            self, make_executor, runner_cls, llm_json):
        runner = runner_cls([
            RunOutcome(passed=0, failed=1, returncode=1),
            RunOutcome(passed=0, failed=1, returncode=1),
        ])
        executor = make_executor(runner=runner, llm=llm_json())
        outcome = await executor.run("为 label 生成单元测试")
        assert [a["source"] for a in outcome.attempts] == ["llm", "fallback"]
        assert outcome.generation_source == "llm"
        assert outcome.success is False


class TestCoverageStage:
    """第 5 段：覆盖率达标两态与代理指标"""

    @pytest.mark.asyncio
    async def test_below_threshold_fails(self, make_executor, runner_cls):
        runner = runner_cls([RunOutcome(
            passed=2, returncode=0, coverage_percent=20.0, coverage_source="real")])
        outcome = await make_executor(runner=runner).run("为 label 生成单元测试")
        assert outcome.success is False
        assert outcome.run["passed"] == 2  # 用例本身是过的
        assert outcome.coverage["passed"] is False
        assert outcome.stages[-1]["ok"] is False

    @pytest.mark.asyncio
    async def test_proxy_coverage_is_labelled(self, make_executor, runner_cls):
        runner = runner_cls([RunOutcome(
            passed=2, returncode=0, coverage_percent=75.0, coverage_source="proxy")])
        outcome = await make_executor(runner=runner).run("为 label 生成单元测试")
        assert outcome.coverage["source"] == "proxy"
        assert outcome.success is True
        assert "proxy" in outcome.details

    @pytest.mark.asyncio
    async def test_threshold_is_configurable(self, make_executor, runner_cls):
        runner = runner_cls([RunOutcome(
            passed=2, returncode=0, coverage_percent=60.0, coverage_source="real")])
        executor = make_executor(runner=runner, threshold=0.8)
        outcome = await executor.run("为 label 生成单元测试")
        assert outcome.coverage["threshold"] == 80.0
        assert outcome.success is False

    @pytest.mark.asyncio
    async def test_timeout_marks_success_false(self, make_executor, runner_cls):
        runner = runner_cls([RunOutcome(returncode=-1, timed_out=True)])
        outcome = await make_executor(runner=runner).run("为 label 生成单元测试")
        assert outcome.success is False
        assert outcome.run["timed_out"] is True


class TestExecutorContract:
    """Experimenter 执行器契约"""

    @pytest.mark.asyncio
    async def test_run_as_executor_returns_triple(self, d3_executor):
        from types import SimpleNamespace
        hypothesis = SimpleNamespace(statement="为 label 生成单元测试", approach="")
        success, metric, details = await d3_executor.run_as_executor(hypothesis, 1)
        assert success is True
        assert metric == 0.9
        assert "sample_mod:label" in details

    @pytest.mark.asyncio
    async def test_target_taken_from_hypothesis_approach(self, d3_executor):
        from types import SimpleNamespace
        hypothesis = SimpleNamespace(
            statement="任意任务", approach="target: sample_mod:add\n补充说明")
        success, _, details = await d3_executor.run_as_executor(hypothesis, 1)
        assert success is True
        assert "sample_mod:add" in details

    @pytest.mark.asyncio
    async def test_missing_target_falls_back_to_default(self, d3_executor):
        from types import SimpleNamespace
        hypothesis = SimpleNamespace(statement="任意任务", approach="没有目标前缀")
        _, _, details = await d3_executor.run_as_executor(hypothesis, 1)
        assert "sample_mod:label" in details

    @pytest.mark.asyncio
    async def test_bound_method_is_coroutine_function(self, d3_executor):
        """契约成立的前提：必须传绑定方法（实例本身不会被识别为协程函数）"""
        import inspect
        assert inspect.iscoroutinefunction(d3_executor.run_as_executor) is True
        assert inspect.iscoroutinefunction(d3_executor) is False

    def test_target_from_hypothesis_none_without_prefix(self):
        from types import SimpleNamespace
        assert D3Executor._target_from_hypothesis(
            SimpleNamespace(approach="abc")) is None
        assert D3Executor._target_from_hypothesis(SimpleNamespace()) is None


class TestBuildTestgenPrompt:
    """prompt 构造（含超长截断）"""

    def test_prompt_includes_source_and_contract(self, tmp_path, sample_module):
        from jkos_core.selfboot.d3_testgen import build_testgen_prompt
        prompt = build_testgen_prompt(locate_function(sample_module, "add", root=tmp_path))
        assert "def add(a: int, b: int) -> int:" in prompt
        assert "必须包含 assert" in prompt

    def test_long_source_is_truncated(self, tmp_path, sample_module):
        from jkos_core.selfboot.d3_testgen import build_testgen_prompt
        target = locate_function(sample_module, "add", root=tmp_path)
        target.source = "x = 1\n" * (mod.MAX_PROMPT_CHARS // 3)
        assert "超长截断" in build_testgen_prompt(target)