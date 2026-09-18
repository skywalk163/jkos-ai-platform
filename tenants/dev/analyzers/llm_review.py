"""LLM 审查节点（M1 任务 1.2 之二）——把 diff 交给大模型做语义审查

设计：
  - 输入：黑板分区 diff（GitAdapter.collect_diff 输出）；
  - 输出：write_keys=["llm_findings"]，结构 {llm_findings: [...], llm_summary, provider, simulated}；
  - LLM 输出容错：优先 json.loads，其次正则抽首个 [...] 数组，失败则整段作 raw 摘要，
    保证节点永不因模型输出格式问题挂掉；
  - patch 预算截断（MAX_PROMPT_CHARS），避免大 diff 撑爆 token。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

MAX_PROMPT_CHARS = 12000
MAX_FILE_PATCH_CHARS = 3000

JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")


def build_review_prompt(diff: Dict[str, Any]) -> str:
    """把 diff 压缩成审查 prompt（每文件 patch 截断，总预算截断）"""
    parts: List[str] = []
    budget = MAX_PROMPT_CHARS
    for f in diff.get("files", []):
        if budget <= 0:
            parts.append(f"...（其余 {len(diff.get('files', [])) - len(parts)} 个文件因长度预算被省略）")
            break
        chunk = f"### {f['path']} (+{f['additions']}/-{f['deletions']})\n```diff\n{f['patch'][:MAX_FILE_PATCH_CHARS]}\n```"
        parts.append(chunk)
        budget -= len(chunk)
    return (
        "你是资深代码审查员。请审查以下 git diff 中的新增代码，"
        "重点关注：安全漏洞、明显 bug、资源泄漏、错误处理缺失。\n"
        "只输出 JSON 数组（不要其它文字），元素结构：\n"
        '[{"path": "文件路径", "line": 行号或null, "severity": "high|medium|low", "message": "问题说明"}]\n'
        "没有问题则输出 []。\n\n" + "\n".join(parts)
    )


def parse_llm_findings(content: str) -> Dict[str, Any]:
    """容错解析 LLM 输出为 findings 列表 + 摘要"""
    text = (content or "").strip()
    # 剥掉 markdown 代码围栏
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    candidates = [text]
    match = JSON_ARRAY_RE.search(text)
    if match:
        candidates.insert(0, match.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, list):
            findings = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                findings.append({
                    "path": str(item.get("path") or ""),
                    "line": item.get("line"),
                    "severity": str(item.get("severity") or "medium").lower(),
                    "message": str(item.get("message") or ""),
                    "source": "llm",
                })
            return {"findings": findings, "summary": f"LLM 审查命中 {len(findings)} 项"}
        if isinstance(data, dict) and isinstance(data.get("findings"), list):
            data["findings"] = [{**f, "source": "llm"} for f in data["findings"] if isinstance(f, dict)]
            return {"findings": data["findings"], "summary": str(data.get("summary") or "LLM 审查完成")}
    # 完全解析失败：原文作摘要，不给空结论
    return {"findings": [], "summary": f"LLM 输出非 JSON，原文摘录: {text[:300]}"}


async def node_llm_review(ctx) -> Dict[str, Any]:
    """D1 工作流的 llm_review 节点处理器（黑板式：read diff → write llm_findings）"""
    diff = (ctx.blackboard or {}).get("diff") or (ctx.prev_output or {}).get("diff")
    if not diff:
        raise ValueError("黑板上没有 diff 分区（collect_diff 未先行执行）")
    result = await ctx.engine.llm.chat_text(
        build_review_prompt(diff),
        tenant_id=ctx.instance["tenant_id"],
        purpose="workflow",
        ref_type="workflow_instance",
        ref_id=ctx.instance["id"],
    )
    parsed = parse_llm_findings(result.content)
    return {
        "llm_findings": parsed["findings"],
        "llm_summary": parsed["summary"],
        "provider": result.provider,
        "simulated": result.simulated,
        "tokens": result.total_tokens,
    }
