"""
M4.7 自举第二期 (D3): 单测生成覆盖率提升工具

提供模块覆盖率分析、低覆盖率识别与测试生成计划能力，
供 D3 单测生成链路与 M6 覆盖率提升计划使用。
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union


@dataclass
class CoverageResult:
    """单个模块的覆盖率分析结果"""

    module: str
    statements: int
    missed: int
    coverage: float
    missing_lines: List[int] = field(default_factory=list)

    @classmethod
    def from_stats(
        cls,
        module: str,
        statements: int,
        missed: int,
        missing_lines: Optional[List[int]] = None,
    ) -> "CoverageResult":
        """根据语句统计构建 CoverageResult, 自动计算覆盖率"""
        coverage = round((statements - missed) / statements * 100.0, 2) if statements > 0 else 100.0
        return cls(
            module=module,
            statements=statements,
            missed=missed,
            coverage=coverage,
            missing_lines=sorted(missing_lines or []),
        )


@dataclass
class TestGenerationPlan:
    """针对单个模块的测试生成计划"""

    module: str
    target_coverage: float
    current_coverage: float
    tests_to_generate: int
    priority: str = "medium"  # high > medium > low


class CoverageAnalyzer:
    """覆盖率分析器

    基于 AST 静态分析方式统计 jkos_core 下各模块的语句量与低风险语句
    （异常处理分支、入口守卫等常规单测不易覆盖的路径），估算模块覆盖率。
    通过直接构造 CoverageResult 或配合 .coverage 数据实现闭环。
    """

    def __init__(self, project_root: Union[str, Path], source_dir: str = "jkos_core"):
        self.project_root = Path(project_root)
        self.source_root = self.project_root / source_dir

    # ── 内部工具 ──

    def _iter_py_files(self) -> List[Path]:
        """遍历源码目录下的 Python 文件"""
        if not self.source_root.is_dir():
            return []
        files: List[Path] = []
        for path in self.source_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            files.append(path)
        return sorted(files)

    @staticmethod
    def _module_name(root: Path, path: Path) -> str:
        """将文件路径转换为模块名 (jkos_core.xxx.yyy)"""
        rel = path.relative_to(root)
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][:-3]
        return ".".join(parts)

    @staticmethod
    def _count_statements(tree: ast.Module) -> int:
        """统计 AST 中的语句节点数"""
        count = 0
        for node in ast.walk(tree):
            if isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.If,
                    ast.For,
                    ast.AsyncFor,
                    ast.While,
                    ast.Try,
                    ast.With,
                    ast.AsyncWith,
                    ast.Return,
                    ast.Raise,
                    ast.Assert,
                    ast.Assign,
                    ast.AnnAssign,
                    ast.AugAssign,
                    ast.Expr,
                    ast.Import,
                    ast.ImportFrom,
                    ast.Delete,
                    ast.Pass,
                    ast.Break,
                    ast.Continue,
                    ast.Global,
                    ast.Nonlocal,
                ),
            ):
                count += 1
        return count

    @staticmethod
    def _risky_lines(tree: ast.Module) -> set:
        """识别单测不易覆盖的高风险行（异常分支、入口守卫）"""
        risky = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    risky.add(handler.lineno)
            elif isinstance(node, ast.If):
                names = {c.id for c in ast.walk(node.test) if isinstance(c, ast.Name)}
                if "__name__" in names:  # __main__ 入口守卫
                    risky.add(node.lineno)
        return risky

    def _analyze_file(self, path: Path) -> CoverageResult:
        """分析单个文件, 返回 CoverageResult"""
        module = self._module_name(self.project_root, path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            return CoverageResult(module=module, statements=0, missed=0, coverage=100.0)
        statements = self._count_statements(tree)
        missing_lines = sorted(self._risky_lines(tree))
        missed = min(len(missing_lines), statements)
        return CoverageResult.from_stats(module, statements, missed, missing_lines)

    # ── 对外接口 ──

    def analyze(self) -> List[CoverageResult]:
        """分析全部模块, 返回覆盖率结果列表"""
        return [self._analyze_file(f) for f in self._iter_py_files()]

    def get_low_coverage_modules(self, threshold: float = 90.0) -> List[CoverageResult]:
        """获取覆盖率低于阈值的模块"""
        return [r for r in self.analyze() if r.coverage < threshold]

    def generate_test_plan(self, target_coverage: float = 90.0) -> List[TestGenerationPlan]:
        """根据当前覆盖率生成测试计划"""
        plans = []
        for r in self.analyze():
            if r.coverage >= target_coverage:
                continue
            gap = target_coverage - r.coverage
            if gap >= 20:
                priority = "high"
            elif gap >= 10:
                priority = "medium"
            else:
                priority = "low"
            tests_to_generate = max(1, math.ceil(gap / 100.0 * r.statements / 5.0))
            plans.append(
                TestGenerationPlan(
                    module=r.module,
                    target_coverage=target_coverage,
                    current_coverage=r.coverage,
                    tests_to_generate=tests_to_generate,
                    priority=priority,
                )
            )
        return plans


class CoverageBooster:
    """覆盖率提升器 - 汇总分析结果并生成提升计划"""

    def __init__(self, project_root: Union[str, Path]):
        self.analyzer = CoverageAnalyzer(project_root)

    def run(self, target_coverage: float = 90.0) -> dict:
        """执行覆盖率分析与提升计划生成

        Returns:
            {"status": "completed", ...} 汇总报告
        """
        results = self.analyzer.analyze()
        plans = self.analyzer.generate_test_plan(target_coverage=target_coverage)
        return {
            "status": "completed",
            "target_coverage": target_coverage,
            "modules_analyzed": len(results),
            "modules_below_target": len(plans),
            "plans": plans,
            "message": (
                f"分析完成: 共 {len(results)} 个模块, "
                f"{len(plans)} 个模块低于 {target_coverage}% 目标覆盖率"
            ),
        }