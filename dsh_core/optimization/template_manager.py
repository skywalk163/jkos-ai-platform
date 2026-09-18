"""DSH 优化引擎 - 模板管理系统（12.3）

把固化流程（Process）沉淀为可复用模板（Template），
内置 50+ 业务场景种子模板，支持创建/编辑/删除、版本管理与回滚、
按任务语义的搜索与推荐、使用统计。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from dsh_core.exploration.knowledge_base import keyword_score, tokenize
from dsh_core.optimization.base import (
    AutomationResult,
    Process,
    ProcessStep,
    Template,
    now_iso,
)
from dsh_core.optimization.templates.seed_data import SEED_TEMPLATES

DEFAULT_DB_PATH = (
    Path(__file__).resolve().parents[1] / "optimization" / "template_db" / "templates.db"
)

# 模板执行单步骤的基础成本（模板复用确定性产出，token 开销极低）
STEP_COST = 20

# 模板匹配的任务级关键字重合度阈值：低于该值视为语义未命中，
# 避免“…方案”等仅共享通用词而误匹配到“营销活动方案”类模板。
MIN_TEMPLATE_MATCH = 0.5

# 模板匹配的任务级关键字重合度阈值：低于该值视为语义未命中，
# 避免“…方案”等仅共享通用词而误匹配到“营销活动方案”类模板。
MIN_TEMPLATE_MATCH = 0.5


def _steps_from_actions(actions: List[str]) -> List[ProcessStep]:
    """把动作文案序列转成链式依赖的步骤列表（步骤之间自动拼接 depends_on）。"""
    steps: List[ProcessStep] = []
    for action in actions:
        action = str(action or "").strip()
        if not action:
            continue
        steps.append(
            ProcessStep(
                name=f"step_{len(steps) + 1}",
                action=action,
                params={},
                depends_on=[steps[-1].name] if steps else [],
                status="pending",
            )
        )
    return steps


class TemplateManager:
    """模板管理系统：管理可复用模板，支持创建/编辑/删除、版本管理与语义推荐。

    用法：
        tm = TemplateManager()                                # 自动装载 50+ 种子模板
        tpl = await tm.recommend_template("生成周报")          # 语义推荐
        result = await tm.execute_template(tpl)               # 按模板执行
        tpl2 = await tm.create_template(process)              # 从固化流程沉淀模板
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._write_lock = asyncio.Lock()
        self._init_sync()

    # ---------- 生命周期 ----------

    def _init_sync(self) -> None:
        """幂等建表；模板库为空时装载内置种子模板（构造函数内同步执行）。"""
        self._connect()
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS template_store(
                 template_id TEXT NOT NULL,
                 version INTEGER NOT NULL,
                 status TEXT NOT NULL,
                 data TEXT NOT NULL,
                 created_at TEXT NOT NULL,
                 PRIMARY KEY (template_id, version)
               )"""
        )
        self._conn.commit()
        self._seed_initial_sync()

    def _connect(self) -> None:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

    async def initialize(self) -> None:
        """幂等建表；模板库为空时装载内置种子模板。由自动化引擎/门面异步 await。"""
        self._init_sync()

    async def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def states(self) -> Dict[str, Any]:
        return {"db_path": self.db_path, "template_count": self.count()}

    # ---------- 种子模板装载（仅空库时执行一次） ----------

    def _seed_initial_sync(self) -> int:
        self._connect()
        n = self._conn.execute("SELECT COUNT(*) FROM template_store").fetchone()[0]
        if n > 0:
            return 0
        created = 0
        for entry in SEED_TEMPLATES:
            meta: Dict[str, Any] = {}
            category = (entry.get("category") or "").strip()
            if category:
                meta["category"] = category
            tpl = Template(
                template_id=f"t{int(time.time() * 1000) + created}",
                name=entry.get("name", ""),
                task=entry.get("task", ""),
                steps=_steps_from_actions(entry.get("steps", []) or []),
                description=entry.get("description", "") or "",
                version=1,
                created_at=now_iso(),
                meta=meta,
            )
            self._conn.execute(
                """INSERT INTO template_store(template_id, version, status, data, created_at)
                   VALUES (?, ?, 'active', ?, ?)""",
                (tpl.template_id, 1, json.dumps(tpl.to_dict(), ensure_ascii=False), tpl.created_at),
            )
            created += 1
        self._conn.commit()
        return created

    # ---------- 内部同步执行 ----------

    def _store_sync(self, template: Template, *, status: str = "active") -> None:
        self._connect()
        self._conn.execute(
            """INSERT OR REPLACE INTO template_store(template_id, version, status, data, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (template.template_id, template.version, status,
             json.dumps(template.to_dict(), ensure_ascii=False), template.created_at),
        )
        self._conn.commit()

    def _archive_sync(self, template_id: str, version: int) -> None:
        self._connect()
        self._conn.execute(
            "UPDATE template_store SET status='archived' WHERE template_id=? AND version=?",
            (template_id, version),
        )
        self._conn.commit()

    def _get_sync(self, template_id: str, version: Optional[int] = None,
                  active_only: bool = True) -> Optional[Dict[str, Any]]:
        self._connect()
        if version is not None:
            row = self._conn.execute(
                "SELECT data FROM template_store WHERE template_id=? AND version=?",
                (template_id, version),
            ).fetchone()
        else:
            sql = "SELECT data FROM template_store WHERE template_id=?"
            args: tuple = (template_id,)
            if active_only:
                sql += " AND status='active'"
            sql += " ORDER BY version DESC LIMIT 1"
            row = self._conn.execute(sql, args).fetchone()
        return json.loads(row["data"]) if row else None

    def _prev_sync(self, template_id: str, version: int) -> Optional[Dict[str, Any]]:
        self._connect()
        row = self._conn.execute(
            "SELECT data FROM template_store WHERE template_id=? AND version<?"
            " ORDER BY version DESC LIMIT 1",
            (template_id, version),
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def _all_sync(self) -> List[Dict[str, Any]]:
        self._connect()
        rows = self._conn.execute(
            "SELECT data FROM template_store WHERE status='active' ORDER BY created_at ASC"
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def _manage_sync(self, template_id: str, version: int) -> None:
        self._connect()
        self._conn.execute("DELETE FROM template_store WHERE template_id=? AND version=?",
                           (template_id, version))
        self._conn.commit()

    def _count_sync(self) -> int:
        self._connect()
        return self._conn.execute("SELECT COUNT(*) FROM template_store").fetchone()[0]

    def _clear_sync(self) -> int:
        self._connect()
        n = self._conn.execute("DELETE FROM template_store").rowcount
        self._conn.commit()
        return n

    # ---------- 序列化辅助 ----------

    @staticmethod
    def _hydrate(d: Dict[str, Any]) -> Template:
        steps = [ProcessStep(**s) for s in d.get("steps", [])]
        return Template(
            template_id=d["template_id"],
            name=d.get("name", ""),
            task=d.get("task", ""),
            steps=steps,
            description=d.get("description", ""),
            version=d.get("version", 1),
            use_count=d.get("use_count", 0),
            success_count=d.get("success_count", 0),
            success_rate=d.get("success_rate", 1.0),
            created_at=d.get("created_at", now_iso()),
            meta=d.get("meta", {}),
        )

    # ---------- 对外查询 ----------

    def count(self) -> int:
        self._connect()
        return self._conn.execute("SELECT COUNT(*) FROM template_store").fetchone()[0]

    # ---------- 核心逻辑 ----------

    async def create_template(self, process: Process) -> Template:
        """把固化流程沉淀为可复用模板（v1）。"""
        if not process or not process.task:
            raise ValueError("process.task 不能为空")
        steps = [ProcessStep(**s.to_dict()) for s in process.steps]
        base_ts = int(time.time() * 1000)
        async with self._write_lock:
            # 生成不与既有 (template_id, version) 主键冲突的唯一 id。
            # 种子模板 id 形如 t{base+0..60}，直接取当前毫秒可能落入该窗口，
            # 被 INSERT OR REPLACE 覆盖已有行导致 count 不增长。
            template_id = f"t{base_ts}"
            bump = 0
            while True:
                d = await asyncio.to_thread(self._get_sync, template_id, 1)
                if d is None:
                    break
                bump += 1
                template_id = f"t{base_ts + bump}"
            tpl = Template(
                template_id=template_id,
                name=process.name or f"模板-{process.task}",
                task=process.task,
                steps=steps,
                description=process.description or "",
                version=1,
                created_at=now_iso(),
                meta={"source_process_id": process.process_id},
            )
            await asyncio.to_thread(self._store_sync, tpl)
        return tpl

    async def get(self, template_id: str, version: Optional[int] = None) -> Optional[Template]:
        d = await asyncio.to_thread(self._get_sync, template_id, version)
        return self._hydrate(d) if d else None

    async def list_templates(self) -> List[Template]:
        rows = await asyncio.to_thread(self._all_sync)
        return [self._hydrate(r) for r in rows]

    async def update_template(self, template_id: str, *, steps: Optional[List[ProcessStep]] = None,
                              name: Optional[str] = None,
                              description: Optional[str] = None) -> Template:
        """编辑模板：历史版本归档，生成新版本并成为 active。"""
        cur = await self.get(template_id)
        if cur is None:
            raise KeyError(f"模板不存在: {template_id}")
        updated = Template(
            template_id=cur.template_id,
            name=name or cur.name,
            task=cur.task,
            steps=steps or cur.steps,
            description=description if description is not None else cur.description,
            version=cur.version + 1,
            use_count=cur.use_count,
            success_count=cur.success_count,
            success_rate=cur.success_rate,
            created_at=now_iso(),
            meta=dict(cur.meta),
        )
        async with self._write_lock:
            await asyncio.to_thread(self._archive_sync, cur.template_id, cur.version)
            await asyncio.to_thread(self._store_sync, updated)
        return updated

    async def rollback(self, template_id: str) -> Template:
        """回滚模板：恢复最近一次历史版本的步骤与描述，生成新版本。"""
        cur = await self.get(template_id)
        if cur is None:
            raise KeyError(f"模板不存在: {template_id}")
        prev_d = await asyncio.to_thread(self._prev_sync, template_id, cur.version)
        if prev_d is None:
            raise ValueError("该模板没有可回滚的历史版本")
        prev = self._hydrate(prev_d)
        rolled = Template(
            template_id=cur.template_id,
            name=cur.name,
            task=cur.task,
            steps=prev.steps,
            description=prev.description,
            version=cur.version + 1,
            use_count=cur.use_count,
            success_count=cur.success_count,
            success_rate=cur.success_rate,
            created_at=now_iso(),
            meta=dict(cur.meta),
        )
        async with self._write_lock:
            await asyncio.to_thread(self._archive_sync, cur.template_id, cur.version)
            await asyncio.to_thread(self._store_sync, rolled)
        return rolled

    async def delete_template(self, template_id: str) -> bool:
        """删除模板（删除当前 active 版本）。返回 False 表示不存在。"""
        cur = await self.get(template_id)
        if cur is None:
            return False
        async with self._write_lock:
            await asyncio.to_thread(self._manage_sync, template_id, cur.version)
        return True

    async def recommend_template(self, task: str) -> Optional[Template]:
        """按任务语义推荐最匹配的模板（12.3 推荐）。

        任务级关键字重合度低于 MIN_TEMPLATE_MATCH 时返回 None（视为未命中，
        交由探索式执行），避免“…方案”等仅共享通用词而误匹配到同名模板。
        """
        task = (task or "").strip()
        if not task:
            raise ValueError("task 不能为空")
        rows = await asyncio.to_thread(self._all_sync)
        if not rows:
            raise ValueError("模板库为空，请先创建模板")
        templates = [self._hydrate(r) for r in rows]
        scored = []
        for t in templates:
            task_kw = keyword_score(task, t.task)
            score = task_kw + 0.5 * keyword_score(task, t.description)
            scored.append((score, task_kw, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored[0][0] <= 0:
            # 语义未命中时，按任务与描述的关键字重合度、使用次数兜底排序
            toks = tokenize(task)
            scored.sort(
                key=lambda x: (len(toks & tokenize(x[2].task + x[2].description)), x[2].use_count),
                reverse=True,
            )
        _, best_task_kw, best = scored[0]
        if best_task_kw < MIN_TEMPLATE_MATCH:
            return None
        return best

    async def has_template(self, task: str) -> bool:
        """是否存在语义匹配的模板（供自动化执行引擎分支判断）。"""
        try:
            return (await self.recommend_template(task)) is not None
        except ValueError:
            return False

    async def search_templates(self, keyword: str) -> List[Template]:
        """按关键字搜索模板，按语义相关度排序。"""
        keyword = (keyword or "").strip()
        rows = await asyncio.to_thread(self._all_sync)
        templates = [self._hydrate(r) for r in rows]
        if not keyword:
            return templates
        results = []
        for t in templates:
            text = " ".join([
                t.name, t.task, t.description,
                *[s.action for s in t.steps],
            ])
            if (tokenize(keyword) & tokenize(text)) or (keyword in text):
                results.append((keyword_score(keyword, text), t))
        results.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in results]

    async def record_run(self, template_id: str, *, success: bool = True,
                         duration_ms: int = 0) -> Optional[Template]:
        """记录一次模板使用，统计 use_count / success_count / success_rate。"""
        cur = await self.get(template_id)
        if cur is None:
            return None
        cur.use_count += 1
        if success:
            cur.success_count += 1
        cur.success_rate = cur.success_count / cur.use_count
        async with self._write_lock:
            await asyncio.to_thread(self._store_sync, cur)
        return cur

    async def execute_template(self, template: Template) -> AutomationResult:
        """按模板步骤执行一次（确定性复用产出），并回写使用统计。"""
        if template is None or not template.task:
            raise ValueError("template 不能为空")
        t0 = time.monotonic()
        total = 0
        outputs: List[str] = []
        for step in template.steps:
            outputs.append(f"【{template.task}】{step.action}（已完成）")
            total += STEP_COST + max(1, len(str(step.action)))
        code = int(len(template.steps) * 200 + 100)
        output = "\n".join(outputs)
        duration_ms = max(1, int((time.monotonic() - t0) * 1000))
        await self.record_run(template.template_id, success=True, duration_ms=duration_ms)
        return AutomationResult(
            run_id=f"r{code}{int(time.time() * 1000)}",
            task=template.task,
            success=True,
            source="template",
            token_used=max(1, total),
            tokens_saved=0,
            duration_ms=duration_ms,
            summary=f"按模板[{template.name}|v{template.version}]执行 {len(template.steps)} 个步骤",
            output=output,
            meta={"template_id": template.template_id, "template_version": template.version},
        )

    async def clear(self) -> int:
        """清空模板库（测试/运维用）。"""
        async with self._write_lock:
            return await asyncio.to_thread(self._clear_sync)