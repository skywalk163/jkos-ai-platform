"""DSH 探索引擎 - 知识积累系统（11.4，P1）

职责：
  1. store          —— 沉淀探索成功的方案（任务 → 步骤 → 结果）；
  2. store_failure  —— 沉淀失败的教训（任务 → 失败原因），供后续规避；
  3. search         —— 语义检索：关键词覆盖打分，返回按相关度降序的知识条目。

实现：SQLite 单文件持久化（stdlib sqlite3 + asyncio.to_thread），
不依赖外部基础设施（Postgres/Redis），与 workflow 引擎"状态全部落 SQLite、
进程随时可死、重启即恢复"的设计一致。默认库文件位于
jkos_core/exploration/knowledge_base/knowledge.db。
"""
from __future__ import annotations

import asyncio
from contextlib import closing
import json
import logging
import re
import sqlite3
import uuid
from pathlib import Path
from typing import List, Optional

from jkos_core.exploration.base import Knowledge, Result, Solution

logger = logging.getLogger("dsh.exploration")

_EN_WORD = re.compile(r"[a-zA-Z0-9_]+")
_CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")

# 中英文停用词：检索打分时剔除，避免"的/了/是"等空词干扰
_STOPWORDS = frozenset({
    "的", "了", "和", "与", "及", "或", "在", "是", "对", "于", "为", "中",
    "将", "把", "被", "等", "并", "而", "就", "都", "也", "这", "那", "个",
    "要", "会", "能", "可以", "进行", "以及", "一个", "使用",
    "the", "a", "an", "is", "are", "to", "of", "and", "or", "for", "with",
    "in", "on", "at", "by", "it", "as", "be",
})


def tokenize(text: str) -> set:
    """切词：英文单词 + 中文单字/二字词，用于关键词覆盖打分"""
    tokens: set = set()
    for w in _EN_WORD.findall(text.lower()):
        if len(w) >= 2 and w not in _STOPWORDS:
            tokens.add(w)
    cjk = "".join(_CJK_CHAR.findall(text))
    for ch in cjk:
        if ch not in _STOPWORDS:
            tokens.add(ch)
    for i in range(len(cjk) - 1):
        bigram = cjk[i : i + 2]
        if not any(c in _STOPWORDS for c in bigram):
            tokens.add(bigram)
    return tokens


def keyword_score(query: str, text: str) -> float:
    """查询文本对目标文本的关键词覆盖率 0.0-1.0"""
    q, t = tokenize(query), tokenize(text)
    if not q or not t:
        return 0.0
    return len(q & t) / len(q)


class KnowledgeBase:
    """知识积累系统：SQLite 持久化 + 语义检索"""

    DEFAULT_DB_PATH = Path(__file__).parent / "knowledge_base" / "knowledge.db"

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path or self.DEFAULT_DB_PATH)
        self._initialized = False
        self._write_lock = asyncio.Lock()

    # ---------- 生命周期 ----------
    async def initialize(self) -> None:
        """建库建表（幂等）"""
        if self._initialized:
            return
        await asyncio.to_thread(self._create_schema)
        self._initialized = True

    def _create_schema(self) -> None:
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge (
                    knowledge_id TEXT PRIMARY KEY,
                    task        TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    category    TEXT NOT NULL DEFAULT 'solution',
                    outcome     TEXT NOT NULL DEFAULT 'success',
                    created_at  TEXT NOT NULL,
                    meta        TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_task ON knowledge(task)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_outcome ON knowledge(outcome)")

    async def close(self) -> None:
        """预留关闭接口（按操作开连接，无长期资源需回收）"""
        self._initialized = False

    # ---------- 写入 ----------
    async def store(self, solution: Solution, result: Result) -> Knowledge:
        """沉淀一次成功的方案"""
        steps = "\n".join(
            f"{i + 1}. {s}" for i, s in enumerate(solution.steps)) or "无"
        knowledge = Knowledge(
            knowledge_id=uuid.uuid4().hex[:16],
            task=solution.task,
            content=(
                f"任务：{solution.task}\n"
                f"步骤：\n{steps}\n"
                f"结果：{result.summary or '验证通过'}"
            ),
            category="solution",
            outcome="success",
            meta={
                "solution_id": solution.solution_id,
                "source_type": solution.source_type,
                "metric": result.metric,
            },
        )
        await self._insert(knowledge)
        logger.info("知识已沉淀: %s (%s)", knowledge.knowledge_id, solution.task)
        return knowledge

    async def store_failure(self, solution: Solution, error: str) -> Knowledge:
        """沉淀一次失败的教训（含原因，供后续规避）"""
        knowledge = Knowledge(
            knowledge_id=uuid.uuid4().hex[:16],
            task=solution.task,
            content=f"任务：{solution.task}\n失败原因：{error or '未知'}",
            category="failure",
            outcome="failure",
            meta={
                "solution_id": solution.solution_id,
                "source_type": solution.source_type,
            },
        )
        await self._insert(knowledge)
        logger.warning("失败教训已沉淀: %s (%s)", knowledge.knowledge_id, solution.task)
        return knowledge

    async def _insert(self, knowledge: Knowledge) -> None:
        await self.initialize()
        async with self._write_lock:
            await asyncio.to_thread(self._insert_sync, knowledge)

    def _insert_sync(self, knowledge: Knowledge) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO knowledge"
                " (knowledge_id, task, content, category, outcome, created_at, meta)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    knowledge.knowledge_id,
                    knowledge.task,
                    knowledge.content,
                    knowledge.category,
                    knowledge.outcome,
                    knowledge.created_at,
                    json.dumps(knowledge.meta, ensure_ascii=False),
                ),
            )

    # ---------- 检索 ----------
    async def search(self, task: str, limit: int = 10,
                     category: Optional[str] = None) -> List[Knowledge]:
        """语义检索：关键词覆盖打分，返回按相关度降序的知识条目"""
        await self.initialize()
        return await asyncio.to_thread(self._search_sync, task, limit, category)

    def _search_sync(self, task: str, limit: int,
                     category: Optional[str]) -> List[Knowledge]:
        # 单文件规模全量打分，排序精确且足够快
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            if category:
                cursor = conn.execute(
                    "SELECT * FROM knowledge WHERE category = ?", (category,))
            else:
                cursor = conn.execute("SELECT * FROM knowledge")
            candidates = [
                Knowledge(
                    knowledge_id=row[0], task=row[1], content=row[2],
                    category=row[3], outcome=row[4], created_at=row[5],
                    meta=json.loads(row[6] or "{}"),
                )
                for row in cursor.fetchall()
            ]
        scored = []
        for k in candidates:
            # 任务名 + 内容一起打分，召回更全
            score = max(keyword_score(task, k.task), keyword_score(task, k.content))
            if score > 0:
                scored.append((score, k))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [k for _, k in scored[:limit]]

    async def count(self) -> int:
        """知识条目总数"""
        await self.initialize()
        return await asyncio.to_thread(self._count_sync)

    def _count_sync(self) -> int:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            row = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()
            return int(row[0])

    async def all(self, limit: int = 100) -> List[Knowledge]:
        """最近 limit 条知识（时间倒序），用于巡检/报表"""
        await self.initialize()

        def _fetch():
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                cursor = conn.execute(
                    "SELECT * FROM knowledge ORDER BY created_at DESC LIMIT ?",
                    (limit,))
                return [
                    Knowledge(
                        knowledge_id=r[0], task=r[1], content=r[2],
                        category=r[3], outcome=r[4], created_at=r[5],
                        meta=json.loads(r[6] or "{}"),
                    )
                    for r in cursor.fetchall()
                ]

        return await asyncio.to_thread(_fetch)