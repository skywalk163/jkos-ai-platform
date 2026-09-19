#!/usr/bin/env python3
"""M18.3 三案例产品化演示脚本（案例A 开发 / 案例B 媒体 / 案例C 酒厂）

用途：M18「产品化 P1」三方验收的端到端证据。Windows 与 FreeBSD 均可直接运行：

    python scripts/demo_three_cases.py [--out reports/m18/demo_three_cases.json]

演示内容（全部在进程内直跑工作流引擎，不依赖任何外部服务）：
  a) 案例A DSH-Dev    ：d1_code_review（采集 diff → 规则扫描 ‖ LLM 审查 → 汇总报告 → 审批发布），
                        含人工审批闭环（WAITING_APPROVAL → 同意 → COMPLETED）
  b) 案例B DSH-Media  ：media.content_production（策划 → 生成 → 审核 → 发布）
  c) 案例C DSH-Winery ：winery.quality_traceability（批次 → 记录 → 质检 → 追溯）
                        + winery.production_scheduling（销售预测 → 需求聚合 → 排程 → 采购 → 物流 → 监控）

成功：输出 JSON 证据到 --out，并打印唯一终态标记 M18.3-DEMO-OK（退出码 0）；
失败：打印 M18.3-DEMO-FAIL 与失败原因（退出码 1）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from jkos_core.audit import AuditLogger  # noqa: E402
from jkos_core.bootstrap import register_tenant_workflows  # noqa: E402
from jkos_core.db import (  # noqa: E402
    INSTANCE_COMPLETED,
    ApprovalTaskRepo,
    Database,
    DatabaseConfig,
    TenantRepo,
    WorkflowRepo,
)
from jkos_core.llm import build_llm_router  # noqa: E402
from jkos_core.workflow import WorkflowEngine  # noqa: E402

# git 空树对象：任意仓库可解析，与提交历史无关（案例A 用于采集全量 diff）
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# 演示用例定义：(案例标识, 说明, 工作流码, 租户码, context)
CASES = [
    ("A", "DSH-Dev 代码审查（d1_code_review）", "d1_code_review", "dev", {
        "repo_path": str(PROJECT_ROOT),
        "base": EMPTY_TREE,
    }),
    ("B", "DSH-Media 内容生产（media.content_production）", "media.content_production", "media", {
        "topic": "秋季品鉴会",
    }),
    ("C", "DSH-Winery 质量追溯（winery.quality_traceability）", "winery.quality_traceability", "winery", {
        "product": "赤霞珠干红·750ml",
        "quantity": 1200,
    }),
    ("C", "DSH-Winery 生产调度（winery.production_scheduling）",
     "winery.production_scheduling", "winery", {"region": "华东-上海", "window": "2026-Q4"}),
]


def build_engine(workdir: str) -> tuple[Database, WorkflowEngine]:
    """构造演示用引擎（独立临时库 + 三租户工作流）"""
    db = Database(DatabaseConfig(path=os.path.join(workdir, "demo.db"))).connect()
    db.migrate()
    comps = SimpleNamespace(
        db=db,
        tenants=TenantRepo(db),
        workflows=WorkflowRepo(db),
        approvals=ApprovalTaskRepo(db),
        audit=AuditLogger(db),
        llm=build_llm_router(),  # 无 API Key 时走确定性模拟供应商
        jwt=None,
    )
    engine = WorkflowEngine(comps)
    register_tenant_workflows(engine)  # dev 显式注册（media/winery 由 nodes.py 惰性挂载）
    return db, engine


async def drive_approvals(engine: WorkflowEngine, instance_id: str,
                          tenant_code: str, max_rounds: int = 8) -> list:
    """自动走完实例的审批节点（按任务审批人列表逐个同意），返回决策记录"""
    decisions: list = []
    for _ in range(max_rounds):
        pending = [
            t for t in engine.list_pending_approvals(tenant_code)
            if t.get("instance_id") == instance_id
        ]
        if not pending:
            break
        task = pending[0]
        decided = {d["task"] for d in decisions}
        approvers = task.get("approvers") or ["admin"]
        used = {d["approver"] for d in decisions if d["task"] == task["id"]}
        approver = next((a for a in approvers if a not in used), None)
        if approver is None:
            if task["id"] in decided:
                break
            approver = approvers[0]
        await engine.approve_task(task["id"], True, approver, reason="演示自动同意")
        decisions.append({"task": task["id"], "approver": approver})
    return decisions


async def run_case(engine: WorkflowEngine, label: str, title: str,
                   workflow_code: str, tenant_code: str, context: dict) -> dict:
    """跑通单个演示用例，返回证据字典（含审批轨迹与步骤状态）"""
    status = await engine.start(workflow_code, tenant_code, context)
    instance_id = status["instance"]["id"]
    waiting = status["instance"]["status"]
    decisions = await drive_approvals(engine, instance_id, tenant_code)
    if decisions:
        status = engine.status(instance_id)  # 审批后刷新快照（status 为同步方法）

    steps = status["steps"]
    failed = [s for s in steps if s["status"] != "SUCCEEDED"]
    final = status["instance"]["status"]
    ok = final == INSTANCE_COMPLETED and not failed
    print(f"== {label}: {title} ==")
    print(f"   实例: {instance_id}  首停: {waiting}  终态: {final}  "
          f"步骤: {len(steps)}  审批决策: {len(decisions)}")
    for s in steps:
        print(f"   - {s['node_code']:<14} {s['status']}")
    if failed:
        print(f"   失败步骤: {[s['node_code'] for s in failed]}")
    return {
        "case": label,
        "title": title,
        "workflow_code": workflow_code,
        "tenant_code": tenant_code,
        "instance_id": instance_id,
        "initial_status": waiting,
        "final_status": final,
        "steps": [{"node_code": s["node_code"], "status": s["status"]} for s in steps],
        "approval_decisions": decisions,
        "passed": ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M18.3 三案例产品化演示")
    parser.add_argument("--out", default="reports/m18/demo_three_cases.json",
                        help="证据 JSON 输出路径（默认 reports/m18/demo_three_cases.json）")
    args = parser.parse_args()

    workdir = tempfile.mkdtemp(prefix="jkos_m18_demo_")
    db, engine = build_engine(workdir)
    results: list = []
    try:
        for label, title, code, tenant, context in CASES:
            try:
                results.append(asyncio.run(
                    run_case(engine, label, title, code, tenant, context)))
            except Exception as exc:  # 单案例失败不阻断其余案例，最终统一报错
                print(f"== {label}: {title} ==")
                print(f"   [失败] {type(exc).__name__}: {exc}")
                results.append({
                    "case": label, "title": title, "workflow_code": code,
                    "tenant_code": tenant, "final_status": "ERROR",
                    "error": f"{type(exc).__name__}: {exc}", "passed": False,
                })
    finally:
        db.close()

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = PROJECT_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    passed = all(r["passed"] for r in results)
    payload = {
        "milestone": "M18.3",
        "cases": results,
        "passed": passed,
        "total": len(results),
        "ok_count": sum(1 for r in results if r["passed"]),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n证据已写入: {out_path}")
    print(f"通过 {payload['ok_count']}/{payload['total']}")
    if passed:
        print("M18.3-DEMO-OK")
        return 0
    print("M18.3-DEMO-FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
