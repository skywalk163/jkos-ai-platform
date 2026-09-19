"""DSH 优化引擎 - 流程固化引擎（12.1）

把探索引擎（M11）产出的 ExploreResult 固化为可重复执行的流程（Process）：
提取关键步骤 → 生成执行动作 → 添加异常处理与校验 → 落库持久化，
并支持版本管理与回滚（升级生成新版本，回滚恢复上一个版本的动作序列）。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from jkos_core.optimization.base import Process, ProcessStep, now_iso

DEFAULT_DB_PATH = (
    Path(__file__).resolve().parents[1] / "optimization" / "process_cache" / "process_cache.db"
)

# 固化流程末尾追加的校验步骤，防止产物不符合任务目标
VERIFY_STEP_ACTION = "校验流程输出与任务目标是否一致，不一致则走异常处理回退"


def _extract_steps(exploration: Any) -> List[ProcessStep]:
    """从 ExploreResult 提取关键步骤序列。

    优先取 Solution.steps（探索结论中的执行步骤文案），
    否则按 Subtask 的 name/description 推导；最后追加校验步骤。
    """
    actions: List[str] = []
    names: List[str] = []
    solution = getattr(exploration, "solution", None)
    if solution is not None and getattr(solution, "steps", None):
        for s in solution.steps:
            actions.append(str(s or "").strip())
            names.append("")
    else:
        subtasks = getattr(exploration, "subtasks", None) or []
        for st in subtasks:
            actions.append(str(getattr(st, "description", "") or "").strip())
            names.append(str(getattr(st, "name", "") or "").strip())
    actions = [a for a in actions if a]

    steps: List[ProcessStep] = []
    for i, action in enumerate(actions):
        steps.append(
            ProcessStep(
                name=f"step_{i + 1}",
                action=action,
                params={},
                depends_on=[steps[-1].name] if i > 0 else [],
                status="pending",
            )
        )
        if names[i]:
            steps[-1].name = names[i]
        if i == 0:
            steps[-1].depends_on = []
            continue
        steps[-1].depends_on = [steps[i - 1].name]
    if steps:
        # 异常处理 + 校验：追加在动作序列之后
        steps.append(
            ProcessStep(
                name="verify",
                action=VERIFY_STEP_ACTION,
                params={},
                depends_on=[steps[-1].name],
                status="pending",
            )
        )
    return steps


class ProcessSolidifier:
    """流程固化引擎：ExploreResult → Process，带版本管理与回滚。

    用法：
        solidifier = ProcessSolidifier()
        process = await solidifier.solidify(exploration)   # 首次固化 v1
        p2 = await solidifier.upgrade(process.process_id)  # 升级 v2
        p3 = await solidifier.rollback(process.process_id) # 回滚（生成新版本）
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._write_lock = asyncio.Lock()
        self._init_sync()

    # ---------- 生命周期 ----------

    def _init_sync(self) -> None:
        """幂等建表，进程重启后自动恢复（构造函数内同步执行）。"""
        self._connect()
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS process_store(
                 process_id TEXT NOT NULL,
                 version INTEGER NOT NULL,
                 status TEXT NOT NULL,
                 data TEXT NOT NULL,
                 created_at TEXT NOT NULL,
                 PRIMARY KEY (process_id, version)
               )"""
        )
        self._conn.commit()

    def _connect(self) -> None:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

    async def initialize(self) -> None:
        """幂等建表，进程重启后自动恢复。由自动化引擎/门面异步 await。"""
        self._init_sync()

    async def close(self) -> None:
        """进程退出/空闲时关闭连接。"""
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def states(self) -> Dict[str, Any]:
        return {
            "db_path": self.db_path,
            "process_count": self.count(),
        }

    # ---------- 内部同步执行（跑在线程池里） ----------

    def _store_sync(self, process: Process) -> None:
        self._connect()
        self._conn.execute(
            """INSERT OR REPLACE INTO process_store(process_id, version, status, data, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (process.process_id, process.version, process.status,
             json.dumps(process.to_dict(), ensure_ascii=False), process.created_at),
        )
        self._conn.commit()

    def _archive_sync(self, process_id: str, version: int) -> None:
        self._connect()
        self._conn.execute(
            "UPDATE process_store SET status='archived' WHERE process_id=? AND version=?",
            (process_id, version),
        )
        self._conn.commit()

    def _get_sync(self, process_id: str, version: Optional[int] = None,
                  active_only: bool = True) -> Optional[Dict[str, Any]]:
        self._connect()
        if version is not None:
            row = self._conn.execute(
                "SELECT data, status FROM process_store WHERE process_id=? AND version=?",
                (process_id, version),
            ).fetchone()
        else:
            sql = "SELECT data, status FROM process_store WHERE process_id=?"
            args: tuple = (process_id,)
            if active_only:
                sql += " AND status='active'"
            sql += " ORDER BY version DESC LIMIT 1"
            row = self._conn.execute(sql, args).fetchone()
        if row is None:
            return None
        # 行级 status 列是权威值（归档/激活由 _archive_sync 维护），覆盖 data JSON 中可能滞后的旧值
        d = json.loads(row["data"])
        d["status"] = row["status"]
        return d

    def _all_sync(self) -> List[Dict[str, Any]]:
        self._connect()
        rows = self._conn.execute(
            "SELECT data FROM process_store WHERE status='active' ORDER BY created_at DESC"
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def _manage_sync(self, process_id: str, version: int) -> None:
        self._connect()
        self._conn.execute("DELETE FROM process_store WHERE process_id=? AND version=?", (process_id, version))
        self._conn.commit()

    def _count_sync(self) -> int:
        self._connect()
        return self._conn.execute("SELECT COUNT(*) FROM process_store").fetchone()[0]

    def _clear_sync(self) -> int:
        self._connect()
        n = self._conn.execute("DELETE FROM process_store").rowcount
        self._conn.commit()
        return n

    # ---------- 序列化辅助 ----------

    @staticmethod
    def _hydrate(d: Dict[str, Any]) -> Process:
        steps = [ProcessStep(**s) for s in d.get("steps", [])]
        return Process(
            process_id=d["process_id"],
            name=d.get("name", ""),
            task=d.get("task", ""),
            steps=steps,
            description=d.get("description", ""),
            version=d.get("version", 1),
            base_version=d.get("base_version"),
            status=d.get("status", "active"),
            runs=d.get("runs", 0),
            success_count=d.get("success_count", 0),
            success_rate=d.get("success_rate", 1.0),
            avg_duration_ms=d.get("avg_duration_ms", 0),
            created_at=d.get("created_at", now_iso()),
            meta=d.get("meta", {}),
        )

    # ---------- 对外查询 ----------

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM process_store").fetchone()[0]

    # ---------- 核心逻辑 ----------

    async def solidify(self, exploration: Any) -> Process:
        """把探索结果固化为可执行流程（生成 v1）。"""
        task = str(getattr(exploration, "task", "") or "").strip()
        if not task:
            raise ValueError("exploration.task 不能为空")
        steps = _extract_steps(exploration)
        src = getattr(exploration, "source", "") or ""
        summary = getattr(exploration, "summary", "")
        process = Process(
            process_id=f"p{int(time.time() * 1000)}",
            name=f"固化-{task}",
            task=task,
            steps=steps,
            description=str(summary or "由探索结果固化的可重复执行流程"),
            version=1,
            status="active",
            meta={
                "source": src,
                "candidate_count": len(getattr(exploration, "candidates", None) or []),
                "knowledge_count": len(getattr(exploration, "knowledge", None) or []),
                "exception_handling": "步骤失败时重试一次，仍失败则降级为空结果并记录异常",
            },
        )
        async with self._write_lock:
            await asyncio.to_thread(self._store_sync, process)
        return process

    async def get(self, process_id: str, version: Optional[int] = None) -> Optional[Process]:
        """读取某流程：不传 version 取最新 active 版本。"""
        d = await asyncio.to_thread(self._get_sync, process_id, version)
        return self._hydrate(d) if d else None

    async def list_processes(self) -> List[Process]:
        """列出所有 active 流程（按创建时间倒序）。"""
        rows = await asyncio.to_thread(self._all_sync)
        return [self._hydrate(r) for r in rows]

    async def upgrade(self, process_id: str, *, steps: Optional[List[ProcessStep]] = None,
                      name: Optional[str] = None, description: Optional[str] = None) -> Process:
        """升级流程生成新版本：保留历史版本，新版本成为 active。"""
        cur = await self.get(process_id)
        if cur is None:
            raise KeyError(f"流程不存在: {process_id}")
        updated = Process(
            process_id=cur.process_id,
            name=name or cur.name,
            task=cur.task,
            steps=steps or cur.steps,
            description=description or cur.description,
            version=cur.version + 1,
            base_version=cur.version,
            status="active",
            runs=cur.runs,
            success_count=cur.success_count,
            success_rate=cur.success_rate,
            avg_duration_ms=cur.avg_duration_ms,
            created_at=now_iso(),
            meta=dict(cur.meta),
        )
        async with self._write_lock:
            await asyncio.to_thread(self._archive_sync, cur.process_id, cur.version)
            await asyncio.to_thread(self._store_sync, updated)
        return updated

    async def rollback(self, process_id: str) -> Process:
        """回滚流程：恢复上一个 base_version 的动作序列，生成新版本并保留历史。"""
        cur = await self.get(process_id)
        if cur is None:
            raise KeyError(f"流程不存在: {process_id}")
        if cur.base_version is None:
            raise ValueError("该流程没有可回滚的历史版本")
        base = await self.get(process_id, cur.base_version)
        if base is None:
            raise ValueError(f"历史版本 {cur.base_version} 不存在")
        rolled = Process(
            process_id=cur.process_id,
            name=cur.name,
            task=cur.task,
            steps=base.steps,
            description=base.description,
            version=cur.version + 1,
            base_version=cur.version,
            status="active",
            runs=cur.runs,
            success_count=cur.success_count,
            success_rate=cur.success_rate,
            avg_duration_ms=cur.avg_duration_ms,
            created_at=now_iso(),
            meta=dict(cur.meta),
        )
        async with self._write_lock:
            await asyncio.to_thread(self._archive_sync, cur.process_id, cur.version)
            await asyncio.to_thread(self._store_sync, rolled)
        return rolled

    async def record_run(self, process_id: str, *, success: bool = True, duration_ms: int = 0) -> Optional[Process]:
        """执行一次后回写统计：runs/success_count/success_rate/avg_duration_ms。"""
        cur = await self.get(process_id)
        if cur is None:
            return None
        runs = cur.runs + 1
        success_count = cur.success_count + (1 if success else 0)
        avg_duration = (cur.avg_duration_ms * cur.runs + duration_ms) // runs
        cur.runs = runs
        cur.success_count = success_count
        cur.success_rate = success_count / runs
        cur.avg_duration_ms = avg_duration
        async with self._write_lock:
            await asyncio.to_thread(self._store_sync, cur)
        return cur

    async def delete(self, process_id: str) -> bool:
        """删除流程（全部版本）。"""
        cur = await self.get(process_id)
        if cur is None:
            return False
        async with self._write_lock:
            await asyncio.to_thread(self._manage_sync, process_id, cur.version)
        return True

    async def clear(self) -> int:
        """清空全部流程（默认不调用，供测试/运维）。"""
        async with self._write_lock:
            return await asyncio.to_thread(self._clear_sync)