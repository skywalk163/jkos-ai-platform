"""M7 任务 7.2：缓存策略优化 — BloomFilter / MultiLayerCache"""

import time

import pytest

from dsh_core.cache.manager import MemoryCache
from dsh_core.cache.multi_layer import BloomFilter, MultiLayerCache


def test_bloom_filter_add_and_contains():
    """布隆过滤器：add 后命中，未标记键不命中（默认 1M 位，误判可忽略）"""
    bf = BloomFilter()
    bf.add("user:1")
    bf.add("user:2")
    assert "user:1" in bf
    assert "user:2" in bf
    assert "user:absent" not in bf


def test_bloom_filter_properties_and_count():
    """布隆过滤器：size / num_hashes / count"""
    bf = BloomFilter(size=1 << 16, num_hashes=3)
    assert bf.size == 1 << 16
    assert bf.num_hashes == 3
    for i in range(5):
        bf.add(f"k{i}")
    assert bf.count == 5


def test_bloom_filter_validation():
    """布隆过滤器：非法参数抛 ValueError"""
    with pytest.raises(ValueError):
        BloomFilter(size=63)
    with pytest.raises(ValueError):
        BloomFilter(num_hashes=0)


async def test_mlc_default_layers():
    """MultiLayerCache：默认 L1/L2 就位，统计初始为 0（未触网）"""
    cache = MultiLayerCache()
    assert cache.l1 is not None
    assert cache.l2 is not None
    assert cache.metrics()["l1_hits"] == 0


async def test_get_l1_hit_skips_loader():
    """get：L1 命中直接返回，不触碰 loader"""
    cache = MultiLayerCache(memory_cache=MemoryCache(), redis_cache=MemoryCache())
    calls = []

    async def loader(key):
        calls.append(key)
        return "db"

    await cache.set("k", "v")
    assert await cache.get("k", loader=loader) == "v"
    m = cache.metrics()
    assert m["l1_hits"] == 1
    assert m["l1_misses"] == 0
    assert calls == []


async def test_get_l2_hit_backfills_l1():
    """get：L2 命中回填 L1，二次读取命中 L1"""
    cache = MultiLayerCache(memory_cache=MemoryCache(), redis_cache=MemoryCache())
    await cache.set("k", "v")
    await cache.l1.delete("k")  # 模拟 L1 过期，L2 仍命中
    assert await cache.get("k") == "v"
    m = cache.metrics()
    assert m["l1_misses"] == 1
    assert m["l2_hits"] == 1
    assert await cache.get("k") == "v"  # 回填后 L1 命中
    assert cache.metrics()["l1_hits"] == 1


async def test_bloom_blocks_penetration():
    """get：布隆短路挡住缓存穿透（从未写入的键不触达 loader）"""
    cache = MultiLayerCache(memory_cache=MemoryCache(), redis_cache=MemoryCache())
    calls = []

    async def loader(key):
        calls.append(key)
        return "expensive"

    assert await cache.get("never-written", loader=loader) is None
    m = cache.metrics()
    assert m["penetration_blocks"] == 1
    assert calls == []


async def test_read_through_loader_after_delete():
    """get：布隆保留已删除键的位 → loader 回源并回填双层缓存"""
    cache = MultiLayerCache(memory_cache=MemoryCache(), redis_cache=MemoryCache())
    calls = []
    await cache.set("k", "old")
    await cache.delete("k")  # 双层删除，布隆位保留

    async def loader(key):
        calls.append(key)
        return "fresh"

    assert await cache.get("k", loader=loader) == "fresh"
    m = cache.metrics()
    assert m["db_loads"] == 1
    assert calls == ["k"]
    # 回填后普通读取直接命中
    assert await cache.get("k") == "fresh"


async def test_delete_clears_both_layers():
    """delete：同时清理 L1 与 L2"""
    cache = MultiLayerCache(memory_cache=MemoryCache(), redis_cache=MemoryCache())
    await cache.set("k", "v")
    await cache.delete("k")
    assert await cache.l1.get("k") is None
    assert await cache.l2.get("k") is None
    assert await cache.get("k") is None


async def test_set_marks_bloom_and_both_layers():
    """set：双写 L1/L2 并标记布隆（ttl_jitter=0 时 TTL 原样）"""
    cache = MultiLayerCache(memory_cache=MemoryCache(), redis_cache=MemoryCache(),
                            default_ttl=300, ttl_jitter=0)
    await cache.set("k", {"n": 1}, ttl=100)
    assert await cache.l1.get("k") == {"n": 1}
    assert await cache.l2.get("k") == {"n": 1}
    assert cache.metrics()["bloom_count"] == 1


async def test_ttl_jitter_bounds():
    """get 回填 TTL 抖动：落在 ±10% 区间内（ttl=1000 → 900..1100）"""
    cache = MultiLayerCache(memory_cache=MemoryCache(), redis_cache=MemoryCache(),
                            ttl_jitter=0.1)
    await cache.set("k", "v", ttl=1000)
    entry = cache.l1._store["k"]
    remaining = entry["expires_at"] - time.time()
    assert 890 <= remaining <= 1110


async def test_metrics_hit_rates():
    """metrics：命中率统计（M7 7.2 验收指标）"""
    cache = MultiLayerCache(memory_cache=MemoryCache(), redis_cache=MemoryCache())
    await cache.set("a", "1")
    await cache.set("b", "2")
    await cache.set("c", "3")
    assert await cache.get("a") == "1"          # L1 命中
    await cache.l1.delete("b")
    await cache.l2.delete("b")
    assert await cache.get("b") is None         # L1 缺失 → L2 缺失
    await cache.l1.delete("c")
    assert await cache.get("c") == "3"          # L1 缺失 → L2 命中（回填）
    assert await cache.get("d") is None         # 布隆拦截（穿透）
    m = cache.metrics()
    assert m["l1_hits"] == 1
    assert m["l1_misses"] == 3
    assert m["l2_hits"] == 1
    assert m["l2_misses"] == 1
    assert m["penetration_blocks"] == 1
    assert m["bloom_count"] == 3
    assert m["l1_hit_rate"] == round(1 / 4, 4)
    assert m["l2_hit_rate"] == round(1 / 2, 4)
    assert m["overall_hit_rate"] == round(2 / 4, 4)