#!/usr/bin/env python3
"""M17 收官基准脚本：量化 M12 验收四口径（Token 消耗 / 成功率 / 模板覆盖 / 执行时间）。

口径定义（M17plus 风险：先定口径再补勾）：
  口径1  Token 消耗降低 ≥90% ：同一任务首次全量执行 vs 缓存回放（回放 ≤ 全量 10%）
  口径2  自动化执行成功率 >95% ：模板任务 + 探索任务合计成功率
  口径3  模板库覆盖 50+ 场景 ：TemplateManager.count() ≥ 50（内置种子 61 条）
  口径4  自动化执行时间 < 人工执行的 20% ：人工基线按 30 分钟/任务计，自动化须 < 360s/任务

隔离策略：全部 DB（process/template/token）指向临时目录，保证可重复测量，
不污染开发库。成功指标表无“人工执行基线”行，此处以 30 分钟/任务为人工基线。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jkos_core.optimization import (  # noqa: E402
    AutomationEngine,
    OptimizationEngine,
    ProcessSolidifier,
    TemplateManager,
    TokenOptimizer,
)
from jkos_core.optimization.templates.seed_data import SEED_TEMPLATES  # noqa: E402

# ---------------------------------------------------------------- 口径阈值
MANUAL_BASELINE_SECONDS = 30 * 60          # 人工执行一次任务基线 30 分钟
TIME_RATIO_LIMIT = 0.20                    # 自动化时间 < 人工 20%
TOKEN_SAVING_LIMIT = 0.90                  # Token 降低 ≥90%
SUCCESS_RATE_LIMIT = 0.95                  # 成功率 >95%
TEMPLATE_COUNT_LIMIT = 50                  # 模板覆盖 ≥50 场景


def _novel_task() -> str:
    """构造一个不与任何种子模板语义匹配的探索任务名。"""
    return f"m17基准探索任务-{uuid.uuid4().hex[:6]}"


async def build_engine(tmp: Path) -> OptimizationEngine:
    """用临时目录构建隔离 DB 的优化引擎。"""
    db = tmp / "db"
    db.mkdir(parents=True, exist_ok=True)
    solidifier = ProcessSolidifier(db_path=db / "process_cache.db")
    templates = TemplateManager(db_path=db / "templates.db")
    optimizer = TokenOptimizer(
        db_path=db / "token_cache.db",
        report_dir=tmp / "reports" / "token_usage",
    )
    automation = AutomationEngine(
        templates=templates,
        optimizer=optimizer,
        log_dir=tmp / "reports" / "automation",
    )
    engine = OptimizationEngine(
        solidifier=solidifier,
        optimizer=optimizer,
        templates=templates,
        automation=automation,
    )
    await engine.initialize()
    return engine


async def metric_token_saving(engine: OptimizationEngine) -> dict:
    """口径1：同一任务首次全量 vs 缓存回放，节省率 ≥90%。"""
    task = _novel_task()
    first = await engine.execute(task)
    assert first.source == "exploration", f"首次执行源应为 exploration，实际 {first.source}"
    replay = await engine.execute(task)
    assert replay.source == "cache", f"第二次执行源应为 cache，实际 {replay.source}"
    full = max(first.token_used, 1)
    replay_cost = max(replay.token_used, 1)
    saving = 1 - replay_cost / full
    return {
        "首次全量 token": first.token_used,
        "缓存回放 token": replay.token_used,
        "节省率": round(saving, 4),
        "达标(≥90%)": saving >= TOKEN_SAVING_LIMIT,
    }


async def metric_success_rate(engine: OptimizationEngine) -> dict:
    """口径2：模板任务抽样 + 探索任务，合计成功率 >95%。"""
    tasks = []
    seen: set[str] = set()
    for entry in SEED_TEMPLATES:
        t = (entry.get("task") or "").strip()
        if t and t not in seen:
            seen.add(t)
            tasks.append(t)
        if len(tasks) >= 40:
            break
    tasks.extend(_novel_task() for _ in range(5))
    results = [await engine.execute(t) for t in tasks]
    ok = sum(1 for r in results if r.success)
    rate = ok / len(results)
    return {
        "任务总数": len(results),
        "成功数": ok,
        "成功率": round(rate, 4),
        "达标(>95%)": rate > SUCCESS_RATE_LIMIT,
    }


async def metric_template_count(engine: OptimizationEngine) -> dict:
    """口径3：模板库覆盖场景数 ≥50（count 为同步方法，勿 await）。"""
    n = engine.templates.count()
    return {
        "模板场景数": n,
        "达标(≥50)": n >= TEMPLATE_COUNT_LIMIT,
    }


async def metric_exec_time(engine: OptimizationEngine) -> dict:
    """口径4：自动化平均执行耗时 < 人工基线 20%（即 <360s/任务）。"""
    task = _novel_task()
    durations_ms = []
    for _ in range(5):
        r = await engine.execute(task)
        durations_ms.append(r.duration_ms)
    avg_ms = sum(durations_ms) / len(durations_ms)
    limit_ms = MANUAL_BASELINE_SECONDS * TIME_RATIO_LIMIT * 1000
    return {
        "人工基线": f"{MANUAL_BASELINE_SECONDS}s/任务",
        "上限(20%)": f"{int(limit_ms)}ms/任务",
        "自动化平均耗时": f"{avg_ms:.1f}ms/任务",
        "达标(<20%)": avg_ms < limit_ms,
    }


async def main() -> int:
    print("=" * 72)
    print("M17 收官基准：M12 验收四口径量化")
    print("=" * 72)
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="m17_bench_") as tmpd:
        tmp = Path(tmpd)
        engine = await build_engine(tmp)
        try:
            metrics = {
                "口径1 Token消耗降低≥90%": await metric_token_saving(engine),
                "口径2 自动化成功率>95%": await metric_success_rate(engine),
                "口径3 模板覆盖≥50场景": await metric_template_count(engine),
                "口径4 执行时间<人工20%": await metric_exec_time(engine),
            }
        finally:
            await engine.close()

    elapsed = time.perf_counter() - start
    print()
    all_pass = True
    for name, m in metrics.items():
        flag = "PASS" if m["达标(≥90%)" if "达标(≥90%)" in m else
                      ("达标(>95%)" if "达标(>95%)" in m else
                       ("达标(≥50)" if "达标(≥50)" in m else "达标(<20%)"))] else "FAIL"
        all_pass = all_pass and flag == "PASS"
        print(f"[{flag}] {name}")
        for k, v in m.items():
            if k.startswith("达标"):
                continue
            print(f"       {k}: {v}")

    print("-" * 72)
    print(f"耗时 {elapsed:.1f}s，结果：{'全部达标 ✔' if all_pass else '存在未达标项 ✘'}")
    print("=" * 72)

    # 基准报告落盘（M17plus 产出物：Token 消耗基准报告）
    report = Path(__file__).resolve().parents[2] / "reports" / "benchmarks"
    report.mkdir(parents=True, exist_ok=True)
    (report / "bench_optimization_report.json").write_text(
        json.dumps({"elapsed_seconds": round(elapsed, 2), "metrics": metrics},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"基准报告已写入 {report / 'bench_optimization_report.json'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))