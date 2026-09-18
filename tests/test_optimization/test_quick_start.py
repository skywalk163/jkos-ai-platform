"""M12 验收：快速验证示例 —— 首次 token_used = 15000，后续 <= 1000。"""

import pytest


@pytest.mark.asyncio
async def test_first_exploration_is_15000_then_cache_under_1000(engine):
    """全新任务首次全量探索计费 15000，再次执行命中缓存且低成本。"""
    await engine.initialize()
    try:
        task = "fjlz-m12-accept-20240612 探索一个全新的业务场景：为县域电商设计冷链履约方案"
        r1 = await engine.execute(task)
        assert r1.success is True
        assert r1.source in ("exploration", "template")
        assert r1.token_used == 15000

        r2 = await engine.execute(task)
        assert r2.success is True
        assert r2.source == "cache"
        assert r2.token_used <= 1000
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_same_task_twice_is_cached(engine):
    """同一任务连续两次执行，第二次必须走缓存路径。"""
    await engine.initialize()
    try:
        task = "fjlz-m12-fast-20240612 快速检查：低代码平台构建后台管理页面的步骤"
        r1 = await engine.execute(task)
        r2 = await engine.execute(task)
        assert r1.source in ("exploration", "template")
        assert r2.source == "cache"
        assert r1.token_used >= r2.token_used
    finally:
        await engine.close()