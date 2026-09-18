# 极快AI操作系统 (JKOS)

> Jikuai AI OS —— 基于 FreeBSD + deepseek-harness 构建的企业级 AI 服务操作系统

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Tests](https://img.shields.io/badge/tests-789%20passed-brightgreen)]()

> **品牌说明**：本项目原名「DSH AI 中台」，现更名为**极快AI操作系统**（英文 **Jikuai AI OS**，简称 **JKOS**）。设计路线为结合开源项目 [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)（FreeBSD 版）作为 Agent 执行层，在 FreeBSD 系统下构建一整套 AI 服务操作系统；与 deepseek-harness 的深度集成将在后续里程碑落地，当前阶段自研实现为多租户企业服务层（REST / 多租户 MCP 网关 / RBAC / 审计 / 多 LLM 路由）。

## 🚀 快速开始

```bash
# 安装依赖
pip install -e ".[dev,test]"

# 初始化数据库
dsh-server init

# 启动服务
dsh-server

# 或只启动 MCP Server
dsh-server mcp --port 3000
```

## ✨ 核心特性

### 多模态能力
- **文本**: 查询、生成、翻译、摘要、流式生成
- **图像**: OCR 识别、图像理解、图像生成
- **语音**: 语音转文字 (Whisper)、文字转语音、声纹识别
- **视频**: 抽帧、转码、字幕提取、内容理解

### Agent 互联
- **MCP 协议**: 对接 Cursor/Trae/Claude Desktop 等本地 Agent
- **A2A 协议**: 对接 Dify/Coze/GPTs 等远程 SaaS Agent
- **8 个预定义工具**: 文本查询、文本生成、OCR、ASR、视频分析、RAG 等

### 企业级特性
- **确定性优先**: 编解码/转码/OCR/ASR 全部用专用工具，大模型只做语义
- **原子插件化**: 所有能力为原子插件（借鉴 Cordis 设计），热插拔、独立版本
- **FreeBSD 安全**: ZFS 快照、Linux Jail 隔离、宿主 `/usr` 只读
- **全链路审计**: 所有操作可追溯、可审计

## 📦 功能模块

### LLM 供应商 (M10)
| 供应商 | 默认模型 | Base URL |
|--------|----------|----------|
| DeepSeek | deepseek-chat | https://api.deepseek.com |
| OpenAI | gpt-4o | https://api.openai.com |
| 通义千问 (Qwen) | qwen-turbo | https://dashscope.aliyuncs.com |
| Claude (Anthropic) | claude-3-5-sonnet-20241022 | https://api.anthropic.com |
| Simulated | - | 本地模拟 |

### 认证授权 (M8)
- **JWT 双 Token**: Access Token (15分钟) + Refresh Token (7天)
- **RBAC 权限模型**: 10 角色 × 10 资源权限矩阵
- **API 密钥管理**: 前缀标识、加密存储、速率限制

### 安全加固 (M8)
- **速率限制**: 多租户令牌桶限流
- **CSRF 防护**: 双重 Cookie 验证
- **安全头**: HSTS、X-Frame-Options、CSP
- **CORS 白名单**: 动态配置

### 性能优化 (M7)
- **ConnectionPool**: 数据库连接池（min/max 配置、泄漏检测、健康检查）
- **MultiLayerCache**: 多级缓存 + 布隆过滤器（L1 内存 / L2 Redis）
- **AsyncTaskQueue**: 异步任务队列（优先级、超时、重试）
- **LLM Router 缓存**: 流式响应缓存、请求合并

### 可观测性 (M8)
- **健康检查**: `/healthz` 端点，检查各组件连通性
- **就绪检查**: `/readyz` 端点，检查服务就绪状态
- **Prometheus 指标**: `/metrics` 端点，暴露运行时指标
- **审计日志**: 全链路操作审计

### 探索引擎 (M11)
- **TaskDecomposer**: 任务分解器
- **SolutionSearcher**: 解决方案搜索器
- **Experimenter**: 实验执行器
- **KnowledgeBase**: 知识库管理

### 自动化引擎 (M12)
- **AutomationEngine**: 自动化任务编排引擎
- **ProcessSolidifier**: 流程固化器（将探索流程固化为可复用自动化）
- **TokenOptimizer**: Token 优化器（上下文压缩、成本控制）
- **TemplateManager**: 自动化模板管理

## 📅 里程碑记录

> M2-M4 为早期规划实现的里程碑，功能已折叠计入后续里程碑提交，无独立提交记录。

| 里程碑 | 交付内容 | 提交 |
|--------|---------|------|
| M2 内容管线 | 内容生产 + 舆情情感（已折叠实现） | - |
| M3 基础设施 | LLM 路由 + 通知 + 缓存（已折叠实现） | - |
| M4 酒厂案例 | 生产调度 + 追溯 + 适配器（已折叠实现） | - |
| M6 测试治理 | 测试覆盖治理（114 用例） | `abda890` |
| M7 性能优化 | 连接池 / 多级缓存 / 异步队列 / LLM 路由缓存 | `5b37850` |
| M8 认证与安全 | JWT 双 Token / RBAC / API 密钥 / 限流 / CSRF / 可观测性 | `96a8128`, `60c03d2` |
| M9 文档 | 开发 / API / 部署运维 / 架构文档 | `1158332` |
| M10 LLM | 多供应商（DeepSeek / OpenAI / Qwen / Claude / Simulated） | `1382116` |
| M11 探索引擎 | 任务分解 / 方案搜索 / 实验执行 / 知识库 | `a7c87ba` |
| M12 自动化引擎 | 自动化编排 / 流程固化 / Token 优化 / 模板管理 | `5802bb8`（合入 `80cad99`） |
| M13 工具标准化 | 工具注册表 / 工具市场元数据 / 可见性域 / 版本管理 / 工具路由 | `5b7c380` |
| M14 MCP 网关多租户 | 租户级工具可见性 / 租户级配额限流 / 可选认证上下文 / 工具归属管理 | `f825b86` |

## 📁 项目结构

```
dsh-ai-platform/
├── pyproject.toml              # 项目配置
├── dsh_core/                   # 核心代码
│   ├── __init__.py             # 包初始化
│   ├── cli.py                  # CLI 入口
│   ├── plugin_cli.py           # 插件 CLI
│   ├── bootstrap.py            # 启动引导
│   ├── auth/                   # 认证授权 (M8)
│   │   ├── jwt.py              # JWT 双 Token
│   │   ├── rbac.py             # RBAC 权限模型
│   │   ├── apikey.py           # API 密钥管理
│   │   ├── context.py          # 认证上下文
│   │   └── dependencies.py     # FastAPI 依赖注入
│   ├── api/                    # API 路由
│   │   ├── routes.py           # 主路由
│   │   ├── security.py         # 安全中间件
│   │   ├── metrics.py          # 健康检查/指标
│   │   └── marketplace_routes.py # 插件市场路由
│   ├── llm/                    # LLM 供应商 (M10)
│   │   ├── base.py             # 供应商基类
│   │   ├── router.py           # LLM 路由器
│   │   ├── deepseek.py         # DeepSeek 供应商
│   │   ├── openai.py           # OpenAI 供应商
│   │   ├── qwen.py             # 通义千问供应商
│   │   ├── claude.py           # Claude 供应商
│   │   └── simulated.py        # 模拟供应商
│   ├── cache/                  # 缓存 (M7)
│   │   ├── manager.py          # 缓存管理器
│   │   └── multi_layer.py      # 多级缓存 + 布隆过滤器
│   ├── db/                     # 数据库 (M7)
│   │   ├── connection.py       # 连接池
│   │   ├── postgres.py         # PostgreSQL 客户端
│   │   ├── repos.py            # 仓储层
│   │   ├── schema.py           # 数据模型
│   │   ├── tenant_schema.py    # 多租户 Schema
│   │   └── ulid.py             # ULID 生成器
│   ├── bus/                    # 消息总线
│   │   └── nats.py             # NATS 客户端
│   ├── plugins/                # 插件系统
│   │   ├── base.py             # 插件 SDK
│   │   ├── registry.py         # 插件注册表
│   │   ├── marketplace.py      # 插件市场
│   │   ├── i18n.py             # 国际化
│   │   └── ocr/                # OCR 插件
│   │       └── plugin.py
│   ├── mcp/                    # MCP Server
│   │   └── server.py           # MCP 协议实现
│   ├── storage/                # 存储管理
│   │   └── manager.py          # 存储管理器
│   ├── workflow/               # 工作流引擎
│   │   ├── base.py             # 节点基类
│   │   ├── engine.py           # 工作流引擎
│   │   ├── nodes.py            # 预定义节点
│   │   └── approval.py         # 审批节点
│   ├── exploration/            # 探索引擎 (M11)
│   │   ├── base.py             # 基类
│   │   ├── engine.py           # 探索引擎
│   │   ├── decomposer.py       # 任务分解器
│   │   ├── solution_searcher.py # 解决方案搜索器
│   │   ├── experimenter.py     # 实验执行器
│   │   ├── knowledge_base.py   # 知识库
│   │   └── seed_data.py        # 种子数据
│   ├── audit/                  # 审计日志
│   │   └── logger.py           # 审计记录器
│   ├── notify/                 # 通知服务
│   │   └── channels.py         # 通知渠道
│   ├── metrics/                # 指标收集
│   │   └── collector.py        # 指标收集器
│   ├── utils/                  # 工具库
│   │   ├── asyncq.py           # 异步任务队列 (M7)
│   │   └── ratelimit.py        # 速率限制 (M8)
│   └── models/                 # 数据模型
│       └── __init__.py
├── scripts/                    # 运维脚本
│   └── freebsd-init.sh         # FreeBSD 环境初始化
├── tests/                      # 测试套件
│   ├── conftest.py             # 测试配置
│   ├── test_mcp.py             # MCP 测试
│   ├── test_storage.py         # 存储测试
│   ├── test_m7_*.py            # M7 性能测试
│   ├── test_m8_*.py            # M8 安全测试
│   ├── test_m10_llm.py         # M10 LLM 测试
│   └── plugins/
│       └── test_ocr.py         # OCR 插件测试
└── docs/                       # 文档
    ├── api/                    # API 文档
    ├── dev/                    # 开发文档
    ├── ops/                    # 运维文档
    └── architecture/           # 架构文档
```

## 🧪 测试

```bash
# 运行所有测试
pytest tests/ -v

# 覆盖率报告
pytest tests/ --cov=dsh_core --cov-report=html

# 特定测试文件
pytest tests/test_m8_auth.py
pytest tests/test_m10_llm.py
```

**当前测试状态**: 789 passed ✅

## 📋 预定义 MCP 工具

| 工具名 | 描述 |
|--------|------|
| `dsh_text_query` | 查询 DSH 文本数据 |
| `dsh_text_generate` | 大模型文本生成 |
| `dsh_ocr_extract` | 图片 OCR 文字识别 |
| `dsh_audio_transcribe` | 语音转文字 (ASR) |
| `dsh_video_analyze` | 视频内容分析 |
| `dsh_rag_query` | 多模态 RAG 检索 |
| `dsh_session_list` | 列出对话会话 |
| `dsh_resource_list` | 列出多模态资源 |

## 🔧 开发

```bash
# 安装开发依赖
pip install -e ".[dev,test]"

# 代码检查
ruff check dsh_core/

# 类型检查
mypy dsh_core/

# 格式化
ruff format dsh_core/
```

## 📚 文档

- [开发指南](docs/dev/guide.md)
- [API 文档](docs/api/openapi.md)
- [部署运维指南](docs/ops/deploy.md)
- [架构设计](docs/architecture/)
- [技术设计白皮书](docs/多模态AI-Agent中台-技术设计白皮书.md)
- [10 周实施计划](docs/多模态AI-Agent中台-实施计划.md)

## 📜 License

Apache-2.0
