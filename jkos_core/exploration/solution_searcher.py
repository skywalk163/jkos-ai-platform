"""DSH 探索引擎 - 方案搜索与匹配（11.2，P0）

三步搜索：语义（知识库）→ 代码（jkos_core 源码）→ 文档（docs 目录），
合并去重后按相关度排序返回，应答控制在 2 秒内（限时扫描保护）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, List, Optional, Tuple

from jkos_core.exploration.base import SolutionCandidate
from jkos_core.exploration.knowledge_base import KnowledgeBase, keyword_score

logger = logging.getLogger("dsh.exploration")

RESPONSE_BUDGET_SECONDS = 2.0  # 设计规格：应答 < 2 秒


def _default_root() -> Path:
    """仓库根目录：jkos_core/exploration/../.. = dsh-ai-platform"""
    return Path(__file__).resolve().parents[2]


class SolutionSearcher:
    """方案搜索与匹配：知识库 → 代码 → 文档 → 合并排序"""

    def __init__(self, knowledge_base: Optional[KnowledgeBase] = None,
                 llm: Optional[Any] = None, *,
                 repo_root: Optional[str] = None):
        self.knowledge_base = knowledge_base
        self.llm = llm
        self.repo_root = Path(repo_root) if repo_root else _default_root()
        self.last_duration_ms = 0

    async def search(self, task: str, *, limit: int = 8) -> List[SolutionCandidate]:
        """三步搜索，返回按相关度降序的候选方案"""
        started = time.monotonic()
        task = (task or "").strip()
        if not task:
            return []
        candidates: List[SolutionCandidate] = []
        candidates.extend(await self._search_knowledge(task, limit))
        candidates.extend(await self._search_code(task, limit))
        candidates.extend(await self._search_docs(task, limit))
        ranked = self._dedup_and_rank(candidates, limit)
        self.last_duration_ms = int((time.monotonic() - started) * 1000)
        return ranked

    # ---------- 第一步：语义搜索（知识库） ----------
    async def _search_knowledge(self, task: str, limit: int) -> List[SolutionCandidate]:
        if self.knowledge_base is None:
            return []
        try:
            hits = await self.knowledge_base.search(task, limit=limit)
        except Exception as exc:
            logger.warning("知识库检索失败: %s", exc)
            return []
        return [
            SolutionCandidate(
                solution_id=uuid.uuid4().hex[:16],
                title=f"[知识库] {hit.task}",
                description=hit.content[:500],
                source_type="knowledge",
                source_ref=hit.knowledge_id,
                similarity=keyword_score(task, hit.content),
                meta={"knowledge_id": hit.knowledge_id, "outcome": hit.outcome,
                      "category": hit.category},
            )
            for hit in hits
        ]

    # ---------- 第二步：代码搜索（jkos_core） ----------
    async def _search_code(self, task: str, limit: int) -> List[SolutionCandidate]:
        root = self.repo_root / "jkos_core"
        if not root.is_dir():
            return []
        return await asyncio.to_thread(
            self._scan_dir, task, root, limit,
            "code", (".py",), ("__pycache__",))

    # ---------- 第三步：文档搜索（docs） ----------
    async def _search_docs(self, task: str, limit: int) -> List[SolutionCandidate]:
        root = self.repo_root / "docs"
        if not root.is_dir():
            return []
        return await asyncio.to_thread(
            self._scan_dir, task, root, limit,
            "doc", (".md", ".txt"), ())

    def _scan_dir(self, task: str, root: Path, limit: int, source_type: str,
                  suffixes: Tuple[str, ...],
                  skip_dirs: Tuple[str, ...]) -> List[SolutionCandidate]:
        """扫描目录：读取文件头部做关键词打分；限时防慢"""
        deadline = time.monotonic() + RESPONSE_BUDGET_SECONDS
        found: List[SolutionCandidate] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fname in filenames:
                if time.monotonic() > deadline:
                    logger.warning("方案搜索超时预算，提前返回（目录: %s）", root)
                    self._finalize(found, limit)
                    return found
                if not fname.endswith(suffixes):
                    continue
                path = Path(dirpath) / fname
                header = self._read_header(path)
                if not header:
                    continue
                score = keyword_score(task, header)
                if score > 0:
                    rel = path.relative_to(self.repo_root).as_posix()
                    first_line = (header.strip().splitlines()[0][:80]
                                  if header.strip() else rel)
                    found.append(SolutionCandidate(
                        solution_id=uuid.uuid4().hex[:16],
                        title=f"[{source_type}] {first_line}",
                        description=header[:500],
                        source_type=source_type,
                        source_ref=rel,
                        similarity=score,
                        meta={"path": rel},
                    ))
        self._finalize(found, limit)
        return found

    @staticmethod
    def _finalize(found: List[SolutionCandidate], limit: int) -> None:
        found.sort(key=lambda c: c.similarity, reverse=True)
        if len(found) > limit:
            del found[limit:]

    @staticmethod
    def _read_header(path: Path, max_lines: int = 40) -> str:
        """读取文件头部摘要（docstring/前几行，遇到 import 即停）"""
        lines = []
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for _ in range(max_lines):
                    line = fh.readline()
                    if not line:
                        break
                    if line.lstrip().startswith(("import ", "from ")):
                        break
                    lines.append(line)
        except OSError:
            return ""
        return "".join(lines)

    # ---------- 合并排序 ----------
    def _dedup_and_rank(self, candidates: List[SolutionCandidate],
                        limit: int) -> List[SolutionCandidate]:
        """去重（同 source_ref 只留一个）+ 相关度降序；知识库命中优先"""
        seen = set()
        unique = []
        for c in candidates:
            key = f"{c.source_type}:{c.source_ref}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(c)
        order = {"knowledge": 0, "doc": 1, "code": 2}
        unique.sort(
            key=lambda c: (c.similarity, -order[c.source_type]), reverse=True)
        return unique[:limit]