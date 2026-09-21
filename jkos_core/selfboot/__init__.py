"""极快AI操作系统 - 自举层（M20）

**JKOS 管理 JKOS**：用 M17 的自动化优化引擎驱动 JKOS 自身的探索/复盘/优化流程。

本包是**编排层**（消费 `jkos_core.exploration` 与 `jkos_core.optimization`），
与 `jkos_core/bootstrap.py`（组件装配）职责不同，故独立成包。

- `d3_testgen`：D3 单元测试生成执行器（探索 → 用例设计 → 生成 → 运行 → 覆盖率）
- `loop`：四段闭环编排（探索 → 固化 → 模板化 → 自动化执行）
"""
from __future__ import annotations

from jkos_core.selfboot.d3_testgen import (
    DEFAULT_TARGET,
    D3_STAGES,
    D3Executor,
    D3Outcome,
    FunctionNotFound,
    FunctionTarget,
    PytestRunner,
    RunOutcome,
    SubprocessPytestRunner,
    TargetSpecError,
    deterministic_cases,
    locate_function,
    parse_target,
    pipeline_steps,
    validate_generated_code,
)

__all__ = [
    "DEFAULT_TARGET",
    "D3_STAGES",
    "D3Executor",
    "D3Outcome",
    "FunctionNotFound",
    "FunctionTarget",
    "PytestRunner",
    "RunOutcome",
    "SubprocessPytestRunner",
    "TargetSpecError",
    "deterministic_cases",
    "locate_function",
    "parse_target",
    "pipeline_steps",
    "validate_generated_code",
]