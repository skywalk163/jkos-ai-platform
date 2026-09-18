"""M12 自动化优化引擎 - 测试公共 fixtures。

所有组件均指向 tmp_path 下的独立 SQLite 数据库，
不触碰仓库内的真实存储，测试之间互不影响。
"""

import pytest

from dsh_core.optimization import OptimizationEngine
from dsh_core.optimization.automation_engine import AutomationEngine
from dsh_core.optimization.process_solidifier import ProcessSolidifier
from dsh_core.optimization.template_manager import TemplateManager
from dsh_core.optimization.token_optimizer import TokenOptimizer


@pytest.fixture
def solidifier(tmp_path):
    return ProcessSolidifier(db_path=str(tmp_path / "solidifier.db"))


@pytest.fixture
def optimizer(tmp_path):
    return TokenOptimizer(db_path=str(tmp_path / "optimizer.db"))


@pytest.fixture
def templates(tmp_path):
    return TemplateManager(db_path=str(tmp_path / "templates.db"))


@pytest.fixture
def automation(templates, optimizer):
    return AutomationEngine(templates=templates, optimizer=optimizer)


@pytest.fixture
def engine(solidifier, optimizer, templates, automation):
    """使用临时存储注入的完整门面（未启动，测试内自行 initialize/close）。"""
    return OptimizationEngine(
        solidifier=solidifier,
        optimizer=optimizer,
        templates=templates,
        automation=automation,
    )