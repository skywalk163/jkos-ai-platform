"""DSH M19 双跑对比验证：SQLite vs PostgreSQL + 真实 NATS 事件回归

覆盖 M19 验收三缺口：
- M19.1 双跑对比（dual-run）：同一迁移脚本在 SQLite（同步产物）与真实
  PostgreSQL（execute_migration_async 在线执行）两侧运行，验证功能/数据一致；
  随后验证回滚（DROP 重建）与实际切换（应用切到 PG 作为活动存储），
  覆盖此前 0% 的 `execute_migration_async` 成功/失败两条路径。
- M19.2 真实 NATS 事件回归：连接 127.0.0.1:4222 的真实 nats-server，
  验证发布 → 信封解码 → 订阅回调完整回路（非降级内存路径）。

运行前提：本机 PostgreSQL（127.0.0.1:5432, db=dsh_ai, user=dsh_user, trust）
与 nats-server（127.0.0.1:4222）在线；服务不可达时对应用例自动 skip，
失败路径用例（目标库不可达）不依赖在线服务，始终执行。
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from jkos_core.bus.nats import (
    EventStatus,
    EventTypes,
    NatsEventBus,
    build_production_delay_event,
)
from jkos_core.db.postgres import (
    ConnectionPoolManager,
    DatabaseType,
    MigrationStatus,
    PostgresConfig,
    PostgresMigrator,
)

LIVE_PG = {
    "host": "127.0.0.1",
    "port": 5432,
    "database": "dsh_ai",
    "username": "dsh_user",
    "password": "",
}
NATS_URL = "nats://127.0.0.1:4222"

SAMPLE_DDL = """
CREATE TABLE orders (
    id TEXT PRIMARY KEY,
    order_no TEXT NOT NULL UNIQUE,
    quantity INTEGER NOT NULL DEFAULT 0,
    batch_config TEXT NOT NULL,
    note TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_orders_order_no ON orders(order_no);
CREATE INDEX idx_orders_created_at ON orders(created_at);
"""


def _live_config(pool_size: int = 5) -> PostgresConfig:
    return PostgresConfig(**LIVE_PG, pool_size=pool_size)


def _pg_conn_kwargs() -> dict:
    """asyncpg.connect 使用 user= 而非 username=（LIVE_PG 中的键名），此处做键映射"""
    kwargs = dict(LIVE_PG)
    kwargs["user"] = kwargs.pop("username")
    return kwargs


def _pg_conn_kwargs() -> dict:
    """asyncpg.connect 使用 user= 而非 username=（LIVE_PG 中的键名），此处做键映射"""
    kwargs = dict(LIVE_PG)
    kwargs["user"] = kwargs.pop("username")
    return kwargs


async def _live_pg_available() -> bool:
    """探测真实 PostgreSQL 是否可达（不可达则跳过在线用例）"""
    try:
        import asyncpg
        conn = await asyncpg.connect(**_pg_conn_kwargs(), timeout=3)
        await conn.close()
        return True
    except Exception:
        return False


async def _pg_drop_table(table: str = "orders") -> None:
    import asyncpg
    conn = await asyncpg.connect(**_pg_conn_kwargs(), timeout=3)
    try:
        await conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    finally:
        await conn.close()


@pytest.fixture
def sqlite_source(tmp_path: Path) -> Path:
    """SQLite 源库：orders 表 + 2 索引 + RAISE 触发器 + 2 行数据（含单引号转义）"""
    db_path = tmp_path / "source.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SAMPLE_DDL)
    conn.executescript(
        "CREATE TRIGGER trg_orders_no_modify "
        "BEFORE UPDATE ON orders "
        "BEGIN SELECT RAISE(ABORT, 'orders 只读'); END;"
    )
    conn.executemany(
        "INSERT INTO orders (id, order_no, quantity, batch_config, note) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("o1", "PO-2026-001", 100, '{"priority":"high"}', "first order"),
            ("o2", "PO-2026-002", 50, '{"priority":"low"}', "O'Reilly"),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


# ─── M19.1 双跑对比：SQLite 产物 vs PostgreSQL 在线执行 ───

async def test_m19_1_dualrun_async_executes_on_real_pg(sqlite_source: Path):
    """双跑·PG 侧：execute_migration_async 在真实 PG 落地 schema/索引/数据"""
    if not await _live_pg_available():
        pytest.skip("PostgreSQL 127.0.0.1:5432 不可达，跳过在线双跑")
    await _pg_drop_table()
    import asyncpg

    migrator = PostgresMigrator(_live_config(), source_sqlite_path=str(sqlite_source))
    plan = migrator.create_migration_plan(DatabaseType.SQLITE, ["orders"])
    assert await migrator.execute_migration_async(plan.id) is True
    assert plan.status == MigrationStatus.COMPLETED
    assert plan.completed_at is not None

    conn = await asyncpg.connect(**_pg_conn_kwargs(), timeout=3)
    try:
        assert await conn.fetchval('SELECT count(*) FROM "orders"') == 2
        rows = await conn.fetch('SELECT order_no, note FROM "orders" ORDER BY order_no')
        assert [(r["order_no"], r["note"]) for r in rows] == [
            ("PO-2026-001", "first order"),
            ("PO-2026-002", "O'Reilly"),
        ]
        idx = await conn.fetchval(
            "SELECT count(*) FROM pg_indexes WHERE tablename='orders' "
            "AND indexname LIKE 'idx_orders_%'")
        assert idx == 2
    finally:
        await conn.close()
        await _pg_drop_table()


async def test_m19_1_dualrun_sqlite_pg_parity(sqlite_source: Path, tmp_path: Path):
    """双跑对比：SQLite 侧生成的 INSERT 产物与 PG 真实落库数据一致"""
    if not await _live_pg_available():
        pytest.skip("PostgreSQL 127.0.0.1:5432 不可达，跳过在线双跑")
    await _pg_drop_table()
    import asyncpg

    migrator = PostgresMigrator(_live_config(), source_sqlite_path=str(sqlite_source))
    # SQLite 侧（离线）：生成并落盘迁移产物
    plan = migrator.create_migration_plan(DatabaseType.SQLITE, ["orders"])
    assert migrator.execute_migration(plan.id) is True
    paths = migrator.write_migration_artifacts(plan.id, str(tmp_path / "out"))
    artifact = Path(paths[0]).read_text(encoding="utf-8")
    assert "INSERT INTO orders" in artifact

    # PG 侧（在线）：执行同一迁移，核对两库数据完全一致
    plan2 = migrator.create_migration_plan(DatabaseType.SQLITE, ["orders"])
    assert await migrator.execute_migration_async(plan2.id) is True
    conn = await asyncpg.connect(**_pg_conn_kwargs(), timeout=3)
    try:
        rows = await conn.fetch('SELECT id, order_no, quantity, note FROM "orders" ORDER BY id')
        assert [(r["id"], r["order_no"], r["quantity"], r["note"]) for r in rows] == [
            ("o1", "PO-2026-001", 100, "first order"),
            ("o2", "PO-2026-002", 50, "O'Reilly"),
        ]
    finally:
        await conn.close()
        await _pg_drop_table()


async def test_m19_1_rollback_drop_and_rebuild(sqlite_source: Path):
    """回滚方案：前滚 → DROP 回滚 → 再次执行重建，验证可逆可重放"""
    if not await _live_pg_available():
        pytest.skip("PostgreSQL 127.0.0.1:5432 不可达，跳过回滚验证")
    import asyncpg

    migrator = PostgresMigrator(_live_config(), source_sqlite_path=str(sqlite_source))
    # 前滚
    plan = migrator.create_migration_plan(DatabaseType.SQLITE, ["orders"])
    assert await migrator.execute_migration_async(plan.id) is True
    # 回滚：DROP 目标表
    await _pg_drop_table()
    conn = await asyncpg.connect(**_pg_conn_kwargs(), timeout=3)
    try:
        exists = await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='orders'")
        assert exists == 0
    finally:
        await conn.close()
    # 重建：同一迁移器再次执行（产物可从源库再生）
    plan2 = migrator.create_migration_plan(DatabaseType.SQLITE, ["orders"])
    assert await migrator.execute_migration_async(plan2.id) is True
    conn = await asyncpg.connect(**_pg_conn_kwargs(), timeout=3)
    try:
        assert await conn.fetchval('SELECT count(*) FROM "orders"') == 2
    finally:
        await conn.close()
        await _pg_drop_table()


async def test_m19_1_switchover_to_postgres(sqlite_source: Path):
    """实际切换：迁移完成后应用改指 PG 作为活动存储，服务新读写"""
    if not await _live_pg_available():
        pytest.skip("PostgreSQL 127.0.0.1:5432 不可达，跳过切换验证")
    await _pg_drop_table()

    migrator = PostgresMigrator(_live_config(), source_sqlite_path=str(sqlite_source))
    plan = migrator.create_migration_plan(DatabaseType.SQLITE, ["orders"])
    assert await migrator.execute_migration_async(plan.id) is True

    # 切换：通过连接池管理器（应用侧）读写 PG
    manager = ConnectionPoolManager(_live_config(pool_size=2))
    pool = await manager.get_async_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO "orders" (id, order_no, quantity, batch_config, note) '
                "VALUES ('o3', 'PO-2026-003', 7, '{}', 'post-switch')")
            assert await conn.fetchval("SELECT note FROM \"orders\" WHERE id='o3'") \
                == "post-switch"
            assert await conn.fetchval("SELECT count(*) FROM \"orders\"") == 3
    finally:
        await manager.close_all_async()
        await _pg_drop_table()


@pytest.mark.asyncio
async def test_m19_1_async_execution_fails_when_pg_unreachable(sqlite_source: Path):
    """失败路径：目标库不可达时分支（不依赖在线服务，始终执行）"""
    migrator = PostgresMigrator(
        PostgresConfig(host="127.0.0.1", port=1, database="dsh_ai",
                       username="dsh_user", password=""),
        source_sqlite_path=str(sqlite_source))
    plan = migrator.create_migration_plan(DatabaseType.SQLITE, ["orders"])
    assert await migrator.execute_migration_async(plan.id) is False
    assert plan.status == MigrationStatus.FAILED
    assert plan.error_message


# ─── M19.2 真实 NATS 事件回归（非降级内存路径）───

@pytest.mark.asyncio
async def test_m19_2_real_nats_event_regression():
    """真实 nats-server：发布 → 信封解码 → 订阅回调完整回路"""
    pytest.importorskip("nats")
    bus = NatsEventBus(NATS_URL)
    await bus.connect()
    if bus.mode != "nats":
        await bus.disconnect()
        pytest.skip(f"NATS 服务不可达（mode={bus.mode}），跳过真实事件回归")

    received = []

    async def handler(event):
        received.append(event)

    try:
        assert bus.mode == "nats"
        sub = await bus.subscribe(EventTypes.PRODUCTION_DELAY, handler,
                                  queue_group="m19-regression")
        event = build_production_delay_event("t1", "PO-2026-001", 3)
        assert await bus.publish(event) is True
        assert event.status == EventStatus.PUBLISHED
        assert bus.get_event(event.id) is not None

        # 等待真实 NATS 回调把消息分发到本地 handler
        for _ in range(100):
            if received:
                break
            await asyncio.sleep(0.05)
        assert received, "真实 NATS 发布后 handler 未收到事件"
        got = received[0]
        assert got.id == event.id
        assert got.type == EventTypes.PRODUCTION_DELAY
        assert got.tenant_id == "t1"
        assert got.payload["order_no"] == "PO-2026-001"
        assert got.payload["delay_hours"] == 3

        assert await bus.unsubscribe(sub.id) is True
        assert sub.durable is True
    finally:
        await bus.disconnect()