"""M7 任务 7.1：数据库连接池优化 — ConnectionPool / ConnectionPoolTimeout"""

import tempfile
from pathlib import Path

import pytest

from dsh_core.db.connection import ConnectionPool, ConnectionPoolTimeout, DatabaseConfig


async def test_pool_start_prefills_min_size_and_is_idempotent():
    """start：预热 min_size 个空闲连接（幂等）"""
    pool = ConnectionPool(DatabaseConfig(path=":memory:"), min_size=2, max_size=4)
    await pool.start()
    m = pool.metrics()
    assert m["idle"] == 2
    assert m["active"] == 0
    assert m["created"] == 2
    await pool.start()  # 幂等：不重复创建
    assert pool.metrics()["created"] == 2
    await pool.close()


async def test_pool_acquire_release_roundtrip():
    """acquire/release：借用后归还，连接可复用"""
    pool = ConnectionPool(DatabaseConfig(path=":memory:"), min_size=1, max_size=2)
    await pool.start()
    conn = await pool.acquire()
    m = pool.metrics()
    assert m["active"] == 1
    conn.execute("SELECT 1")
    await pool.release(conn)
    m = pool.metrics()
    assert m["active"] == 0
    assert m["idle"] == 1
    # 归还后可再次借用
    conn2 = await pool.acquire()
    conn2.execute("SELECT 1")
    await pool.release(conn2)
    assert pool.metrics()["checkouts"] == 2
    await pool.close()


async def test_pool_acquire_grows_up_to_max_size():
    """acquire：按需创建连接，上限为 max_size"""
    pool = ConnectionPool(DatabaseConfig(path=":memory:"), min_size=1, max_size=3)
    await pool.start()
    conns = [await pool.acquire() for _ in range(3)]
    assert pool.metrics()["active"] == 3
    assert pool.metrics()["created"] == 3
    for c in conns:
        await pool.release(c)
    await pool.close()


async def test_pool_acquire_timeout_raises():
    """池满且等待超时 → ConnectionPoolTimeout（占用唯一连接不归还）"""
    pool = ConnectionPool(DatabaseConfig(path=":memory:"), min_size=1, max_size=1,
                          acquire_timeout=0.2)
    await pool.start()
    conn = await pool.acquire()  # 占满唯一连接
    with pytest.raises(ConnectionPoolTimeout):
        await pool.acquire()
    m = pool.metrics()
    assert m["timeouts"] >= 1
    assert m["waits"] >= 1
    await pool.release(conn)
    await pool.close()


async def test_pool_acquire_after_close_raises():
    """close 后 acquire 抛 RuntimeError"""
    pool = ConnectionPool(DatabaseConfig(path=":memory:"), min_size=1, max_size=2)
    await pool.start()
    await pool.close()
    with pytest.raises(RuntimeError):
        await pool.acquire()


async def test_pool_close_recycles_leaked_connections(caplog):
    """close：借出未归还的连接视为泄漏，强制回收并告警"""
    pool = ConnectionPool(DatabaseConfig(path=":memory:"), min_size=1, max_size=2)
    await pool.start()
    conn = await pool.acquire()  # 不归还（泄漏）
    await pool.close()
    assert pool.metrics()["active"] == 0
    assert "泄漏" in caplog.text


async def test_pool_release_evicts_dead_connection():
    """release：健康检查失败的死连接被剔除（evictions +1）"""
    pool = ConnectionPool(DatabaseConfig(path=":memory:"), min_size=1, max_size=2)
    await pool.start()
    conn = await pool.acquire()
    conn._conn.close()  # 模拟连接失效（底层 sqlite 连接被外部关闭）
    await pool.release(conn)
    assert pool.metrics()["evictions"] == 1
    # 死连接被剔除后池内仍有可用连接
    conn2 = await pool.acquire()
    conn2.execute("SELECT 1")
    await pool.release(conn2)
    await pool.close()


async def test_pool_file_db_reuse_and_visibility():
    """文件库：连接池内多连接共享同一数据库（写后可见）"""
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "pool.db")
        pool = ConnectionPool(DatabaseConfig(path=path), min_size=1, max_size=2)
        await pool.start()
        conn = await pool.acquire()
        conn.execute("CREATE TABLE IF NOT EXISTS m7_t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO m7_t(v) VALUES ('a')")
        await pool.release(conn)
        conn2 = await pool.acquire()
        rows = conn2.query("SELECT v FROM m7_t")
        assert rows and rows[0]["v"] == "a"
        await pool.release(conn2)
        await pool.close()


def test_pool_invalid_size_raises():
    """非法池大小参数 → ValueError"""
    with pytest.raises(ValueError):
        ConnectionPool(min_size=0)
    with pytest.raises(ValueError):
        ConnectionPool(max_size=0)
    with pytest.raises(ValueError):
        ConnectionPool(min_size=5, max_size=3)


async def test_pool_metrics_fields():
    """metrics：M7 7.1 验收监控字段"""
    pool = ConnectionPool(DatabaseConfig(path=":memory:"), min_size=1, max_size=2)
    await pool.start()
    m = pool.metrics()
    assert set(m) == {"min_size", "max_size", "idle", "active", "total",
                      "created", "checkouts", "waits", "timeouts", "evictions"}
    assert m["min_size"] == 1
    assert m["max_size"] == 2
    await pool.close()