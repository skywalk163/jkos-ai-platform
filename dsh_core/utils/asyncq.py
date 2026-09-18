"""DSH 工具集 - 异步任务队列（M7 任务 7.3）

异步任务队列优化：
  - 统一提交/消费异步任务，支持并发度（workers）控制；
  - 有界队列实现背压：submit() 队列满时等待，try_submit() 非阻塞；
  - 失败隔离：单个任务异常不影响其他任务与 worker；
  - 运行指标：stats() 返回队列长度 / 已处理 / 失败 / 并发度。

用法：
    q = AsyncTaskQueue(workers=4, queue_size=1000)
    await q.start()
    await q.submit(my_coro_func)
    await q.wait_all()
    await q.stop()
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger("dsh.utils")


class AsyncTaskQueue:
    """有界异步任务队列（M7 任务 7.3 验收：异步、背压、指标）"""

    Task = Callable[[], Awaitable[Any]]
    # 停止哨兵：worker 收到后退出
    _SENTINEL: Optional[Task] = None

    def __init__(self, workers: int = 4, queue_size: int = 1000):
        if workers < 1:
            raise ValueError("workers 至少为 1")
        self.workers = workers
        self.queue_size = queue_size
        self._queue: "asyncio.Queue[Optional[AsyncTaskQueue.Task]]" = asyncio.Queue(maxsize=queue_size)
        self._worker_tasks: "set[asyncio.Task]" = set()
        self._stopped = False
        self._processed = 0
        self._failed = 0

    async def start(self) -> None:
        """启动 workers（幂等）"""
        if self._worker_tasks:
            return
        self._stopped = False
        for i in range(self.workers):
            task = asyncio.create_task(self._worker(f"worker-{i}"))
            self._worker_tasks.add(task)

    async def _worker(self, name: str) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is None:  # 停止哨兵
                    break
                try:
                    await item()
                    self._processed += 1
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # 失败隔离：只计数，不中断 worker
                    self._failed += 1
                    logger.exception("异步任务 %s 执行失败: %s", name, e)
            finally:
                self._queue.task_done()

    async def submit(self, task: Task) -> None:
        """提交任务；队列满则等待（背压）"""
        if self._stopped:
            raise RuntimeError("任务队列已停止，禁止提交")
        await self._queue.put(task)

    def try_submit(self, task: Task) -> bool:
        """非阻塞提交；队列满返回 False"""
        if self._stopped:
            return False
        try:
            self._queue.put_nowait(task)
            return True
        except asyncio.QueueFull:
            return False

    async def wait_all(self) -> None:
        """等待队列中全部任务处理完成（含在途）"""
        await self._queue.join()

    async def stop(self, *, drain: bool = True) -> None:
        """停止队列：drain=True 先处理完已提交任务，否则丢弃剩余任务"""
        self._stopped = True
        if not drain:
            while True:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except asyncio.QueueEmpty:
                    break
        if not self._worker_tasks:
            return
        for _ in self._worker_tasks:  # 每 worker 一个哨兵
            await self._queue.put(None)
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()

    def stats(self) -> Dict[str, Any]:
        return {
            "pending": self._queue.qsize(),
            "processed": self._processed,
            "failed": self._failed,
            "workers": self.workers,
            "maxsize": self.queue_size,
            "stopped": self._stopped,
        }