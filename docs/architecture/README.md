# 架构设计文档

## 目录

1. [系统架构](#系统架构)
2. [核心模块设计](#核心模块设计)
3. [数据流设计](#数据流设计)
4. [安全架构](#安全架构)
5. [性能设计](#性能设计)
6. [可观测性设计](#可观测性设计)
7. [扩展性设计](#扩展性设计)

---

## 系统架构

### 整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                          客户端层 (Client Layer)                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────┐  │
│  │  Web UI  │  │  CLI     │  │  MCP     │  │  A2A     │  │ SDK   │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───┬───┘  │
└───────┼──────────────┼──────────────┼──────────────┼─────────────┼────┘
        │              │              │              │             │
        ▼              ▼              ▼              ▼             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        API 网关层 (Gateway Layer)                    │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  FastAPI (RESTful) + Middleware Stack                         │  │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────┐ │  │
│  │  │ CORS        │ │ Auth        │ │ Rate Limit  │ │ Security│ │  │
│  │  │ Middleware  │ │ Middleware  │ │ Middleware  │ │ Headers │ │  │
│  │  └─────────────┘ └─────────────┘ └─────────────┘ └─────────┘ │  │
│  └───────────────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────────────┐
│                        服务层 (Service Layer)                        │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐    │
│  │ LLM Router │  │ Workflow   │  │ Exploration│  │ Plugins    │    │
│  │            │  │ Engine     │  │ Engine     │  │ System     │    │
│  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘    │
│        │               │               │               │           │
│  ┌─────┴───────────────┴───────────────┴───────────────┴─────┐     │
│  │                    核心服务 (Core Services)                   │     │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │     │
│  │  │ Auth     │  │ Cache    │  │ Bus      │  │ Storage  │    │     │
│  │  │ Service  │  │ Service  │  │ Service  │  │ Service  │    │     │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │     │
│  └──────────────────────────────────────────────────────────────┘     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────────────┐
│                      基础设施层 (Infrastructure Layer)               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │
│  │PostgreSQL│  │   Redis  │  │   NATS   │  │  MinIO   │            │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘            │
└─────────────────────────────────────────────────────────────────────┘
```

### 模块依赖关系

```
dsh_core/
│
├── auth              [无外部依赖]
│   ├── jwt.py        # JWT 双 Token 机制
│   ├── rbac.py       # RBAC 权限模型
│   ├── apikey.py     # API 密钥管理
│   └── context.py    # 认证上下文
│
├── llm               [依赖 auth, cache]
│   ├── base.py       # 供应商基类
│   ├── router.py     # LLM 路由器
│   ├── deepseek.py   # DeepSeek 供应商
│   ├── openai.py     # OpenAI 供应商
│   ├── qwen.py       # 通义千问供应商
│   ├── claude.py     # Claude 供应商
│   └── simulated.py  # 模拟供应商
│
├── cache             [依赖 db]
│   ├── manager.py    # 缓存管理器
│   └── multi_layer.py # 多级缓存 + 布隆过滤器
│
├── db                [无外部依赖]
│   ├── connection.py # 连接池
│   ├── postgres.py   # PostgreSQL 客户端
│   ├── repos.py      # 仓储层
│   ├── schema.py     # 数据模型
│   └── ulid.py       # ULID 生成器
│
├── bus               [无外部依赖]
│   └── nats.py       # NATS 客户端
│
├── plugins           [无外部依赖]
│   ├── base.py       # 插件 SDK
│   ├── registry.py   # 插件注册表
│   └── marketplace.py # 插件市场
│
├── storage           [无外部依赖]
│   └── manager.py    # 存储管理器
│
├── workflow          [依赖 llm, plugins]
│   ├── base.py       # 节点基类
│   ├── engine.py     # 工作流引擎
│   ├── nodes.py      # 预定义节点
│   └── approval.py   # 审批节点
│
├── exploration       [依赖 llm, workflow]
│   ├── base.py       # 基类
│   ├── engine.py     # 探索引擎
│   ├── decomposer.py # 任务分解器
│   ├── solution_searcher.py
│   ├── experimenter.py
│   └── knowledge_base.py
│
├── api               [依赖所有服务层]
│   ├── routes.py     # 主路由
│   ├── security.py   # 安全中间件
│   └── metrics.py    # 健康检查/指标
│
├── mcp               [依赖 api, llm]
│   └── server.py     # MCP 协议实现
│
├── audit             [依赖 db]
│   └── logger.py     # 审计记录器
│
├── notify            [依赖 bus]
│   └── channels.py   # 通知渠道
│
├── metrics           [依赖 db]
│   └── collector.py  # 指标收集器
│
├── utils             [无外部依赖]
│   ├── asyncq.py     # 异步任务队列
│   └── ratelimit.py  # 速率限制
│
└── models            [无外部依赖]
    └── __init__.py   # 数据模型
```

---

## 核心模块设计

### 认证授权模块 (auth)

**设计目标**:
- 双 Token 机制，平衡安全性与用户体验
- RBAC 权限模型，支持细粒度权限控制
- API 密钥管理，支持非交互式访问

**核心类**:

```
auth/
├── jwt.py            # JWT 管理器
│   ├── JWTManager    # JWT 令牌管理
│   ├── TokenPair     # 令牌对 (access + refresh)
│   └── TokenBlacklist # 令牌黑名单
│
├── rbac.py           # RBAC 权限管理
│   ├── RBACManager   # RBAC 管理器
│   ├── Permission    # 权限枚举
│   └── Role          # 角色定义
│
├── apikey.py         # API 密钥管理
│   ├── APIKeyManager # API 密钥管理器
│   └── APIKey        # API 密钥模型
│
├── context.py        # 认证上下文
│   ├── AuthContext   # 认证上下文
│   └── get_current_user # 获取当前用户
│
└── dependencies.py   # FastAPI 依赖注入
    ├── require_auth  # 认证依赖
    └── require_permission # 权限依赖
```

**权限矩阵**:

| 角色 | 读取 | 写入 | 删除 | 管理 |
|------|------|------|------|------|
| admin | ✓ | ✓ | ✓ | ✓ |
| editor | ✓ | ✓ | ✗ | ✗ |
| viewer | ✓ | ✗ | ✗ | ✗ |
| guest | ✓ | ✗ | ✗ | ✗ |

### LLM 供应商模块 (llm)

**设计目标**:
- 统一接口，支持多供应商
- 自动降级，保证可用性
- 流式响应，提升用户体验

**核心类**:

```
llm/
├── base.py           # 供应商基类
│   ├── LLMProvider   # 供应商基类
│   ├── ChatMessage   # 消息模型
│   └── ChatResponse  # 响应模型
│
├── router.py         # LLM 路由器
│   ├── LLMRouter     # 路由器
│   ├── build_llm_router # 构建函数
│   └── ProviderChain # 供应商链
│
├── deepseek.py       # DeepSeek 供应商
│   └── DeepSeekProvider
│
├── openai.py         # OpenAI 供应商
│   └── OpenAIProvider
│
├── qwen.py           # 通义千问供应商
│   └── QwenProvider
│
├── claude.py         # Claude 供应商
│   └── ClaudeProvider
│
└── simulated.py      # 模拟供应商
    └── SimulatedProvider
```

**供应商链**:

```
deepseek → openai → qwen → claude → simulated
   ↓         ↓        ↓        ↓         ↓
  优先     降级     降级     降级     兜底
```

### 缓存模块 (cache)

**设计目标**:
- 多级缓存，提升性能
- 布隆过滤器，防止缓存穿透
- 自动降级，保证可用性

**核心类**:

```
cache/
├── manager.py        # 缓存管理器
│   ├── CacheManager  # 缓存管理器
│   └── CacheBackend  # 缓存后端接口
│
└── multi_layer.py    # 多级缓存
    ├── MultiLayerCache # 多级缓存
    ├── BloomFilter   # 布隆过滤器
    └── CacheMetrics  # 缓存指标
```

**缓存层级**:

```
请求 → L1 内存缓存 (TTL: 5分钟)
         ↓ miss
       L2 Redis 缓存 (TTL: 1小时)
         ↓ miss
       数据库/LLM
         ↓
       回写 L1 + L2
```

### 数据库模块 (db)

**设计目标**:
- 连接池，提升性能
- 多租户，支持 SaaS
- 健康检查，保证可用性

**核心类**:

```
db/
├── connection.py     # 连接池
│   ├── ConnectionPool # 连接池
│   ├── Connection    # 连接包装
│   └── PoolMetrics   # 池指标
│
├── postgres.py       # PostgreSQL 客户端
│   ├── PostgresClient # PostgreSQL 客户端
│   └── QueryBuilder  # 查询构建器
│
├── repos.py          # 仓储层
│   ├── BaseRepository # 基础仓储
│   └── UserRepository # 用户仓储
│
├── schema.py         # 数据模型
│   ├── Base          # 基础模型
│   └── models        # 具体模型
│
├── tenant_schema.py  # 多租户 Schema
│   └── TenantContext # 租户上下文
│
└── ulid.py           # ULID 生成器
    └── ULID          # ULID 生成器
```

---

## 数据流设计

### LLM 调用数据流

```
用户请求
    ↓
API 网关
    ↓
认证中间件 (验证 Token)
    ↓
权限中间件 (检查权限)
    ↓
速率限制中间件
    ↓
LLM Router
    ↓
检查缓存 (L1 + L2)
    ↓ hit
返回缓存结果
    ↓ miss
选择供应商 (按优先级)
    ↓
调用 LLM API
    ↓
解析响应
    ↓
写入缓存 (L1 + L2)
    ↓
返回结果
```

### 工作流执行数据流

```
工作流定义
    ↓
解析节点
    ↓
拓扑排序
    ↓
按依赖顺序执行
    ↓
节点执行
    ├── 文本生成 → LLM Router
    ├── 数据查询 → 数据库
    ├── OCR 识别 → 插件系统
    └── 审批 → 审批服务
    ↓
收集结果
    ↓
返回执行结果
```

---

## 安全架构

### 认证流程

```
用户登录
    ↓
验证凭据
    ↓
生成 Access Token (15分钟)
    ↓
生成 Refresh Token (7天)
    ↓
返回 Token Pair
    ↓
API 请求携带 Access Token
    ↓
验证 Token 签名
    ↓
检查 Token 黑名单
    ↓
检查 Token 过期
    ↓
验证通过，处理请求
    ↓
Token 过期
    ↓
使用 Refresh Token 获取新 Access Token
```

### 授权流程

```
API 请求
    ↓
获取用户身份
    ↓
获取用户角色
    ↓
获取所需权限
    ↓
检查角色权限
    ↓
允许/拒绝
```

### 安全措施

| 层级 | 措施 | 说明 |
|------|------|------|
| 网络层 | HTTPS | 传输加密 |
| 网络层 | CORS | 跨域限制 |
| 应用层 | JWT | 认证授权 |
| 应用层 | RBAC | 权限控制 |
| 应用层 | 速率限制 | 防刷 |
| 数据层 | 加密 | 敏感数据加密 |
| 数据层 | 审计日志 | 操作追溯 |

---

## 性能设计

### 连接池设计

```python
from jkos_core.db.connection import ConnectionPool

# 连接池配置（SQLite 内置连接池）
pool = ConnectionPool(
    min_size=2,     # 最小连接数
    max_size=10,    # 最大连接数
)

# 监控指标（metrics()）
metrics = pool.metrics()
print(f"空闲: {metrics['idle']}, 活跃: {metrics['active']}, 总: {metrics['total']}")
```

### 缓存策略

| 数据类型 | 缓存层级 | TTL | 说明 |
|----------|----------|-----|------|
| LLM 响应 | L1 + L2 | 1小时 | 相同提示词缓存 |
| 用户会话 | L1 + L2 | 7天 | 会话数据 |
| 配置信息 | L1 | 24小时 | 系统配置 |
| 热点数据 | L1 | 5分钟 | 高频访问数据 |

### 异步处理

```python
# 异步任务队列
queue = AsyncTaskQueue(
    max_workers=10,      # 最大并发数
    default_timeout=300, # 默认超时
    max_retries=3        # 最大重试次数
)

# 提交任务
task_id = await queue.submit(
    func=heavy_computation,
    args=(arg1, arg2),
    priority=1
)
```

---

## 可观测性设计

### 指标收集

```python
# 指标类型
metrics = {
    "requests_total": Counter,      # 请求总数
    "requests_duration": Histogram, # 请求延迟
    "llm_calls_total": Counter,     # LLM 调用次数
    "cache_hits": Counter,          # 缓存命中次数
    "cache_misses": Counter,        # 缓存未命中次数
    "db_connections": Gauge,        # 数据库连接数
    "queue_size": Gauge             # 队列大小
}
```

### 健康检查

```yaml
# 健康检查端点
GET /healthz
  - 数据库连接
  - 缓存连接
  - LLM 供应商

GET /readyz
  - 数据库就绪
  - 缓存就绪
  - 服务就绪
```

### 日志规范

```python
# 日志格式
{
    "timestamp": "2026-03-13T10:00:00Z",
    "level": "INFO",
    "trace_id": "abc123",
    "span_id": "def456",
    "service": "dsh-api",
    "method": "POST",
    "path": "/api/v1/chat",
    "status": 200,
    "duration_ms": 150,
    "user_id": "user123"
}
```

---

## 扩展性设计

### 插件系统

```python
# 插件接口
class PluginBase:
    name: str
    version: str
    description: str
    
    async def initialize(self, context):
        """初始化"""
        
    async def execute(self, input_data):
        """执行"""
        
    async def cleanup(self):
        """清理"""
```

### 工作流节点

```python
# 节点接口
class WorkflowNode:
    node_code: str
    node_type: str
    
    async def execute(self, context):
        """执行"""
        
    async def validate(self, config):
        """验证配置"""
```

### 多租户支持

```python
# 租户上下文
class TenantContext:
    tenant_id: str
    tenant_code: str
    
    @classmethod
    def get_current(cls):
        """获取当前租户"""
        
    @classmethod
    def set_current(cls, tenant):
        """设置当前租户"""
```

---

## 相关文档

- [开发指南](../dev/guide.md)
- [API 文档](../api/openapi.md)
- [部署运维指南](../ops/deploy.md)
- [技术设计白皮书](../../多模态AI-Agent中台-技术设计白皮书.md)
