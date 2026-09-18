# ADR 0001: deepseek-harness 以 JKOS 托管的 SDK sidecar 形态作为智能中枢

Status: Accepted
Date: 2026-09-18

## Context

JKOS 需要引入 DeepSeek 官方 agent harness（Node/Cordis 架构）作为智能中枢。可选形态有三种：(a) 经官方 Python SDK 以长驻 sidecar 子进程嵌入；(b) 仅作高级 LLM 执行器；(c) 独立并行服务（Web UI + API 网关自持，JKOS 只做反代/SSO）。harness 官方提供 Python SDK（`deepseek-harness-sdk`，JSON-RPC over stdio，支持 session/prompt/事件流）与 external plugin/MCP 扩展机制；FreeBSD 无预编译 wheel，需源码构建 Node carrier（仓库自带 FREEBSD.md runbook）。

## Decision

采用 (a)：JKOS 新增 `jkos_core/harness/` 网关，经官方 Python SDK 托管单一 harness sidecar，会话/工具编排/子代理由 harness 承载；同时做 ToolBridge（工具桥接）、JWT 身份传导、租户配额与 Web UI 反代的全量集成。`jkos_core.llm.router` 保留为直连兜底，workflow 引擎不动。LLM 凭据由 JKOS `.env` 统一注入 sidecar，harness 不落密钥文件。

## Consequences

- 正向：复用 harness 成熟的 agent 编排与插件生态；对外仍是单一 JKOS API 面；凭据收敛在 `.env`；工具桥接复用 M14 租户可见性，权限面可控
- 负向：harness 处于 developer preview，SDK 协议 breaking 风险由 JKOS 承担；引入 Node 运行时依赖（0.82 需源码构建，构建链条长）；进程托管与崩溃恢复逻辑新增运维面
- follow-up constraint：实现必须锁定 harness 克隆 commit；升级 harness 走独立变更并重跑集成冒烟；ToolBridge 只暴露租户可见工具，不得绕过 `acquire_by_tenant` 配额
