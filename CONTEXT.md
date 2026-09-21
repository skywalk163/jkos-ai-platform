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
| 自举闭环 (Self-Bootstrap Loop) | 用 M17 的自动化优化引擎驱动 JKOS 自身的探索/复盘/优化流程（探索 → 固化 → 模板化 → 自动化执行） | `jkos_core/selfboot/`；编排层，消费 `exploration` + `optimization`，不重造组件 |
| D3 执行器 | 自举闭环中「实验」的具体内容：单测生成管道（选定函数 → 用例设计 → 生成 → 运行 → 覆盖率报告） | `jkos_core/selfboot/d3_testgen.py`；LLM 可用走真实生成，否则确定性兜底 |
