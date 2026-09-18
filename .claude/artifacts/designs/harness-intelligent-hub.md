# deepseek-harness 智能中枢集成 Spec

> Status: ALIGNED
> Author: skywalk
> Last updated: 2026-09-18

## Background

JKOS（极快AI操作系统，`jkos_core`，Python/FastAPI）需要引入 DeepSeek 官方 agent harness（Node/Cordis 架构，内网 Gitea `skywalk/deepseek-harness`，本地克隆 `G:\dswork\AI\deepseek-harness`）作为**智能中枢**：承接 Agent 会话、多步工具编排与子代理。harness 官方提供 Python SDK（`deepseek-harness-sdk` + `deepseek-harness-runtime-bin`，JSON-RPC over stdio），并支持 external plugin/MCP 扩展与 Web UI（:3080）。

## In scope

- **HarnessGateway 模块**（`jkos_core/harness/`）：sidecar 生命周期管理（经 `deepseek-harness-sdk` 启动/健康检查/崩溃重启）、JSON-RPC 客户端封装
- **会话 API**：REST（创建/列出会话、发 prompt）+ SSE（agent 状态/消息/子代理事件流转发）
- **ToolBridge**：把 JKOS ToolRegistry（`jkos_core/mcp/registry.py`）的工具经 harness external plugin / MCP 机制暴露给 agent
- **身份与配额**：JKOS JWT 身份传导进 harness 会话上下文；租户级配额限流沿用现有令牌桶（`jkos_core/auth/dependencies.py` 的 `acquire_by_tenant` 模式）
- **Web UI 代理**：JKOS 反代 harness Web UI（登录态打通为后续细化项）
- **凭据统一供给**：`DEEPSEEK_API_KEY` 与 `DSH_HOME` 由 JKOS `.env` 注入 sidecar，harness 不落密钥文件
- **部署**：Windows 本地用 runtime-bin wheel 开发打通；0.82（FreeBSD）源码构建（Node carrier，node24 + pnpm，仓库自带 `FREEBSD.md` runbook）到 `/data/dsh/harness`

## Out of scope

- 修改 deepseek-harness 上游代码（保持 vendor 原样，补丁走其 patches 机制则另立议题）
- workflow 引擎节点改造（D1 等工作流不动）
- harness desktop 应用、A2A 远程互联
- `DSH_*` 环境变量前缀更名、目录名变更（沿用 M15 ADR）

## Assumptions

- harness 处于 developer preview，SDK 协议可能 breaking——**已锁定版本 `65e04a5a07`**（2026-09-17，tag `jkos-lock-20260918` + 分支 `jkos-lock`），升级走独立变更
- 0.82 工具链**已装齐**（2026-09-18 实测）：node v24.19.0、npm 11.18.0、pnpm 11.7.0、gmake 4.4.1 + `~/bin/make` shim、python3 3.12.14、bash 5.3.15；安装路径 `sudo pkg install`（ai 用户 wheel 组免密 sudo）
- `deepseek-harness-sdk` 的 session/prompt/events API 足以覆盖「创建会话→发 prompt→收事件」闭环（已据 `python/sdk/README.md` 确认）
- Windows x64 wheel 可直接 pip 安装（官方发布平台含 Windows x64）

## Solution（sketch）

```
jkos_core/harness/
  runtime.py     # SidecarManager: 经 SDK 启动 dsh --profile sdk，健康检查，崩溃重启，DSH_HOME 归 JKOS 管
  gateway.py     # JSON-RPC 封装：create_session / send_prompt / events 流
  routes.py      # /api/v1/harness/sessions…（REST）+ /api/v1/harness/sessions/{id}/stream（SSE）
  toolbridge.py  # ToolRegistry → harness external plugin/MCP 暴露
  proxy.py       # harness Web UI 反代
```

请求链：JKOS REST → HarnessGateway（JWT 鉴权 + 租户限流）→ sidecar（stdio JSON-RPC）→ harness runtime → LLM/工具/子代理 → 事件流回传 → JKOS SSE。凭据流：JKOS `.env` → sidecar env（`DEEPSEEK_API_KEY`, `DSH_HOME=/data/dsh/harness-home`），不写盘、不入日志。

## Edge cases & risks

| Category | Notes |
|---|---|
| Boundary conditions | sidecar 单实例 vs 多实例（先单实例，配额按会话并发控制）；DSH_HOME 目录权限（0.82 上 ai 用户可写） |
| Failure modes | sidecar 崩溃 → 重启并标 degraded；SDK 协议不兼容 → 版本锁定 + 冒烟门禁；SSE 客户端断连 → 会话继续、事件丢弃策略 |
| Risks | developer preview breaking changes（最高风险）；FreeBSD 源码构建链条长；工具桥接放大权限面（工具越权调用） |
| Mitigation | 锁 commit + 集成冒烟门禁；FREEBSD.md runbook 已验证；ToolBridge 默认只暴露租户可见工具（复用 M14 租户可见性） |

## Acceptance criteria

- AC-1 Windows 本地：`deepseek-harness-sdk` wheel 安装后，集成测试（无 runtime 则 skip）能启动 sidecar → 创建会话 → 发 prompt → 收到 assistant 事件
- AC-2 `POST /api/v1/harness/sessions` 返回 201；`POST .../messages` 建立 SSE 流并至少转发 1 个 agent 事件
- AC-3 ToolBridge：harness agent 成功调用 ≥1 个 JKOS 注册工具（e2e），匿名/越租户工具调用被拒
- AC-4 携带有效 JWT 的请求身份出现在 harness 会话上下文；无凭证请求 401
- AC-5 租户配额：超限的 harness 会话请求返回 429 + `Retry-After`
- AC-6 JKOS 反代路径返回 harness Web UI 页面（HTTP 200 + 主资源非空）
- AC-7 0.82：源码构建完成；全量回归 789 + 新增用例全部 passed；集成冒烟含 harness 闭环
- AC-8 现有 789 用例零回归（无 skip 激增、无 fixture 冲突）

## Open questions

- ~~harness 版本锁定~~ → **已解决**：锁定 `65e04a5a07`（tag `jkos-lock-20260918`）
- ~~0.82 node24 是否已安装~~ → **已解决**：v24.19.0 + pnpm 11.7.0 + gmake shim 已装（sudo pkg / npm -g）
- ~~Web UI 登录态打通方案~~ → **已解决（实施中发现）**：`dsh web` 要求 `?token=`（无令牌 401），令牌换 cookie 后 303 重定向。JKOS proxy 内实现「令牌注入 + 内部跟随重定向 + `Set-Cookie` 由代理 jar 承载」，调用方无需感知。AC-6 端到端验证通过。
- ~~ToolBridge 用 external plugin 还是 MCP server 形态~~ → **形态已定（MCP），但装配未通**：`dsh-mcp-client` 行（`streamable-http`）写入 `cordis.patch.yml` 后 harness 未建立连接（JKOS `/mcp` 零请求）。已排除协议不兼容；定位在 **profile 依赖闭包/manifest 层**。AC-3 待续，下一步见 plan 的「实施后记」。
- **新发现（实施期）**：harness `cordis.patch.yml` 的 patch 行**必须带既有行 `id` 才能生效**，无 `id` 会被静默忽略（LLM 行踩过：缺 `id: llm-deepseek` 导致 `protocol` 覆盖失效、100% `HTTP_404`）。既有行 id 参考 `deepseek-harness/snapshots/session/*/cordis.yml`。

## Core entities (ontology)

| Entity | Type | Key fields | Relationship |
|---|---|---|---|
| HarnessRuntime | 进程 | profile, DSH_HOME, pid, health | 被 HarnessGateway 托管 |
| HarnessGateway | 模块 | sidecar 引用, 配额桶 | 暴露 HubSession，桥接 ToolBridge |
| HubSession | 实体 | id, tenant, user, events 流 | 1 runtime : N sessions |
| ToolBridge | 模块 | 工具白名单（租户可见性过滤） | ToolRegistry → harness |
| DSH_HOME | 配置 | 路径（JKOS 数据目录下） | harness 运行家目录 |

## Interview metadata

- Mode: default
- Waves: 3
- Final ambiguity: 20.5%
- Status: PASSED

### Clarity breakdown
| Dimension | Score | Weight | Weighted |
|---|---|---|---|
| Goal | 0.85 | 0.40 | 0.34 |
| Scope | 0.80 | 0.25 | 0.20 |
| AC | 0.55 | 0.25 | 0.1375 |
| Context | 0.85 | 0.10 | 0.085 |
| Ambiguity | | | 19.75% → 达标 |
