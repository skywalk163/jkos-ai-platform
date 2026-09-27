"""DSH 执行器 SPI 与注册表（M21 任务 21.1，executor SPI）

对外暴露：Executor、DispatchResult、ExecutorError、
ExecutorNotAuthorizedError、ExecutorRegistry、MockExecutor。
"""
from jkos_core.executors.base import (
    DispatchResult,
    Executor,
    ExecutorError,
    ExecutorNotAuthorizedError,
)
from jkos_core.executors.mock import MockExecutor
from jkos_core.executors.registry import ExecutorRegistry

__all__ = [
    "Executor",
    "DispatchResult",
    "ExecutorError",
    "ExecutorNotAuthorizedError",
    "ExecutorRegistry",
    "MockExecutor",
]