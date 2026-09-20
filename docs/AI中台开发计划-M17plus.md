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

- `tests/test_optimization/` 独立测试目录（test_process_solidifier / test_token_optimizer / test_template_manager / test_automation_engine / test_facade_lifecycle）
- `scripts/benchmarks/bench_optimization.py` Token 消耗与耗时基准脚本
- 覆盖率报告（`jkos_core/optimization/` 各模块 ≥ 85%）
- Token 消耗基准报告（首次全量 vs 命中缓存，量化 90%+ 目标）
- 《AI中台开发计划-M6-M10.md》M12 验收补勾 + 版本更新说明

### M17 验收（对应 M12 验收，全部达成才可勾选）

- [x] Token 消耗降低 **90%+**（基准：首次 15000 → 缓存回放 100，节省 99.33%）
- [x] 自动化执行成功率 **> 95%**（45/45 = 100%）
- [x] 模板库覆盖 **50+ 场景**（61 场景，含 `templates/seed_data.py` 种子模板扩充）
- [x] 自动化执行时间 **< 人工执行的 20%**（基准：24.8ms/任务 vs 人工基线 1800s/任务）
- [x] `jkos_core/optimization/` 覆盖率 **≥ 85%**（实测各模块 98%~100%）

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
| 18.1 生产调度 + 质量追溯 | P0 | 3天 | ✅ 完成（2026-09-19）。`tenants/winery/` 案例C：批次追踪、步骤留痕、异常告警闭环；修复注册冲突见版本记录 0.3.0 |
| 18.2 多租户 L2 schema 隔离 | P0 | 3天 | ✅ 完成（2026-09-20）。SQLite 独立 `.db` 真实隔离 + PG DDL 渲染就绪 + 运行时接线（默认关闭）+ 生命周期/安全加固；见版本记录 0.4.0 |
| 18.3 产品化三方验收 | P1 | 2天 | ✅ 完成（2026-09-20）。三案例演示脚本（`scripts/demo_three_cases.py`）、服务器验收脚本（`scripts/m18_accept82.sh`）、验收报告与已知问题清单 |

### 产出物

- 生产调度/质量追溯模块与用例
- 多租户隔离迁移说明与验证用例
- 三案例 P1 验收报告（`docs/M18-产品化P1验收报告.md`）

### M18 验收

- [x] 案例C 酒厂 4 条工作流端到端可跑通（`tests/test_winery.py` 7/7）
- [x] 多租户 L2 物理隔离真实落地：独立 `.db` 文件 + 幂等迁移，`tenant_schema.py` 覆盖率 **100%**
- [x] 运行时接线默认关闭、行为等价（隔离关闭时 `tenant_schemas is None`，既有用例零退化）；开启后 L2 租户实例只落独立库、L1 仍走主库、跨租户查询 403
- [x] 三案例演示全部 COMPLETED（4/4），终态标记 `M18.3-DEMO-OK`
- [x] 全量回归 **999 passed / 3 skipped / 0 failed**（M16 基线 887，零退化）；行覆盖率 **93%**（≥ 88%）
- [x] 0.82 双 venv 复验：py3.12 / py3.11 均 **999 passed / 3 skipped**（覆盖率 93%），`m18_accept82.sh` 终态 `M18-ACCEPT-OK`
- [x] 已知问题清单收敛（6 条，见验收报告 §七）

---

## 🚀 M19: 基础设施迁移（2周）

### 目标

按既有 ADR 落地基础设施升级，双跑对比验证后再切换：

- **SQLite（WAL）→ PostgreSQL**：迁移脚本 + 双跑对比（功能/性能一致）
- **内嵌事件总线 → NATS**：事件发布订阅替换，保留兼容层

### 任务清单

| 任务 | 优先级 | 预估工时 | 说明 |
| --- | --- | --- | --- |
| 19.1 PostgreSQL 迁移 | P0 | 5天 | 🔄 代码层完成（2026-09-20）：真实 DDL/数据转换 + asyncpg 连接池 + 迁移产物落盘；**待补**双跑对比报告、回滚方案、实际切换 |
| 19.2 NATS 总线替换 | P1 | 4天 | 🔄 代码层完成（2026-09-20）：真实 nats-py 接入 + 内嵌模式降级兼容层 + 事件回归用例；**待补**真实 NATS 服务上的回归结果 |

### 产出物

- PostgreSQL 迁移脚本与回滚方案、双跑对比报告（迁移脚本已就绪，回滚方案与双跑对比待补）
- NATS 替换说明与事件回归结果（替换与兼容层已就绪，真实服务回归待补）

### M19 验收（进行中）

- [x] 迁移脚本真实化：SQLite DDL/数据 → PostgreSQL（类型映射、`AUTOINCREMENT`→`SERIAL`、索引随表、JSON→JSONB、数据 INSERT 转义）
- [x] 真实 asyncpg 连接池（懒加载 + `ConnectionError` 语义 + 关闭接口）
- [x] NATS 总线真实接入 + 兼容层（未安装/不可达自动降级内嵌内存模式）
- [x] 全量回归零退化：本地 py3.14 与 0.82 双 venv 均 **1017 passed / 4 skipped / 0 failed**（M18 基线 999）
- [ ] 双跑对比报告（SQLite vs PostgreSQL 功能/性能一致）
- [ ] 回滚方案与实际切换
- [ ] 真实 NATS 服务上的事件回归结果
- [ ] 覆盖率不低于上一轮（当前总覆盖率 **92%**，M18 为 93%；缺口集中在需在线服务的真实连接路径）

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
| 全量测试 | 887 passed / 3 skipped | 不退化，稳步增长。M18 实测 **999 passed / 3 skipped / 0 failed** |
| 行覆盖率 | 88% | ≥ 88% 且 `optimization/` 各模块 ≥ 85%（M18 实测总覆盖率 **93%**） |
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
print(result.token_used)  # 首次全量 15000

# 再次执行（命中缓存，回放成本 ≤ 全量 10%）
result2 = await engine.execute("生成周报")
print(result2.source, result2.token_used)  # cache, 回放成本 100

await engine.close()
```

基准验证命令：

```bash
python -m pytest tests/test_optimization/ -q --tb=short --cov=jkos_core.optimization --cov-report=term-missing
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

- **0.5.0**（2026-09-20）：M19 代码层完成（验收未闭环）。19.1 PostgreSQL 迁移层真实化（`SQLITE_TO_PG_TYPE_MAP` 类型映射、DDL 解析与 `sqlite_master` 内省、真实数据 INSERT 导出、迁移产物落盘、asyncpg 连接池懒加载）；19.2 NATS 总线真实化（nats-py 接入 + 未安装/不可达自动降级内嵌模式 + 事件信封跨进程分发与幂等回调）；新增 `tests/test_m19.py` 19 用例，全量回归 **1017 passed / 4 skipped / 0 failed**（本地 py3.14 与 0.82 双 venv）。收口修复：`pyproject.toml` 重复键致 TOML 非法（阻断 pytest）、`nats.py` 重复赋值、`postgres.py` 末尾缺换行。待补：双跑对比报告、回滚方案、真实 NATS 服务回归。
- **0.4.0**（2026-09-20）：M18 收官（18.1/18.2/18.3 全部达成）。18.2 多租户 L2 物理隔离落地（SQLite 独立 `.db` + PG DDL 渲染就绪 + 运行时接线默认关闭 + 生命周期/安全加固，`tenant_schema.py` 覆盖率 100%）；18.3 三案例验收交付（`scripts/demo_three_cases.py`、`scripts/m18_accept82.sh`、`docs/M18-产品化P1验收报告.md`，演示 4/4 COMPLETED / `M18.3-DEMO-OK`）；全量回归 **999 passed / 3 skipped / 0 failed**、行覆盖率 **93%**（M16 基线 887 / 88%，零退化）。
- **0.3.0**（2026-09-19）：M18.1 收官。新增案例C 酒厂生产调度 + 质量追溯（`tenants/winery/`，20 节点 + 4 工作流 + `tests/test_winery.py` 7 用例）；修复 `nodes.py` 重复挂载冲突（重复的 winery 懒挂载块令 `BUILTIN_NODES.update(WINERY_NODES)` 执行两次，覆盖 media 的 5 个共享 handler；删除重复块 + 改用 `setdefault`，共享键 media 先注册优先），全量回归 **962 passed / 3 skipped / 0 failed**（M16 基线 887，零退化）。
- **0.1.0**（2026-09-19）：M16 收官后制定 M17+ 延续规划，M17 聚焦 M12 自动化优化引擎收官。
- **0.2.0**（2026-09-19）：M17 收官。`jkos_core/optimization/` 各模块覆盖率 98%~100%（≥85% 达标），四口径基准达标（Token 15000→100、节省 99.33%；成功率 45/45=100%；模板库 61 场景；自动执行 24.8ms/任务），《M6-M10》M12 验收 4/4 补勾；新增 `tests/test_optimization/` 78 用例与 `scripts/benchmarks/bench_optimization.py`，`run_coverage.sh` 采用 `--cov=jkos_core`。