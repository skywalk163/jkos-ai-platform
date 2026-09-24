# ADR 0002: JKOS 以 deepseek-harness 插件形态发布（接入层反转）

Status: Accepted
Date: 2026-09-24

## Context

ADR 0001 选定 (a)：JKOS 经官方 Python SDK 托管 harness sidecar，并在 JKOS 内自行完成工具桥接、身份传导、租户配额与 Web UI 反代（`jkos_core/harness/`，901 行，M16 落地）。该形态在 FreeBSD 企业部署上验收通过，但对「开源分发」带来三个直接冲突：

1. **宿主关系反了**：JKOS 承担 harness 的进程生命周期、Web UI 反代，并在自己的 API 里重造会话与事件流 —— 这些都是 harness 已有的能力；
2. **依赖面大**：harness 处于 developer preview，SDK 协议的 breaking 风险由 JKOS 承担；FreeBSD 无预编译 wheel，需源码构建 Node carrier，构建链条长；
3. **分发形态错位**：要让外部用户「装了 deepseek-harness 就能用」，前提是用户不必先部署一整套 JKOS 企业栈。

复核 M16 代码后发现一个关键事实：**该集成从未修改 harness 任何代码** —— 它只是生成 harness 的 profile patch，并消费 harness 内置的通用 MCP 客户端 `@deepseek-ai/dsh-mcp-client`（`jkos_core/harness/toolbridge.py`）。协议层早已解耦，真正的问题只是宿主方向。

## Decision

反转接入层，作为**独立分支** `jkos-dsh-plugin` 与 `main` 并行维护：

- **harness 当宿主，JKOS 当插件**：`plugin/` 是一个 npm bundle（`dsh.bundle.patch` + `dsh.client`），只用两个标准边界对接 —— harness 的 Web 路由表（`ctx.webServer.register` 的 `/jkos` 前缀路由）与 harness 内置的 MCP 客户端行（指向 JKOS `/mcp`）；
- **零构建**：Client 半包是手写经典脚本（`window.__ModuleLoader__.load({ id, factory })`），不引入 TypeScript / JSX / 打包器 —— 仓库外的 bundle 复刻不了 harness 内部的 `clientBundle` 构建预设，手写是这个位置唯一稳的形态；
- **鉴权不另造**：复用 harness 的 `connection` 信任围栏（Host/Origin 校验 + 浏览器会话鉴权），插件只问 `requestRejection`；
- **中台能力两线共享**：`jkos_core/`（认证、多租户、工具注册表与可见性、配额、MCP 网关）不动；`main` 的 `jkos_core/harness/` 保留不删，作为回退路径。

## Consequences

- **正向**：插件可独立分发（github / gitcode 开源通道），用户侧只需 harness + JKOS 服务；不再承担 harness 进程托管与 UI 反代的运维面；不改 harness 任何代码，摆脱 SDK 协议 breaking 的耦合；中台改动两条线同时受益
- **负向**：两线并行，共享代码靠 cherry-pick 同步，长期存在发散成本（`README.md` / `CHANGELOG.md` / `CONTEXT.md` 是与 `main` 共享的文件，改动需克制）；反代只支持 http 上游、不转发 WebSocket 升级；面板的浏览器侧渲染没有自动化覆盖（验证机 Web 只绑 loopback，需 SSH 端口转发后人工确认）
- **保留**：`jkos_core/harness/` 的托管实现（config / runtime / gateway / routes / proxy / toolbridge）在 `main` 继续有效；本分支不删，回退路径完好
- **follow-up constraint**：
  - 插件层任何改动都必须走「本地语法/结构自检 + 验证机复验」两步；`--dump-config` 是 boot-free 的（不评估 `!!js`、不做启动检查），不能单独作为验收依据；
  - `jkos_core/` 的改动小步提交、一次一件事，便于两线互相挑拣；
  - 凭据、token、内网地址一律不进本分支任何文件 —— 该分支是公开的；
  - **未决**：JKOS 控制台根路由当前挂在 MCP 服务上（0.82 上为 3000），是否拆出独立控制台服务；面板是否要支持非 iframe 的原生渲染（需进 harness 客户端构建体系）