"""DSH 缓存层 - Redis 缓存接口（M3 任务 3.3）

实现 Redis 缓存接口，支持缓存 LLM 响应、工作流结果等。
"""
from __future__ import annotations

import abc
import json
import logging
import os
import time
from typing import Any, Dict, Optional

import redis.asyncio as aioredis

logger = logging.getLogger("dsh.cache")


# ─── 缓存基类 ───

class CacheBackend(abc.ABC):
    """缓存后端基类"""

    @abc.abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        pass

    @abc.abstractmethod
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """设置缓存值

        Args:
            key: 缓存键
            value: 缓存值
            ttl: 过期时间（秒），None 表示不过期
        """
        pass

    @abc.abstractmethod
    async def delete(self, key: str) -> bool:
        """删除缓存值"""
        pass

    @abc.abstractmethod
    async def exists(self, key: str) -> bool:
        """检查缓存键是否存在"""
        pass


# ─── 内存缓存（开发/测试用）───

class MemoryCache(CacheBackend):
    """内存缓存后端（开发/测试用）

    注意：进程重启后数据丢失，不适合生产环境。
    """

    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}

    async def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        if key not in self._store:
            return None
        entry = self._store[key]
        if entry["expires_at"] and entry["expires_at"] < time.time():
            del self._store[key]
            return None
        return entry["value"]

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """设置缓存值"""
        expires_at = None
        if ttl is not None:
            expires_at = time.time() + ttl
        self._store[key] = {
            "value": value,
            "expires_at": expires_at,
        }

    async def delete(self, key: str) -> bool:
        """删除缓存值"""
        if key in self._store:
            del self._store[key]
            return True
        return False

    async def exists(self, key: str) -> bool:
        """检查缓存键是否存在"""
        return key in self._store and (
            self._store[key]["expires_at"] is None or
            self._store[key]["expires_at"] >= time.time()
        )

    def clear(self) -> None:
        """清空缓存"""
        self._store.clear()


# ─── Redis 缓存（生产用）───

class RedisCache(CacheBackend):
    """Redis 缓存后端（生产用，M7 任务 7.2 升级为真实 redis-py 异步实现）

    环境变量：
      - DSH_REDIS_URL: Redis 连接 URL（默认 redis://localhost:6379/0）

    配置项（config dict）：
      - url: 覆盖 DSH_REDIS_URL
      - connect_timeout: 连接超时（默认 3s）
      - operation_timeout: 单次操作超时（默认 3s）

    Redis 不可用时自动降级：get 返回 None、set 忽略、delete/exists 返回 False，
    仅输出告警日志，不阻塞业务。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._url = self.config.get("url") or os.getenv(
            "DSH_REDIS_URL", "redis://localhost:6379/0"
        )
        self._connect_timeout = float(self.config.get("connect_timeout", 3.0))
        self._operation_timeout = float(self.config.get("operation_timeout", 3.0))
        self._client: Optional[aioredis.Redis] = None

    async def _get_client(self) -> Optional[aioredis.Redis]:
        """惰性建立连接；失败返回 None 并告警降级"""
        if self._client is None:
            try:
                self._client = aioredis.from_url(
                    self._url,
                    decode_responses=True,
                    socket_connect_timeout=self._connect_timeout,
                    socket_timeout=self._operation_timeout,
                    health_check_interval=30,
                )
                await self._client.ping()
            except Exception as e:
                logger.warning("Redis 不可用，缓存降级: %s", e)
                self._client = None
                return None
        return self._client

    async def get(self, key: str) -> Optional[Any]:
        """获取缓存值（JSON 反序列化；Redis 不可用返回 None 降级）"""
        client = await self._get_client()
        if client is None:
            return None
        try:
            raw = await client.get(key)
            return json.loads(raw) if raw is not None else None
        except Exception as e:
            logger.warning("Redis get 失败: %s", e)
            return None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """设置缓存值（JSON 序列化）"""
        client = await self._get_client()
        if client is None:
            return
        try:
            await client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
        except Exception as e:
            logger.warning("Redis set 失败: %s", e)

    async def delete(self, key: str) -> bool:
        """删除缓存值"""
        client = await self._get_client()
        if client is None:
            return False
        try:
            return bool(await client.delete(key))
        except Exception as e:
            logger.warning("Redis delete 失败: %s", e)
            return False

    async def exists(self, key: str) -> bool:
        """检查缓存键是否存在"""
        client = await self._get_client()
        if client is None:
            return False
        try:
            return bool(await client.exists(key))
        except Exception as e:
            logger.warning("Redis exists 失败: %s", e)
            return False


# ─── 缓存管理器 ───

class CacheManager:
    """缓存管理器：统一缓存接口"""

    def __init__(self, backend: Optional[CacheBackend] = None):
        # 默认使用内存缓存
        self.backend: CacheBackend = backend or MemoryCache()

    async def get(self, key: str, default: Optional[Any] = None) -> Any:
        """获取缓存值"""
        value = await self.backend.get(key)
        return value if value is not None else default

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """设置缓存值"""
        await self.backend.set(key, value, ttl)

    async def delete(self, key: str) -> bool:
        """删除缓存值"""
        return await self.backend.delete(key)

    async def exists(self, key: str) -> bool:
        """检查缓存键是否存在"""
        return await self.backend.exists(key)

    def get_backend_type(self) -> str:
        """获取后端类型"""
        return type(self.backend).__name__


# ─── LLM 响应缓存 ───

class LLMCachedResponse:
    """LLM 响应缓存包装器

    用于缓存 LLM 响应，避免重复调用。
    """

    def __init__(self, cache_manager: CacheManager, ttl: int = 3600):
        self.cache = cache_manager
        self.ttl = ttl

    def _make_key(self, prompt: str, model: str, temperature: float) -> str:
        """生成缓存键"""
        import hashlib
        key_data = f"{prompt}:{model}:{temperature}"
        return f"llm:{hashlib.md5(key_data.encode()).hexdigest()}"

    async def get_cached(self, prompt: str, model: str, temperature: float) -> Optional[Any]:
        """获取缓存的 LLM 响应"""
        key = self._make_key(prompt, model, temperature)
        return await self.cache.get(key)

    async def set_cached(self, prompt: str, model: str, temperature: float, response: Any) -> None:
        """缓存 LLM 响应"""
        key = self._make_key(prompt, model, temperature)
        await self.cache.set(key, response, ttl=self.ttl)
