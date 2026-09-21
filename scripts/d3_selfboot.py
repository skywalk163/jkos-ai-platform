#!/usr/bin/env python3
"""M20.3 自举闭环演示脚本（JKOS 管理 JKOS）

用途：M20「自举第二期」端到端演示与验收证据。Windows 与 FreeBSD 均可直接运行：

    python scripts/d3_selfboot.py [--out reports/m20/d3_selfboot_loop.json]

演示内容（进程内直跑，不依赖 PostgreSQL / NATS / 外部服务）：

  1. **首次闭环**：探索（真实 ExplorationEngine）→ 固化（ProcessSolidifier）
     → 模板化（TemplateManager）→ 自动化执行（模板复用）
     D3 执行器的业务内容是「单元测试生成」：选定函数 → 用例设计 → 生成 → 运行 → 覆盖率报告
  2. **复现**：对同一 task 再跑一次，验证复现走的是**本次新建的模板**（template_hit）
  3. **量化**：冷启动基准 Token vs 复现 Token、首次耗时 vs 复现耗时

LLM 策略（混合模式）：
  - 默认接入真实 LLM 路由（有凭据走真实生成，无凭据自动降级为模拟供应商）；
  - `--no-llm` 显式不接 LLM —— D3 走确定性兜底路径，证明闭环离线可复现。

成功：输出 JSON 证据到 --out，并打印唯一终态标记 M20.3-DEMO-OK（退出码 0）；
失败：打印 M20.3-DEMO-FAIL 与失败原因（退出码 1）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from jkos_core.llm import build_llm_router  # noqa: E402
from jkos_core.selfboot.loop import DEFAULT_TASK, SelfBootstrapEngine  # noqa: E402

# 闭环成立的最小判据（与 docs/M20-自举闭环复盘报告.md 一致）
MIN_TOKEN_SAVING_RATIO = 0.9


async def run_demo(task: str, *, target: str | None, use_llm: bool,
                   data_dir: str | None, report_dir: str) -> dict:
    """跑首次闭环 + 复现 + 统计，返回证据字典"""
    llm = build_llm_router() if use_llm else None
    engine = SelfBootstrapEngine(
        llm=llm, target=target, data_dir=data_dir, report_dir=report_dir)
    try:
        first = await engine.run_loop(task, save_report=True)
        replay = await engine.replay(task, save_report=True)
        stats = await engine.stats(limit=3)
    finally:
        await engine.close()

    savings = first.savings
    checks = {
        "four_stages_ok": all(s["ok"] for s in first.stages),
        "explore_success": bool(first.first_run.get("success")),
        "template_hit": bool(first.template_hit),
        "replay_uses_same_template": (
            replay.template_hit
            and replay.replay_run.get("template_id") == first.template_id),
        "replay_source_is_template": replay.replay_run.get("source") == "template",
        "token_saving_above_90pct": (
            savings.get("token_saved_ratio", 0.0) > MIN_TOKEN_SAVING_RATIO),
    }
    return {
        "milestone": "M20.3",
        "task": task,
        "target": first.target,
        "llm_mode": "router" if use_llm else "offline-deterministic",
        "llm": first.llm,
        "first_run": first.to_dict(),
        "replay": replay.to_dict(),
        "stats": stats,
        "checks": checks,
        "passed": all(checks.values()),
    }


def print_evidence(payload: dict) -> None:
    """打印人类可读的演示证据"""
    first, replay = payload["first_run"], payload["replay"]
    savings = first["savings"]
    print("== M20.3 D3 自举闭环演示（JKOS 管理 JKOS） ==")
    print(f"task: {payload['task']}")
    print(f"target: {payload['target']}     LLM 模式: {payload['llm_mode']}")
    print(f"LLM 生成: {first['llm']}")
    print("\n-- 首次闭环四段 --")
    for stage in first["stages"]:
        flag = "OK  " if stage["ok"] else "FAIL"
        print(f"   [{flag}] {stage['name']:<9} {stage['detail']}")
    print("\n-- D3 流水线五阶段（单元测试生成） --")
    for stage in (first.get("d3") or {}).get("stages", []):
        flag = "OK  " if stage["ok"] else "FAIL"
        print(f"   [{flag}] {stage['stage']:<9} {stage['detail']}")
    print(f"   用例 {len((first.get('d3') or {}).get('cases', []))} 个，"
          f"生成方式 {first['first_run'].get('generation_source', '?')}，"
          f"覆盖率 {(first['first_run'].get('coverage') or {}).get('percent', '?')}%")
    print("\n-- 量化：冷启动 vs 复现 --")
    print(f"   Token : {savings['cold_start_tokens']} → {savings['replay_tokens']}"
          f"（省 {savings['token_saved_ratio'] * 100:.2f}%）")
    print(f"   耗时  : {savings['first_duration_ms']} ms → "
          f"{savings['replay_duration_ms']} ms"
          f"（省 {savings['duration_saved_ratio'] * 100:.2f}%）")
    print("\n-- 复现（同一 task 再跑一次） --")
    print(f"   模板命中: {replay['template_hit']}  "
          f"复用模板: {replay['replay_run'].get('template_id')}  "
          f"来源: {replay['replay_run'].get('source')}")
    print(f"\n-- 判据 --")
    for name, ok in payload["checks"].items():
        print(f"   [{'OK' if ok else 'FAIL'}] {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="M20.3 D3 自举闭环演示")
    parser.add_argument("--out", default="reports/m20/d3_selfboot_loop.json",
                        help="证据 JSON 输出路径（默认 reports/m20/d3_selfboot_loop.json）")
    parser.add_argument("--task", default=DEFAULT_TASK,
                        help="闭环任务文案（默认使用含独有词的 DEFAULT_TASK）")
    parser.add_argument("--target", default=None,
                        help="D3 目标函数 <模块>:<限定名>（默认用平台内置示例目标）")
    parser.add_argument("--no-llm", action="store_true",
                        help="不接 LLM：D3 走确定性兜底，证明闭环离线可复现")
    parser.add_argument("--data-dir", default=None,
                        help="闭环数据目录（默认每次运行使用独立临时目录）")
    args = parser.parse_args()

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = PROJECT_ROOT / out_path
    # 每次运行使用独立数据目录：避免上一轮的探索知识/模板干扰本次结论
    data_dir = args.data_dir or tempfile.mkdtemp(prefix="jkos_m20_selfboot_")

    try:
        payload = asyncio.run(run_demo(
            args.task, target=args.target, use_llm=not args.no_llm,
            data_dir=data_dir, report_dir=str(out_path.parent)))
    except Exception as exc:  # 演示失败要给出可读原因而非堆栈
        print(f"[失败] {type(exc).__name__}: {exc}")
        print("M20.3-DEMO-FAIL")
        return 1

    payload["data_dir"] = data_dir
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print_evidence(payload)
    print(f"\n证据已写入: {out_path}")
    if payload["passed"]:
        print("M20.3-DEMO-OK")
        return 0
    failed = [k for k, ok in payload["checks"].items() if not ok]
    print(f"未通过判据: {failed}")
    print("M20.3-DEMO-FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())