"""M17 补充测试：TokenOptimizer（12.2）——估算、缓存、增量、批量与用量审计。"""

import json

import pytest

from jkos_core.optimization.token_optimizer import (
    PIPELINE_BASE,
    REPLAY_COST,
    TokenOptimizer,
    _replay_cost,
    estimate_full,
    estimate_tokens,
)

TASK = "fjlz-m12-token-季度复盘"


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("你好") == 2
    assert estimate_tokens("hello world") == 2
    assert estimate_tokens("中英 mixed abc") == 4


def test_estimate_full():
    assert estimate_full("生成周报") == PIPELINE_BASE + estimate_tokens("生成周报")


def test_replay_cost_caps_at_10_percent():
    assert _replay_cost(3000) == REPLAY_COST
    assert _replay_cost(1000) == REPLAY_COST
    assert _replay_cost(500) == 50
    assert _replay_cost(5) == 1


@pytest.mark.asyncio
async def test_optimize_miss_then_hit(optimizer):
    """首访全量计费并写缓存；命中后按回放成本计费，节省 ≥90%。"""
    full = estimate_full(TASK)
    opt1 = await optimizer.optimize(TASK, content="周报正文内容")
    assert opt1.status == "planned"
    assert opt1.cached is False
    assert opt1.token_used == full
    assert opt1.tokens_saved == 0
    assert opt1.meta["full_tokens"] == full
    assert optimizer.count_cache() == 1

    opt2 = await optimizer.optimize(TASK)
    assert opt2.status == "cached"
    assert opt2.cached is True
    assert opt2.token_used == _replay_cost(full)
    assert opt2.tokens_saved == full - opt2.token_used
    assert opt2.saved_ratio >= 0.9
    assert opt2.cached_content == "周报正文内容"
    assert opt2.meta["hit_count"] == 1
    assert opt2.meta["full_tokens"] == full
    assert opt2.meta["incremental"] is False

    opt3 = await optimizer.optimize(TASK)
    assert opt3.meta["hit_count"] == 2


@pytest.mark.asyncio
async def test_optimize_miss_without_content_does_not_cache(optimizer):
    opt1 = await optimizer.optimize(TASK)
    assert opt1.status == "planned"
    assert optimizer.count_cache() == 0
    # 无缓存：再次执行仍按全量计费
    opt2 = await optimizer.optimize(TASK, content="内容")
    assert opt2.status == "planned"
    opt3 = await optimizer.optimize(TASK)
    assert opt3.cached is True


@pytest.mark.asyncio
async def test_optimize_incremental(optimizer):
    base = "基础内容"
    extra = "基础内容扩展新数据"
    await optimizer.store_cache(TASK, base, full_tokens=2000)
    opt = await optimizer.optimize(TASK, content=extra, incremental=True)
    delta = estimate_tokens(extra) - estimate_tokens(base)
    assert opt.cached is True
    assert opt.token_used == _replay_cost(2000) + max(0, delta)
    assert opt.cached_content == extra
    assert opt.meta["incremental"] is True


@pytest.mark.asyncio
async def test_optimize_incremental_same_content_no_delta(optimizer):
    await optimizer.store_cache(TASK, "基础内容")
    opt = await optimizer.optimize(TASK, content="基础内容", incremental=True)
    assert opt.cached is True
    assert opt.token_used == _replay_cost(estimate_full(TASK))
    assert opt.meta["incremental"] is True


@pytest.mark.asyncio
async def test_optimize_hit_with_extra_content_keeps_original(optimizer):
    await optimizer.store_cache(TASK, "缓存内容A")
    opt = await optimizer.optimize(TASK, content="新内容B")
    assert opt.cached is True
    assert opt.cached_content == "缓存内容A"


@pytest.mark.asyncio
async def test_optimize_previous_param_is_ignored(optimizer):
    opt = await optimizer.optimize(TASK, content="内容", previous="旧版本内容")
    assert opt.status == "planned"
    assert optimizer.count_cache() == 1


@pytest.mark.asyncio
async def test_store_cache_then_hit(optimizer):
    await optimizer.store_cache(TASK, "显式缓存内容", full_tokens=2000)
    assert optimizer.count_cache() == 1
    opt = await optimizer.optimize(TASK)
    assert opt.cached is True
    assert opt.cached_content == "显式缓存内容"
    assert opt.token_used == _replay_cost(2000)


@pytest.mark.asyncio
async def test_optimize_batch_dedup_then_all_cached(optimizer):
    tasks = ["fjlz-m12-batch-A", "fjlz-m12-batch-B"]
    r1 = await optimizer.optimize_batch(tasks, contents=["内容A", "内容B"])
    assert r1.task == ", ".join(tasks)
    assert r1.status == "planned"
    assert r1.cached is False
    assert r1.meta["total_count"] == 2
    assert r1.meta["cached_count"] == 0
    assert r1.token_used == sum(estimate_full(t) for t in tasks)
    assert set(r1.plan) == set(tasks)
    assert optimizer.count_cache() == 2

    r2 = await optimizer.optimize_batch(tasks)
    assert r2.status == "cached"
    assert r2.cached is True
    assert r2.meta["cached_count"] == 2
    assert r2.token_used == sum(_replay_cost(estimate_full(t)) for t in tasks)
    assert r2.plan == []


@pytest.mark.asyncio
async def test_record_usage_and_usage_stats(tmp_path):
    """用量审计：落库 + 写 JSON 报告文件，统计口径正确。"""
    optimizer = TokenOptimizer(
        db_path=str(tmp_path / "tok.db"),
        report_dir=str(tmp_path / "reports"),
    )
    u1 = await optimizer.record_usage(TASK, "explore", 1500, 1500, cached=False)
    assert u1.usage_id.startswith("u")
    report = optimizer.report_dir / f"{u1.usage_id}.json"
    assert report.exists()
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["usage_id"] == u1.usage_id
    assert data["module"] == "explore"
    assert data["token_used"] == 1500

    await optimizer.record_usage(TASK, "cache", 100, 1500, cached=True)
    stats = await optimizer.usage_stats()
    assert stats["runs"] == 2
    assert stats["total_tokens_full"] == 3000
    assert stats["total_tokens_used"] == 1600
    assert stats["saved_ratio"] == pytest.approx(1400 / 3000)
    assert stats["report_dir"] == str(optimizer.report_dir)
    await optimizer.close()


@pytest.mark.asyncio
async def test_usage_stats_empty(tmp_path):
    optimizer = TokenOptimizer(
        db_path=str(tmp_path / "tok.db"),
        report_dir=str(tmp_path / "reports"),
    )
    stats = await optimizer.usage_stats()
    assert stats["runs"] == 0
    assert stats["total_tokens_full"] == 0
    assert stats["total_tokens_used"] == 0
    assert stats["saved_ratio"] == 0.0
    await optimizer.close()


@pytest.mark.asyncio
async def test_clear_cache(optimizer):
    await optimizer.store_cache(TASK, "内容")
    assert await optimizer.clear_cache() == 1
    assert optimizer.count_cache() == 0
    opt = await optimizer.optimize(TASK)
    assert opt.cached is False


@pytest.mark.asyncio
async def test_states_and_initialize(tmp_path):
    optimizer = TokenOptimizer(
        db_path=str(tmp_path / "tok.db"),
        report_dir=str(tmp_path / "reports"),
    )
    await optimizer.initialize()
    st = optimizer.states()
    assert st["db_path"] == optimizer.db_path
    assert st["cache_entries"] == 0
    assert st["report_count"] == 0
    await optimizer.close()


@pytest.mark.asyncio
async def test_close_then_initialize_recovers(optimizer):
    await optimizer.close()
    await optimizer.initialize()
    assert optimizer.count_cache() == 0