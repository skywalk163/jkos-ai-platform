"""DSH 存储模块 - 统一管理 PostgreSQL、Redis、MinIO"""

from __future__ import annotations
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncpg
import redis.asyncio as redis

# MinIO 可选导入
try:
    from minio import Minio
    from minio.error import S3Error
    MINIO_AVAILABLE = True
except ImportError:
    MINIO_AVAILABLE = False
    Minio = None
    S3Error = Exception

logger = logging.getLogger("dsh.storage")


@dataclass
class StorageConfig:
    """存储配置"""
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "dsh"
    postgres_user: str = "dsh"
    postgres_password: str = "dsh_pass"
    postgres_pool_min: int = 5
    postgres_pool_max: int = 20
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "dsh"
    minio_secure: bool = False

    @classmethod
    def from_env(cls) -> "StorageConfig":
        import os
        return cls(
            postgres_host=os.getenv("DSH_PG_HOST", "localhost"),
            postgres_port=int(os.getenv("DSH_PG_PORT", "5432")),
            postgres_db=os.getenv("DSH_PG_DB", "dsh"),
            postgres_user=os.getenv("DSH_PG_USER", "dsh"),
            postgres_password=os.getenv("DSH_PG_PASS", "dsh_pass"),
            redis_host=os.getenv("DSH_REDIS_HOST", "localhost"),
            redis_port=int(os.getenv("DSH_REDIS_PORT", "6379")),
            minio_endpoint=os.getenv("DSH_MINIO_ENDPOINT", "localhost:9000"),
            minio_access_key=os.getenv("DSH_MINIO_ACCESS_KEY", "minioadmin"),
            minio_secret_key=os.getenv("DSH_MINIO_SECRET_KEY", "minioadmin"),
        )


class StorageManager:
    """存储管理器 - 统一入口"""

    def __init__(self, config: Optional[StorageConfig] = None):
        self.config = config or StorageConfig()
        self._pg_pool: Optional[asyncpg.Pool] = None
        self._redis: Optional[redis.Redis] = None
        self._minio: Optional[Minio] = None
        self._initialized: bool = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        self._pg_pool = await asyncpg.create_pool(
            host=self.config.postgres_host,
            port=self.config.postgres_port,
            database=self.config.postgres_db,
            user=self.config.postgres_user,
            password=self.config.postgres_password,
            min_size=self.config.postgres_pool_min,
            max_size=self.config.postgres_pool_max,
        )
        logger.info(f"PostgreSQL 连接池已建立: {self.config.postgres_host}:{self.config.postgres_port}")
        self._redis = redis.Redis(
            host=self.config.redis_host, port=self.config.redis_port,
            db=self.config.redis_db, password=self.config.redis_password or None,
            decode_responses=True,
        )
        await self._redis.ping()
        logger.info(f"Redis 连接已建立: {self.config.redis_host}:{self.config.redis_port}")
        if MINIO_AVAILABLE and Minio:
            self._minio = Minio(
                self.config.minio_endpoint,
                access_key=self.config.minio_access_key,
                secret_key=self.config.minio_secret_key,
                secure=self.config.minio_secure,
            )
            if not self._minio.bucket_exists(self.config.minio_bucket):
                self._minio.make_bucket(self.config.minio_bucket)
            logger.info(f"MinIO 连接已建立: {self.config.minio_endpoint}")
        else:
            logger.warning("MinIO 不可用，对象存储功能将无法使用")
        self._initialized = True

    async def cleanup(self) -> None:
        if self._pg_pool:
            await self._pg_pool.close()
        if self._redis:
            await self._redis.close()
        self._initialized = False

    @property
    def pg(self) -> asyncpg.Pool:
        if not self._pg_pool:
            raise RuntimeError("PostgreSQL 未初始化")
        return self._pg_pool

    @property
    def redis(self) -> redis.Redis:
        if not self._redis:
            raise RuntimeError("Redis 未初始化")
        return self._redis

    @property
    def minio(self):
        if not self._minio:
            raise RuntimeError("MinIO 未初始化或不可用")
        return self._minio

    async def execute(self, query: str, *args) -> str:
        async with self._pg_pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args) -> List[asyncpg.Record]:
        async with self._pg_pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetch_one(self, query: str, *args) -> Optional[asyncpg.Record]:
        async with self._pg_pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    @asynccontextmanager
    async def transaction(self):
        async with self._pg_pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    async def cache_set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        try:
            await self._redis.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl)
            return True
        except Exception as e:
            logger.error(f"缓存设置失败: {key} - {e}")
            return False

    async def cache_get(self, key: str) -> Optional[Any]:
        try:
            v = await self._redis.get(key)
            return json.loads(v) if v else None
        except Exception:
            return None

    async def upload_file(self, object_name: str, file_path: str,
                          content_type: str = "application/octet-stream",
                          metadata: Optional[Dict[str, str]] = None) -> str:
        if not self._minio:
            raise RuntimeError("MinIO 不可用，无法上传文件")
        self._minio.fput_object(self.config.minio_bucket, object_name, file_path,
                                content_type=content_type, metadata=metadata)
        return object_name

    async def download_file(self, object_name: str, file_path: str) -> bool:
        if not self._minio:
            logger.error("MinIO 不可用，无法下载文件")
            return False
        try:
            self._minio.fget_object(self.config.minio_bucket, object_name, file_path)
            return True
        except S3Error:
            return False

    async def delete_file(self, object_name: str) -> bool:
        if not self._minio:
            logger.error("MinIO 不可用，无法删除文件")
            return False
        try:
            self._minio.remove_object(self.config.minio_bucket, object_name)
            return True
        except S3Error:
            return False

    async def get_presigned_url(self, object_name: str, expires: int = 3600) -> str:
        if not self._minio:
            raise RuntimeError("MinIO 不可用，无法生成预签名 URL")
        return self._minio.presigned_get_object(self.config.minio_bucket, object_name, expires)


INIT_SQL = """
CREATE TABLE IF NOT EXISTS plugins (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
    category TEXT NOT NULL, plugin_type TEXT NOT NULL,
    config JSONB DEFAULT '{}', enabled BOOLEAN DEFAULT true,
    installed_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, user_id TEXT, tenant_id TEXT, title TEXT,
    metadata JSONB DEFAULT '{}', created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL, content TEXT NOT NULL, metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS multimodal_resources (
    id TEXT PRIMARY KEY, tenant_id TEXT, user_id TEXT,
    category TEXT NOT NULL, file_name TEXT NOT NULL, file_size BIGINT,
    mime_type TEXT, storage_path TEXT NOT NULL, metadata JSONB DEFAULT '{}',
    status TEXT DEFAULT 'processed', created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS agent_registrations (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, agent_type TEXT NOT NULL,
    endpoint TEXT NOT NULL, capabilities JSONB DEFAULT '[]',
    auth_config JSONB DEFAULT '{}', registered_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY, timestamp TIMESTAMPTZ DEFAULT NOW(),
    user_id TEXT, tenant_id TEXT, action TEXT NOT NULL,
    resource_type TEXT, resource_id TEXT, request_id TEXT,
    metadata JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_resources_tenant ON multimodal_resources(tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);
"""


async def init_database(config: StorageConfig) -> None:
    pool = await asyncpg.create_pool(
        host=config.postgres_host, port=config.postgres_port,
        database=config.postgres_db, user=config.postgres_user,
        password=config.postgres_password,
    )
    async with pool.acquire() as conn:
        for stmt in INIT_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    await conn.execute(stmt)
                except Exception as e:
                    logger.warning(f"SQL 警告: {e}")
    await pool.close()
    logger.info("数据库初始化完成")
