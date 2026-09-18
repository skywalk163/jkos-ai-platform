"""DSH 多级缓存（M7 任务 7.2）

L1 内存缓存 + L2 Redis 缓存，读路径 L1 → L2 → 业务 loader；
布隆过滤器防止缓存穿透（penetration）；TTL 随机抖动防止缓存雪崩（avalanche）；
metrics() 提供命中率等监控指标（monitoring）。
"""
import hashlib
import logging
import random
from typing import Any, Callable, Dict, Optional

from jkos_core.cache.manager import CacheBackend, MemoryCache, RedisCache

logger = logging.getLogger("dsh.cache")

# 回源加载器：loader(key) -> value | None
Loader = Callable[[str], Any]


class BloomFilter:
    """布隆过滤器（M7 7.2 缓存穿透保护）

    位数组实现：以 md5/sha256 双哈希派生 num_hashes 个独立位；
    只增不删（delete 时保留布隆位——宁可误判存在也不放行穿透查询）。
    """

    def __init__(self, size: int = 1 << 20, num_hashes: int = 7):
        if size < 64:
            raise ValueError("布隆过滤器 size 至少为 64 位")
        if num_hashes < 1:
            raise ValueError("布隆过滤器 num_hashes 至少为 1")
        self._size = size
        self._num_hashes = num_hashes
        self._bits = bytearray((size + 7) // 8)
        self._count = 0

    def _indexes(self, key: str):
        """双哈希法：index_i = (h1 + i * h2) % size"""
        h1 = int.from_bytes(hashlib.md5(key.encode("utf-8")).digest()[:8], "big")
        h2 = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")
        for i in range(self._num_hashes):
            yield (h1 + i * h2) % self._size

    def add(self, key: str) -> None:
        for idx in self._indexes(key):
            byte, bit = divmod(idx, 8)
            self._bits[byte] |= 1 << bit
        self._count += 1

    def __contains__(self, key: str) -> bool:
        return all(
            self._bits[idx // 8] & (1 << (idx % 8))
            for idx in self._indexes(key)
        )

    @property
    def size(self) -> int:
        return self._size

    @property
    def num_hashes(self) -> int:
        return self._num_hashes

    @property
    def count(self) -> int:
        """已标记的键数量（监控用）"""
        return self._count


class MultiLayerCache:
    """L1 内存 + L2 Redis 多级缓存（M7 任务 7.2）

    参数：
        memory_cache: L1 后端（默认 MemoryCache）
        redis_cache:  L2 后端（默认 RedisCache，Redis 不可用时自动降级）
        default_ttl:  默认过期秒数（默认 300）
        ttl_jitter:   TTL 随机抖动比例 [0,1)（默认 0.1，防雪崩）
        bloom_size / bloom_hashes: 布隆过滤器位长与哈希数

    读路径：L1 → L2 → loader(key)（业务回源）；set 时双写两级并标记布隆。
    """

    def __init__(
        self,
        memory_cache: Optional[CacheBackend] = None,
        redis_cache: Optional[CacheBackend] = None,
        *,
        default_ttl: int = 300,
        ttl_jitter: float = 0.1,
        bloom_size: int = 1 << 20,
        bloom_hashes: int = 7,
    ):
        self.l1 = memory_cache or MemoryCache()
        self.l2 = redis_cache or RedisCache()
        self.default_ttl = default_ttl
        self.ttl_jitter = ttl_jitter
        self._bloom = BloomFilter(size=bloom_size, num_hashes=bloom_hashes)
        self._stats = {
            "l1_hits": 0,
            "l1_misses": 0,
            "l2_hits": 0,
            "l2_misses": 0,
            "db_loads": 0,
            "penetration_blocks": 0,
        }

    # ── 内部工具 ──

    def _jittered_ttl(self, ttl: Optional[int]) -> int:
        """TTL 随机抖动（±ttl_jitter 比例），防止大量键同时过期引发雪崩"""
        ttl = ttl if ttl is not None else self.default_ttl
        if ttl <= 0:
            return ttl
        span = max(1, int(ttl * self.ttl_jitter))
        return max(1, ttl + random.randint(-span, span))

    # ── 读 / 写 ──

    async def get(self, key: str, loader: Optional[Loader] = None) -> Any:
        """读取缓存；未命中 L2 且有 loader 时回源并回填（read-through）"""
        v = await self.l1.get(key)
        if v is not None:
            self._stats["l1_hits"] += 1
            return v
        self._stats["l1_misses"] += 1

        if key not in self._bloom:
            # 布隆未标记 → 该键从未写入缓存，直接短路（防穿透）
            self._stats["penetration_blocks"] += 1
            return None

        v = await self.l2.get(key)
        if v is not None:
            self._stats["l2_hits"] += 1
            await self.l1.set(key, v, self._jittered_ttl(None))
            return v
        self._stats["l2_misses"] += 1

        if loader is None:
            return None
        v = await loader(key)
        if v is not None:
            self._stats["db_loads"] += 1
            await self.set(key, v)
        return v

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """双写 L1 + L2 并标记布隆（TTL 自动随机抖动）"""
        ttl_j = self._jittered_ttl(ttl)
        await self.l1.set(key, value, ttl_j)
        await self.l2.set(key, value, ttl_j)
        self._bloom.add(key)

    async def delete(self, key: str) -> None:
        """删除两级缓存键（布隆位保守保留，避免绕过防护）"""
        await self.l1.delete(key)
        await self.l2.delete(key)

    # ── 监控 ──

    def metrics(self) -> Dict[str, Any]:
        """缓存命中率等监控指标（M7 7.2 验收：命中率监控）"""
        s = self._stats
        l1_total = s["l1_hits"] + s["l1_misses"]
        l2_total = s["l2_hits"] + s["l2_misses"]
        return {
            **s,
            "bloom_count": self._bloom.count,
            "l1_hit_rate": round(s["l1_hits"] / l1_total, 4) if l1_total else 0.0,
            "l2_hit_rate": round(s["l2_hits"] / l2_total, 4) if l2_total else 0.0,
            "overall_hit_rate": round(
                (s["l1_hits"] + s["l2_hits"]) / max(1, l1_total), 4
            ),
        }