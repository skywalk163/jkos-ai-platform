"""复现「同 task 两个模板平分时 recommend_template 返回最旧模板」假设。

模拟真实链路（loop.py 第 3/4 阶段）：
  第 3 段 create_template 新建模板 B（模拟 v5-ex-2 的 t1790287229900）
  第 4 段 recommend_template(task) —— 库中已有更早的模板 A（模拟 v4-ex-1 的 t1790287181665）
期望：A/B 同 task、description 相同（或同为空的默认值）→ 完全平分。

修复前：Python 稳定排序保留 _all_sync 旧→新顺序 → 返回最旧 A → matched=False → success=False。
修复后：推荐排序 key 增加 created_at 次要键（最新优先）→ 返回最新 B → matched=True → success=True。

本脚本断言修复后行为：平分时必须返回最新模板 B。

说明：
- 「A 更旧」通过 _backdate_created_at 从库层面确定性构造（列 + data JSON 两处一致），
  不再依赖 time.sleep 制造跨秒差异——now_iso() 为秒级截断，Windows 时钟下 sleep(1.1)
  仍可能让 A/B 落在同一秒，导致 created_at 胜负键失效（上次运行实测同秒 14:28:51）。
- SQLite 连接在 finally 中显式关闭，断言失败时也能释放模板库文件句柄，
  避免 Windows 临时目录清理报 WinError 32（PermissionError）。
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from jkos_core.optimization.base import Process, ProcessStep
from jkos_core.optimization.template_manager import TemplateManager
from jkos_core.exploration.knowledge_base import keyword_score


TASK = "写一个函数：两数相加并返回结果"

# 明确早于 B 创建时间的旧时间戳（ISO-8601，秒级，UTC）
OLD_ISO = "2020-01-01T00:00:00+00:00"


def make_process(task: str, pid: str) -> Process:
    return Process(
        process_id=pid,
        name=f"固化-{task}",
        task=task,
        steps=[ProcessStep(name="明确需求", action="明确功能与输入输出"),
               ProcessStep(name="编写实现", action="实现两数相加函数"),
               ProcessStep(name="验证输出", action="补充注释并验证")],
        description="",  # 与真实场景一致：description 为空串
    )


def _backdate_created_at(db_path: str, template_id: str, target_iso: str) -> None:
    """把模板库中指定模板的 created_at 改写为目标时间（确定性构造「更旧」）。

    两处必须同时改，保证一致（_all_sync 按 created_at 列排序，_hydrate 从
    data JSON 内读取 created_at 字段）。
    """
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT data FROM template_store WHERE template_id=?", (template_id,)
        ).fetchone()
        assert row is not None, f"模板行不存在: {template_id}"
        d = json.loads(row[0])
        d["created_at"] = target_iso
        conn.execute(
            "UPDATE template_store SET created_at=?, data=? WHERE template_id=?",
            (target_iso, json.dumps(d, ensure_ascii=False), template_id),
        )
        conn.commit()
    finally:
        conn.close()


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "templates.db")
        tm = TemplateManager(db_path=db_path)
        try:
            print(f"[DB] {db_path}  种子模板数: {tm.count()}")

            # 1) 模拟第一次运行（v4-ex-1）：创建旧模板 A
            old = await tm.create_template(make_process(TASK, "rp-old-process"))
            print(f"[A-旧] template_id={old.template_id}  created_at={old.created_at}")

            # 2) 确定性构造「A 更旧」：把 A 的 created_at（列 + JSON）回填为明确更早的值，
            #    与真实场景中「v4 比 v5 早创建」等价
            _backdate_created_at(db_path, old.template_id, OLD_ISO)
            print(f"[A-改] created_at 回填为 {OLD_ISO}")

            # 3) 模拟第二次运行（v5-ex-2）：创建新模板 B（本轮刚创建的被推荐对象）
            new = await tm.create_template(make_process(TASK, "rp-new-process"))
            print(f"[B-新] template_id={new.template_id}  created_at={new.created_at}")

            # 4) 模拟 run_loop 第 4 阶段：recommend_template(task)
            recommended = await tm.recommend_template(TASK)
            print(f"[推荐] template_id={recommended.template_id if recommended else None} "
                  f"created_at={recommended.created_at if recommended else '-'}")

            # 完整等价于 loop.py:
            #   matched = recommended is not None and recommended.template_id == template.template_id
            #   result.template_hit = bool(result.template_hit and matched)
            matched = recommended is not None and recommended.template_id == new.template_id
            print(f"[判定] matched={matched}  ⇒  template_hit={matched}  ⇒  success={matched}")

            # 5) 打印积分排序（前 8 名），直观展示平分局面（含修复后的 created_at 胜负键）
            rows = tm._all_sync()
            scored = []
            for r in rows:
                t = TemplateManager._hydrate(r)
                task_kw = keyword_score(TASK, t.task)
                score = task_kw + 0.5 * keyword_score(TASK, t.description)
                scored.append((score, t.template_id, t.task[:20], t.created_at))
            scored.sort(key=lambda x: (x[0], x[3]), reverse=True)
            print("[积分榜 前8]（score 降序 + created_at 降序）")
            for s in scored[:8]:
                print(f"  score={s[0]:.4f} id={s[1]} task={s[2]!r} created_at={s[3]}")

            # 回归断言：平分时必须返回最新模板 B
            assert old.template_id != new.template_id, "两模板 template_id 必须不同（防冲突 id 生成）"
            assert recommended is not None, "平分局势下 recommend_template 不应返回 None"
            assert recommended.template_id == new.template_id, \
                f"平分时必须返回最新模板 B: 期望 {new.template_id}, 实际 {recommended.template_id}"
            assert matched, "闭环判定必须 matched → template_hit → success"
            print("\n[结论] 修复生效: 同 task 平分时 recommend_template 返回最新模板 B，"
                  "matched=True → template_hit=True → success=True")
        finally:
            # 6) 无论断言成败都显式关闭 SQLite 连接，避免 Windows 临时目录清理 WinError 32
            await tm.close()


if __name__ == "__main__":
    asyncio.run(main())