"""M7 任务 7.3：异步优化 — AsyncTaskQueue"""

import asyncio

import pytest

from jkos_core.utils.asyncq import AsyncTaskQueue


async def test_submit_and_wait_all():
    """submit：worker 并发执行任务，wait_all 等待全部完成"""
    q = AsyncTaskQueue(workers=2)
    await q.start()
    done = []

    async def task(x):
        await asyncio.sleep(0.01)
        done.append(x)

    for i in range(6):
        await q.submit(lambda i=i: task(i))
    await q.wait_all()
    assert sorted(done) == list(range(6))
    s = q.stats()
    assert s["processed"] == 6
    assert s["failed"] == 0
    await q.stop()


async def test_failure_isolation():
    """失败隔离：单任务异常仅计数，不影响其他任务"""
    q = AsyncTaskQueue(workers=2)
    await q.start()
    ok = []

    async def good(x):
        await asyncio.sleep(0.005)
        ok.append(x)

    async def bad():
        raise RuntimeError("boom")

    await q.submit(bad)
    for i in range(4):
        await q.submit(lambda i=i: good(i))
    await q.wait_all()
    assert sorted(ok) == [0, 1, 2, 3]
    s = q.stats()
    assert s["failed"] == 1
    assert s["processed"] == 4
    await q.stop()


async def test_try_submit_when_queue_full():
    """背压：队列满时 try_submit 返回 False 不抛异常"""
    q = AsyncTaskQueue(workers=1, queue_size=2)
    await q.start()
    gate = asyncio.Event()

    async def blocked():
        await gate.wait()

    await q.submit(blocked)   # worker 取走并阻塞在 gate
    await q.submit(blocked)   # 排队 1
    await q.submit(blocked)   # 排队 2（满）
    assert q.try_submit(blocked) is False
    gate.set()
    await q.wait_all()
    await q.stop()


async def test_submit_after_stop_raises():
    """停止后 submit 抛 RuntimeError、try_submit 返回 False"""
    q = AsyncTaskQueue(workers=1)
    await q.start()
    await q.stop()

    async def task():
        return None

    with pytest.raises(RuntimeError):
        await q.submit(task)
    assert q.try_submit(task) is False


async def test_stop_with_drain_runs_all():
    """stop(drain=True)：处理完已提交任务"""
    q = AsyncTaskQueue(workers=2)
    await q.start()
    done = []

    async def task(x):
        await asyncio.sleep(0.005)
        done.append(x)

    for i in range(5):
        await q.submit(lambda i=i: task(i))
    await q.stop(drain=True)
    assert sorted(done) == [0, 1, 2, 3, 4]
    assert q.stats()["stopped"] is True


async def test_stop_without_drain_discards_pending():
    """stop(drain=False)：执行中任务自然结束，排队任务被丢弃"""
    q = AsyncTaskQueue(workers=1)
    await q.start()
    done = []

    async def medium():
        await asyncio.sleep(0.05)
        done.append("m")

    async def pending():
        done.append("p")

    await q.submit(medium)      # worker 正在执行
    await q.submit(pending)     # 排队中
    await asyncio.sleep(0.01)   # 确保 medium 已被 worker 取走
    await q.stop(drain=False)
    assert "m" in done
    assert "p" not in done
    assert q.stats()["pending"] == 0


async def test_stop_idle_closes_cleanly():
    """stop：空闲队列可干净退出且可重启（start 幂等重置停止标记）"""
    q = AsyncTaskQueue(workers=2)
    await q.start()
    await q.stop()
    assert q.stats()["stopped"] is True
    await q.start()
    assert q.stats()["stopped"] is False
    await q.submit(lambda: asyncio.sleep(0))
    await q.stop()


async def test_stats_fields():
    """stats：M7 7.3 验收指标字段"""
    q = AsyncTaskQueue(workers=1)
    assert set(q.stats()) == {"pending", "processed", "failed",
                              "workers", "maxsize", "stopped"}
    await q.stop()


def test_invalid_workers_raises():
    """workers 非法 → ValueError"""
    with pytest.raises(ValueError):
        AsyncTaskQueue(workers=0)