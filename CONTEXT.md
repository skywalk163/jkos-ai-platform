# Context

## Glossary

| Term | Meaning | Notes |
|---|---|---|
| JKOS | 极快AI操作系统（Jikuai AI OS），本项目本体，Python/FastAPI | 原名 DSH AI 中台，M15 完成代码层更名 |
| 智能中枢 (Intelligent Hub) | 由 deepseek-harness runtime 承载的 Agent 编排能力：会话、工具编排、子代理 | 经官方 Python SDK 以 sidecar 方式嵌入，不是独立并行系统 |
| HarnessRuntime | 被 JKOS 托管的 `dsh` sidecar 进程（JSON-RPC over stdio） | 单实例起步；崩溃由 JKOS 重启并标 degraded |
| HarnessGateway | `jkos_core/harness/` 网关模块：sidecar 生命周期 + 会话 API + 事件流转发 | JKOS 对外唯一 harness 入口 |
| HubSession | 一次中枢会话（harness session 的 JKOS 视图），带 tenant/user 身份 | REST 创建，SSE 收事件 |
| ToolBridge | 把 JKOS ToolRegistry 工具暴露给 harness agent 的桥 | 倾向 MCP 形态；只暴露租户可见工具（复用 M14 可见性） |
| DSH_HOME | harness 运行家目录，由 JKOS 管理与注入 | harness 自身密钥文件不落盘；凭据仅经 JKOS `.env` 注入 |
