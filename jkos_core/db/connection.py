"""DSH 数据层 - SQLite 连接管理与版本化迁移

线程模型：单连接 + RLock 串行化写操作（MVP 规模 <10 写/s 足够，
ADR-1 推翻条件出现时整体替换为 PostgreSQL 连接池）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from jkos_core.db.schema import MIGRATIONS

logger = logging.getLogger("dsh.db")


def _close_conn_safe(conn: Optional[sqlite3.Connection]) -> None:
    """GC 兜底：宿主对象被回收时若连接未显式关闭，则在此关闭（防 ResourceWarning 泄漏）。"""
    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass


def utc_now() -> str:
    """ISO 8601 UTC 时间串（毫秒精度）——全库统一时间格式"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class DatabaseConfig:
    path: str = "/var/dsh/dsh.db"

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        import os

        return cls(path=os.getenv("DSH_DB_PATH", "/var/dsh/dsh.db"))


class Database:
    """SQLite 连接封装：WAL + 外键强制 + 事务上下文 + 版本化迁移"""

    def __init__(self, config: Optional[DatabaseConfig] = None):
        self.config = config or DatabaseConfig()
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    # ─── 生命周期 ───

    def connect(self) -> "Database":
        if self._conn is not None:
            return self
        path = self.config.path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # isolation_level=None：autocommit 模式——单条 execute 立即提交，
        # 避免隐式事务悬挂导致进程退出回滚（0.2 联调发现的跨连接不可见 bug）；
        # 多语句原子性一律走 transaction()（BEGIN IMMEDIATE）或 migrate()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        weakref.finalize(self, _close_conn_safe, self._conn)
        # WAL：并发读不阻塞写；NORMAL 在 WAL 下安全且更快；外键必须显式开启
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        mode = self._conn.execute("PRAGMA journal_mode").fetchone()[0]
        logger.info("SQLite 已连接: %s (journal=%s)", path, mode)
        return self

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ─── 基础操作 ───

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            self._ensure()
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure()
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """事务上下文：BEGIN IMMEDIATE 获取写锁，异常自动回滚"""
        with self._lock:
            self._ensure()
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def _ensure(self) -> None:
        if self._conn is None:
            raise RuntimeError("数据库未连接，请先调用 connect()")

    # ─── 版本化迁移 ───

    def migrate(self) -> List[int]:
        """按序应用未执行的迁移，返回本次应用的版本号（幂等，可重复调用）"""
        applied: List[int] = []
        with self._lock:
            self._ensure()
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                " version INTEGER PRIMARY KEY,"
                " description TEXT NOT NULL,"
                " applied_at TEXT NOT NULL)"
            )
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for version, description, statements in MIGRATIONS:
                if version <= current:
                    continue
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in statements:
                        self._conn.execute(stmt)
                    self._conn.execute(
                        "INSERT INTO schema_version (version, description, applied_at)"
                        " VALUES (?,?,?)",
                        (version, description, utc_now()),
                    )
                except Exception:
                    self._conn.rollback()
                    raise
                self._conn.commit()
                applied.append(version)
                logger.info("迁移 v%d 已应用: %s", version, description)
        return applied

    def version(self) -> int:
        row = self.query_one("SELECT MAX(version) AS v FROM schema_version")
        return int(row["v"] or 0) if row else 0


# ─── 连接池（M7 任务 7.1：大小可配置 / 泄漏检测 / 健康检查 / 监控指标）───

class ConnectionPoolTimeout(Exception):
    """连接池获取超时——全部连接被占用且未归还（连接泄漏会被此机制暴露）"""


class ConnectionPool:
    """SQLite 连接池（M7 任务 7.1）

    特性：
      - min_size / max_size 可配置，start() 时预热 min_size 个空闲连接；
      - acquire() 借用时做健康检查（SELECT 1），死连接自动剔除并新建；
      - 池满时等待 release() 信号，acquire_timeout 秒内未等到即抛出
        ConnectionPoolTimeout（连接泄漏检测：借用未归还 = 超时）；
      - metrics() 返回监控指标（空闲/借用/累计借用/等待/超时/剔除/创建）。
    """

    def __init__(
        self,
        config: Optional[DatabaseConfig] = None,
        *,
        min_size: int = 5,
        max_size: int = 50,
        acquire_timeout: float = 5.0,
    ):
        if min_size < 1 or max_size < 1 or min_size > max_size:
            raise ValueError(f"连接池大小配置非法: min_size={min_size}, max_size={max_size}")
        self.config = config or DatabaseConfig()
        self.min_size = min_size
        self.max_size = max_size
        self.acquire_timeout = acquire_timeout
        self._idle: "asyncio.Queue[Database]" = asyncio.Queue()
        self._active: Dict[int, Database] = {}  # id(conn) -> 借用中的连接
        self._condition = asyncio.Condition()
        self._started = False
        self._closed = False
        # 监控指标
        self._created = 0
        self._checkouts = 0
        self._waits = 0
        self._timeouts = 0
        self._evictions = 0

    def _connect(self) -> Database:
        conn = Database(self.config).connect()
        self._created += 1
        return conn

    def _is_healthy(self, conn: Database) -> bool:
        """健康检查：能执行 SELECT 1 视为存活"""
        try:
            conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def _close_conn(self, conn: Database) -> None:
        try:
            conn.close()
        except Exception:
            pass

    async def start(self) -> None:
        """预热 min_size 个空闲连接（幂等）"""
        if self._started or self._closed:
            return
        for _ in range(self.min_size):
            self._idle.put_nowait(self._connect())
        self._started = True

    async def acquire(self) -> Database:
        """借用连接；池满等待，超时抛 ConnectionPoolTimeout（泄漏检测）"""
        if self._closed:
            raise RuntimeError("连接池已关闭")
        if not self._started:
            await self.start()
        deadline = time.monotonic() + self.acquire_timeout
        while True:
            conn: Optional[Database] = None
            try:
                conn = self._idle.get_nowait()
            except asyncio.QueueEmpty:
                pass
            if conn is not None:
                if self._is_healthy(conn):
                    self._active[id(conn)] = conn
                    self._checkouts += 1
                    return conn
                # 死连接：健康检查剔除并关闭（自动重连）
                self._evictions += 1
                self._close_conn(conn)
            elif len(self._active) < self.max_size:
                conn = self._connect()
                self._active[id(conn)] = conn
                self._checkouts += 1
                return conn
            # 池满：等待 release() 释放连接
            self._waits += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._timeouts += 1
                raise ConnectionPoolTimeout(
                    f"连接池获取超时（{self.acquire_timeout}s）："
                    f"{len(self._active)}/{self.max_size} 连接被占用，疑似连接泄漏"
                )
            async with self._condition:
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    self._timeouts += 1
                    raise ConnectionPoolTimeout(
                        f"连接池获取超时（{self.acquire_timeout}s）："
                        f"{len(self._active)}/{self.max_size} 连接被占用，疑似连接泄漏"
                    ) from None

    async def release(self, conn: Database) -> None:
        """归还连接；死/超量连接自动剔除，池关闭时直接销毁"""
        key = id(conn)
        if key not in self._active:
            logger.warning("release 收到非本池借出的连接，忽略: %s", key)
            return
        del self._active[key]
        if self._closed:
            self._close_conn(conn)
        elif self._idle.qsize() >= self.max_size or not self._is_healthy(conn):
            self._evictions += 1
            self._close_conn(conn)
        else:
            self._idle.put_nowait(conn)
        async with self._condition:
            self._condition.notify()

    async def close(self) -> None:
        """关闭全部连接（借用中的视为泄漏，强制回收并告警）"""
        self._closed = True
        leaked = list(self._active.values())
        if leaked:
            logger.warning("close 时仍有 %d 个连接未归还（泄漏）", len(leaked))
        for conn in leaked:
            self._close_conn(conn)
        self._active.clear()
        while True:
            try:
                self._close_conn(self._idle.get_nowait())
            except asyncio.QueueEmpty:
                break
        self._started = False

    def metrics(self) -> Dict[str, Any]:
        """监控指标（M7 任务 7.1 验收）"""
        idle = self._idle.qsize()
        return {
            "min_size": self.min_size,
            "max_size": self.max_size,
            "idle": idle,
            "active": len(self._active),
            "total": idle + len(self._active),
            "created": self._created,
            "checkouts": self._checkouts,
            "waits": self._waits,
            "timeouts": self._timeouts,
            "evictions": self._evictions,
        }
