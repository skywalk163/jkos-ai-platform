# Changelog

> 记录极快AI操作系统（JKOS，原名 DSH AI 中台）各里程碑与后续变更。
> 格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本语义参考 [SemVer](https://semver.org/lang/zh-CN/)。
> M2-M4 为早期规划实现的里程碑，功能已折叠计入后续里程碑提交，无独立提交记录。

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