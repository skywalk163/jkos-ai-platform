# 🚀 JKOS 开发计划 - M17+（M16 收官后的延续规划）

> **更名公告**：DSH AI 中台已于 2026-09-18 正式更名为 **JKOS（极快AI操作系统）**。
> 本文档延续《AI中台开发计划-M6-M10.md》路线图模式，规划 M17 及后续里程碑。
> M16 已于 2026-09-18 出具收官报告，工作区干净（`git status` 无未提交变更），以下为下一轮计划。

- 计划制定人：DSH AI 中台 / JKOS 开发组
- 版本：0.1.0
- 生成日期：2026-09-19
- 前置计划：《AI中台开发计划-M6-M10.md》（v1.1，覆盖 M6–M12）

---

## 🎯 背景与现状（M16 收官快照）

### M16 收官数据

| 指标 | 数值 |
| --- | --- |
| 测试结果 | **887 passed / 3 skipped / 180 warnings，58.16s** |
| 行覆盖率 | **88%**（7601 statements，934 missing） |
| M16 提交链 | `749e096` → `b5ccbc2` → `f5168b4`（前置 `1b4968f` + `afa41a8`） |
| M16 规模 | 28 文件变更，+2955 / −10 |
| 基线失败 | `TestStreamTurn::test_event_sequence`（判定 flaky，单独重跑通过） |

### 未闭环项分析：M12 自动化优化引擎

路线图《M6-M10》中 **M12「自动化优化引擎」的验收标准全部未勾选**，是规划中唯一未闭环的技术里程碑。
四大组件与当前覆盖率：

| 组件 | 模块 | 覆盖率 | 状态 |
| --- | --- | --- | --- |
| 流程固化引擎（12.1） | `jkos_core/optimization/process_solidifier.py` | **32%** | 已实现，严重缺测 |
| Token优化器（12.2） | `jkos_core/optimization/token_optimizer.py` | 待补测 | 已实现，随门面覆盖 |
| 模板管理系统（12.3） | `jkos_core/optimization/template_manager.py` | **70%** | 已实现，缺测 |
| 自动化执行引擎（12.4） | `jkos_core/optimization/automation_engine.py` | **74%** | 已实现，缺测 |

- 实现形态：`OptimizationEngine` 统一门面（缓存 → 模板 → 探索 三层执行），组件非 stub、功能完整。
- **缺独立测试目录**：`glob **/test_optimization*` 无结果，优化引擎用例不是独立成册、验收口径无法量化。
- 路径备注：路线图原文引用 `dsh_core/optimization/`，实际项目为 `jkos_core/optimization/`（更名后），下文中一律按实际路径。

### 其他低覆盖模块（M17 顺带补强）

`mcp/registry.py`（51%）、`notify/__init__.py`（55%）、`plugins/ocr/plugin.py`（57%）。

---

## 🚀 M17: 自动化引擎收官（P0，1周）

### 目标

1. `jkos_core/optimization/` 全部模块覆盖率 ≥ **85%**（当前最低 32%）。
2. 建立自动化优化引擎独立测试目录与基准脚本，量化验收口径。
3. **补勾路线图《M6-M10》M12 验收项**（M12 验收见 §M17 验收，全部达成后方可勾选）。
4. 连带补强 `mcp/registry.py`、`notify/__init__.py`、`plugins/ocr/plugin.py` 至 ≥ 80%。

### 任务清单

| 任务 | 优先级 | 预估工时 | 说明 |
| --- | --- | --- | --- |
| 17.1 流程固化引擎测试补齐 | P0 | 2天 | `ProcessSolidifier` 32% → ≥85%；覆盖 固化/版本/回滚/归档/统计 全链路 |
| 17.2 Token优化器 + 模板管理测试补齐 | P0 | 1.5天 | `TokenOptimizer`（缓存/增量/批量）与 `TemplateManager`（CRUD/版本/搜索/统计）独立用例 |
| 17.3 自动化执行引擎测试补齐 | P0 | 1.5天 | `AutomationEngine`：定时执行/事件触发/人工审批/执行日志/Token 用量汇总 |
| 17.4 验收基准验证 + 补勾 | P0 | 1天 | 基准脚本量化四口径；在《M6-M10》文档补勾 M12 验收项并更新版本记录 |

### 产出物

- `tests/optimization/` 独立测试目录（test_process_solidifier / test_token_optimizer / test_template_manager / test_automation_engine / test_optimization_engine）
- `scripts/benchmarks/bench_optimization.py` Token 消耗与耗时基准脚本
- 覆盖率报告（`jkos_core/optimization/` 各模块 ≥ 85%）
- Token 消耗基准报告（首次全量 vs 命中缓存，量化 90%+ 目标）
- 《AI中台开发计划-M6-M10.md》M12 验收补勾 + 版本更新说明

### M17 验收（对应 M12 验收，全部达成才可勾选）

- [ ] Token 消耗降低 **90%+**（基准：首次全量 vs 缓存回放）
- [ ] 自动化执行成功率 **> 95%**
- [ ] 模板库覆盖 **50+ 场景**（含 `templates/seed_data.py` 种子模板扩充）
- [ ] 自动化执行时间 **< 人工执行的 20%**（基准量化）
- [ ] `jkos_core/optimization/` 覆盖率 **≥ 85%**（当前 32%~74%）

---

## 🚀 M18: 产品化 P1（三案例，2周）

### 目标

以三案例交付物为基准（案例A DSH-Dev / 案例B DSH-Media / 案例C DSH-Winery），补齐生产级能力：

- 生产调度与质量追溯（案例C 酒厂：批次追踪闭环）
- 多租户 L2 schema 隔离（租户数据物理隔离）
- 三案例回归 **零退化**（在 M16 基线 887 passed 之上全绿）

### 任务清单

| 任务 | 优先级 | 预估工时 | 说明 |
| --- | --- | --- | --- |
| 18.1 生产调度 + 质量追溯 | P0 | 3天 | 批次追踪、步骤留痕、异常告警闭环 |
| 18.2 多租户 L2 schema 隔离 | P0 | 3天 | 数据层隔离 + 迁移脚本 + 回归 |
| 18.3 产品化三方验收 | P1 | 2天 | 三案例演示脚本、验收文档、已知问题清单收敛 |

### 产出物

- 生产调度/质量追溯模块与用例
- 多租户隔离迁移说明与验证用例
- 三案例 P1 验收报告

---

## 🚀 M19: 基础设施迁移（2周）

### 目标

按既有 ADR 落地基础设施升级，双跑对比验证后再切换：

- **SQLite（WAL）→ PostgreSQL**：迁移脚本 + 双跑对比（功能/性能一致）
- **内嵌事件总线 → NATS**：事件发布订阅替换，保留兼容层

### 任务清单

| 任务 | 优先级 | 预估工时 | 说明 |
| --- | --- | --- | --- |
| 19.1 PostgreSQL 迁移 | P0 | 5天 | 迁移脚本、双跑对比、切换 |
| 19.2 NATS 总线替换 | P1 | 4天 | 兼容层 + 事件回归 |

### 产出物

- PostgreSQL 迁移脚本与回滚方案、双跑对比报告
- NATS 替换说明与事件回归结果

---

## 🚀 M20: 自举第二期（D3 自举闭环，2周）

### 目标

**JKOS 管理 JKOS**：用自动化优化引擎（M17 成果）驱动 JKOS 自身的探索/复盘/优化流程，与 deepseek-harness 融合，形成自举闭环演示。

| 任务 | 优先级 | 预估工时 | 说明 |
| --- | --- | --- | --- |
| 20.1 D3 自举脚本 | P0 | 4天 | 探索 → 固化 → 模板化 → 自动化执行闭环 |
| 20.2 harness 融合 | P0 | 3天 | 在 harness 会话内调用 JKOS 优化引擎 |
| 20.3 闭环演示 | P1 | 2天 | 端到端演示与复盘报告 |
| 20.4 README 清理 | P2 | 0.5天 | 清除「DSH AI 中台」旧称残留，统一为 JKOS |

### 产出物

- D3 自举闭环脚本与演示记录
- harness 融合调用示例
- README 统一更名后的文档快照

---

## 📅 开发路线图（按周）

| 里程碑 | 计划窗口 | 内容 |
| --- | --- | --- |
| M17 | 第1周 | 自动化引擎收官（测试补齐 + 基准 + 补勾 M12 验收） |
| M18 | 第2–3周 | 产品化 P1（三案例 + 多租户 L2 隔离） |
| M19 | 第4–5周 | 基础设施迁移（PostgreSQL + NATS） |
| M20 | 第6–7周 | 自举第二期（D3 自举闭环 + 文档清理） |

---

## 🔀 并发任务说明

- **M17 可独立推进**，依赖 M16 收官基线（已达成）。
- **M18 与 M19 可部分并发**：M18 侧重功能层（调度/租户隔离），M19 侧重基础设施层（存储/总线），但多租户迁移需在 PostgreSQL 落地后验证，故 M19 建议先行的部分是 PostgreSQL 迁移脚本，其余并行。
- **M20 依赖 M17 成果**（自动化引擎）+ M19 基础设施（PostgreSQL/NATS 上的自举更稳定）。
- 每轮里程碑结束必须：全量测试通过、覆盖率不低于上一轮、CHANGELOG 更新、git 提交链完整。

---

## 📊 成功指标（M17–M20 总体）

| 指标 | M16 基线 | M17+ 目标 |
| --- | --- | --- |
| 全量测试 | 887 passed / 3 skipped | 不退化，稳步增长 |
| 行覆盖率 | 88% | ≥ 88% 且 `optimization/` 各模块 ≥ 85% |
| Token 消耗 | 未量化 | 缓存回放 ≤ 全量 10% |
| 自动化成功率 | 未量化 | > 95% |
| 模板库 | 种子模板 | 50+ 场景 |
| 基础设施 | SQLite + 内嵌总线 | PostgreSQL + NATS（双跑对比后切换） |
| 文档 | M16 收官报告 | M17+ 计划文档 + 各轮验收记录 |

---

## 🚀 快速开始（M17 验证示例）

```python
from jkos_core.optimization import OptimizationEngine

engine = OptimizationEngine()
await engine.initialize()

# 首次执行（全量成本）
result = await engine.execute("生成周报")
print(result.token_used)  # 首次全量约 15000

# 再次执行（命中缓存，回放成本 ≤ 全量 10%）
result2 = await engine.execute("生成周报")
print(result2.source, result2.token_used)  # cache, 1000 以内

await engine.close()
```

基准验证命令：

```bash
python -m pytest tests/optimization/ -q --tb=short --cov=jkos_core.optimization --cov-report=term-missing
python scripts/benchmarks/bench_optimization.py   # 输出 Token 消耗与耗时对比报告
```

---

## ⚠️ 风险与依赖

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| M12 验收量化主观（90% / 95% / 20%） | 验收口径争议 | 17.4 基准脚本先定口径再补勾 |
| 业务模块依赖 SQLite（WAL）语义 | M19 迁移回归 | 双跑对比 + 回滚方案 |
| 模板库 50+ 场景内容来源 | 达标周期拉长 | 由既有三案例流程反推模板，M17 先行种子扩充 |
| flaky 用例复发（`TestStreamTurn`） | 基线抖动 | 已记录；重跑判定后入 CHANGELOG |

---

## 📝 版本记录

- **0.1.0**（2026-09-19）：M16 收官后制定 M17+ 延续规划，M17 聚焦 M12 自动化优化引擎收官。