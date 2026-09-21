"""M20 D3 执行器 - 目标函数定位测试（ast，不 import 被测模块）"""
from __future__ import annotations

from pathlib import Path

import pytest

from jkos_core.selfboot import d3_testgen as mod
from jkos_core.selfboot.d3_testgen import (
    FunctionNotFound,
    TargetSpecError,
    locate_function,
    parse_target,
    resolve_module_path,
)


class TestParseTarget:
    """目标描述解析"""

    def test_parses_module_and_qualname(self):
        assert parse_target("a.b.c:func") == ("a.b.c", "func")

    def test_tolerates_surrounding_whitespace(self):
        assert parse_target("  a.b:func  ") == ("a.b", "func")

    def test_rejects_missing_colon(self):
        with pytest.raises(TargetSpecError):
            parse_target("a.b.func")

    def test_rejects_empty_parts(self):
        with pytest.raises(TargetSpecError):
            parse_target(":func")
        with pytest.raises(TargetSpecError):
            parse_target("a.b:")


class TestResolveModulePath:
    """模块名 → 仓库内文件路径（不 import）"""

    def test_resolves_dotted_module(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "m.py").write_text("x = 1\n", encoding="utf-8")
        assert resolve_module_path("pkg.m", root=tmp_path) == (tmp_path / "pkg" / "m.py")

    def test_falls_back_to_package_init(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
        assert resolve_module_path("pkg", root=tmp_path) == tmp_path / "pkg" / "__init__.py"

    def test_rejects_path_escape(self, tmp_path):
        outside = tmp_path.parent / "outside_mod.py"
        outside.write_text("x = 1\n", encoding="utf-8")
        with pytest.raises(TargetSpecError):
            resolve_module_path("../outside_mod.py", root=tmp_path)

    def test_rejects_missing_file(self, tmp_path):
        with pytest.raises(TargetSpecError):
            resolve_module_path("nope.missing", root=tmp_path)


class TestLocateFunction:
    """函数定位：模块级 / 异步 / 方法 / 嵌套"""

    def test_locates_module_level_function(self, tmp_path, sample_module):
        target = locate_function(sample_module, "add", root=tmp_path)
        assert target.module == sample_module
        assert target.qualname == "add"
        assert target.is_method is False
        assert target.is_async is False
        assert target.has_docstring is True
        assert [p.name for p in target.params] == ["a", "b"]
        assert [p.annotation for p in target.params] == ["int", "int"]
        assert "def add(a: int, b: int) -> int:" in target.source
        assert target.lineno < target.end_lineno
        assert target.import_stmt() == f"from {sample_module} import add"

    def test_locates_async_function(self, tmp_path, sample_module):
        target = locate_function(sample_module, "afetch", root=tmp_path)
        assert target.is_async is True
        assert [p.name for p in target.params] == ["name"]

    def test_locates_class_method_and_skips_self(self, tmp_path, sample_module):
        target = locate_function(sample_module, "Calc.mul", root=tmp_path)
        assert target.is_method is True
        assert target.owner == "Calc"
        assert target.attr == "mul"
        assert [p.name for p in target.params] == ["a", "b"]  # self 被剔除
        assert target.import_stmt() == f"from {sample_module} import Calc"

    def test_locates_nested_function(self, tmp_path, sample_module):
        target = locate_function(sample_module, "Calc.outer.inner", root=tmp_path)
        assert target.qualname == "Calc.outer.inner"
        assert "def inner(x: int)" in target.source

    def test_collects_keyword_only_params(self, tmp_path, sample_module):
        target = locate_function(sample_module, "kwonly", root=tmp_path)
        assert [p.name for p in target.params] == ["a", "flag", "name"]
        # 无注解形参的注解文本为空串
        assert [p.annotation for p in target.params] == ["int", "", "str"]

    def test_raises_for_unknown_name(self, tmp_path, sample_module):
        with pytest.raises(FunctionNotFound) as exc:
            locate_function(sample_module, "no_such_func", root=tmp_path)
        assert "no_such_func" in str(exc.value)

    def test_raises_when_target_is_class(self, tmp_path, sample_module):
        with pytest.raises(FunctionNotFound):
            locate_function(sample_module, "Calc", root=tmp_path)

    def test_raises_for_missing_module_file(self, tmp_path):
        with pytest.raises(TargetSpecError):
            locate_function("absent_mod", "func", root=tmp_path)

    def test_uses_injected_source_path(self, tmp_path):
        path = tmp_path / "elsewhere.py"
        path.write_text("def f(x: str):\n    return x\n", encoding="utf-8")
        target = locate_function("virtual.module", "f", source_path=path)
        assert target.source.startswith("def f(x: str):")
        assert target.import_stmt() == "from virtual.module import f"

    def test_to_dict_roundtrip(self, tmp_path, sample_module):
        payload = locate_function(sample_module, "label", root=tmp_path).to_dict()
        assert payload["qualname"] == "label"
        assert payload["params"] == [
            {"name": "text", "annotation": "str"},
        ]
        assert payload["has_docstring"] is True
        assert payload["is_async"] is False


class TestPipelineStages:
    """流水线阶段常量的完备性"""

    def test_five_stages_in_order(self):
        assert mod.D3_STAGES == ("select", "design", "generate", "run", "coverage")
        assert mod.pipeline_steps() == [
            mod.STAGE_LABELS[s] for s in mod.D3_STAGES
        ]

    def test_labels_are_terse(self):
        # 阶段文案会被固化为流程步骤，模板复现成本按文本长度计 → 必须简短
        assert all(len(text) <= 30 for text in mod.STAGE_LABELS.values())

    def test_default_target_is_inside_repo(self):
        module, qualname = parse_target(mod.DEFAULT_TARGET)
        assert resolve_module_path(module) == (
            Path(mod.REPO_ROOT) / "jkos_core" / "exploration" / "knowledge_base.py"
        )
        assert qualname == "keyword_score"