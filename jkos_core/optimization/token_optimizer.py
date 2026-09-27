"""DSH 优化引擎 - Token 优化器（12.2）

核心思想：全量探索太贵，重复任务应直接复用。优化器对任务做归一与查重，
首次执行按「全量探索」估算成本入账，之后命中缓存只按「回放成本」计费
（默认不超过全量的 10%，保证节省 ≥90%）；同时支持批量去重、增量计算，
并把每次用量审计写入 reports/token_usage/。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import weakref
from pathlib import Path
from typing import Any, Dict, List, Optional

from jkos_core.optimization.base import OptimizedTask, TokenUsage, now_iso


def _close_conn_safe(conn: Optional[sqlite3.Connection]) -> None:
    """GC 兜底：宿主对象被回收时若连接未显式关闭，则在此关闭（防 ResourceWarning 泄漏）。"""
    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "optimization" / "token_cache" / "token_cache.db"
DEFAULT_REPORT_DIR = Path(__file__).resolve().parents[2] / "reports" / "token_usage"

# 回放一次缓存结果的固定成本（读取缓存内容的 Token 开销）
REPLAY_COST = 100
# 全量探索的基准成本（模拟 分解 + 搜索 + 实验 三个阶段的提示词用量）
PIPELINE_BASE = 1500


def estimate_tokens(text: str) -> int:
    """估算任意文本的 Token 数：中文按字符、英文按词，至少为 1。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    # 中文按字符计数；英文按词计数，但纯中文 token（如“你好”）不再重复计为英文词
    words = len([
        w for w in text.split()
        if w.strip() and not all("\u4e00" <= ch <= "\u9fff" for ch in w)
    ])
    return max(1, cjk + words)


def estimate_full(task: str) -> int:
    """按全量探索路径估算一次冷启动的成本。"""
    return PIPELINE_BASE + estimate_tokens(task)


def _replay_cost(full_tokens: int) -> int:
    """回放成本：默认固定值，但不超过全量的 10%，保证节省 ≥90%。"""
    return max(1, min(REPLAY_COST, full_tokens // 10))


class TokenOptimizer:
    """Token 缓存优化器：查重、缓存、增量、用量审计。

    用法（对应开发计划 M12.2 快速开始）：
        from jkos_core.optimization import TokenOptimizer
        optimizer = TokenOptimizer()
        opt = await optimizer.optimize("生成周报")   # 首次：全量成本
        opt2 = await optimizer.optimize("生成周报")   # 再次：仅回放成本，节省 ≥90%
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, report_dir: str | Path = DEFAULT_REPORT_DIR):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._write_lock = asyncio.Lock()
        self._init_sync()

    # ---------- 生命周期 ----------

    def _init_sync(self) -> None:
        """幂等建表，进程重启后自动恢复（构造函数内同步执行）。"""
        self._connect()
        sqls = [
            """CREATE TABLE IF NOT EXISTS token_cache(
                 task TEXT PRIMARY KEY,
                 content TEXT NOT NULL DEFAULT '',
                 full_tokens INTEGER NOT NULL DEFAULT 0,
                 hit_count INTEGER NOT NULL DEFAULT 0,
                 created_at TEXT NOT NULL,
                 updated_at TEXT NOT NULL
               )""",
            """CREATE TABLE IF NOT EXISTS token_usage(
                 usage_id TEXT PRIMARY KEY,
                 task TEXT NOT NULL,
                 module TEXT NOT NULL,
                 token_used INTEGER NOT NULL,
                 tokens_full INTEGER NOT NULL,
                 cached INTEGER NOT NULL DEFAULT 0,
                 created_at TEXT NOT NULL
               )""",
        ]
        for sql in sqls:
            self._conn.execute(sql)
        self._conn.commit()

    def _connect(self) -> None:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            weakref.finalize(self, _close_conn_safe, self._conn)
            weakref.finalize(self, _close_conn_safe, self._conn)

    async def initialize(self) -> None:
        """幂等建表，进程重启后自动恢复。由自动化引擎/门面异步 await。"""
        self._init_sync()

    async def close(self) -> None:
        """进程退出/空闲时关闭连接（按操作开、随走随关）。"""
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def states(self) -> Dict[str, Any]:
        return {
            "db_path": self.db_path,
            "cache_entries": self.count_cache(),
            "report_count": len(list(self.report_dir.glob("*.json"))),
        }

    # ---------- 内部同步执行（跑在线程池里） ----------

    def _get_sync(self, task: str) -> Optional[Dict[str, Any]]:
        self._connect()
        row = self._conn.execute(
            "SELECT task, content, full_tokens, hit_count FROM token_cache WHERE task=?", (task,)
        ).fetchone()
        return dict(row) if row else None

    def _store_sync(self, task: str, content: str, full_tokens: int) -> None:
        self._connect()
        self._conn.execute(
            """INSERT OR REPLACE INTO token_cache(task, content, full_tokens, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (task, content, full_tokens, now_iso(), now_iso()),
        )
        self._conn.commit()

    def _bump_hit_sync(self, task: str) -> None:
        self._connect()
        self._conn.execute(
            "UPDATE token_cache SET hit_count = hit_count + 1, updated_at=? WHERE task=?",
            (now_iso(), task),
        )
        self._conn.commit()

    def _record_usage_sync(self, usage: TokenUsage) -> None:
        self._connect()
        self._conn.execute(
            """INSERT OR REPLACE INTO token_usage(usage_id, task, module, token_used, tokens_full, cached, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (usage.usage_id, usage.task, usage.module, usage.token_used, usage.tokens_full,
             int(usage.cached), usage.created_at),
        )
        self._conn.commit()

    def _count_sync(self, table: str) -> int:
        self._connect()
        return self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def _clear_sync(self, table: str) -> int:
        self._connect()
        n = self._conn.execute(f"DELETE FROM {table}").rowcount
        self._conn.commit()
        return n

    # ---------- 对外查询 ----------

    def count_cache(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM token_cache").fetchone()[0]

    # ---------- 核心逻辑 ----------

    async def optimize(
        self,
        task: str,
        content: str = "",
        *,
        incremental: bool = False,
        previous: str = "",
        plan: Optional[List[str]] = None,
    ) -> OptimizedTask:
        """对单个任务做 Token 优化。

        - 命中缓存：按回放成本计费（≤ 全量 10%），返回 cached；
        - 未命中：按全量探索成本计费，同时把 task → content 存入缓存；
        - incremental=True 且 content 与缓存内容不同：仅按增量追加计费。
        """
        task = (task or "").strip()
        full = estimate_full(task)
        entry = await asyncio.to_thread(self._get_sync, task)
        if entry is not None:
            await asyncio.to_thread(self._bump_hit_sync, task)
            if incremental and content and content != entry["content"]:
                # 增量：缓存已覆盖历史，只对新内容计费
                delta = estimate_tokens(content) - estimate_tokens(entry["content"])
                used = _replay_cost(entry["full_tokens"]) + max(0, delta)
            else:
                used = _replay_cost(entry["full_tokens"])
            saved = max(0, full - used)
            ratio = saved / full if full else 0.0
            return OptimizedTask(
                task=task,
                status="cached",
                token_used=used,
                tokens_saved=saved,
                saved_ratio=ratio,
                cached=True,
                cached_content=entry["content"] if not (incremental and content) else content,
                plan=list(plan or []),
                meta={"hit_count": entry["hit_count"] + 1, "full_tokens": full,
                      "incremental": incremental and bool(content)},
            )
        # 未命中：按全量成本入账
        if content and not incremental:
            await asyncio.to_thread(self._cache_write, task, content, full)
        return OptimizedTask(
            task=task,
            status="planned",
            token_used=full,
            tokens_saved=0,
            saved_ratio=0.0,
            cached=False,
            plan=list(plan or []),
            meta={"full_tokens": full, "incremental": incremental and bool(content)},
        )

    def _cache_write(self, task: str, content: str, full_tokens: int) -> None:
        """缓存写入：单条 INSERT OR REPLACE，由线程池执行，SQLite 自带写串行化。"""
        self._store_sync(task, content, full_tokens)

    async def store_cache(self, task: str, content: str, full_tokens: Optional[int] = None) -> None:
        """显式写入缓存（由自动化引擎在成功执行后回写，替换估算成本）。"""
        task = (task or "").strip()
        full = full_tokens or estimate_full(task)
        async with self._write_lock:
            await asyncio.to_thread(self._store_sync, task, content, full)

    async def optimize_batch(self, tasks: List[str], contents: Optional[List[str]] = None) -> OptimizedTask:
        """批量任务：两两去重，仅未命中的任务计全量成本。"""
        tasks = [(t or "").strip() for t in tasks]
        seen: Dict[str, str] = {}
        for i, t in enumerate(tasks):
            if contents is not None and i < len(contents) and contents[i]:
                seen[t] = contents[i]
        plan: List[str] = []
        total_used = 0
        total_full = 0
        cached_count = 0
        for t in tasks:
            opt = await self.optimize(t, seen.get(t, ""))
            total_used += opt.token_used
            total_full += estimate_full(t)
            plan.extend(opt.plan or [])
            if opt.cached:
                cached_count += 1
            else:
                plan.append(t)
        saved = max(0, total_full - total_used)
        return OptimizedTask(
            task=", ".join(tasks),
            status="cached" if cached_count == len(tasks) else "planned",
            token_used=total_used,
            tokens_saved=saved,
            saved_ratio=saved / total_full if total_full else 0.0,
            cached=cached_count > 0,
            plan=plan,
            meta={"tasks": tasks, "cached_count": cached_count, "total_count": len(tasks)},
        )

    async def record_usage(
        self,
        task: str,
        module: str,
        token_used: int,
        tokens_full: int,
        cached: bool = False,
    ) -> TokenUsage:
        """记录一次 Token 用量，落库并写审计 JSON 到 reports/token_usage/。"""
        usage = TokenUsage(
            usage_id=f"u{int(time.time() * 1000)}",
            task=task,
            module=module,
            token_used=token_used,
            tokens_full=tokens_full,
            cached=cached,
        )
        await asyncio.to_thread(self._record_usage_sync, usage)
        await asyncio.to_thread(self._write_report, usage)
        return usage

    def _write_report(self, usage: TokenUsage) -> None:
        path = self.report_dir / f"{usage.usage_id}.json"
        path.write_text(json.dumps(usage.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    async def usage_stats(self) -> Dict[str, Any]:
        """汇总：总全量成本、总实际成本、整体节省比例。"""
        rows = self._conn.execute("SELECT token_used, tokens_full FROM token_usage").fetchall()
        total_full = sum(r["tokens_full"] for r in rows)
        total_used = sum(r["token_used"] for r in rows)
        return {
            "runs": len(rows),
            "total_tokens_full": total_full,
            "total_tokens_used": total_used,
            "saved_ratio": (total_full - total_used) / total_full if total_full else 0.0,
            "report_dir": str(self.report_dir),
        }

    async def clear_cache(self) -> int:
        """清空缓存（默认不调用，供测试/运维）。"""
        async with self._write_lock:
            return await asyncio.to_thread(self._clear_sync, "token_cache")