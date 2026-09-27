"""DSH 执行器注册表（M21 任务 21.1，executor SPI）

name → Executor 实例，带租户授权白名单：
  - register(name, executor, tenant_codes=None)：None/空=全部租户可用；
  - get / dispatch 校验租户授权，超权抛 ExecutorNotAuthorizedError。

引擎经注册表访问执行器（comps.executors 注入），保持引擎与具体执行器解耦。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from jkos_core.executors.base import (
    DispatchResult,
    Executor,
    ExecutorError,
    ExecutorNotAuthorizedError,
)


class ExecutorRegistry:
    """执行器注册表（租户白名单授权）"""

    def __init__(self) -> None:
        self._executors: Dict[str, Executor] = {}
        self._tenant_allow: Dict[str, List[str]] = {}

    def register(self, name: str, executor: Executor,
                 tenant_codes: Optional[List[str]] = None) -> None:
        executor.name = name
        self._executors[name] = executor
        self._tenant_allow[name] = tenant_codes or []

    def unregister(self, name: str) -> None:
        self._executors.pop(name, None)
        self._tenant_allow.pop(name, None)

    def names(self) -> List[str]:
        return list(self._executors)

    def get(self, name: str, tenant_code: str) -> Executor:
        executor = self._executors.get(name)
        if executor is None:
            raise ExecutorError(f"未注册执行器: {name}")
        allow = self._tenant_allow.get(name, [])
        if allow and tenant_code not in allow:
            raise ExecutorNotAuthorizedError(
                f"租户 {tenant_code} 无权使用执行器 {name}（白名单: {allow}）"
            )
        return executor

    async def dispatch(
        self, name: str, tenant_code: str, *,
        instance_id: str, step_id: str, node_code: str,
        payload: Dict[str, Any], context: Optional[Dict[str, Any]] = None,
    ) -> DispatchResult:
        executor = self.get(name, tenant_code)
        return await executor.dispatch(
            instance_id, step_id, node_code, payload, context=context,
        )