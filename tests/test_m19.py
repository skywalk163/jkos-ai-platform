"""DSH M19 验证：PostgreSQL 迁移层真实化 + NATS 总线真实化

- M19.1: PostgresMigrator 真实 SQLite->PostgreSQL DDL/数据转换，asyncpg 连接池
- M19.2: NatsEventBus 真实 nats-py 连接（缺失/不可达时降级内嵌内存模式）
"""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from jkos_core.bus.nats import (
    EventBusFactory,
    EventStatus,
    EventTypes,
    NatsEventBus,
    build_production_delay_event,
    build_sentiment_raised_event,
)
from jkos_core.db.postgres import (
    SQLITE_TO_PG_TYPE_MAP,
    ConnectionPoolManager,
    DatabaseType,
    MigrationStatus,
    PostgresConfig,
    PostgresMigrator,
    create_postgres_migrator,
)


# ─── M19.1 公共辅助 ───

def _pg_config() -> PostgresConfig:
    return PostgresConfig(host="127.0.0.1", port=1,
                          database="dsh_ai", username="dsh_user", password="p")


def _make_migrator(source_sqlite_path: Path) -> PostgresMigrator:
    return PostgresMigrator(_pg_config(), source_sqlite_path=str(source_sqlite_path))


SAMPLE_DDL = """CREATE TABLE orders (
    id TEXT PRIMARY KEY,
    order_no VARCHAR(64) NOT NULL UNIQUE,
    quantity INTEGER NOT NULL DEFAULT 0,
    total_amount DECIMAL(12,2) DEFAULT 0.00,
    is_urgent BOOLEAN NOT NULL DEFAULT 0,
    batch_config JSON NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);"""


@pytest.fixture
def sqlite_source(tmp_path: Path) -> Path:
    """临时 SQLite 源库：含索引与 RAISE 触发器"""
    db_path = tmp_path / "source.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE orders (
            id TEXT PRIMARY KEY,
            order_no TEXT NOT NULL UNIQUE,
            quantity INTEGER NOT NULL DEFAULT 0,
            batch_config TEXT NOT NULL,
            note TEXT,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX idx_orders_order_no ON orders(order_no);
        CREATE INDEX idx_orders_created ON orders(created_at);
        CREATE TRIGGER trg_orders_note BEFORE UPDATE ON orders
        BEGIN SELECT CASE WHEN NEW.note IS NULL THEN RAISE(ABORT, 'note required') END; END;
    """)
    conn.executemany(
        "INSERT INTO orders (id, order_no, quantity, batch_config, note) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("o1", "PO-2026-001", 100, '{"line": "A"}', "first"),
            ("o2", "PO-2026-002", 50, '{"line": "B"}', "O'Reilly"),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


# ─── M19.1: PostgreSQL 迁移层 ───

def test_m19_1_type_map():
    assert SQLITE_TO_PG_TYPE_MAP["INTEGER"] == "INTEGER"
    assert SQLITE_TO_PG_TYPE_MAP["DATETIME"] == "TIMESTAMP"
    assert SQLITE_TO_PG_TYPE_MAP["JSON"] == "JSONB"
    assert SQLITE_TO_PG_TYPE_MAP["BOOLEAN"] == "BOOLEAN"
    assert SQLITE_TO_PG_TYPE_MAP["BLOB"] == "BYTEA"


def test_m19_1_generate_from_ddl_string(tmp_path: Path):
    migrator = PostgresMigrator(_pg_config())
    statements = migrator.generate_migration_sql("orders", sqlite_ddl=SAMPLE_DDL)
    text = "\n".join(statements)
    assert "CREATE TABLE IF NOT EXISTS orders" in text
    assert '"order_no" VARCHAR(64) NOT NULL UNIQUE' in text
    assert '"quantity" INTEGER NOT NULL DEFAULT 0' in text
    assert '"total_amount" DECIMAL(12,2) DEFAULT 0.00' in text
    assert "BOOLEAN NOT NULL" in text
    assert "JSONB NOT NULL" in text
    assert '"created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP' in text


def test_m19_1_generate_autoincrement(tmp_path: Path):
    migrator = PostgresMigrator(_pg_config())
    statements = migrator.generate_migration_sql(
        "counter",
        sqlite_ddl="CREATE TABLE counter (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);",
    )
    text = "\n".join(statements)
    assert '"id" SERIAL PRIMARY KEY' in text


def test_m19_1_generate_from_sqlite_source(sqlite_source: Path):
    migrator = _make_migrator(sqlite_source)
    statements = migrator.generate_migration_sql("orders")
    text = "\n".join(statements)
    assert "CREATE TABLE IF NOT EXISTS orders" in text
    assert "TIMESTAMP NOT NULL" in text
    assert "CREATE INDEX IF NOT EXISTS idx_orders_created" in text
    assert "RAISE" in text  # SQLite RAISE 触发器以注释形式保留


def test_m19_1_generate_data_sql(sqlite_source: Path):
    migrator = _make_migrator(sqlite_source)
    statements = migrator.generate_data_migration_sql("orders")
    text = "\n".join(statements)
    assert "INSERT INTO orders" in text
    assert "PO-2026-001" in text
    assert "O''Reilly" in text  # 单引号转义


def test_m19_1_generate_data_sql_without_source(tmp_path: Path):
    migrator = PostgresMigrator(_pg_config())
    statements = migrator.generate_data_migration_sql("orders")
    assert any(statement.startswith("--") for statement in statements)


def test_m19_1_execute_migration_artifacts(tmp_path: Path, sqlite_source: Path):
    migrator = _make_migrator(sqlite_source)
    plan = migrator.create_migration_plan(DatabaseType.SQLITE, ["orders"])
    assert migrator.execute_migration(plan.id) is True
    assert plan.status == MigrationStatus.COMPLETED
    assert plan.completed_at is not None
    paths = migrator.write_migration_artifacts(plan.id, str(tmp_path / "out"))
    assert len(paths) == 1
    content = Path(paths[0]).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS orders" in content
    assert "INSERT INTO orders" in content


def test_m19_1_execute_migration_missing_plan():
    migrator = create_postgres_migrator(_pg_config())
    assert migrator.execute_migration("missing") is False


def test_m19_1_sync_pool_contract():
    manager = ConnectionPoolManager(PostgresConfig(pool_size=5))
    assert manager.get_pool()["size"] == 5
    stats = manager.get_stats()
    assert "pool_count" in stats
    assert "async_pool_count" in stats
    assert "config" in stats


async def test_m19_1_async_pool_unreachable():
    # 端口 1 必然拒绝连接 -> 应抛 ConnectionError
    manager = ConnectionPoolManager(_pg_config())
    with pytest.raises(ConnectionError):
        await manager.get_async_pool()
    await manager.close_all_async()


# ─── M19.2 公共辅助：FakeNATS 连接 ───

class FakeNatsSubscription:
    def __init__(self, subject, queue, cb):
        self.subject = subject
        self.queue = queue
        self.cb = cb
        self.unsubscribed = False

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class FakeNatsConnection:
    def __init__(self, fail_publish: bool = False):
        self.published = []
        self.subscriptions = []
        self.closed = False
        self.fail_publish = fail_publish

    async def publish(self, subject: str, payload: bytes) -> None:
        if self.fail_publish:
            raise RuntimeError("simulated NATS publish failure")
        self.published.append((subject, payload))

    async def subscribe(self, subject: str, queue=None, cb=None):
        sub = FakeNatsSubscription(subject, queue, cb)
        self.subscriptions.append(sub)
        return sub

    async def close(self) -> None:
        self.closed = True


# ─── M19.2: NATS 总线 ───

async def test_m19_2_connect_embedded_when_nats_missing(monkeypatch):
    # 模拟 nats-py 不可用：替换 sys.modules["nats"] 为 stub，
    # 使 NatsEventBus.connect() 内的 `import nats` 解析到抛出异常的 connect，
    # 从而确定性地（不依赖真实端口/网络行为）走到 embedded 降级路径。
    import sys
    from types import ModuleType

    fake_nats = ModuleType("nats")

    async def _connect_fail(*args, **kwargs):
        raise ConnectionError("simulated: NATS unreachable")

    fake_nats.connect = _connect_fail
    monkeypatch.setitem(sys.modules, "nats", fake_nats)

    bus = NatsEventBus(nats_url="nats://127.0.0.1:4222")
    await bus.connect()
    assert bus._connected is True
    assert bus._nc is None
    assert bus.mode == "embedded"
    event = build_sentiment_raised_event("t1", "high", 0.9, "weibo", "bad")
    assert await bus.publish(event) is True
    assert event.status == EventStatus.PUBLISHED
    await bus.disconnect()
    assert bus._connected is False


async def test_m19_2_publish_real_nats():
    bus = NatsEventBus()
    fake = FakeNatsConnection()
    bus._nc = fake
    bus._connected = True
    event = build_production_delay_event("t1", "PO-2026-001", 3)
    assert await bus.publish(event) is True
    assert len(fake.published) == 1
    subject, payload = fake.published[0]
    assert subject == EventTypes.PRODUCTION_DELAY
    envelope = json.loads(payload.decode())
    assert envelope["id"] == event.id
    assert envelope["type"] == EventTypes.PRODUCTION_DELAY
    assert envelope["payload"]["order_no"] == "PO-2026-001"
    assert bus.get_event(event.id) is not None


async def test_m19_2_publish_real_failure():
    bus = NatsEventBus()
    bus._nc = FakeNatsConnection(fail_publish=True)
    bus._connected = True
    event = build_production_delay_event("t1", "PO-9", 1)
    assert await bus.publish(event) is False
    assert event.status == EventStatus.PENDING
    assert bus.get_event(event.id) is None


async def test_m19_2_subscribe_with_queue_group():
    bus = NatsEventBus()
    fake = FakeNatsConnection()
    bus._nc = fake
    bus._connected = True

    async def handler(event):
        pass

    sub = await bus.subscribe(EventTypes.WORKFLOW_STARTED, handler,
                              queue_group="workers")
    assert sub.durable is True
    assert len(fake.subscriptions) == 1
    assert fake.subscriptions[0].subject == EventTypes.WORKFLOW_STARTED
    assert fake.subscriptions[0].queue == "workers"
    # 取消订阅应联动关闭真实 NATS 订阅
    assert await bus.unsubscribe(sub.id) is True
    assert fake.subscriptions[0].unsubscribed is True


async def test_m19_2_unsubscribe_absent():
    bus = NatsEventBus()
    bus._nc = FakeNatsConnection()
    assert await bus.unsubscribe("missing") is False


async def test_m19_2_disconnect_closes_real():
    bus = NatsEventBus()
    fake = FakeNatsConnection()
    bus._nc = fake
    await bus.disconnect()
    assert fake.closed is True
    assert bus._nc is None
    assert bus._connected is False


async def test_m19_2_envelope_roundtrip_via_callback():
    """NATS 远端消息经订阅回调 -> 信封解码 -> handle_event 幂等分发"""
    bus = NatsEventBus()
    fake = FakeNatsConnection()
    bus._nc = fake
    bus._connected = True
    handled = []

    async def handler(event):
        handled.append(event)

    await bus.subscribe(EventTypes.PRODUCTION_DELAY, handler,
                        queue_group="workers")
    event = build_production_delay_event("t1", "PO-2026-001", 3)
    envelope = json.dumps(bus._event_to_dict(event)).encode()
    assert len(fake.subscriptions) == 1
    await fake.subscriptions[0].cb(SimpleNamespace(data=envelope))
    assert len(handled) == 1
    assert handled[0].payload["order_no"] == "PO-2026-001"


async def test_m19_2_factory_env_url(monkeypatch):
    monkeypatch.setenv("DSH_NATS_URL", "nats://custom-host:4223")
    bus = EventBusFactory.create()
    assert isinstance(bus, NatsEventBus)
    assert bus.nats_url == "nats://custom-host:4223"


async def test_m19_2_connect_real_nats_when_available():
    """nats 客户端可用且服务器可达时进入真实模式（此处环境跳过）"""
    nats = pytest.importorskip("nats")
    bus = NatsEventBus("nats://localhost:4222")
    try:
        await bus.connect()
    except Exception:
        pytest.skip("无 NATS 服务，跳过真实连接冒烟")
    try:
        assert bus.mode in ("nats", "embedded")
        assert bus._connected is True
    finally:
        await bus.disconnect()