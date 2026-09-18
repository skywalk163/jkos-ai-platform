"""DSH 租户开发 - Git 适配器（M1 任务 1.1）

职责：从本地 git 仓库采集变更差异（diff），作为 D1 代码审查工作流的输入源。
开发计划 1.1 的 webhook/轮询形态在 MVP 阶段收敛为"本地仓库 diff 采集"：
  - 每日自举（1.6）由定时脚本直接对本仓库跑 collect_diff；
  - webhook 接收端（外部 push 事件）在 M2 web 层落地，payload 复用本模块输出结构。

安全约定：subprocess 一律列表参数（无 shell 注入面）；仓库路径必须存在。
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_PATCH_CHARS = 20000   # 单文件 patch 截断上限（防超大 diff 撑爆上下文/LLM）


class GitAdapterError(RuntimeError):
    """git 命令执行失败"""


def _run_git(repo_path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if proc.returncode != 0:
        raise GitAdapterError(f"git {' '.join(args)} 失败: {proc.stderr.strip()[:500]}")
    return proc.stdout


def _split_patch_by_file(full_patch: str) -> Dict[str, str]:
    """把 unified diff 全文按 'diff --git a/.. b/..' 边界切到各文件"""
    patches: Dict[str, str] = {}
    current_path: Optional[str] = None
    buffer: List[str] = []
    for line in full_patch.splitlines():
        if line.startswith("diff --git "):
            if current_path:
                patches[current_path] = "\n".join(buffer)
            # diff --git a/<path> b/<path>（取 b 侧；rename 场景以 b 为准）
            parts = line.split(" b/", 1)
            current_path = parts[1] if len(parts) == 2 else line
            buffer = [line]
        elif current_path:
            buffer.append(line)
    if current_path:
        patches[current_path] = "\n".join(buffer)
    return patches


def collect_diff(
    repo_path: str,
    base: str = "HEAD~1",
    head: str = "HEAD",
    max_patch_chars: int = MAX_PATCH_CHARS,
) -> Dict[str, Any]:
    """采集 base..head 的变更差异。

    返回:
      {repo, base, head, files: [{path, additions, deletions, patch}],
       total_additions, total_deletions, collected_at}
    """
    repo = Path(repo_path).resolve()
    if not repo.exists():
        raise FileNotFoundError(f"仓库路径不存在: {repo}")
    numstat = _run_git(repo, "diff", "--numstat", base, head)
    full_patch = _run_git(repo, "diff", base, head)
    patches = _split_patch_by_file(full_patch)
    base_sha = _run_git(repo, "rev-parse", "--short", base).strip()
    head_sha = _run_git(repo, "rev-parse", "--short", head).strip()

    files: List[Dict[str, Any]] = []
    total_add = total_del = 0
    for line in numstat.splitlines():
        if not line.strip():
            continue
        add_text, del_text, path = line.split("\t", 2)
        additions = 0 if add_text == "-" else int(add_text)      # "-" 为二进制文件
        deletions = 0 if del_text == "-" else int(del_text)
        patch = patches.get(path, "")
        if len(patch) > max_patch_chars:
            patch = patch[:max_patch_chars] + "\n... (patch 截断)"
        files.append({
            "path": path, "additions": additions, "deletions": deletions, "patch": patch,
        })
        total_add += additions
        total_del += deletions

    return {
        "repo": str(repo), "base": base_sha, "head": head_sha,
        "files": files, "total_additions": total_add, "total_deletions": total_del,
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
