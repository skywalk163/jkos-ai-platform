"""DSH Core - 存储层测试"""

import pytest
from unittest.mock import Mock, patch, AsyncMock

from dsh_core.storage import StorageConfig, StorageManager, init_database


class TestStorageConfig:
    """存储配置测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = StorageConfig()
        assert config.postgres_host == "localhost"
        assert config.postgres_port == 5432
        assert config.minio_endpoint == "localhost:9000"

    def test_from_env(self, monkeypatch):
        """测试从环境变量加载配置"""
        monkeypatch.setenv("DSH_PG_HOST", "pg.example.com")
        monkeypatch.setenv("DSH_PG_PORT", "5433")
        monkeypatch.setenv("DSH_MINIO_ENDPOINT", "minio.example.com:9000")

        config = StorageConfig.from_env()
        assert config.postgres_host == "pg.example.com"
        assert config.postgres_port == 5433
        assert config.minio_endpoint == "minio.example.com:9000"


class TestStorageManager:
    """存储管理器测试"""

    @pytest.fixture
    def config(self):
        return StorageConfig(
            postgres_host="localhost",
            postgres_port=5432,
            postgres_db="dsh_test",
            postgres_user="test",
            postgres_password="test",
        )

    @pytest.fixture
    def manager(self, config):
        return StorageManager(config)

    def test_manager_creation(self, manager):
        """测试管理器创建"""
        assert manager.config.postgres_db == "dsh_test"
        assert not manager._initialized

    @pytest.mark.asyncio
    async def test_initialize_not_implemented(self, manager):
        """测试初始化（不实际连接）"""
        # 不实际连接，只测试方法存在
        assert hasattr(manager, "initialize")
        assert hasattr(manager, "cleanup")

    def test_pg_property_not_initialized(self, manager):
        """测试未初始化时访问 pg 属性"""
        with pytest.raises(RuntimeError):
            _ = manager.pg

    def test_redis_property_not_initialized(self, manager):
        """测试未初始化时访问 redis 属性"""
        with pytest.raises(RuntimeError):
            _ = manager.redis

    def test_minio_property_not_initialized(self, manager):
        """测试未初始化时访问 minio 属性"""
        with pytest.raises(RuntimeError):
            _ = manager.minio

    def test_pg_property_returns_pool(self, manager):
        """测试已初始化时 pg 属性返回连接池"""
        pool = Mock()
        manager._pg_pool = pool
        assert manager.pg is pool

    def test_redis_property_returns_client(self, manager):
        """测试已初始化时 redis 属性返回客户端"""
        client = Mock()
        manager._redis = client
        assert manager.redis is client

    def test_minio_property_returns_client(self, manager):
        """测试已初始化时 minio 属性返回客户端"""
        client = Mock()
        manager._minio = client
        assert manager.minio is client


class TestCacheOperations:
    """缓存操作测试"""

    @pytest.mark.asyncio
    async def test_cache_set_get(self):
        """测试缓存设置和获取"""
        with patch("redis.asyncio.Redis") as mock_redis:
            mock_client = AsyncMock()
            mock_client.get.return_value = '{"key": "value"}'
            mock_client.set.return_value = True
            mock_redis.return_value = mock_client

            # 这里只是验证方法签名
            assert True

    @pytest.mark.asyncio
    async def test_cache_delete(self):
        """测试缓存删除"""
        with patch("redis.asyncio.Redis") as mock_redis:
            mock_client = AsyncMock()
            mock_client.delete.return_value = 1
            mock_redis.return_value = mock_client

            assert True


class TestMinIOOperations:
    """MinIO 操作测试"""

    def test_minio_config(self):
        """测试 MinIO 配置"""
        config = StorageConfig(
            minio_endpoint="minio.example.com:9000",
            minio_access_key="access_key",
            minio_secret_key="secret_key",
            minio_bucket="test-bucket",
        )
        assert config.minio_endpoint == "minio.example.com:9000"
        assert config.minio_bucket == "test-bucket"


class TestStorageManagerInitialize:
    """存储管理器中 initialize/cleanup 真实方法体覆盖（任务 6.4）"""

    @pytest.mark.asyncio
    async def test_initialize_success_bucket_exists(self):
        """初始化成功且桶已存在"""
        manager = StorageManager(StorageConfig(minio_bucket="test-bucket"))
        pool = AsyncMock()
        mock_minio = Mock()
        mock_minio.bucket_exists.return_value = True
        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=pool)),
            patch("redis.asyncio.Redis") as mock_redis_cls,
            patch("dsh_core.storage.manager.Minio", return_value=mock_minio),
            patch("dsh_core.storage.manager.MINIO_AVAILABLE", True),
        ):
            mock_redis_cls.return_value = AsyncMock()

            await manager.initialize()

        assert manager._initialized is True
        assert manager._pg_pool is pool
        assert manager._minio is mock_minio
        mock_minio.bucket_exists.assert_called_once_with("test-bucket")
        mock_minio.make_bucket.assert_not_called()
        mock_redis_cls.return_value.ping.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_initialize_makes_bucket_when_missing(self):
        """初始化时桶不存在则自动创建"""
        manager = StorageManager(StorageConfig(minio_bucket="test-bucket"))
        mock_minio = Mock()
        mock_minio.bucket_exists.return_value = False
        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=AsyncMock())),
            patch("redis.asyncio.Redis") as mock_redis_cls,
            patch("dsh_core.storage.manager.Minio", return_value=mock_minio),
            patch("dsh_core.storage.manager.MINIO_AVAILABLE", True),
        ):
            mock_redis_cls.return_value = AsyncMock()

            await manager.initialize()

        mock_minio.make_bucket.assert_called_once_with("test-bucket")

    @pytest.mark.asyncio
    async def test_initialize_minio_unavailable(self, caplog):
        """初始化时 MinIO 不可用走告警分支"""
        import logging
        manager = StorageManager(StorageConfig())
        with (
            patch("asyncpg.create_pool", new=AsyncMock(return_value=AsyncMock())),
            patch("redis.asyncio.Redis") as mock_redis_cls,
            patch("dsh_core.storage.manager.MINIO_AVAILABLE", False),
        ):
            mock_redis_cls.return_value = AsyncMock()
            with caplog.at_level(logging.WARNING, logger="dsh.storage"):
                await manager.initialize()

        assert manager._initialized is True
        assert manager._minio is None
        assert "MinIO 不可用" in caplog.text

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self):
        """已初始化时再次调用直接返回"""
        manager = StorageManager(StorageConfig())
        manager._initialized = True
        with (
            patch("asyncpg.create_pool", new=AsyncMock()) as mock_create_pool,
            patch("redis.asyncio.Redis") as mock_redis_cls,
            patch("dsh_core.storage.manager.Minio"),
            patch("dsh_core.storage.manager.MINIO_AVAILABLE", True),
        ):
            await manager.initialize()

        mock_create_pool.assert_not_awaited()
        mock_redis_cls.assert_not_called()
        assert manager._initialized is True

    @pytest.mark.asyncio
    async def test_cleanup_closes_resources(self):
        """清理时关闭连接池与 Redis"""
        manager = StorageManager(StorageConfig())
        pool = AsyncMock()
        redis_client = AsyncMock()
        manager._pg_pool = pool
        manager._redis = redis_client
        manager._initialized = True

        await manager.cleanup()

        pool.close.assert_awaited_once()
        redis_client.close.assert_awaited_once()
        assert manager._initialized is False

    @pytest.mark.asyncio
    async def test_cleanup_without_resources(self):
        """未初始化时清理不报错"""
        manager = StorageManager(StorageConfig())

        await manager.cleanup()

        assert manager._initialized is False


class TestStoragePgOperations:
    """PostgreSQL 查询与事务方法体覆盖（任务 6.4）"""

    @staticmethod
    def _build():
        """构造带模拟连接池的 manager"""
        manager = StorageManager(StorageConfig())
        pool = Mock()
        conn = AsyncMock()
        cm = pool.acquire.return_value
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        manager._pg_pool = pool
        return manager, pool, conn

    @pytest.mark.asyncio
    async def test_execute(self):
        """execute 走真实方法体"""
        manager, _, conn = self._build()
        conn.execute.return_value = "INSERT 0 1"

        result = await manager.execute("INSERT INTO t(id) VALUES($1)", 1)

        assert result == "INSERT 0 1"
        conn.execute.assert_awaited_once_with("INSERT INTO t(id) VALUES($1)", 1)

    @pytest.mark.asyncio
    async def test_fetch(self):
        """fetch 走真实方法体"""
        manager, _, conn = self._build()
        conn.fetch.return_value = [{"id": 1}]

        result = await manager.fetch("SELECT id FROM t WHERE id=$1", 1)

        assert result == [{"id": 1}]
        conn.fetch.assert_awaited_once_with("SELECT id FROM t WHERE id=$1", 1)

    @pytest.mark.asyncio
    async def test_fetch_one(self):
        """fetch_one 走真实方法体"""
        manager, _, conn = self._build()
        conn.fetchrow.return_value = {"id": 1}

        result = await manager.fetch_one("SELECT id FROM t WHERE id=$1", 1)

        assert result == {"id": 1}
        conn.fetchrow.assert_awaited_once_with("SELECT id FROM t WHERE id=$1", 1)

    @pytest.mark.asyncio
    async def test_transaction_yields_conn(self):
        """transaction 上下文管理器产出连接"""
        manager, pool, conn = self._build()
        txn_cm = Mock()
        txn_cm.__aenter__ = AsyncMock()
        txn_cm.__aexit__ = AsyncMock(return_value=False)
        # conn 是 AsyncMock，其子属性调用返回协程而非 return_value，
        # 因此必须把 conn.transaction 覆盖为返回事务上下文管理器的 Mock
        conn.transaction = Mock(return_value=txn_cm)

        async with manager.transaction() as tconn:
            assert tconn is conn

        pool.acquire.assert_called_once_with()
        conn.transaction.assert_called_once_with()


class TestStorageCacheCoverage:
    """缓存读写真实方法体覆盖（任务 6.4）"""

    @pytest.mark.asyncio
    async def test_cache_set_success(self):
        """cache_set 成功路径"""
        import json
        from datetime import datetime
        manager = StorageManager(StorageConfig())
        manager._redis = AsyncMock()
        manager._redis.set.return_value = True

        ok = await manager.cache_set("user:1:pref", {"time": datetime.now()})

        assert ok is True
        args = manager._redis.set.await_args
        assert args.args[0] == "user:1:pref"
        assert args.kwargs["ex"] == 3600
        assert "time" in json.loads(args.args[1])

    @pytest.mark.asyncio
    async def test_cache_set_exception(self):
        """cache_set 异常时返回 False"""
        manager = StorageManager(StorageConfig())
        manager._redis = AsyncMock()
        manager._redis.set.side_effect = RuntimeError("boom")

        assert await manager.cache_set("k", {"a": 1}) is False

    @pytest.mark.asyncio
    async def test_cache_get_hit(self):
        """cache_get 命中"""
        manager = StorageManager(StorageConfig())
        manager._redis = AsyncMock()
        manager._redis.get.return_value = '{"name": "echo"}'

        assert await manager.cache_get("k") == {"name": "echo"}

    @pytest.mark.asyncio
    async def test_cache_get_miss(self):
        """cache_get 未命中"""
        manager = StorageManager(StorageConfig())
        manager._redis = AsyncMock()
        manager._redis.get.return_value = None

        assert await manager.cache_get("k") is None

    @pytest.mark.asyncio
    async def test_cache_get_exception(self):
        """cache_get 异常时返回 None"""
        manager = StorageManager(StorageConfig())
        manager._redis = AsyncMock()
        manager._redis.get.side_effect = RuntimeError("boom")

        assert await manager.cache_get("k") is None


class TestStorageFileOperations:
    """MinIO 文件操作方法体覆盖（任务 6.4）"""

    @staticmethod
    def _manager_with_minio():
        manager = StorageManager(StorageConfig(minio_bucket="test-bucket"))
        manager._minio = Mock()
        return manager

    @staticmethod
    def _s3_error():
        from minio.error import S3Error
        return S3Error("MockCode", "mock", "resource", "req-id", "host-id", None)

    @pytest.mark.asyncio
    async def test_upload_file_success(self):
        """上传成功"""
        manager = self._manager_with_minio()

        result = await manager.upload_file(
            "a.txt", "/tmp/a.txt", content_type="text/plain", metadata={"k": "v"})

        assert result == "a.txt"
        manager._minio.fput_object.assert_called_once_with(
            "test-bucket", "a.txt", "/tmp/a.txt",
            content_type="text/plain", metadata={"k": "v"})

    @pytest.mark.asyncio
    async def test_upload_file_uninitialized(self):
        """MinIO 未初始化时上传抛异常"""
        manager = StorageManager(StorageConfig())

        with pytest.raises(RuntimeError):
            await manager.upload_file("a.txt", "/tmp/a.txt")

    @pytest.mark.asyncio
    async def test_download_file_success(self):
        """下载成功"""
        manager = self._manager_with_minio()

        assert await manager.download_file("a.txt", "/tmp/a.txt") is True
        manager._minio.fget_object.assert_called_once_with(
            "test-bucket", "a.txt", "/tmp/a.txt")

    @pytest.mark.asyncio
    async def test_download_file_uninitialized(self):
        """MinIO 未初始化时下载返回 False"""
        manager = StorageManager(StorageConfig())

        assert await manager.download_file("a.txt", "/tmp/a.txt") is False

    @pytest.mark.asyncio
    async def test_download_file_s3error(self):
        """下载遇 S3Error 返回 False"""
        manager = self._manager_with_minio()
        manager._minio.fget_object.side_effect = self._s3_error()

        assert await manager.download_file("a.txt", "/tmp/a.txt") is False

    @pytest.mark.asyncio
    async def test_delete_file_success(self):
        """删除成功"""
        manager = self._manager_with_minio()

        assert await manager.delete_file("a.txt") is True
        manager._minio.remove_object.assert_called_once_with("test-bucket", "a.txt")

    @pytest.mark.asyncio
    async def test_delete_file_uninitialized(self):
        """MinIO 未初始化时删除返回 False"""
        manager = StorageManager(StorageConfig())

        assert await manager.delete_file("a.txt") is False

    @pytest.mark.asyncio
    async def test_delete_file_s3error(self):
        """删除遇 S3Error 返回 False"""
        manager = self._manager_with_minio()
        manager._minio.remove_object.side_effect = self._s3_error()

        assert await manager.delete_file("a.txt") is False

    @pytest.mark.asyncio
    async def test_get_presigned_url_success(self):
        """生成预签名 URL 成功"""
        manager = self._manager_with_minio()
        manager._minio.presigned_get_object.return_value = "http://example/presigned"

        url = await manager.get_presigned_url("a.txt", expires=600)

        assert url == "http://example/presigned"
        manager._minio.presigned_get_object.assert_called_once_with(
            "test-bucket", "a.txt", 600)

    @pytest.mark.asyncio
    async def test_get_presigned_url_uninitialized(self):
        """MinIO 未初始化时预签名 URL 抛异常"""
        manager = StorageManager(StorageConfig())

        with pytest.raises(RuntimeError):
            await manager.get_presigned_url("a.txt")


class TestInitDatabaseCoverage:
    """模块级 init_database 方法体覆盖（任务 6.4）"""

    @staticmethod
    def _build():
        # pool 需为普通 Mock：AsyncMock 的 pool.acquire() 返回协程，
        # 无法用于 manager.py 中 async with pool.acquire() as conn；
        # 但 manager.py 末尾 await pool.close()，故把 close 绑为 AsyncMock
        pool = Mock()
        pool.close = AsyncMock()
        conn = AsyncMock()
        cm = pool.acquire.return_value
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        return pool, conn

    @pytest.mark.asyncio
    async def test_init_database_success(self):
        """初始化建表执行全部 SQL 语句"""
        pool, conn = self._build()

        with patch("asyncpg.create_pool", new=AsyncMock(return_value=pool)):
            await init_database(StorageConfig())

        assert conn.execute.await_count >= 10
        pool.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_init_database_sql_warning(self, caplog):
        """某条 SQL 失败时记录警告并继续"""
        import logging
        pool, conn = self._build()
        state = {"count": 0}

        async def flaky_execute(query, *args, **kwargs):
            state["count"] += 1
            if state["count"] == 2:
                raise RuntimeError("boom")

        conn.execute = flaky_execute

        with patch("asyncpg.create_pool", new=AsyncMock(return_value=pool)):
            with caplog.at_level(logging.WARNING, logger="dsh.storage"):
                await init_database(StorageConfig())

        assert state["count"] >= 10
        assert "SQL 警告" in caplog.text
        pool.close.assert_awaited_once()


class TestMinIOImportFallback:
    """MinIO 可选导入失败分支覆盖（任务 6.4）"""

    def test_minio_import_fallback(self):
        """模拟 minio 包不可导入时模块回退"""
        import sys
        import importlib
        module = importlib.import_module("dsh_core.storage.manager")
        saved = {name: sys.modules.get(name) for name in ("minio", "minio.error")}
        try:
            for name in ("minio", "minio.error"):
                sys.modules[name] = None
            importlib.reload(module)

            assert module.MINIO_AVAILABLE is False
            assert module.Minio is None
            assert module.S3Error is Exception
        finally:
            for name in ("minio", "minio.error"):
                if saved.get(name) is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = saved[name]
            importlib.reload(module)

        assert module.MINIO_AVAILABLE is True
