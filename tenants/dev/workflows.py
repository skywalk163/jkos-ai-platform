"""租户 dev 工作流（M1 任务 1.3/1.4/1.6）——D1 代码审查（本仓库自举）

工作流 d1_code_review（黑板式协作，§8.1.1 范式 B）：
  1. collect_diff      采集本仓库 diff → 黑板分区 diff
  2. rule_scan ∥ llm_review   并行组 scan：规则扫描器与大模型审查同时读 diff，
     各写各的分区（rule_findings / llm_findings，定义期 write_keys 互斥校验）
  3. summarize         汇总两路发现 → markdown 报告落 reports/ → 黑板分区 report
  4. publish_review    审批节点（§8.2）：any 或签 / low 风险（24h 超时自动通过）
     ——报告发布前人工确认，构成"每日自动审查出报告并推送"的人工干预点

使用：
  from tenants.dev import workflows
  workflows.register(engine)   # 注册节点处理器 + D1 定义（bootstrap 调用）
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from jkos_core.workflow.base import ApprovalSpec, NodeSpec, WorkflowDef
from jkos_core.workflow.nodes import WORKFLOW_REGISTRY
from tenants.dev.adapters.git import collect_diff
from tenants.dev.analyzers.llm_review import node_llm_review
from tenants.dev.analyzers.rules import scan_patch, summarize_findings

logger = logging.getLogger("dsh.tenants.dev")

# 默认审查仓库：本包所在仓库根（tenants/dev/workflows.py → 上溯三级）
DEFAULT_REPO = str(Path(__file__).resolve().parents[2])


# ─── D1 节点处理器 ───

async def node_collect_diff(ctx) -> Dict[str, Any]:
    """采集 diff → 黑板分区 diff（context 可传 repo_path / base）"""
    context = ctx.instance.get("context") or {}
    diff = collect_diff(
        context.get("repo_path") or DEFAULT_REPO,
        base=context.get("base") or "HEAD~1",
    )
    return {"diff": diff}


async def node_rule_scan(ctx) -> Dict[str, Any]:
    """规则扫描 diff 新增行 → 黑板分区 rule_findings"""
    diff = (ctx.blackboard or {}).get("diff") or (ctx.prev_output or {}).get("diff")
    if not diff:
        raise ValueError("黑板上没有 diff 分区（collect_diff 未先行执行）")
    findings = scan_patch(diff.get("files"))
    return {"rule_findings": findings, "rule_summary": summarize_findings(findings)}


async def node_summarize(ctx) -> Dict[str, Any]:
    """汇总黑板全部分区 → markdown 报告文件 + 黑板分区 report"""
    board = ctx.blackboard or {}
    diff = board.get("diff") or {}
    rule_findings = board.get("rule_findings") or []
    llm_findings = board.get("llm_findings") or []
    all_findings = list(rule_findings) + list(llm_findings)
    summary = summarize_findings(all_findings)

    lines = [
        f"# D1 代码审查报告 · {diff.get('repo', '')}",
        f"- 区间: {diff.get('base', '?')}..{diff.get('head', '?')} · 采集于 {diff.get('collected_at', '?')}",
        f"- 变更文件 {len(diff.get('files', []))} 个（+{diff.get('total_additions', 0)}/-{diff.get('total_deletions', 0)}）",
        f"- 命中: high={summary['high']} medium={summary['medium']} low={summary['low']}（共 {summary['total']}）",
        "",
    ]
    for f in all_findings:
        lines.append(
            f"- **[{f.get('severity', '?').upper()}] {f.get('code') or f.get('source', 'llm')}** "
            f"`{f.get('path', '?')}:{f.get('line', '?')}` — {f.get('message', '')}"
        )
        if f.get("snippet"):
            lines.append(f"  - `{f['snippet']}`")
    if not all_findings:
        lines.append("- 无命中：本次变更未触发任何规则或 LLM 发现")
    markdown = "\n".join(lines)

    report_dir = Path((ctx.instance.get("context") or {}).get("report_dir")
                      or Path(diff.get("repo") or DEFAULT_REPO) / "reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = report_dir / f"{ts}-code-review-{diff.get('head', 'unk')}.md"
    report_path.write_text(markdown, encoding="utf-8")
    logger.info("审查报告已生成 %s（命中 %d）", report_path, summary["total"])
    return {
        "report": {
            "path": str(report_path), "summary": summary,
            "base": diff.get("base"), "head": diff.get("head"),
            "files_reviewed": len(diff.get("files", [])),
        }
    }


# ─── D1 工作流定义（黑板并行组 + 人工审批）───

D1_CODE_REVIEW = WorkflowDef(
    code="d1_code_review",
    description=(
        "案例A D1 代码审查: 采集diff → (规则扫描 ‖ LLM审查) 黑板并行 → 汇总报告 → "
        "低风险人工确认发布（any/low，24h 超时自动通过）"
    ),
    nodes=[
        NodeSpec("collect_diff", "dev_collect_diff", write_keys=["diff"]),
        NodeSpec("rule_scan", "dev_rule_scan", parallel_group="scan",
                 write_keys=["rule_findings"], read_keys=["diff"]),
        NodeSpec("llm_review", "dev_llm_review", node_type="llm", parallel_group="scan",
                 write_keys=["llm_findings"], read_keys=["diff"]),
        NodeSpec("summarize", "dev_summarize",
                 write_keys=["report"], read_keys=["diff", "rule_findings", "llm_findings"]),
        NodeSpec("publish_review", "noop", node_type="approval",
                 approval=ApprovalSpec(approvers=["admin"], mode="any", risk="low")),
    ],
)


def register(engine) -> None:
    """向引擎注册租户节点处理器与 D1 工作流（幂等，bootstrap/测试均可调用）"""
    for name, handler in {
        "dev_collect_diff": node_collect_diff,
        "dev_rule_scan": node_rule_scan,
        "dev_llm_review": node_llm_review,
        "dev_summarize": node_summarize,
    }.items():
        engine.register_node(name, handler)
    WORKFLOW_REGISTRY[D1_CODE_REVIEW.code] = D1_CODE_REVIEW
