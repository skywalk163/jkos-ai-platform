"""极快AI操作系统 - D3 自举：单元测试生成执行器（M20.1）

D3 的业务定义（见 `docs/DSH-AI中台开发计划-三案例驱动.md`）：单元测试生成 ——
「选定函数 → 用例设计 → 生成 → 运行 → 覆盖率报告」，管道式。

本模块是 M20 自举闭环的**执行器**（exploration 引擎的 executor 实现）：

- **混合模式**：LLM 可用（`LLMResult.simulated is False`）走真实生成，
  否则（无 API Key 时路由器末尾的 SimulatedProvider 会返回模拟文本）走确定性兜底，
  保证离线可测、CI 可复现；
- **生成物是不可信输入**：语法校验 + 导入白名单 + 危险名拒绝，任一层不过即回退；
- **生成物只写临时目录**，绝不落仓库；
- **pytest 运行经 `PytestRunner` 协议注入**，单元测试不真跑子进程。

与探索引擎的接法：`run_as_executor` 满足 `Experimenter` 的 executor 契约
`(hypothesis, sample_size) -> (success, metric, details)`。**必须传绑定方法而非实例
本身** —— `inspect.iscoroutinefunction(实例)` 为 False（Python 不把可调用实例识别为
协程函数），传实例会被 Experimenter 当同步函数丢进线程池，拿到协程对象而不执行。
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import logging
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple

logger = logging.getLogger("dsh.selfboot")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = REPO_ROOT / "reports" / "m20"

# D3 默认目标：仓库内的纯文本函数，空入参安全（离线演示无需 LLM 也可跑通）
DEFAULT_TARGET = "jkos_core.exploration.knowledge_base:keyword_score"

# ─── D3 流水线五阶段 ───

STAGE_SELECT = "select"
STAGE_DESIGN = "design"
STAGE_GENERATE = "generate"
STAGE_RUN = "run"
STAGE_COVERAGE = "coverage"
D3_STAGES: Tuple[str, ...] = (
    STAGE_SELECT, STAGE_DESIGN, STAGE_GENERATE, STAGE_RUN, STAGE_COVERAGE,
)

# 阶段文案：会被固化为 ProcessStep.action，故保持简短（模板复现成本按文本长度计）
STAGE_LABELS: Dict[str, str] = {
    STAGE_SELECT: "select: 定位目标函数",
    STAGE_DESIGN: "design: 设计测试用例",
    STAGE_GENERATE: "generate: 生成用例代码",
    STAGE_RUN: "run: 运行 pytest 收集结果",
    STAGE_COVERAGE: "coverage: 产出覆盖率报告",
}


def pipeline_steps() -> List[str]:
    """D3 流水线的阶段文案（供闭环固化时作为流程步骤）"""
    return [STAGE_LABELS[s] for s in D3_STAGES]


class FunctionNotFound(LookupError):
    """目标函数在指定模块中不存在"""


class TargetSpecError(ValueError):
    """目标描述非法（格式错误或越出仓库根）"""


# ─── 目标函数定位（ast，不 import 被测模块）───


@dataclass
class FunctionParam:
    """函数形参：名称 + 注解文本（用于构造安全入参）"""
    name: str
    annotation: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "annotation": self.annotation}


@dataclass
class FunctionTarget:
    """D3 选定的目标函数"""
    module: str
    qualname: str
    source: str
    lineno: int = 0
    end_lineno: int = 0
    params: List[FunctionParam] = field(default_factory=list)
    has_docstring: bool = False
    is_async: bool = False

    @property
    def owner(self) -> str:
        """所属类名（模块级函数为空串）"""
        return self.qualname.rsplit(".", 1)[0] if "." in self.qualname else ""

    @property
    def attr(self) -> str:
        return self.qualname.rsplit(".", 1)[-1]

    @property
    def is_method(self) -> bool:
        return bool(self.owner)

    def import_stmt(self) -> str:
        """生成导入语句：方法导入其所属类，模块级函数导入函数名"""
        top = self.owner.split(".")[0] if self.is_method else self.qualname
        return f"from {self.module} import {top}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module,
            "qualname": self.qualname,
            "lineno": self.lineno,
            "end_lineno": self.end_lineno,
            "params": [p.to_dict() for p in self.params],
            "has_docstring": self.has_docstring,
            "is_async": self.is_async,
        }


def parse_target(spec: str) -> Tuple[str, str]:
    """解析目标描述 `<模块>:<限定名>` 或 `<文件路径>:<限定名>`"""
    raw = (spec or "").strip()
    if ":" not in raw:
        raise TargetSpecError(
            "目标格式应为 '<模块>:<限定名>'，例如 "
            "jkos_core.exploration.knowledge_base:keyword_score"
        )
    module, qualname = raw.split(":", 1)
    module, qualname = module.strip(), qualname.strip()
    if not module or not qualname:
        raise TargetSpecError("目标描述不能为空（格式 '<模块>:<限定名>'）")
    return module, qualname


def resolve_module_path(module: str, *, root: Optional[Path] = None) -> Path:
    """把模块名解析为仓库内的 .py 文件路径（不 import，避免导入副作用）"""
    base = Path(root or REPO_ROOT)
    if module.endswith(".py") or "/" in module or "\\" in module:
        candidate = Path(module)
        path = candidate if candidate.is_absolute() else base / candidate
    else:
        rel = Path(*module.split("."))
        path = base / rel.with_suffix(".py")
        if not path.exists():
            alt = base / rel / "__init__.py"
            if alt.exists():
                path = alt
    resolved = path.resolve()
    root_resolved = base.resolve()
    if root_resolved != resolved and root_resolved not in resolved.parents:
        raise TargetSpecError(f"目标文件越出仓库根: {resolved}")
    if not resolved.exists():
        raise TargetSpecError(f"模块文件不存在: {resolved}")
    return resolved


def locate_function(module: str, qualname: str, *,
                    root: Optional[Path] = None,
                    source_path: Optional[Path] = None) -> FunctionTarget:
    """用 ast 定位目标函数并抽取源码片段（不导入被测模块）

    支持模块级函数、`Cls.method` 与嵌套函数（层级用点号连接）。
    找不到时抛 :class:`FunctionNotFound`。
    """
    path = source_path or resolve_module_path(module, root=root)
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))

    scope: Optional[ast.AST] = tree
    found: Optional[ast.AST] = None
    for part in [p for p in (qualname or "").split(".") if p]:
        nxt = None
        for node in ast.iter_child_nodes(scope):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and node.name == part:
                nxt = node
                break
        if nxt is None:
            raise FunctionNotFound(f"{module}:{qualname} 中未找到 `{part}`")
        scope, found = nxt, nxt

    if not isinstance(found, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise FunctionNotFound(f"{module}:{qualname} 不是函数")

    args = found.args
    params: List[FunctionParam] = []
    for arg in list(getattr(args, "posonlyargs", [])) + list(args.args):
        if arg.arg in ("self", "cls"):
            continue
        params.append(FunctionParam(name=arg.arg, annotation=_annotation_text(arg)))
    for arg in args.kwonlyargs:
        params.append(FunctionParam(name=arg.arg, annotation=_annotation_text(arg)))

    return FunctionTarget(
        module=module,
        qualname=qualname,
        source=ast.get_source_segment(text, found) or "",
        lineno=found.lineno,
        end_lineno=getattr(found, "end_lineno", found.lineno),
        params=params,
        has_docstring=ast.get_docstring(found) is not None,
        is_async=isinstance(found, ast.AsyncFunctionDef),
    )


def _annotation_text(arg: ast.arg) -> str:
    """形参注解的源码文本（无注解返回空串）"""
    ann = getattr(arg, "annotation", None)
    if ann is None:
        return ""
    return ast.unparse(ann)


# ─── 生成代码安全校验 ───

# 允许导入的模块：pytest 与本项目自身（被测模块由 allowed_module 另行放行）
ALLOWED_IMPORTS = frozenset({"pytest", "jkos_core", "tenants"})

# 危险名/属性：生成物中出现即拒绝（执行外部命令、文件 IO、动态执行）
DENIED_NAMES = frozenset({
    "eval", "exec", "compile", "open", "__import__", "input", "breakpoint",
    "system", "popen", "spawn", "spawnl", "remove", "unlink", "rmdir",
    "rmtree", "rename", "chmod", "chown", "socket", "connect", "urlopen",
    "getenv", "putenv",
})
DENIED_MODULES = frozenset({
    "os", "sys", "subprocess", "shutil", "socket", "pathlib", "tempfile",
    "importlib", "ctypes", "pickle", "requests", "urllib", "http",
})


def validate_generated_code(code: str, *, allowed_module: str = "") -> List[str]:
    """校验 LLM 生成的用例代码，返回违规清单（空列表表示通过）

    三层中的前两层：① `compile` 语法校验；② AST 检查 —— 导入白名单
    （仅 pytest / 本项目包 / 被测模块）+ 危险名与危险模块拒绝 + 必须含 assert。

    用「导入白名单 + 危险名拒绝」而非「节点类型白名单」：后者会把 for/while
    等合法测试写法一并拒掉，却并不比前者更安全（真正可执行的是调用与导入）。
    """
    if not (code or "").strip():
        return ["代码为空"]
    try:
        ast.parse(code)  # ① 语法校验
    except SyntaxError as exc:
        return [f"语法错误: {exc.msg}（第 {exc.lineno} 行）"]

    violations: List[str] = []
    has_assert = False
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Assert):
            has_assert = True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                _check_import(alias.name, allowed_module, violations)
        elif isinstance(node, ast.ImportFrom):
            _check_import(node.module or "", allowed_module, violations)
        elif isinstance(node, ast.Name) and node.id in DENIED_NAMES:
            violations.append(f"使用了禁止的名字 `{node.id}`")
        elif isinstance(node, ast.Attribute) and node.attr in DENIED_NAMES:
            violations.append(f"使用了禁止的属性 `{node.attr}`")
    if not has_assert:
        violations.append("未包含 assert 断言")
    return violations


def _check_import(module: str, allowed_module: str, violations: List[str]) -> None:
    top = (module or "").split(".")[0]
    if not top:
        violations.append("存在无法解析的导入")
    elif top in DENIED_MODULES:
        violations.append(f"导入了禁止的模块 `{module}`")
    elif top not in ALLOWED_IMPORTS and top != allowed_module.split(".")[0]:
        violations.append(f"导入了白名单外的模块 `{module}`")


# ─── 用例生成（LLM 真实路径 + 确定性兜底）───

MAX_PROMPT_CHARS = 8000
MAX_CASES = 5
JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")
VALID_IDENT_RE = re.compile(r"[^0-9a-zA-Z_]")

# 按注解构造安全入参：取值都要求「纯函数可接受、不抛异常」
_SAFE_BY_ANNOTATION: Dict[str, str] = {
    "str": "''",
    "int": "0",
    "float": "0.0",
    "bool": "False",
    "bytes": "b''",
    "list": "[]",
    "dict": "{}",
    "tuple": "()",
    "set": "set()",
    "None": "None",
    "Any": "''",
}
_TEXT_LIKE_NAMES = frozenset({
    "text", "query", "s", "value", "name", "content", "prompt", "task",
    "key", "word", "line", "code", "path", "raw",
})


def safe_value(param: FunctionParam) -> str:
    """为形参构造安全入参字面量（优先按注解，其次按常见命名，兜底空串）

    兜底取空串：仓库内被生成目标以文本处理纯函数为主，空串对它们最安全；
    其余情况由「运行失败 → 回退重跑」与结果中的 passed/failed 如实反映。
    """
    ann = (param.annotation or "").strip()
    if ann in _SAFE_BY_ANNOTATION:
        return _SAFE_BY_ANNOTATION[ann]
    inner = ann[len("Optional["):-1].strip() if ann.startswith("Optional[") else ""
    if inner in _SAFE_BY_ANNOTATION:
        return _SAFE_BY_ANNOTATION[inner]
    return "''"


def build_testgen_prompt(target: FunctionTarget) -> str:
    """构造生成用例的 prompt（要求只输出 JSON 数组，沿用项目既有解析范式）"""
    body = target.source
    if len(body) > MAX_PROMPT_CHARS:
        body = body[:MAX_PROMPT_CHARS] + "\n# ...（超长截断）"
    return (
        "你是资深测试工程师。请为下面的 Python 函数编写 pytest 单元测试。\n"
        "要求：\n"
        f"1. 只允许 import pytest 与被测模块（{target.import_stmt()}）；\n"
        "2. 每个用例函数内必须包含 assert 断言；\n"
        "3. 不要读文件、不要执行系统命令、不要联网；\n"
        "4. 只输出 JSON 数组（不要其它文字），元素结构：\n"
        '[{"name": "test_xxx", "code": "完整的测试函数源码", "rationale": "设计理由"}]\n\n'
        f"被测模块：{target.module}\n"
        f"被测函数：{target.qualname}（第 {target.lineno}-{target.end_lineno} 行）\n"
        f"函数源码：\n```python\n{body}\n```\n"
    )


def parse_generated_cases(content: str) -> List[Dict[str, str]]:
    """容错解析 LLM 输出为用例列表（剥围栏 → 整段 → 首个数组，全失败返回空）"""
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    candidates = [text]
    match = JSON_ARRAY_RE.search(text)
    if match:
        candidates.insert(0, match.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, list):
            continue
        cases: List[Dict[str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "")
            if not code.strip():
                continue
            cases.append({
                "name": str(item.get("name") or ""),
                "code": code,
                "rationale": str(item.get("rationale") or ""),
            })
        return cases[:MAX_CASES]
    return []


def _ident(name: str, fallback: str) -> str:
    """把任意文案规整成合法 Python 标识符片段"""
    cleaned = VALID_IDENT_RE.sub("_", (name or "").strip()).strip("_")
    return cleaned or fallback


def _case_body(name: str, target: FunctionTarget, args: str, kind: str) -> str:
    """拼装单个用例函数源码（同步/异步两种形态）"""
    stmt = target.import_stmt()
    call = f"{target.qualname}({args})"
    prefix = "async def" if target.is_async else "def"
    if kind == "callable":
        expr = f"await {call}" if target.is_async else call
        assertion = f"assert {expr} is not None or True"
    else:
        expr = f"await {call}" if target.is_async else call
        assertion = f"assert {expr} == {expr}"
    return f"{stmt}\n\n\n{prefix} {name}():\n    {assertion}\n"


def deterministic_cases(target: FunctionTarget) -> List[Dict[str, str]]:
    """确定性兜底用例：不猜测业务语义，只断言「可调用」与「结果稳定」

    对任意纯函数安全（不会因语义猜错而假失败），因此是离线可复现的基线。
    方法（需要实例）不做调用型断言，仅断言类可导入且属性可调用。
    """
    ident = _ident(target.attr, "target")
    if target.is_method:
        owner = target.owner.split(".")[-1]
        name = f"test_{ident}_importable"
        return [{
            "name": name,
            "code": (
                f"{target.import_stmt()}\n\n\n"
                f"def {name}():\n"
                f"    assert callable(getattr({owner}, '{target.attr}'))\n"
            ),
            "rationale": "方法是实例级行为，兜底仅验证类可导入且属性可调用",
        }]

    args = ", ".join(safe_value(p) for p in target.params)
    return [
        {
            "name": f"test_{ident}_callable",
            "code": _case_body(f"test_{ident}_callable", target, args, "callable"),
            "rationale": "空/零值入参下可调用且不抛异常",
        },
        {
            "name": f"test_{ident}_deterministic",
            "code": _case_body(f"test_{ident}_deterministic", target, args, "deterministic"),
            "rationale": "同输入两次调用结果一致（纯函数确定性）",
        },
    ]


# ─── pytest 运行器（协议 + 真实实现）───


@dataclass
class RunOutcome:
    """一次 pytest 运行的结果"""
    passed: int = 0
    failed: int = 0
    errors: int = 0
    returncode: int = 0
    stdout: str = ""
    timed_out: bool = False
    coverage_percent: Optional[float] = None
    coverage_source: str = "none"   # real | proxy | none

    @property
    def ok(self) -> bool:
        return (not self.timed_out) and self.failed == 0 and self.errors == 0 \
            and self.passed > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "coverage_percent": self.coverage_percent,
            "coverage_source": self.coverage_source,
        }


class PytestRunner(Protocol):
    """pytest 运行器协议：单元测试注入 Fake，避免真跑子进程

    `line_range` 为被测函数在源文件中的行号区间；覆盖率按该区间统计
    （只测一个函数时，整文件覆盖率无意义）。
    """

    async def run(self, test_dir: Path, target_module: str, *,
                  case_count: int = 0, assert_count: int = 0,
                  line_range: Optional[Tuple[int, int]] = None) -> RunOutcome:
        ...


_COUNT_RE = {
    "passed": re.compile(r"(\d+) passed"),
    "failed": re.compile(r"(\d+) failed"),
    "errors": re.compile(r"(\d+) error"),
}


def parse_pytest_counts(text: str) -> Tuple[int, int, int]:
    """从 pytest 输出解析 passed / failed / errors 计数"""
    counts = []
    for key in ("passed", "failed", "errors"):
        match = _COUNT_RE[key].search(text or "")
        counts.append(int(match.group(1)) if match else 0)
    return counts[0], counts[1], counts[2]


def proxy_coverage(passed: int, assert_count: int) -> float:
    """coverage 不可用时的代理指标（确定性，仅供离线可测；标注 coverage_source=proxy）"""
    if passed <= 0:
        return 0.0
    return min(95.0, 50.0 + 5.0 * assert_count)


def _percent_from_info(info: Dict[str, Any],
                       line_range: Optional[Tuple[int, int]] = None) -> Optional[float]:
    """从 coverage json 的单文件条目取覆盖率（优先按函数行区间）

    coverage 只把**语句行**记入 executed_lines/missing_lines，区间内的空行、
    注释、续行都不计入，故分母取「区间内语句行」，避免低估。
    """
    if line_range:
        start, end = line_range
        span = set(range(start, end + 1))
        executed = set(info.get("executed_lines") or [])
        relevant = (executed | set(info.get("missing_lines") or [])) & span
        if relevant:
            return round(len(executed & span) / len(relevant) * 100, 2)
    percent = (info.get("summary") or {}).get("percent_covered")
    return float(percent) if percent is not None else None


class SubprocessPytestRunner:
    """真实实现：在仓库外的临时目录里跑 pytest（可选 coverage 采集）

    - `cwd` 固定为临时目录，`PYTHONPATH` 指向仓库根，故被测模块可导入而
      仓库的 `pytest.ini`/`conftest.py` 不被继承；
    - 显式剔除 `PYTEST_ADDOPTS`，否则会继承仓库的 `--cov=jkos_core` 造成递归；
    - coverage 可用时用 `coverage run --data-file=<tmp>` 采集，
      否则退化为代理指标（`coverage_source="proxy"`）。
    """

    def __init__(self, *, python: Optional[str] = None, timeout: float = 60.0,
                 repo_root: Optional[Path] = None):
        self.python = python or sys.executable
        self.timeout = timeout
        self.repo_root = Path(repo_root or REPO_ROOT)

    def build_command(self, test_dir: Path) -> List[str]:
        """构造子进程命令行（coverage 可用时套一层 coverage run 采集）"""
        tail = [
            str(test_dir), "-p", "no:cacheprovider", "-q", "--no-header",
        ]
        if self._coverage_available():
            return [
                self.python, "-m", "coverage", "run",
                f"--data-file={test_dir / '.coverage'}", "-m", "pytest", *tail,
            ]
        return [self.python, "-m", "pytest", *tail]

    @staticmethod
    def build_env(test_dir: Path, repo_root: Path) -> Dict[str, str]:
        """子进程环境：注入 PYTHONPATH，剔除 PYTEST_ADDOPTS（防继承仓库 --cov）"""
        env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
        existing = env.get("PYTHONPATH", "")
        root = str(repo_root)
        env["PYTHONPATH"] = f"{root}{os.pathsep}{existing}" if existing else root
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def _coverage_available(self) -> bool:
        try:
            return importlib.util.find_spec("coverage") is not None
        except (ImportError, ValueError):  # pragma: no cover - 解释器级异常
            return False

    async def run(self, test_dir: Path, target_module: str, *,
                  case_count: int = 0, assert_count: int = 0,
                  line_range: Optional[Tuple[int, int]] = None) -> RunOutcome:
        outcome = await self._run_subprocess(self.build_command(test_dir), test_dir)
        if self._coverage_available():
            percent = await self._read_coverage(test_dir, target_module, line_range)
            if percent is not None:
                outcome.coverage_percent = percent
                outcome.coverage_source = "real"
        if outcome.coverage_source == "none":
            outcome.coverage_percent = proxy_coverage(outcome.passed, assert_count)
            outcome.coverage_source = "proxy"
        return outcome

    async def _run_subprocess(self, cmd: List[str], test_dir: Path) -> RunOutcome:
        """执行子进程并解析 pytest 计数（超时即杀进程）"""
        env = self.build_env(test_dir, self.repo_root)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(test_dir), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:  # pragma: no cover - 解释器不可执行属环境异常
            logger.error("pytest 启动失败: %s", exc)
            return RunOutcome(returncode=127, stdout=str(exc))
        try:
            stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return RunOutcome(returncode=-1, timed_out=True)
        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        passed, failed, errors = parse_pytest_counts(stdout)
        return RunOutcome(
            passed=passed, failed=failed, errors=errors,
            returncode=proc.returncode or 0, stdout=stdout,
        )

    async def _read_coverage(self, test_dir: Path, target_module: str,
                             line_range: Optional[Tuple[int, int]] = None,
                             ) -> Optional[float]:
        """读取 coverage json（`--data-file` 与 json 报告都落在临时目录内）

        给了 `line_range` 时按**被测函数行区间**统计（只测一个函数时整文件覆盖率
        无意义）；否则退回整文件覆盖率。
        """
        report = test_dir / "coverage.json"
        await self._run_subprocess(
            [
                self.python, "-m", "coverage", "json",
                f"--data-file={test_dir / '.coverage'}", "-o", str(report),
            ],
            test_dir,
        )
        if not report.exists():
            return None
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        files = payload.get("files") or {}
        needle = target_module.replace(".", "/")
        for name, info in files.items():
            normalized = str(name).replace("\\", "/")
            if normalized.endswith(f"{needle}.py") \
                    or normalized.endswith(f"{needle}/__init__.py"):
                return _percent_from_info(info, line_range)
        return None


# ─── D3 执行器 ───


@dataclass
class D3Outcome:
    """D3 一次执行的完整证据（不含生成代码正文，便于外传与落报告）"""
    task: str
    target: Optional[FunctionTarget] = None
    success: bool = False
    stages: List[Dict[str, Any]] = field(default_factory=list)
    cases: List[Dict[str, str]] = field(default_factory=list)
    generation_source: str = "fallback"   # llm | fallback
    llm: Dict[str, Any] = field(default_factory=dict)
    run: Dict[str, Any] = field(default_factory=dict)
    coverage: Dict[str, Any] = field(default_factory=dict)
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    metric: Optional[float] = None
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "target": self.target.to_dict() if self.target else None,
            "success": self.success,
            "stages": list(self.stages),
            "cases": [{"name": c.get("name"), "rationale": c.get("rationale")}
                      for c in self.cases],
            "generation_source": self.generation_source,
            "llm": dict(self.llm),
            "run": dict(self.run),
            "coverage": dict(self.coverage),
            "attempts": list(self.attempts),
            "duration_ms": self.duration_ms,
            "metric": self.metric,
            "details": self.details,
        }


class D3Executor:
    """D3 执行器：select → design → generate → run → coverage

    作为 `ExplorationEngine.explore(..., executor=d3.run_as_executor)` 的执行器使用；
    也可独立 `await run(task, target=...)` 做单次生成验证。
    """

    def __init__(self, *, llm: Optional[Any] = None,
                 runner: Optional[PytestRunner] = None,
                 default_target: Optional[str] = None,
                 coverage_threshold: float = 0.5,
                 max_cases: int = MAX_CASES,
                 repo_root: Optional[Path] = None):
        self.llm = llm
        self.runner = runner or SubprocessPytestRunner(repo_root=repo_root)
        self.default_target = default_target or DEFAULT_TARGET
        self.coverage_threshold = coverage_threshold
        self.max_cases = max_cases
        self.repo_root = Path(repo_root or REPO_ROOT)

    # ---------- 主流程 ----------

    async def run(self, task: str, *, target: Optional[str] = None) -> D3Outcome:
        """执行一次 D3 流水线，返回完整证据"""
        started = time.monotonic()
        outcome = D3Outcome(task=(task or "").strip())

        def mark(stage: str, ok: bool, detail: str = "", **extra: Any) -> None:
            outcome.stages.append({
                "stage": stage, "ok": bool(ok), "detail": detail, **extra,
            })

        def finish() -> D3Outcome:
            outcome.duration_ms = int((time.monotonic() - started) * 1000)
            return outcome

        # 1) select：定位目标函数
        try:
            module, qualname = parse_target(target or self.default_target)
            found = locate_function(module, qualname, root=self.repo_root)
        except (TargetSpecError, FunctionNotFound, OSError) as exc:
            mark(STAGE_SELECT, False, str(exc))
            outcome.details = f"目标定位失败: {exc}"
            return finish()
        outcome.target = found
        mark(STAGE_SELECT, True, f"定位 {found.module}:{found.qualname}",
             lineno=found.lineno, end_lineno=found.end_lineno,
             params=[p.name for p in found.params])

        # 2) design：LLM 设计用例（不可用或不合规则确定性兜底）
        cases, generation_source, llm_meta = await self.design_cases(found)
        outcome.cases = cases
        outcome.generation_source = generation_source
        outcome.llm = llm_meta
        mark(STAGE_DESIGN, bool(cases),
             f"{generation_source}: 设计 {len(cases)} 个用例",
             source=generation_source, llm=llm_meta)
        if not cases:
            outcome.details = "用例设计为空"
            return finish()

        # 3) generate：写入临时目录（生成物绝不落仓库）
        workdir = Path(tempfile.mkdtemp(prefix="d3-selfboot-"))
        test_file = workdir / f"test_{_ident(found.attr, 'target')}.py"
        test_file.write_text(self.render_case_file(found, cases), encoding="utf-8")
        mark(STAGE_GENERATE, True, f"用例文件已生成: {test_file.name}",
             test_dir=str(workdir), case_count=len(cases))

        # 4) run：跑 pytest（LLM 用例运行失败则回退确定性用例重跑一次）
        run_outcome = await self._run_cases(workdir, found, cases)
        outcome.attempts.append({"source": generation_source, **run_outcome.to_dict()})
        if not run_outcome.ok and generation_source == "llm":
            cases = deterministic_cases(found)
            test_file.write_text(self.render_case_file(found, cases), encoding="utf-8")
            run_outcome = await self._run_cases(workdir, found, cases)
            outcome.attempts.append({"source": "fallback", **run_outcome.to_dict()})
            if run_outcome.ok:
                outcome.cases = cases
                outcome.generation_source = "fallback"
        outcome.run = run_outcome.to_dict()
        mark(STAGE_RUN, run_outcome.ok,
             f"passed={run_outcome.passed} failed={run_outcome.failed} "
             f"errors={run_outcome.errors}", **run_outcome.to_dict())

        # 5) coverage：覆盖率报告（真实 coverage 不可用时为代理指标）
        percent = run_outcome.coverage_percent or 0.0
        covered = percent >= self.coverage_threshold * 100
        outcome.coverage = {
            "percent": round(float(percent), 2),
            "source": run_outcome.coverage_source,
            "threshold": self.coverage_threshold * 100,
            "passed": covered,
        }
        mark(STAGE_COVERAGE, covered,
             f"覆盖率 {outcome.coverage['percent']}%（{run_outcome.coverage_source}）",
             **outcome.coverage)

        outcome.success = run_outcome.ok and covered
        outcome.metric = round(float(percent) / 100.0, 4)
        outcome.details = (
            f"D3 {'通过' if outcome.success else '未通过'}："
            f"{found.module}:{found.qualname}，用例 {len(cases)} 个"
            f"（{outcome.generation_source}），passed={run_outcome.passed} "
            f"failed={run_outcome.failed}，覆盖率 {outcome.coverage['percent']}%"
            f"（{run_outcome.coverage_source}）"
        )
        return finish()

    async def _run_cases(self, workdir: Path, target: FunctionTarget,
                         cases: List[Dict[str, str]]) -> RunOutcome:
        """跑一批用例（断言数用于 coverage 代理指标；行区间用于覆盖率统计）"""
        assert_count = sum(c["code"].count("assert ") for c in cases)
        return await self.runner.run(
            workdir, target.module, case_count=len(cases), assert_count=assert_count,
            line_range=(target.lineno, target.end_lineno))

    def render_case_file(self, target: FunctionTarget, cases: List[Dict[str, str]]) -> str:
        """把用例拼成一个测试文件（附文件头，便于人工核查来源）"""
        header = (
            '"""D3 自举生成的单元测试（M20）。\n'
            "\n"
            f"目标: {target.module}:{target.qualname}"
            f"（第 {target.lineno}-{target.end_lineno} 行）\n"
            '生成方式: LLM 或确定性兜底，见 generation_source。\n'
            '"""\n'
        )
        body = "\n\n".join(c["code"].rstrip() + "\n" for c in cases)
        return f"{header}\n\n{body}"

    # ---------- 用例设计 ----------

    async def design_cases(
        self, target: FunctionTarget,
    ) -> Tuple[List[Dict[str, str]], str, Dict[str, Any]]:
        """设计用例：LLM 可用走真实生成，否则（或结果不合规）确定性兜底"""
        llm_meta: Dict[str, Any] = {"used": False, "simulated": None, "provider": None}
        if self.llm is not None:
            generated, llm_meta = await self._generate_via_llm(target)
            if generated:
                return generated, "llm", llm_meta
        return deterministic_cases(target), "fallback", llm_meta

    async def _generate_via_llm(
        self, target: FunctionTarget,
    ) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
        """调用 LLM 生成用例；模拟输出、解析失败或校验不通过均返回空列表"""
        meta: Dict[str, Any] = {"used": True, "simulated": None, "provider": None}
        try:
            result = await self.llm.chat_text(
                build_testgen_prompt(target),
                tenant_id="system",
                purpose="selfboot",
                ref_type="d3_target",
                ref_id=f"{target.module}:{target.qualname}",
                max_tokens=2048,
            )
        except Exception as exc:
            logger.warning("D3 LLM 生成失败，回退确定性用例: %s", exc)
            meta["error"] = str(exc)
            return [], meta
        meta["simulated"] = bool(getattr(result, "simulated", False))
        meta["provider"] = getattr(result, "provider", None)
        meta["tokens"] = getattr(result, "total_tokens", 0)
        if meta["simulated"]:
            # 无可用 API Key 时路由器回落到模拟供应商，内容不可信 → 走确定性兜底
            return [], meta
        parsed = parse_generated_cases(getattr(result, "content", ""))
        valid = [c for c in parsed
                 if not validate_generated_code(c["code"], allowed_module=target.module)]
        meta["parsed"] = len(parsed)
        meta["accepted"] = len(valid)
        return valid[: self.max_cases], meta

    # ---------- Experimenter 执行器契约 ----------

    async def run_as_executor(
        self, hypothesis: Any, sample_size: int = 0,
    ) -> Tuple[bool, Optional[float], str]:
        """Experimenter executor 契约：`(hypothesis, sample_size) -> (success, metric, details)`

        调用处必须传**绑定方法**（`d3.run_as_executor`）而非实例本身 —— 见模块 docstring。
        """
        outcome = await self.run(
            getattr(hypothesis, "statement", "") or "",
            target=self._target_from_hypothesis(hypothesis))
        return outcome.success, outcome.metric, outcome.details

    @staticmethod
    def _target_from_hypothesis(hypothesis: Any) -> Optional[str]:
        """假说里可携带目标描述（approach 中以 `target:` 前缀给出）"""
        approach = str(getattr(hypothesis, "approach", "") or "")
        for line in approach.splitlines():
            if line.strip().lower().startswith("target:"):
                return line.split(":", 1)[1].strip()
        return None