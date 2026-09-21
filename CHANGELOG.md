# Changelog

> 记录极快AI操作系统（JKOS，原名 DSH AI 中台）各里程碑与后续变更。
> 格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本语义参考 [SemVer](https://semver.org/lang/zh-CN/)。
> M2-M4 为早期规划实现的里程碑，功能已折叠计入后续里程碑提交，无独立提交记录。

## [M20] 自举第二期（D3 自举闭环）- 2026-09-21（**验收闭环**）

### 新增（20.1 D3 自举闭环）
- **新增编排层包 `jkos_core/selfboot/`**（消费 `exploration` + `optimization`，与 `bootstrap.py` 的组件装配职责区分）：
  - `d3_testgen.py`：D3 五阶段执行器（`select → design → generate → run → coverage`），业务内容为**单元测试生成**。用 `ast` 定位目标函数（含类方法与嵌套函数，**不 import 被测模块**）；`PytestRunner` 协议 + `SubprocessPytestRunner` 真实实现（临时目录内 `cwd`、注入 `PYTHONPATH`、**剔除 `PYTEST_ADDOPTS` 防继承仓库 `--cov` 造成递归**、超时即杀进程）；覆盖率按**被测函数行区间**统计，`coverage` 不可用时退化为代理指标并标注 `coverage_source`
  - `loop.py`：四段闭环编排 `SelfBootstrapEngine`（探索 → 固化 → 模板化 → 自动化执行）+ `PipelineSearcher`（把 D3 五阶段作为唯一方案候选，替代会扫全仓 doc header 充当步骤的 `SolutionSearcher`）
- **混合模式生成用例**：LLM 可用（`LLMResult.simulated is False`）走真实生成，否则确定性兜底；降级判定基于 `simulated` 而非 `llm is None`（`comps.llm` 为 `LLMRouter`，永不为 None，无凭据时路由末尾 `SimulatedProvider` 返回模拟文本）
- **生成物三层防护**：`compile` 语法校验 + AST 导入白名单（仅 `pytest` / 本项目包 / 被测模块）+ 危险名与危险模块拒绝（`eval`/`exec`/`open`/`os`/`subprocess` 等），任一层不过即回退确定性模板；生成物**只写 `tempfile.mkdtemp()`，绝不落仓库**
- **量化口径**：冷启动基准 = `EXPLORE_BASE × max(子任务数, 5 个 D3 阶段)`（与 `AutomationEngine` 探索分支同源）；复现成本取模板执行的 `token_used`。**实测 7500 → 305 Token（省 95.93%），耗时 3745 ms → 1 ms（省 99.97%）**

### 新增（20.2 harness 融合）
- **两条 MCP 工具**（`jkos_core/mcp/server.py`，工具数 11 → 13）：
  - `dsh_optimize_execute`：跑完整闭环（命中模板则复用确定性产出，否则真实探索并沉淀模板），返回 `source` / `template_hit` / `savings` 与四段阶段摘要（**不含生成代码正文**，避免撑爆响应）
  - `dsh_optimize_stats`：模板库条目数 / Token 缓存条目数 / 用量汇总 / 最近执行记录
- **生命周期**：模块级惰性单例 `get_selfboot_engine(comps)`（仿 `harness/gateway.py` 的 `get_gateway()`），`comps` 取自 `WorkflowEngine.comps`（**不新增构造参数**，`create_app` / `cli.py` 零改动）；`ToolExecutor.cleanup()` 追加 `close_selfboot_engine()` 释放 sqlite 连接
- **分类与可见性**：`dsh_optimize` 归入既有 `ToolCategory.GOVERNANCE`（**不新增枚举值** —— `registry.get_categories()` 返回枚举全集，会打破工具路由分类数断言）；沿用 `ToolVisibility.CORE`，匿名可见，严格模式下匿名 401

### 新增（20.3 演示与验收）
- `scripts/d3_selfboot.py`：首次闭环 + 复现 + 统计，输出六条判据的 JSON 证据，终态 `M20.3-DEMO-OK`；`--no-llm` 显式离线路径证明确定性可复现；每次运行使用独立临时数据目录，避免上一轮的探索知识/模板干扰结论
- `scripts/m20_accept82.sh`：0.82 六段验收（双 venv 全量回归 / `tests/test_selfboot` 独立成册 / 演示双模式 / harness 融合真实调用 / 残留扫描），任一关键段失败即 `exit 1`，终态 `M20-ACCEPT-OK`
- `docs/M20-自举闭环复盘报告.md`：四段证据、量化对比、混合模式双跑、已知问题与边界

### 🐛 修复（实施中暴露）
- **`inspect.iscoroutinefunction(实例)` 为 False**：`Experimenter._run_one` 据此区分同步/异步执行器，可调用实例（`__call__` 为 async）会被判为同步丢进 `asyncio.to_thread`，拿到未 await 的协程对象而静默"成功"→ D3 执行器改为传递**绑定方法** `run_as_executor`，并在 docstring 与用例中锁住该契约
- **`require_approval` 硬编码 `pending_approval=True`**：`AutomationEngine.execute` 在任务已有可推荐模板时直接走模板执行、不挂起审批（既有语义）→ 改为按 `run.meta.status` 如实回传，并补「无模板挂起 / 有模板不挂起」两条用例
- **闭环成立判据缺 `template_hit`**：仅"四段都 OK + 探索成功"不足以证明引擎学到了该任务（推荐可能被内置种子模板抢走）→ `LoopResult.success` 增加 `template_hit` 条件

### 📄 文档（20.4 旧称与陈旧信息收口）
- `README.md`：测试徽章与状态由 `789 passed` 更新为实测值；「8 个预定义工具」→ **13**；工具表补齐 `dsh_approval_*`（3）与 `dsh_optimize_*`（2）；`dsh-server` → `jkos-server`（含 CLI 示例）；项目结构与开发命令中的 `dsh_core/` → `jkos_core/`；里程碑表补齐 M15–M20
- `CONTEXT.md` / 本文件历史条目中的旧称按 M15「历史文档称谓不变」口径保留（`scripts/**` 与历史计划文档同样保留）

### 测试
- 新增 `tests/test_selfboot/`（6 文件、**203 用例**）：`test_d3_locate`（定位 / 解析 / 路径越界防护）、`test_d3_pipeline`（五阶段 / 安全校验 / 兜底与回退 / 失败两态）、`test_d3_runner`（命令构造 / 环境隔离 / 覆盖率解析 / 超时）、`test_selfboot_loop`（四段顺序 / 模板复用语义 / 节省量化 / 生命周期）、`test_selfboot_mcp`（工具注册 / 可见性 / 端点字段 / 单例 / 真实调用）
- MCP 兼容性回归（八件套）**214 passed**，工具数 11 → 13 零退化
- 全量回归：本地 py3.14 **1230 passed / 8 skipped / 0 failed**（用例总数 1238 = M19 记录的 1035 + 本轮新增 203，零失败；8 项 skip 均为需真实 harness sidecar / PostgreSQL / NATS 的环境用例，在 0.82 会转为通过）
- 覆盖率：总覆盖率 **93%**（8695 语句 / 572 missing；M19 为 93%，持平）；新增 `jkos_core/selfboot/` **100%**（619 语句 / 0 未覆盖）

### ✅ 验收闭环（M20 验收达成，详情见 `docs/M20-自举闭环复盘报告.md`）
- **四段闭环成立**：`template_hit=True`、复现 `source="template"` 且复用本次新建 `template_id`、四段 `stages` 全 OK
- **D3 五阶段真实运行**：真跑 pytest 子进程 + 真实覆盖率采集（80.0%，按函数行区间）
- **混合模式双跑**：LLM 路由模式与 `--no-llm` 均 `M20.3-DEMO-OK`
- **harness 融合实测**：匿名 401 → 带 token **13** 工具（驼峰合规）→ 真实调用 `dsh_optimize_execute` 闭环成立（省 95.93%）→ 二次调用命中模板
- **残留收口**：代码层 + README/CONTEXT 扫描 0 命中
- **0.82 双 venv 复验**：py3.12 / py3.11 均 **1229 passed / 9 skipped / 0 failed**（覆盖率 93%），`tests/test_selfboot` 203 passed，`scripts/m20_accept82.sh` 终态 `M20-ACCEPT-OK`（exit 0）

## [M19] 基础设施迁移（PostgreSQL + NATS）- 2026-09-20（**验收闭环**）

### 新增（19.1 PostgreSQL 迁移层真实化）
- **真实 SQLite → PostgreSQL 转换**（`jkos_core/db/postgres.py`）：
  - `SQLITE_TO_PG_TYPE_MAP` 类型映射（`DATETIME`→`TIMESTAMP`、`JSON`→`JSONB`、`BLOB`→`BYTEA` 等）
  - `generate_migration_sql`：三档取源（显式 DDL 解析 / `sqlite_master` 内省 / 通用骨架兜底），解析 CREATE TABLE 列定义（感知括号与引号的顶层逗号切分）、`AUTOINCREMENT`→`SERIAL`、DEFAULT 表达式转换；索引随表产出，SQLite `RAISE` 触发器以注释保留待手工改写
  - `generate_data_migration_sql`：从源库导出真实数据为批量 INSERT（单引号转义、JSON→`::jsonb`、bytes→`E'\\x..'`、时间类型字面量）
  - `execute_migration`（生成迁移产物，目标库无需在线）+ `write_migration_artifacts`（落盘 DDL + 数据 SQL）+ `execute_migration_async`（真实 asyncpg 执行，失败置 FAILED）
- **真实 asyncpg 连接池**：`ConnectionPoolManager.get_async_pool()` 懒加载（asyncpg 未安装或目标库不可达抛 `ConnectionError`），新增 `close_async_pool` / `close_all_async`，`get_stats()` 增加 `async_pool_count`

### 新增（19.2 NATS 总线真实化）
- **真实 nats-py 接入**（`jkos_core/bus/nats.py`，可选依赖组 `nats`）：未安装或服务器不可达时自动降级内嵌内存模式，`mode` 属性区分 `nats` / `embedded`
  - publish 走真实 subject 发布（携带完整事件信封），失败回滚为 `PENDING` 且不落本地事件表
  - subscribe 联动真实 NATS 订阅（支持 queue group），远端消息经信封解码 → `handle_event` 幂等分发；unsubscribe / disconnect 联动取消订阅与关闭连接

### 🐛 修复（本次收口）
- **`pyproject.toml` 重复 `nats = [...]` 键**：导致 TOML 非法、pytest 完全无法启动（阻断性）→ 删除重复行
- **`jkos_core/bus/nats.py`** `__init__` 重复赋值 `_nc_subs` → 删除重复行
- **`jkos_core/db/postgres.py`** 文件末尾缺换行 → 补齐

### 测试
- `tests/test_m19.py`：**19 用例（18 passed / 1 skipped）**，跳过项为真实 NATS 服务不可达时的连接冒烟
- 新增真实双跑/回滚/切换用例：`tests/test_m19_duelrun.py`（真实 PostgreSQL 16.4 在线执行）+ `tests/test_models.py`
- 全量回归：本地 py3.14 **1032 passed / 3 skipped / 0 failed**（M18 基线 999，零退化）
- 覆盖率：总覆盖率 **93%**（M18 为 93%，M16 基线 88%；达标）；`bus/nats.py` **97%**、`db/postgres.py` **86%**、`models/__init__.py` **100%**

### ✅ 验收闭环（M19 验收达成，详情见 `docs/M19-基础设施迁移验收报告.md`）
- **19.1 真实 PostgreSQL 双跑 / 回滚 / 实际切换**：在真实 PG 16.4（127.0.0.1:5432，库 `dsh_ai`）上完成迁移 SQL 生成 → asyncpg 执行 → 源/目标数据双跑对比 → 回滚 → 实际切换，全部通过（三次生产修复：`_execute_async_script` 缓冲生成 SQL 仅提交完整语句，修复 `syntax error at end of input`；`_terminate_statement_lines` 为内省所得索引/触发器语句补全 `;`，修复 `syntax error at or near "CREATE"`；测试侧 `username=`→`user=` 连接参数字典）
- **19.2 真实 NATS 事件回归**：真实 NATS v2.15.0（127.0.0.1:4222）上 publish / ack / durable subscribe 往返通过；`_event_to_dict` 以 `getattr` 回退增强 payload / metadata / status / created_at 健壮性
- **覆盖率 ≥ 93%**：全套件 1032 用例总覆盖率 **93%** 达标
- **双跑对比报告**：`docs/M19-基础设施迁移验收报告.md`（功能/性能一致、回滚方案、切换步骤与证据、已知问题）

## [M18] 产品化 P1（案例C 酒厂 + 多租户 L2 隔离）- 2026-09-20

### 新增（18.1 案例C 酒厂）
- **案例C 酒厂生产调度与质量追溯**（`tenants/winery/`）：20 个业务节点 + 4 条工作流（生产调度 / 质量追溯 / 营销决策 / 危机告警），回收页、复检、批次追踪闭环
- **独立测试**（`tests/test_winery.py`，7 用例）：节点注册 20 项 + 工作流注册 4 条 + 端到端运行 + 生产调度审批路径

### 新增（18.2 多租户 L2 schema 隔离）
- **真实物理隔离**（`jkos_core/db/tenant_schema.py`）：SQLite 模式下 L2 租户 = 独立 `.db` 文件，`create_schema` / `migrate_schema` / `drop_schema` 执行真实 SQL（`MIGRATIONS` 幂等迁移）；PostgreSQL 模式经 `render_pg_schema_sql` 渲染可再生 DDL，假连接串全程零连接（延续 M4.4 契约）
- **运行时接线（默认关闭）**：`TenantIsolationConfig`（`JKOS_TENANT_L2_ENABLED` / `DSH_TENANT_DATA_DIR` / `JKOS_TENANT_L2_CODES`）+ `AppComponents.database_for()` / `engine_for()` + `wire_tenant_isolation()`（单租户失败降级 L1，不阻塞启动）；API 审批路由经 `engine_provider` 按 `ctx.tenant_code` 取租户引擎，L2 模式下跨租户查询 403
- **生命周期与安全**：新增 `close` / `close_all` / `unregister_tenant`；租户码白名单校验（防路径穿越）；`data_dir` 可配置（参数 + 环境变量）
- **独立测试**：`tests/test_m18_2.py`（28 用例）+ `tests/test_m18_2_runtime.py`（9 用例）

### 新增（18.3 产品化三方验收）
- **三案例演示脚本**（`scripts/demo_three_cases.py`）：案例A `d1_code_review`（含审批闭环）/ 案例B `media.content_production` / 案例C `winery.quality_traceability` + `winery.production_scheduling`，输出 JSON 证据并打印终态标记 `M18.3-DEMO-OK`
- **服务器验收脚本**（`scripts/m18_accept82.sh`）：双 venv 全量回归 + 三案例演示 + L2 隔离接线冒烟，终态标记 `M18-ACCEPT-OK`
- **验收报告**（`docs/M18-产品化P1验收报告.md`）：AC 表、演示证据、覆盖率、已知问题清单

### 🐛 修复
- **工作流节点注册冲突**（`jkos_core/workflow/nodes.py`）：winery 懒挂载块被执行两次，`BUILTIN_NODES.update(WINERY_NODES)` 把 media 的 5 个共享 handler（`content_generator` / `content_reviewer` / `sentiment_monitor` / `sentiment_analyzer` / `crisis_alerter`）覆盖，导致 `test_workflow_m2.py` 4 个用例失败。修复：删除重复挂载块 + 改用 `setdefault`（media 先注册优先）；工作流注册表无代码冲突，保持 `update`。
- **租户连接泄漏**（`tenant_schema.py`）：`create_schema` 中 `migrate()` 抛异常时连接既不关闭也不入连接表 → 迁移失败先 `close()` 再抛异常。
- **`connection.py` 重复类定义**：文件末尾重复定义的 `ConnectionPool` / `ConnectionPoolTimeout`（逐行等价）已删除，保留单一定义。
- **`tests/test_winery.py` 审批分支**：`engine.status()` 为同步方法，原 `run(engine.status(...))` 会抛 `TypeError`（该分支此前未被触发）。

### 测试
- 全量回归（本地 py3.14）：**999 passed / 3 skipped / 0 failed**（M16 基线 887 → M17 962 → M18 999，零退化）
- 0.82 服务器双 venv：py3.12 **999 passed / 3 skipped**（49.31s）、py3.11 **999 passed / 3 skipped**（46.62s），覆盖率均 **93%**
- 行覆盖率：**93%**（M16 基线 88%）；`jkos_core/db/tenant_schema.py` **100%**
- 三案例演示：本地与 0.82 均 **4/4 COMPLETED**，终态 `M18.3-DEMO-OK`；`scripts/m18_accept82.sh` 终态 `M18-ACCEPT-OK`

## [M17] 自动化优化引擎收官（M12 验收补勾）- 2026-09-19

### 新增
- **优化引擎独立测试**（`tests/test_optimization/`，78 用例）：ProcessSolidifier（固化/版本/回滚/归档/统计）、TokenOptimizer（缓存/增量/批量）、TemplateManager（CRUD/版本/搜索/统计）、AutomationEngine（定时/事件/审批/日志/Token 汇总）、OptimizationEngine 门面全链路
- **模板库种子扩充**：`templates/seed_data.py` 61 个种子模板，覆盖 50+ 场景
- **四口径基准脚本**（`scripts/benchmarks/bench_optimization.py`）：量化 Token 消耗与执行耗时，报告输出 `reports/benchmarks/bench_optimization_report.json`

### 验收达标（M12 验收 4/4 + M17 验收 5/5 补勾）
- 口径1 Token 消耗：首次全量 **15000** → 缓存回放 **100**，节省 **99.33%**（目标 90%+）
- 口径2 自动化执行成功率：**45/45 = 100%**（目标 >95%）
- 口径3 模板库覆盖：**61 场景**（目标 50+）
- 口径4 自动化执行时间：**24.8ms/任务**，远低于人工基线 1800s/任务的 20% 上限（360s/任务）
- 覆盖率：`jkos_core/optimization/` 各模块 **98%~100%**（base 98% / process_solidifier 98% / template_manager 98% / token_optimizer 99% / facade + automation_engine 100%），目标 ≥85%

### 测试
- `tests/test_optimization/`：**78 passed / 16 warnings**（13.97s）

## [M16] 智能中枢（deepseek-harness 集成）- 2026-09-18

### 新增
- **智能中枢模块**（`jkos_core/harness/`）：把 DeepSeek 官方 agent harness 经官方 Python SDK 以 **sidecar 形态**嵌入 JKOS，承接 Agent 会话、工具编排与子代理
  - `config.py` — `HarnessConfig.from_env()`，**默认关闭**（`JKOS_HARNESS_ENABLED=false`，保证既有功能零回归），密钥字段提供 `redacted()`/`describe()` 脱敏
  - `runtime.py` — `SidecarManager`：懒启动、单实例复用、降级标记、幂等关闭；`dsh_bin` 显式传入以绕开 FreeBSD 无 wheel 的 `deepseek-harness-runtime-bin`
  - `gateway.py` — 会话管理（按租户隔离）+ 事件流；同步 SDK 经 `asyncio.to_thread` 卸载，事件跨线程经 `loop.call_soon_threadsafe` 投递
  - `routes.py` — `/api/v1/harness/*`：`POST /sessions`(201)、`GET /sessions`、`POST /sessions/{id}/messages`(SSE)、`GET /health`；鉴权复用 `get_tenant_context`（401），配额复用租户令牌桶（429 + Retry-After）
  - `toolbridge.py` — ToolBridge：生成 harness `dsh-mcp-client` 配置行（`streamable-http` 直连 JKOS `/mcp`）；**缺 token 时拒绝构建**，防止匿名上下文导致全租户工具可见
  - `proxy.py` — harness Web UI 反代（`/harness/ui/*`），改写 Host 以满足 harness 的 loopback 信任围栏，并对 HTML 绝对资源路径做前缀改写
- **bootstrap 接线**：`AppComponents.harness` 字段 + 启用时构建 + `close()` 清理 sidecar
- **配置模板**：`.env.example` 增 `JKOS_HARNESS_*` / `DEEPSEEK_API_KEY` / `JKOS_MCP_TOKEN` 段落
- **文档**：`CONTEXT.md`（领域词汇表）、`docs/adr/0001-harness-sidecar-intelligent-hub.md`（sidecar 形态决策）

### 📌 部署要点
- **FreeBSD（0.82）**：官方 runtime wheel 无 FreeBSD 版本，须源码构建并使用 `JKOS_HARNESS_DSH_BIN` 指向 launcher；SDK 用 `pip install --no-deps deepseek-harness-sdk`（或从锁定 checkout 安装）安装
- **Windows/macOS/Linux**：直接用 SDK 自带 bundled runtime（`dsh_bin` 留空）
- 凭据由 JKOS 环境注入 harness 子进程，harness 不落密钥文件
- **harness profile patch 生成**（`jkos_core/harness/toolbridge.py`）：`apply_profile_patch()` 写 `$DSH_HOME/profiles/sdk/cordis.patch.yml`，含两行
  - `- id: llm-deepseek` + `protocol: chat-completions`：公网 `api.deepseek.com` 只支持 Chat Completions，harness 默认 messages 协议会 404；**patch 行必须带既有行 id 才生效**（无 id 会被静默忽略）
  - `- insert:` 包裹 `mcp-jkos` 行：ToolBridge 桥接行，经 `streamable-http` 回调 JKOS `/mcp`（默认 `http://127.0.0.1:3000/mcp`）——**新增条目必须用 insert 语义**

### 🧪 测试与验收（AC 全部通过）
- 新增 **81 个用例**（config / gateway / routes / toolbridge / proxy）+ 3 个真实 runtime 集成用例（默认 skip，需 `JKOS_HARNESS_INTEGRATION=1`）
- 回归：本地 py3.14 **870 passed / 3 skipped**；0.82 py3.12 与 py3.11 各 **870 passed / 3 skipped / 88%**
- **AC-1**（0.82 真实 LLM 闭环，`finish_reason=completed`）、**AC-2**、**AC-3**（agent 可见 11 个 `mcp__jkos__*` 工具并成功调用 `dsh_session_list` 拿到真实返回）、**AC-4**、**AC-5**、**AC-6**（Web UI 反代 200 + 资源可达）、**AC-7**、**AC-8** 全部通过

### 🐛 修复
- **JKOS MCP 标准端点协议不合规**（`jkos_core/mcp/server.py`）：`/mcp` 的 `tools/list` 原用 `model_dump()` 输出蛇形字段 `input_schema`/`output_schema`，而 MCP 规范要求驼峰 `inputSchema`/`outputSchema`。外部 MCP 客户端（deepseek-harness 的 `dsh-mcp-client`）做 schema 校验时会拒绝**整个**工具列表，表现为「连接成功但工具不可见」。新增 `to_mcp_wire_tool()` 做字段映射，仅用于标准端点 `/mcp`；REST `/tools` 保持原字段名以兼容既有消费方。

### 📌 harness 集成要点（实施期踩坑记录）
- **Cordis patch 有两种语义**（见 `vendor/include/src/index.ts`）：
  - **覆盖**：条目带 `id`，按 id 合并到**既有**条目；目标不存在报 `entry "<id>" not found`；**无 id 会被静默忽略**
  - **新增**：条目包在 `insert:` 列表里（外层不带 id）
  - 因此 LLM 行（覆盖 bundle 既有 `llm-deepseek`）带 id，ToolBridge 行（新增 mcp-client）用 `insert:`
- **LLM 协议必配**：公网 `api.deepseek.com` 只支持 Chat Completions，harness `deepseek-official` 默认 messages 协议 → 不配 `protocol: chat-completions` 会 100% `HTTP_404`
- **诊断利器**：`dsh --profile sdk --dump-config` 打印合成后的配置树，可直接看出 patch 是否生效
- **Web UI 需令牌**：`dsh web` 启动打印 `?token=`（无令牌 401），令牌换 cookie 后 303 重定向，代理需内部跟随

### 🔒 安全加固（M16 跟进）
- **匿名工具列表泄露修复**（`jkos_core/mcp/server.py`）：`_visible_tools(None)` 原返回**全部**工具（含租户自定义工具的名称/描述/schema），与 `tools/call` 的匿名守卫（匿名仅可调用共享工具）不一致——匿名虽调不了但**看得见**。现改为匿名仅返回共享/CORE 工具（owner 为 None），REST `/tools` 与 JSON-RPC `tools/list` 同时生效，对齐 M14 变更记录「匿名请求仅可见共享工具」的承诺。
- **MCP 服务严格鉴权模式**：新增 `JKOS_MCP_REQUIRE_AUTH`（默认 false，保持 M14 可选鉴权契约、零回归）。开启后工具端点（`/tools`、`/tools/call`、`/mcp`、`/tools/register`、`DELETE /tools/{name}`）对匿名请求（含**无效 token**——不得退化为匿名放行）一律 401；`/health` 保持公开。部署了外部 MCP 客户端（harness ToolBridge）时应开启。
- **0.82 端到端验证**：`JKOS_MCP_REQUIRE_AUTH=true` 下匿名 `tools/list` 401、带 token 11 工具、harness agent 成功调用 `dsh_session_list`（AC3-PASS）；双 venv 回归各 **887 passed / 3 skipped / 88%**。

## [M15] JKOS 代码层更名落地 - 2026-09-18

### 📝 变更
- **包名更名**：`dsh_core` → `jkos_core`（目录、全部 import、pyproject `--cov`/coverage source 同步），约 870 处机械替换
- **CLI 更名**：`dsh-server` → `jkos-server`、`dsh-plugin` → `jkos-plugin`（pyproject scripts、usage 字符串、测试 argv 断言同步）
- **项目名**：pyproject `name` `dsh-ai-platform` → `jkos-ai-platform`（本地目录名不变）
- **品牌文案**：`server.py` 启动横幅 / `jkos_core.cli` 描述与日志 / OpenAPI title（`DSH AI 中台 API` → `极快AI操作系统 API`）/ `locales/{zh,en}` `app.name`（`极快AI操作系统` / `Jikuai AI OS`）/ `plugins/i18n.py` 内置回退文案 / `mcp/server.py` 模拟 OCR/ASR 示例文案
- **测试同步**：品牌断言更新（`test_api_plugin_tool_routes.py`、`test_cli.py`、`test_m5_i18n.py`）；en 文案断言同步为 `Jikuai AI OS`

### 📌 新旧命令映射
| 旧 | 新 |
|---|---|
| `dsh-server` | `jkos-server` |
| `dsh-plugin` | `jkos-plugin` |
| `python -c "...dsh_core.cli..."` | `python -c "...jkos_core.cli..."` |

### 📌 不变项
- `DSH_*` 环境变量前缀、本地目录名 `dsh-ai-platform`、远端 `/data/dsh/*` 路径、历史文档称谓（存档）

## [待推送] - 2026-09-18

### 📝 变更
- **品牌更名**：「DSH AI 中台」更名为**极快AI操作系统**（英文 **Jikuai AI OS**，简称 **JKOS**）。定位修正为：结合开源项目 [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)（FreeBSD 版）在 FreeBSD 系统下构建的一整套 AI 服务操作系统。本批次完成文档与元数据统一更名（README / CHANGELOG / pyproject / docs 全量）；代码层（`dsh_core` 包名、`dsh-server` CLI、API title、locales 品牌文案、部署脚本横幅，涉及 5 处测试断言需同步）与 deepseek-harness 的深度集成将在后续里程碑统一落地。

### 🐛 修复
- **插件创建命令从未可用 bug**（`f3d495d`）：`dsh_core/plugin_cli.py` 的 `cmd_create` 用 `json.loads` 解析 YAML 模板且 `read_text()` 未指定 encoding（Windows GBK 环境必崩），导致 `python plugin_cli.py create` 自引入起无法成功。已改为 `yaml.safe_load/safe_dump + encoding='utf-8'`，模板 `_template/plugin.yaml` 同步修正为合法 YAML 扁平清单（保留 capabilities/health_check/config 全部字段，键结构与插件市场 install 写入格式对齐）。

- **API 密钥校验 bug**（`f0bc302`）：修复 `dsh_core/auth/apikey.py` 中 `_verify_secret` 的密钥哈希计算错误——`hashlib.sha256` 未对拼接字符串 `.encode()`，导致校验结果始终不正确。已修正为 `hashlib.sha256(f"{salt}{secret}".encode()).hexdigest()`，全量回归测试 648 用例全部通过。

### 🧹 清理
- **仓库清理**（`574bf3f`）：取消跟踪 `.coverage`（加入 `.gitignore`）、移除本地 `index.html` 冗余文件（页面已部署至远端 FreeBSD `/data/dsh/index.html`）、README 补充里程碑记录表。

### 📌 已知事项
- `scripts/deploy_index.py` 已失效：脚本读取本地 `G:\dswork\AI\dsh-ai-platform\index.html`（该文件已删除）并部署到 `/data/dsh/index.html`。按决策**保留脚本不动**，仅记录其不再使用；当前部署方式为直接维护远端 `/data/dsh/index.html`，由 `server.py` 的根路由 `FileResponse("/data/dsh/index.html")` 提供服务。

## [M14] MCP 网关多租户 - 2026

> 提交：`f825b86`

### 新增
- **租户级工具可见性**：工具按租户归属管理（`tool_owners` 映射），匿名请求仅可见共享工具；调用越权工具 REST 返回 404，JSON-RPC 返回 `-32601`
- **租户级配额限流**：令牌桶按租户独立限流（`acquire_by_tenant`），匿名流量归入 `anonymous` 桶；REST 超限返回 429 + `Retry-After`，JSON-RPC 返回 `-32029`
- **可选认证上下文**：新增 `get_optional_context` 依赖，`TenantContext` 可为空，公开接口在无凭证时仍可访问

相关代码：`dsh_core/auth/dependencies.py`、`dsh_core/mcp/server.py`

## [M12] 自动化引擎 - 2026

> 提交：`5802bb8`（合入 `80cad99`）

### 新增
- **AutomationEngine**：自动化任务编排引擎
- **ProcessSolidifier**：流程固化器（将探索流程固化为可复用自动化）
- **TokenOptimizer**：Token 优化器（上下文压缩、成本控制）
- **TemplateManager**：自动化模板管理

相关代码：`dsh_core/automation/`

## [M13] 工具标准化 - 2026

> 提交：`5b7c380`

### 新增
- **ToolRegistry**：工具注册表（五索引结构 + 可见性域 + 版本管理 + 单例模式）
- **工具市场元数据**：评分 / 下载量 / 版本历史 / 截图 / 标签 / 作者信息
- **版本管理**：`publish_version` 发布新版本、`set_active_version` 切换激活版本
- **可见性域**：CORE / INDUSTRY / CUSTOM 三级可见性，按租户过滤
- **工具路由**：注册 / 注销 / 版本 / 评分 / 市场信息 / 下载 / 搜索

相关代码：`dsh_core/mcp/registry.py`、`dsh_core/mcp/__init__.py`、`dsh_core/mcp/server.py`、`dsh_core/api/tool_routes.py`、`dsh_core/api/routes.py`、`dsh_core/bootstrap.py`

## [M11] 探索引擎 - 2026

> 提交：`a7c87ba`

### 新增
- **TaskDecomposer**：任务分解器
- **SolutionSearcher**：解决方案搜索器
- **Experimenter**：实验执行器
- **KnowledgeBase**：知识库管理

相关代码：`dsh_core/exploration/`

## [M10] LLM 供应商 - 2026

> 提交：`1382116`

### 新增
- 多供应商接入：DeepSeek、OpenAI、通义千问 (Qwen)、Claude (Anthropic)、Simulated（本地模拟）
- 供应商基类与 LLM 路由器（流式响应缓存、请求合并）
- 每个供应商支持默认模型与 Base URL 配置

相关代码：`dsh_core/llm/`

## [M9] 文档 - 2026

> 提交：`1158332`

### 新增
- 开发指南 `docs/dev/guide.md`
- API 文档 `docs/api/openapi.md`
- 部署运维指南 `docs/ops/deploy.md`
- 架构设计文档 `docs/architecture/`
- 技术设计白皮书、10 周实施计划

## [M8] 认证与安全 - 2026

> 提交：`96a8128`, `60c03d2`

### 新增
- **认证授权**：JWT 双 Token（Access 15 分钟 + Refresh 7 天）、RBAC 权限模型（10 角色 × 10 资源）、API 密钥管理（前缀标识、加密存储、速率限制）
- **安全加固**：多租户令牌桶限流、CSRF 双重 Cookie 验证、安全头（HSTS / X-Frame-Options / CSP）、CORS 白名单动态配置
- **可观测性**：`/healthz`、`/readyz`、`/metrics`（Prometheus）、全链路审计日志

相关代码：`dsh_core/auth/`、`dsh_core/utils/ratelimit.py`、`dsh_core/api/security.py`、`dsh_core/api/metrics.py`、`dsh_core/audit/`

## [M7] 性能优化 - 2026

> 提交：`5b37850`

### 新增
- **ConnectionPool**：数据库连接池（min/max 配置、泄漏检测、健康检查）
- **MultiLayerCache**：多级缓存 + 布隆过滤器（L1 内存 / L2 Redis）
- **AsyncTaskQueue**：异步任务队列（优先级、超时、重试）
- **LLM Router 缓存**：流式响应缓存、请求合并

相关代码：`dsh_core/db/connection.py`、`dsh_core/cache/`、`dsh_core/utils/asyncq.py`

## [M6] 测试治理 - 2026

> 提交：`abda890`

### 新增
- 测试覆盖治理，新增 114 个用例（后续扩展至 648 用例全量通过）
- 测试套件：`tests/test_mcp.py`、`tests/test_storage.py`、`tests/test_m7_*.py`、`tests/test_m8_*.py`、`tests/test_m10_llm.py`、`tests/plugins/test_ocr.py`

## [M2-M4] 早期规划（已折叠）

> 无独立提交记录，功能折叠计入后续里程碑。

- **M2 内容管线**：内容生产 + 舆情情感
- **M3 基础设施**：LLM 路由 + 通知 + 缓存
- **M4 酒厂案例**：生产调度 + 追溯 + 适配器