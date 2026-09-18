# M16 智能中枢（deepseek-harness 集成）Implementation Plan

> Status: APPROVED
> Source: `.claude/artifacts/designs/harness-intelligent-hub.md`（ALIGNED）
> Mode: --deliberate（高风险信号：鉴权 + 配额 + 外部进程托管 + 新公开 API 面）
> Iterations: 2 / 3
> Author: skywalk
> Last updated: 2026-09-18
> 实施状态：AC-1/2/4/5/6/7/8 已通过；**AC-3 未完成**（见「实施后记」）

## 实施后记（2026-09-19，实施中记录）

### 已通过
| AC | 证据 |
|---|---|
| AC-1 | 0.82 真实 runtime 集成测试 **3 passed**（`finish_reason == "completed"`）；sidecar 经 `dsh_bin` 指向源码构建 launcher，锁定版 SDK（`0.0.0.dev0`）与 runtime `0.1.6-alpha.1` 握手成功 |
| AC-2 | `POST /sessions` 201 + SSE 转发 notification/turn/end（TestClient 用例）；真实 HTTP 侧由 `cov82_harness.sh` 覆盖 |
| AC-4 | 401（无凭证）/ 身份前导写入 prompt（`identity_preamble`）用例通过 |
| AC-5 | 429 + `Retry-After`，且按租户隔离用例通过 |
| AC-6 | 0.82 端到端：直连 401 → 经 JKOS 代理 **200（29.5KB）**，`<base>` 改写为 `/harness/ui/`，主 JS 资源经代理 **200（566KB）** |
| AC-7 | 双 venv 回归：py3.12 **868 passed / 88%**、py3.11 **865 passed / 3 skipped**；仅 3.11 正确 skip 集成用例（未装 SDK） |
| AC-8 | 基线 789 → 865（+76 新增），无 skip 激增 |

### AC-3 未完成 —— 精确卡点与新结论
`@deepseek-ai/dsh-mcp-client` 行写入 `cordis.patch.yml` 后，harness **未建立 MCP 连接**（JKOS `/mcp` 收到 0 次请求，`mcp__jkos__*` 工具未注册）。

已知与该卡点直接相关的两条硬事实：
1. **patch 行必须带 `id` 才能覆盖 bundle 既有行** —— 实施中发现：LLM 行最初缺 `id`，导致 `protocol: chat-completions` 被静默忽略（仍走 messages 协议 → `HTTP_404`）；补上 `id: llm-deepseek` 后立刻成功。这是本 plan 未预见的关键机制（参考 `.deepseek-harness/snapshots/session/*/cordis.yml` 才知道既有行 id）。
2. **新增插件行可能需先进入 profile 依赖闭包** —— profile 的 `package.json` 为 `"dependencies": {}`；deepseek-harness `AGENTS.md` 明确「Raw/Web `cordis.yml` bare plugins must appear in their resolver manifest's `dependencies`」，且外部插件需 `dsh plugin --profile sdk add`。这提示 mcp-client 行未被加载的原因在该闭包/manifest 层，而非 MCP 协议不兼容。

**因此重新判定（推翻原判据）**：原 plan 把「协议不兼容」当作主因并准备了 B′（stdio shim）兜底；现有证据表明问题在**配置装配层**（row id / 依赖闭包），而JKOS `/mcp` 的 `initialize` 返回 `protocolVersion: 2024-11-05` 且 curl 直连正常，协议侧未见失败证据。**B′ 暂不应实施**——应先验证闭包路径，否则会在错误层面投入。

**下一步（供后续 plan）**：① 在 profile `package.json` 声明 `@deepseek-ai/dsh-mcp-client` 依赖或用 `dsh plugin --profile sdk add` 安装后再验证；② 验证通过后重跑 AC-3 的越权用例；③ 若闭包路径仍失败，才落 B′ stdio shim。

### 其他实施偏差（相对原 plan）
- **新增 LLM 接入行**（原 plan 无此步）：公网 `api.deepseek.com` 只支持 Chat Completions，而 harness `deepseek-official` 默认 messages 协议 → 不配置则 100% `HTTP_404`。已加入 `llm_provider_row()` / `render_profile_patch()`。
- **Web UI 令牌注入**（原 plan 仅列为 open question）：实测 `dsh web` 要求 `?token=`（无令牌 401），且令牌换 cookie 后 303 重定向。已在 proxy 内实现「令牌注入 + 内部跟随重定向」。
- **`mcp_url` 默认值修正**：原写 `8001/mcp`（API 服务），实际 `/mcp` 由 MCP 服务提供（默认 3000）。
- **测试自纠**：集成用例原先只断言「收到 `turn/end`」，被 `finish_reason=error` 的失败轮掩盖（AC-1 曾假通过）。已改为断言 `finish_reason == "completed"` 并透出错误。
- **顺手清理**：`bootstrap.py` 重复 import 行（Critic 曾建议单拆 commit，实际因处于本次编辑区间一并移除）。

## Requirements summary

把 DeepSeek 官方 agent harness（已在 0.82 源码构建完成，锁定 commit `65e04a5a07`）经官方 Python SDK 以 JKOS 托管的 sidecar 形态嵌入，作为智能中枢：会话/事件流、工具桥接、JWT 身份传导、租户配额、Web UI 反代全量集成。凭据由 JKOS `.env` 统一注入，`llm.router` 保留为直连兜底，workflow 引擎不动。

## Acceptance criteria

- **AC-1**（spec）Windows 本地：SDK 安装后集成测试能启动 sidecar → 创建会话 → 发 prompt → 收到 assistant 事件；无 runtime 时该测试 skip 而非 fail
- **AC-2**（spec）`POST /api/v1/harness/sessions` 返回 201；`POST .../messages` 建立 SSE 流并至少转发 1 个 agent 事件
- **AC-3**（spec）ToolBridge：harness agent 成功调用 ≥1 个 JKOS 注册工具（e2e）；匿名/越租户工具调用被拒
- **AC-4**（spec）携带有效 JWT 的请求身份出现在 harness 会话上下文；无凭证请求 401
- **AC-5**（spec）租户配额：超限的 harness 会话请求返回 429 + `Retry-After`
- **AC-6**（spec）JKOS 反代路径返回 harness Web UI 页面（HTTP 200 + 主资源非空）
- **AC-7**（spec）0.82：全量回归 789 + 新增用例全部 passed；集成冒烟含 harness 闭环
- **AC-8**（spec）现有 789 用例零回归（无 skip 激增、无 fixture 冲突）

## RALPLAN-DR

### Principles

- **最小代码**：优先用官方 SDK 与 harness 既有扩展点（`mcp-client` 配置行），不自造协议栈
- **外科手术式改动**：新增 `jkos_core/harness/` 一个包，既有模块只做最小接线（`bootstrap.py` 加字段、`api/routes.py` 挂载 router），不改 workflow/llm/mcp 既有行为
- **密钥铁律**：`DEEPSEEK_API_KEY` 等只经 JKOS `.env` → 子进程 env，不落盘、不入日志、不进 harness profile 文件
- **版本纪律**：一切 harness 交互对齐锁定 commit `65e04a5a07`；SDK 从锁定 checkout 安装而非 PyPI 最新，避免协议漂移
- **可验证**：每条 AC 对应可执行命令或测试名

### Decision drivers

1. **协议漂移风险**（developer preview，spec 列为最高风险）→ 决定 SDK 来源选型
2. **FreeBSD 无 runtime-bin wheel**（实测：PyPI simple 200 但 `pip index` 无匹配版本）→ 决定运行时选择机制
3. **最小改动面**：JKOS 已有 MCP 服务与租户可见性（M14），ToolBridge 应复用而非重建

### Viable options

**Option A: SDK 从 PyPI 安装 + `dsh_bin` 显式指向源码构建（chosen）**
- 实现思路：Windows 用 PyPI wheel 自带 runtime；FreeBSD/AI 服务器用 `pip install --no-deps` 装 SDK（pydantic 已在 venv），显式传 `dsh_bin` 指向 0.82 源码构建的 launcher，绕开 `deepseek_harness_runtime` 导入
- 改动文件：`jkos_core/harness/runtime.py`、`jkos_core/harness/config.py`
- 证据：`python/sdk/src/deepseek_harness/client.py:459-469` —— `dsh_bin is None` 才 `from deepseek_harness_runtime import ...`，显式传入则完全不导入
- Pros：官方客户端零维护；Windows/FreeBSD 双平台都能跑；SDK 依赖仅 `pydantic`（JKOS 已有 2.13.5）
- Cons：PyPI SDK 版本与本仓库锁定 commit 可能不一致（协议漂移风险）；需 `--no-deps` 绕过 runtime-bin 依赖

**Option B: SDK 从锁定 checkout 本地安装（`pip install --no-deps /data/dsh/harness/python/sdk`）**
- 实现思路：两平台都从锁定仓库安装 SDK 源码，版本与 runtime 严格同源
- 改动文件：同 A，另加 `scripts/harness82_env.sh` 安装步骤
- Pros：**协议漂移风险归零**（SDK 与 runtime 同一 commit）；不依赖 PyPI 上游发布节奏
- Cons：0.82 需 `python/sdk` 依赖 `pydantic>=2.12,<3` 且需 hatchling 构建（`--no-deps` 不装构建依赖，需 `--no-build-isolation`，链条略长）；Windows 侧也要拷贝 checkout

**Option C: 不用 SDK，自实现 stdio JSON-RPC 客户端**
- 实现思路：按 `packages/sdk/protocol` 消息规范手写 newline-delimited JSON-RPC
- 改动文件：`jkos_core/harness/jsonrpc.py`（约 400+ 行）
- Pros：零第三方依赖，完全可控
- Cons：**违反最小代码**（重实现官方已交付的会话/通知/子代理生命周期处理）；协议演进时需自行跟进；无官方测试保障

**Option D: Web UI 独立并行 + JKOS 只做反代（invalidated）**
- invalidation rationale：spec 已在 grill 阶段否掉——「独立并行服务」导致两套系统体验割裂、身份不通、用户明确选择「Agent 编排中枢」

**ToolBridge 子选型（spec open question 落地）**

- **A′: `streamable-http` 直连 JKOS `POST /mcp`（chosen 兜底路径）**
  - 证据：`jkos_core/mcp/server.py:705-750` 已实现 `initialize`(protocolVersion `2024-11-05`)/`tools/list`/`tools/call`；harness `mcp-client` 支持 `transport: streamable-http` + `headers`（`packages/mcp/mcp-client/README.md`）
  - Pros：**零新代码**，一条 profile patch YAML 配置行
  - Cons：JKOS 实现未做 Streamable HTTP 会话管理（无 `Mcp-Session-Id`），harness 官方 SDK 可能拒绝；协议版本 `2024-11-05` 需确认在其 legacy 支持列表
- **B′: stdio shim 代理（chosen 主路径）**
  - 实现思路：`jkos_core/harness/mcp_stdio_bridge.py`（约 150 行）实现 MCP stdio 语义，内部转发到 JKOS REST `/tools`、`/tools/call`；租户 token 经 env 传入
  - Pros：不依赖 JKOS MCP 服务的协议完备性；租户上下文显式可控；故障面隔离
  - Cons：新增一个组件需维护；stdio 语义需自行正确实现（C′ 的小型特化版）

### Implementation steps（基于 Option A + ToolBridge A′优先/B′兜底）

1. **harness 配置模块** — 新增 `jkos_core/harness/config.py`：`HarnessConfig.from_env()` 读 `JKOS_HARNESS_ENABLED`(默认 false，保证零回归)、`DEEPSEEK_API_KEY`、`DSH_HOME`(默认 `/data/dsh/harness-home`)、`JKOS_HARNESS_DSH_BIN`、`JKOS_HARNESS_PROVIDER`(默认 `deepseek-official`)、`JKOS_HARNESS_MODEL`、`JKOS_HARNESS_CWD`；密钥字段不进日志（复用 `jkos_core/auth/dependencies.py:36-47` 的 env 读取风格）
2. **launcher 脚本（0.82）** — 新增 `/data/dsh/harness/dsh-jkos.sh`：`cd /data/dsh/harness && exec node --expose-internals --import tsx/esm apps/cli/src/main.ts "$@"`（脚本定义见 `package.json:190` `dsh:freebsd`），`chmod +x`；Windows 侧 `dsh_bin` 留空走 bundled runtime
3. **Sidecar 生命周期** — 新增 `jkos_core/harness/runtime.py`：`SidecarManager` 封装 `DeepSeekHarness(dsh_home=…, cwd=…, provider=…, model=…, api_key=…)`（API 见 `python/sdk/README.md`），懒启动、单实例、`close()` 幂等、异常标记 degraded；`dsh_bin` 显式传入以绕开 `deepseek_harness_runtime`（`client.py:459-469`）
4. **Gateway 与事件流** — 新增 `jkos_core/harness/gateway.py`：`create_session(tenant,user)`→session_id；`stream_turn(session_id,text)` 为 async generator —— 因 SDK `run()` 是**同步阻塞**，须 `await asyncio.to_thread(...)` 卸载到工作线程，事件经 `on_notification` 回调写入 `asyncio.Queue` 后供 SSE 消费；租户/用户身份写入 session 上下文（会话 metadata + workspace 目录隔离）
5. **REST + SSE 路由** — 新增 `jkos_core/harness/routes.py`：`APIRouter(prefix="/api/v1/harness")`；`POST /sessions`(201)、`POST /sessions/{id}/messages`(SSE)、`GET /sessions`；鉴权用 `Depends(get_tenant_context)`（`jkos_core/auth/dependencies.py:127`，无凭证 401）；配额用 `_global_limiter.acquire_by_tenant(ctx.tenant_id)`（`jkos_core/utils/ratelimit.py:121`），超限返回 429 + `Retry-After`（照抄现有模式 `ratelimit.py:166`）；router 挂载进 `create_app`（`jkos_core/api/routes.py:269-270`）
6. **ToolBridge** — 先用 A′：在 harness profile patch（`$DSH_HOME/profiles/sdk/cordis.patch.yml`）加一行 `dsh-mcp-client` → `transport: streamable-http` / `url: http://127.0.0.1:8001/mcp` / `headers.Authorization` 取 JKOS 内部 token（配置 schema 见 `packages/mcp/mcp-client/README.md`）；若 harness 初始化报协议不兼容，则落 B′：新增 `jkos_core/harness/mcp_stdio_bridge.py` 并改用 `transport: stdio`
7. **Web UI 反代** — 新增 `jkos_core/harness/proxy.py`：把 `/harness/ui/{path}` 代理到 `127.0.0.1:3080`。注意 harness 有 loopback 信任围栏（非 loopback 访问 403，见 `FREEBSD.md` §0），JKOS 与 harness 同机、经 127.0.0.1 访问可满足；转发需剥掉外部 Host 头
8. **bootstrap 接线** — `jkos_core/bootstrap.py`：`AppComponents` 增 `harness: Optional[HarnessGateway] = None` 字段；`build_components()` 在 `tools = get_registry()`（`:97`）之后按 `HarnessConfig.enabled` 条件构建；`close()`（`:47-48`）增 sidecar 清理
9. **env 模板** — `.env.example` 增 `JKOS_HARNESS_*` 与 `DEEPSEEK_API_KEY`(占位空值) 段落
10. **测试** — 新增 `tests/test_harness_config.py`、`tests/test_harness_gateway.py`（注入 fake SDK）、`tests/test_harness_routes.py`（TestClient：401/429/201/SSE）、`tests/test_harness_toolbridge.py`；集成用例 `tests/test_harness_integration.py` 在 runtime 不可用时 `pytest.skip`
11. **文档** — `CHANGELOG.md` 增 `[M16]`；`docs/ops/deploy.md` 增 harness 部署段；spec 状态改 IMPLEMENTED
12. **0.82 部署与冒烟** — 用 `/data/dsh/harness/python/sdk` 或 PyPI `--no-deps` 装 SDK 到 `venv-test312`；扩展 `scripts/cov82_integ.sh` 增 harness 闭环断言；跑双 venv 全量回归

## Workspace setup

- 实施前运行 `git status --short` 与 `git branch --show-current`。当前 `dsh-ai-platform` 有本地 `.git`，**HEAD 为 M15 之后的工作区状态**：M15 改名与本次 M16 同处未提交工作区，必须先为 M15 建立 commit 再开始 M16，否则两批改动无法分离审查/回滚
- 若树已 dirty 且用户要求隔离：`git worktree add -b codex/m16-harness ../dsh-ai-platform-m16`；否则以「先 commit M15、再实施 M16」两段式推进
- 服务器侧 `/data/dsh/code-jkos` 由 `scripts/push82.py` 同步（已配置保留远端 `.git`）；`/data/dsh/harness` 为 vendored 上游，**本 plan 不改其源码**（仅新增 `dsh-jkos.sh` launcher 与 profile patch）

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| SDK 与 runtime 协议漂移（developer preview） | 优先 Option B 路径（锁定 checkout 安装）可归零；若用 PyPI，则 step 12 必须验证 `initialize` 握手成功；失败即回退本地安装 |
| FreeBSD 无 runtime-bin wheel 导致 `pip install` 解析失败 | `pip install --no-deps`（pydantic 已在 venv 2.13.5）+ 显式 `dsh_bin` 绕开 `deepseek_harness_runtime` 导入 |
| 同步 SDK 阻塞 FastAPI 事件循环 | `asyncio.to_thread` 卸载；每会话独立工作线程；避免在路由内直接 `await harness.run()` |
| 多线程 + SSE 与测试 portal 线程的上下文问题（M14 前例：PyTracer `data_stack` 错乱） | harness 相关测试默认 `JKOS_HARNESS_ENABLED=false`，仅集成用例开启；SSE 集成用例用真实 HTTP（`uvicorn` 子进程）+ `curl` 而非 TestClient portal |
| ToolBridge 放大权限面（租户越权调用工具） | 复用 M14 租户可见性：`_visible_tools(ctx)`（`jkos_core/mcp/server.py:648-653`）与 `acquire_by_tenant`（`ratelimit.py:121`）；ToolBridge 不接受无 token 调用 |
| sidecar 崩溃/僵尸进程 | `SidecarManager` 健康检查 + degraded 标记 + 幂等重启；`close()` 确保终止；运维文档记录 `pgrep -f bin.ts` 排障 |
| 密钥泄露到日志/仓库 | `.env` 已有 `.gitignore`；新配置只读 env 不回显；日志仅记录 provider/model，不记录 key；提交前按 user_profile 惯例 grep `api_key|DEEPSEEK` |
| 零回归被破坏（现有 789 用例） | `JKOS_HARNESS_ENABLED` 默认 false，路由条件挂载；harness 依赖缺失不影响既有 import 路径（延迟导入 SDK） |

## Verification steps

- **AC-1**：`pytest tests/test_harness_integration.py -q`（Windows，runtime 可用）→ 1 passed；不可用时输出 1 skipped 且不 fail
- **AC-2**：`pytest tests/test_harness_routes.py::test_create_session_201 tests/test_harness_routes.py::test_sse_stream_forwards_event -q` → 2 passed
- **AC-3**：`pytest tests/test_harness_toolbridge.py -q` → passed（含越租户拒绝用例）；0.82 e2e 见 AC-7
- **AC-4**：`pytest tests/test_harness_routes.py::test_no_token_401 tests/test_harness_routes.py::test_identity_in_context -q` → 2 passed
- **AC-5**：`pytest tests/test_harness_routes.py::test_quota_429 -q` → passed（断言 status 429 且 header 含 `Retry-After`）
- **AC-6**：0.82 `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/api/v1/harness/ui/` → 200；且响应体含 harness 前端主资源引用
- **AC-7**：0.82 `venv-test312` 与 `venv-test` 双跑 `pytest -q` → 各 `789 + N passed`、`TOTAL 88%±`；扩展后的 `scripts/cov82_integ.sh` 全绿含 harness 闭环
- **AC-8**：对比基线 `789 passed`，新增用例数 = N 且无 `skipped` 激增（预期 skip 仅集成用例 1 个）

## Pre-mortem（deliberate）

1. **Scenario**: harness SDK 初始化握手失败（协议版本不匹配），所有 harness 端点 503，集成测试全红
   **Trigger**: 用 PyPI 最新 SDK 配锁定 commit 的 runtime，`protocolVersion` 协商不通过
   **Mitigation**: step 1 就把 SDK 来源做成配置项（`JKOS_HARNESS_SDK_SOURCE=pyPI|local`）；实现顺序上先做「本地安装 + `dsh` 握手冒烟」再写 Gateway，握手不通立刻切 Option B；AC-7 把握手纳入冒烟门禁
2. **Scenario**: SSE 事件流在真实 HTTP 下正常、但在 TestClient 下挂死或事件丢失，测试无法覆盖 AC-2
   **Trigger**: SDK 同步回调在 anyio portal 线程与 FastAPI 事件循环间跨线程投递 `asyncio.Queue`（`call_soon_threadsafe` 用法错误）
   **Mitigation**: 队列投递统一走 `loop.call_soon_threadsafe`；AC-2 除 TestClient 用例外，另加 `uvicorn` 子进程 + `curl -N` 的真实 HTTP 断言（沿用 `cov82_integ.sh` 既有 SSE 测试手法）
3. **Scenario**: ToolBridge 配好后 agent 拿到了全租户工具，越权可调用（安全事故）
   **Trigger**: A′ 路径下 JKOS `/mcp` 用 `get_optional_context`（`jkos_core/mcp/server.py:708`）——匿名即可列出匿名可见工具；若 harness 侧未带 token，可见性退化为匿名域
   **Mitigation**: ToolBridge 配置必须带 `Authorization` header；JKOS 侧 `/mcp` 对 harness 走强制鉴权路径（不接受匿名）；AC-3 显式含「越租户调用被拒」用例；测试断言无 token 时工具列表为空或 401

## Expanded test plan（deliberate）

- **Unit**：`test_harness_config.py`（env 解析、默认关闭、密钥不回显）；`test_harness_gateway.py`（注入 fake SDK：session 创建、事件队列投递、异常→degraded、`close()` 幂等）；`test_harness_toolbridge.py`（可见性过滤、越租户拒绝）
- **Integration**：`test_harness_routes.py` 用 TestClient 覆盖 201/401/429/SSE 事件转发；`test_harness_integration.py` 真 runtime 闭环（不可用则 skip）
- **E2E**：0.82 `scripts/cov82_integ.sh` 扩展 —— 启动 JKOS API 后 `POST /sessions` → `POST /messages` 收 SSE ≥1 事件 → agent 调用一个 JKOS 工具 → `/harness/ui/` 200；全链在真实服务（uvicorn 子进程 + curl）下验证
- **Observability**：`jkos_core/metrics/collector.py` 增 harness 会话计数与 sidecar degraded 计数；日志用 `dsh.harness` logger 记录 sidecar 启停/重启/握手结果（不含密钥）；`/api/v1/harness/health` 暴露 sidecar 状态供运维探活

## ADR

- **Decision**：JKOS 新增 `jkos_core/harness/` 包，经官方 Python SDK 托管**单一 harness sidecar**（FreeBSD 用显式 `dsh_bin` 指向源码构建 launcher，Windows 用 bundled runtime），对外暴露 `/api/v1/harness/*`（REST + SSE）并复用 JKOS 既有 JWT 鉴权与租户配额；ToolBridge 优先用 harness `mcp-client` 配置行直连 JKOS `/mcp`（`streamable-http`），协议不兼容时回退 stdio shim；Web UI 经 JKOS 反代
- **Drivers**：协议漂移风险（决定性，推动 SDK 来源可配置）；FreeBSD 无 runtime wheel（决定 `dsh_bin` 显式注入）；最小改动面（决定复用 M14 鉴权/可见性/配额而非重建）
- **Alternatives considered**：
  - SDK 来源：Option A（PyPI `--no-deps`）**chosen 为默认**；Option B（锁定 checkout 本地安装）**作为协议漂移兜底**，通过 `JKOS_HARNESS_SDK_SOURCE` 切换；Option C（自实现 JSON-RPC）rejected —— 违反最小代码；Option D（独立并行 Web UI）rejected —— grill 阶段已否
  - ToolBridge：A′（`streamable-http` 配置行）chosen 主路径（零代码）；B′（stdio shim）作为 A′ 协议不通时的实现路径
- **Why chosen**：官方 SDK 已交付会话/通知/子代理生命周期，自造协议栈不划算；两个 open question 都有了「先低成本试 A′，有明确失败信号再落 B′」的可执行路径，避免提前重投入
- **Consequences**：
  - 正向：JKOS 获得成熟 agent 编排；对外仍是单一 API 面与统一身份/配额；harness 上游保持 vendored 不被污染，便于跟随升级
  - 负向：新增 Node 运行时与 sidecar 运维面；同步 SDK 与 async FastAPI 之间引入线程桥（复杂度与 M14 线程类问题同类）；harness 处于 developer preview，升级需重跑冒烟
- **Follow-ups**（本次明确不做）：① harness 版本升级流程与协议兼容矩阵；② ToolBridge 的 stdio shim 若最终启用，需补其自身的单元测试完备性评审；③ Web UI 登录态深度打通（JWT→harness 会话 cookie）；④ 多租户 sidecar 池化（当前单实例）

## Review trail

- Planner draft v1：Option A（PyPI SDK + `dsh_bin`）为主，ToolBridge 直连 `/mcp`，12 步实施
- Architect challenge v1：steelman —— PyPI SDK 与锁定 runtime 的协议漂移是**概率性**故障，且失败点在最深处（握手），一旦踩中会侵蚀整条关键路径；tension —— 安装简便（A）vs 版本同源（B）；结论：SDK 来源必须做成配置项而非硬编码
- Critic verdict v1：**REVISE** —— ① 没有为「握手失败」提供实现顺序上的前置探测，风险处置滞后到 step 12；② AC-2 只靠 TestClient，未覆盖真实 HTTP SSE（M14 已有前例表明两者行为可不同）；③ ToolBridge A′ 的匿名退化风险（`get_optional_context`）未被识别，属鉴权漏洞
- Planner draft v2：step 1 增 `JKOS_HARNESS_SDK_SOURCE` 配置项 + ADR 记录双路径；verification 增真实 HTTP SSE 断言；pre-mortem 场景 3 与 AC-3 增补越权用例，ToolBridge 强制带 Authorization
- Architect challenge v2：确认 steelman 已被「SDK 来源可配置 + 先握手冒烟」化解；新 tension 记录为「ToolBridge 零代码 vs 协议可控」并已双路径化（A′/B′）+ 明确失败信号
- Critic verdict v2：**APPROVED**（2 条保留意见，见下）
- Final iterations: 2 / 3

### Critic reservations（APPROVED 仍保留）

- **step 12 / AC-7 的 `789 + N passed`**：plan 未给出 N 的预期区间，实施者无法判断「新增测试是否够」。建议实施时以「每个新模块至少 1 个测试文件、每条 AC 至少 1 个断言用例」为软下限，而非固定数字；若最终 N 明显偏低需在 CHANGELOG 说明。
- **step 6 ToolBridge A′ 的失败判据不够硬**：plan 说「若初始化报协议不兼容则落 B′」，但未定义**如何判定不兼容**（是 harness 启动日志告警、profile 加载失败，还是 agent 工具列表为空？）。建议实现时把判据固定为「`dsh plugin --profile sdk list` 后 `/tools` 出现 `mcp__jkos__*` 命名工具」这一二值信号。
- **`jkos_core/bootstrap.py:44-45` 存在重复行**（`tools: Optional[ToolRegistry] = None` 出现两次，M15 机械替换前后即如此）：本 plan 不动它（外科手术式改动原则），但建议单独拆 commit 清理。