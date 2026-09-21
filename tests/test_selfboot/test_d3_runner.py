"""M20 D3 执行器 - pytest 运行器测试（命令构造 / 环境隔离 / 覆盖率解析）

约定：**不真跑子进程** —— 子进程入口 `_run_subprocess` 一律 monkeypatch，
pytest 输出与 coverage.json 用构造数据驱动。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jkos_core.selfboot import d3_testgen as mod
from jkos_core.selfboot.d3_testgen import (
    RunOutcome,
    SubprocessPytestRunner,
    _percent_from_info,
    parse_pytest_counts,
    proxy_coverage,
)


@pytest.fixture
def runner(tmp_path) -> SubprocessPytestRunner:
    return SubprocessPytestRunner(python="py-test", timeout=5.0, repo_root=tmp_path)


class TestCommandConstruction:
    """子进程命令行构造"""

    def test_plain_pytest_when_coverage_missing(self, runner, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "_coverage_available", lambda: False)
        cmd = runner.build_command(tmp_path)
        assert cmd[:3] == ["py-test", "-m", "pytest"]
        assert "-p" in cmd and "no:cacheprovider" in cmd
        assert "-q" in cmd and "--no-header" in cmd
        assert "coverage" not in cmd

    def test_wraps_with_coverage_when_available(self, runner, tmp_path, monkeypatch):
        monkeypatch.setattr(runner, "_coverage_available", lambda: True)
        cmd = runner.build_command(tmp_path)
        assert cmd[:4] == ["py-test", "-m", "coverage", "run"]
        assert f"--data-file={tmp_path / '.coverage'}" in cmd
        assert "pytest" in cmd

    def test_coverage_probe_handles_broken_find_spec(self, runner, monkeypatch):
        def _boom(name):
            raise ImportError("finder exploded")

        monkeypatch.setattr(mod.importlib.util, "find_spec", _boom)
        assert runner._coverage_available() is False

    def test_timeout_is_configurable(self, tmp_path):
        assert SubprocessPytestRunner(timeout=1.5, repo_root=tmp_path).timeout == 1.5


class TestEnvironmentIsolation:
    """子进程环境：注入 PYTHONPATH、剔除 PYTEST_ADDOPTS"""

    def test_drops_pytest_addopts(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("PYTEST_ADDOPTS", "--cov=jkos_core")
        env = runner.build_env(tmp_path, tmp_path)
        assert "PYTEST_ADDOPTS" not in env

    def test_prepends_repo_root_to_pythonpath(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("PYTHONPATH", "/existing")
        env = runner.build_env(tmp_path, tmp_path)
        assert env["PYTHONPATH"] == f"{tmp_path}{mod.os.pathsep}/existing"

    def test_pythonpath_without_existing(self, runner, tmp_path, monkeypatch):
        monkeypatch.delenv("PYTHONPATH", raising=False)
        assert runner.build_env(tmp_path, tmp_path)["PYTHONPATH"] == str(tmp_path)

    def test_disables_bytecode_writes(self, runner, tmp_path):
        assert runner.build_env(tmp_path, tmp_path)["PYTHONDONTWRITEBYTECODE"] == "1"


class TestParsePytestCounts:
    """pytest 输出计数解析"""

    @pytest.mark.parametrize("text,expected", [
        ("2 passed in 0.12s", (2, 0, 0)),
        ("1 failed, 2 passed in 0.5s", (2, 1, 0)),
        ("1 error, 1 passed", (1, 0, 1)),
        ("3 errors", (0, 0, 3)),
        ("no tests ran in 0.01s", (0, 0, 0)),
        ("", (0, 0, 0)),
    ])
    def test_counts(self, text, expected):
        assert parse_pytest_counts(text) == expected


class TestProxyCoverage:
    """coverage 不可用时的代理指标"""

    def test_zero_when_nothing_passed(self):
        assert proxy_coverage(0, 5) == 0.0

    def test_scales_with_assert_count(self):
        assert proxy_coverage(1, 2) == 60.0

    def test_capped_at_95(self):
        assert proxy_coverage(1, 100) == 95.0


class TestPercentFromInfo:
    """coverage json 条目 → 覆盖率（优先按函数行区间）"""

    INFO = {
        "executed_lines": [10, 11, 12, 20],
        "missing_lines": [13, 21],
        "summary": {"percent_covered": 50.0},
    }

    def test_scopes_to_line_range(self):
        # 区间 10..13 内语句行 = {10,11,12,13}，命中 3 → 75%
        assert _percent_from_info(self.INFO, (10, 13)) == 75.0

    def test_ignores_lines_outside_range(self):
        assert _percent_from_info(self.INFO, (20, 21)) == 50.0

    def test_falls_back_to_file_summary_without_range(self):
        assert _percent_from_info(self.INFO) == 50.0

    def test_falls_back_when_range_has_no_statements(self):
        assert _percent_from_info(self.INFO, (1, 3)) == 50.0

    def test_returns_none_without_summary(self):
        assert _percent_from_info({}, None) is None


class TestRunnerExecution:
    """执行流程（子进程入口被打桩）"""

    @pytest.mark.asyncio
    async def test_parses_counts_and_reads_real_coverage(self, runner, tmp_path,
                                                         monkeypatch):
        async def _fake(cmd, test_dir):
            if cmd[3] == "json":  # coverage json 分支
                (test_dir / "coverage.json").write_text(json.dumps({
                    "files": {
                        "C:\\repo\\sample_mod.py": {
                            "executed_lines": [10, 11],
                            "missing_lines": [12],
                            "summary": {"percent_covered": 66.7},
                        },
                    },
                }), encoding="utf-8")
                return RunOutcome(returncode=0)
            return RunOutcome(passed=3, returncode=0, stdout="3 passed in 0.1s")

        monkeypatch.setattr(runner, "_coverage_available", lambda: True)
        monkeypatch.setattr(runner, "_run_subprocess", _fake)
        outcome = await runner.run(tmp_path, "sample_mod", case_count=3,
                                   assert_count=3, line_range=(10, 12))
        assert outcome.passed == 3
        assert outcome.ok is True
        assert outcome.coverage_source == "real"
        assert outcome.coverage_percent == 66.67

    @pytest.mark.asyncio
    async def test_falls_back_to_proxy_when_report_missing(self, runner, tmp_path,
                                                           monkeypatch):
        async def _fake(cmd, test_dir):
            return RunOutcome(passed=2, returncode=0, stdout="2 passed")

        monkeypatch.setattr(runner, "_coverage_available", lambda: True)
        monkeypatch.setattr(runner, "_run_subprocess", _fake)
        outcome = await runner.run(tmp_path, "sample_mod", assert_count=2)
        assert outcome.coverage_source == "proxy"
        assert outcome.coverage_percent == 60.0

    @pytest.mark.asyncio
    async def test_proxy_only_when_coverage_unavailable(self, runner, tmp_path,
                                                       monkeypatch):
        async def _fake(cmd, test_dir):
            return RunOutcome(passed=1, returncode=0)

        monkeypatch.setattr(runner, "_coverage_available", lambda: False)
        monkeypatch.setattr(runner, "_run_subprocess", _fake)
        outcome = await runner.run(tmp_path, "sample_mod", assert_count=1)
        assert outcome.coverage_source == "proxy"
        assert outcome.coverage_percent == 55.0

    @pytest.mark.asyncio
    async def test_timeout_is_reported(self, runner, tmp_path, monkeypatch):
        async def _fake(cmd, test_dir):
            return RunOutcome(returncode=-1, timed_out=True)

        monkeypatch.setattr(runner, "_coverage_available", lambda: False)
        monkeypatch.setattr(runner, "_run_subprocess", _fake)
        outcome = await runner.run(tmp_path, "sample_mod")
        assert outcome.timed_out is True
        assert outcome.ok is False
        assert outcome.coverage_percent == 0.0  # 无 passed → 代理指标为 0

    @pytest.mark.asyncio
    async def test_read_coverage_handles_broken_json(self, runner, tmp_path,
                                                     monkeypatch):
        async def _fake(cmd, test_dir):
            (test_dir / "coverage.json").write_text("{ 不是 JSON", encoding="utf-8")
            return RunOutcome(returncode=0)

        monkeypatch.setattr(runner, "_run_subprocess", _fake)
        assert await runner._read_coverage(tmp_path, "sample_mod") is None

    @pytest.mark.asyncio
    async def test_read_coverage_returns_none_when_no_file_matches(self, runner,
                                                                   tmp_path,
                                                                   monkeypatch):
        async def _fake(cmd, test_dir):
            (test_dir / "coverage.json").write_text(json.dumps({
                "files": {"other.py": {"summary": {"percent_covered": 10.0}}},
            }), encoding="utf-8")
            return RunOutcome(returncode=0)

        monkeypatch.setattr(runner, "_run_subprocess", _fake)
        assert await runner._read_coverage(tmp_path, "sample_mod") is None

    @pytest.mark.asyncio
    async def test_read_coverage_matches_package_init(self, runner, tmp_path,
                                                      monkeypatch):
        async def _fake(cmd, test_dir):
            (test_dir / "coverage.json").write_text(json.dumps({
                "files": {"repo/pkg/__init__.py": {
                    "summary": {"percent_covered": 100.0}}},
            }), encoding="utf-8")
            return RunOutcome(returncode=0)

        monkeypatch.setattr(runner, "_run_subprocess", _fake)
        assert await runner._read_coverage(tmp_path, "pkg") == 100.0

    @pytest.mark.asyncio
    async def test_missing_percent_covered_key(self, runner, tmp_path, monkeypatch):
        async def _fake(cmd, test_dir):
            (test_dir / "coverage.json").write_text(json.dumps({
                "files": {"sample_mod.py": {"summary": {}}},
            }), encoding="utf-8")
            return RunOutcome(returncode=0)

        monkeypatch.setattr(runner, "_run_subprocess", _fake)
        assert await runner._read_coverage(tmp_path, "sample_mod") is None


class TestRealSubprocessRunner:
    """真实子进程路径的最小验证（跑一个不依赖被测模块的用例文件）"""

    @pytest.mark.asyncio
    async def test_real_run_on_tmp_dir(self, tmp_path):
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "test_ok.py").write_text(
            "def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")
        runner = SubprocessPytestRunner(repo_root=tmp_path, timeout=120.0)
        outcome = await runner.run(workdir, "sample_mod", case_count=1, assert_count=1)
        assert outcome.passed == 1
        assert outcome.failed == 0
        assert outcome.ok is True
        assert outcome.coverage_source in {"real", "proxy"}

    @pytest.mark.asyncio
    async def test_real_run_reports_failures(self, tmp_path):
        workdir = tmp_path / "work2"
        workdir.mkdir()
        (workdir / "test_bad.py").write_text(
            "def test_bad():\n    assert False\n", encoding="utf-8")
        runner = SubprocessPytestRunner(repo_root=tmp_path, timeout=120.0)
        outcome = await runner.run(workdir, "sample_mod", case_count=1, assert_count=1)
        assert outcome.failed == 1
        assert outcome.ok is False
        assert outcome.returncode != 0

    @pytest.mark.asyncio
    async def test_real_run_reports_start_failure(self, tmp_path):
        runner = SubprocessPytestRunner(python=str(tmp_path / "no-such-python"),
                                        repo_root=tmp_path)
        outcome = await runner.run(tmp_path, "sample_mod")
        assert outcome.returncode == 127
        assert outcome.ok is False
        assert outcome.coverage_percent == 0.0

    @pytest.mark.asyncio
    async def test_real_run_times_out(self, tmp_path):
        workdir = tmp_path / "work3"
        workdir.mkdir()
        (workdir / "test_slow.py").write_text(
            "import time\n\n\ndef test_slow():\n    time.sleep(5)\n", encoding="utf-8")
        runner = SubprocessPytestRunner(repo_root=tmp_path, timeout=1.0)
        outcome = await runner.run(workdir, "sample_mod")
        assert outcome.timed_out is True
        assert outcome.ok is False


class TestRunOutcomeModel:
    """RunOutcome 的判定与序列化"""

    @pytest.mark.parametrize("outcome,expected", [
        (RunOutcome(passed=1), True),
        (RunOutcome(passed=1, failed=1), False),
        (RunOutcome(passed=1, errors=1), False),
        (RunOutcome(), False),
        (RunOutcome(passed=1, timed_out=True), False),
    ])
    def test_ok_flag(self, outcome, expected):
        assert outcome.ok is expected

    def test_to_dict(self):
        payload = RunOutcome(passed=2, coverage_percent=80.0,
                             coverage_source="real").to_dict()
        assert payload["passed"] == 2
        assert payload["coverage_source"] == "real"
        assert set(payload) == {
            "passed", "failed", "errors", "returncode", "timed_out",
            "coverage_percent", "coverage_source",
        }