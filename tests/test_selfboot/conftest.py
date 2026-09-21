"""M20 D3 自举执行器 - 测试公共 fixtures。

约定（对齐 tests/test_optimization/conftest.py）：
- 被测模块写到 `tmp_path`，`D3Executor(repo_root=tmp_path)` 只在该目录内解析目标；
- pytest 运行器用 Fake 注入，**单元测试不真跑子进程**；
- LLM 用 SimpleNamespace stub，不触网。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from jkos_core.selfboot.d3_testgen import D3Executor, RunOutcome

# LLM 路径用的合规用例（导入被测模块 + 含 assert），供多处复用
VALID_LLM_CODE = (
    "from sample_mod import label\n"
    "\n"
    "\n"
    "def test_label_empty():\n"
    "    assert label('') == 'empty'\n"
)

SAMPLE_MODULE_SOURCE = '''"""样例模块（D3 测试用，不参与真实业务）"""


def add(a: int, b: int) -> int:
    """两数相加"""
    return a + b


def label(text: str) -> str:
    """给文本加前缀（空串也安全）"""
    if not text:
        return "empty"
    return "v:" + text


async def afetch(name: str) -> str:
    """异步取数"""
    return f"hi {name}"


def kwonly(a: int, *, flag=False, name: str = "x") -> str:
    """含 keyword-only 参数（flag 无注解，name 有注解）"""
    return f"{a}-{flag}-{name}"


class Calc:
    """计算器"""

    def mul(self, a: int, b: int) -> int:
        """乘法"""
        return a * b

    def outer(self) -> int:
        def inner(x: int) -> int:
            return x + 1

        return inner(1)
'''


class FakePytestRunner:
    """测试用运行器：按预设序列返回结果，并记录调用参数"""

    def __init__(self, outcomes: Optional[List[RunOutcome]] = None,
                 default: Optional[RunOutcome] = None):
        self.calls: List[Dict[str, Any]] = []
        self._outcomes = list(outcomes or [])
        self._default = default or RunOutcome(
            passed=2, returncode=0, coverage_percent=90.0, coverage_source="real")

    async def run(self, test_dir: Path, target_module: str, *, case_count: int = 0,
                  assert_count: int = 0, line_range=None) -> RunOutcome:
        self.calls.append({
            "test_dir": Path(test_dir),
            "target_module": target_module,
            "case_count": case_count,
            "assert_count": assert_count,
            "line_range": line_range,
            "files": sorted(p.name for p in Path(test_dir).glob("test_*.py")),
        })
        if self._outcomes:
            return self._outcomes.pop(0)
        return self._default

    def written_source(self) -> str:
        """最近一次写入的用例文件内容"""
        assert self.calls, "运行器尚未被调用"
        test_dir = self.calls[-1]["test_dir"]
        files = sorted(test_dir.glob("test_*.py"))
        assert files, f"临时目录内没有用例文件: {test_dir}"
        return files[0].read_text(encoding="utf-8")


class FakeLLM:
    """LLM stub：返回预设 content / simulated 标记，或抛出异常"""

    def __init__(self, content: str = "", *, simulated: bool = False,
                 provider: str = "fake", total_tokens: int = 42,
                 error: Optional[Exception] = None):
        self.content = content
        self.simulated = simulated
        self.provider = provider
        self.total_tokens = total_tokens
        self.error = error
        self.prompts: List[str] = []
        self.kwargs: List[Dict[str, Any]] = []

    async def chat_text(self, prompt: str, **kwargs: Any):
        self.prompts.append(prompt)
        self.kwargs.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            content=self.content, simulated=self.simulated,
            provider=self.provider, total_tokens=self.total_tokens,
        )


@pytest.fixture
def sample_module(tmp_path) -> Path:
    """把样例模块写入 tmp_path，返回模块名（供 D3Executor 以 tmp_path 为根解析）"""
    (tmp_path / "sample_mod.py").write_text(SAMPLE_MODULE_SOURCE, encoding="utf-8")
    return "sample_mod"


@pytest.fixture
def fake_runner() -> FakePytestRunner:
    return FakePytestRunner()


@pytest.fixture
def d3_executor(tmp_path, sample_module, fake_runner) -> D3Executor:
    """默认目标为样例模块的 label（空串入参安全），repo_root 限定在 tmp_path"""
    return D3Executor(
        runner=fake_runner,
        default_target=f"{sample_module}:label",
        repo_root=tmp_path,
    )


@pytest.fixture
def runner_cls():
    """Fake 运行器类（供用例构造带预设结果的实例）"""
    return FakePytestRunner


@pytest.fixture
def fake_llm_cls():
    """Fake LLM 类（供用例构造自定义输出/异常）"""
    return FakeLLM


@pytest.fixture
def make_executor(tmp_path, sample_module):
    """按需构造执行器（可指定运行器与 LLM），repo_root 固定为 tmp_path"""
    def _make(*, runner=None, llm=None, default_target=None, threshold=0.5):
        return D3Executor(
            runner=runner or FakePytestRunner(),
            llm=llm,
            default_target=default_target or f"{sample_module}:label",
            repo_root=tmp_path,
            coverage_threshold=threshold,
        )
    return _make


@pytest.fixture
def llm_json():
    """构造返回合法 JSON 用例数组的 LLM stub"""
    def _make(cases=None, **kwargs) -> FakeLLM:
        payload = cases if cases is not None else [
            {"name": "test_label_empty", "code": VALID_LLM_CODE,
             "rationale": "空串分支"},
        ]
        return FakeLLM(json.dumps(payload, ensure_ascii=False), **kwargs)
    return _make


@pytest.fixture
def make_loop(tmp_path, sample_module):
    """按需构造四段闭环引擎：数据目录与报告目录都落在 tmp_path（不碰仓库存储）"""
    from jkos_core.selfboot.loop import SelfBootstrapEngine

    def _make(*, llm=None, runner=None, target=None, threshold=0.5) -> SelfBootstrapEngine:
        spec = target or f"{sample_module}:label"
        return SelfBootstrapEngine(
            llm=llm,
            executor=D3Executor(
                runner=runner or FakePytestRunner(), llm=llm, default_target=spec,
                repo_root=tmp_path, coverage_threshold=threshold),
            data_dir=str(tmp_path / "data"),
            report_dir=str(tmp_path / "reports"),
            target=spec,
        )
    return _make


@pytest.fixture(autouse=True)
def _cleanup_tmp_modules():
    """清理样例模块可能留下的模块缓存（本套测试不 import 被测模块，仅防御）"""
    yield
    for name in [n for n in sys.modules if n.startswith("sample_mod")]:
        sys.modules.pop(name, None)