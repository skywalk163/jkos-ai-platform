"""DSH 内存 Mock 执行器（M21 任务 21.1，单测/演示用，无需真实后端）

覆盖 executor SPI 各路径：
  - 正常受理：记录派发（dispatches），返回自增 task_id；
  - 派发失败：fail=True 抛 ExecutorError（验证引擎失败路径）；
  - status / cancel 查询。

真实 dsh / A2A / 异构 agent 后端后续以适配器实现 Executor SPI（M21-b/c/d）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from jkos_core.executors.base import DispatchResult, Executor, ExecutorError


class MockExecutor(Executor):
    """内存 Mock 执行器"""

    name = "mock"

    def __init__(self, fail: bool = False, dispatch_status: str = "ACCEPTED") -> None:
        self.fail = fail
        self.dispatch_status = dispatch_status
        self.dispatches: list = []

    async def dispatch(self, instance_id, step_id, node_code, payload, context=None):
        if self.fail:
            raise ExecutorError(f"mock 派发失败: {node_code}")
        task_id = f"mock-{len(self.dispatches) + 1}-{node_code}"
        self.dispatches.append({
            "task_id": task_id,
            "instance_id": instance_id,
            "step_id": step_id,
            "node_code": node_code,
            "payload": payload,
            "context": context,
        })
        return DispatchResult(task_id=task_id, executor=self.name,
                              status=self.dispatch_status)

    async def cancel(self, task_id: str) -> None:
        return None

    async def status(self, task_id: str) -> Dict[str, Any]:
        return {"task_id": task_id, "executor": self.name, "status": "SUCCEEDED"}