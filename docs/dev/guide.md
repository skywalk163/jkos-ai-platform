# 极快AI操作系统 (JKOS) 开发指南

## 目录

1. [本地开发环境搭建](#本地开发环境搭建)
2. [架构概览](#架构概览)
3. [核心模块文档](#核心模块文档)
4. [配置指南](#配置指南)
5. [插件开发指南](#插件开发指南)
6. [工作流节点开发指南](#工作流节点开发指南)
7. [测试编写指南](#测试编写指南)
8. [API 使用示例](#api-使用示例)
9. [故障排查](#故障排查)

---

## 本地开发环境搭建

### 环境要求

| 组件 | 版本要求 | 说明 |
|------|----------|------|
| Python | 3.10+ | 推荐 3.11+ |
| SQLite | 3.40+ | 开发环境默认 |
| Git | 最新版 | 版本控制 |
| (可选) Redis | 7.0+ | 缓存服务 |
| (可选) PostgreSQL | 15+ | 生产数据库 |

### 快速开始

```bash
# 克隆项目
git clone <repo-url>
cd dsh-ai-platform

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
# 或 venv\Scripts\activate  # Windows

# 安装依赖
pip install -e ".[dev,test]"

# 配置环境变量
cp .env.example .env

# 初始化数据库
dsh-server init

# 运行测试
pytest

# 启动服务
dsh-server
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DSH_JWT_SECRET` | JWT 签名密钥 | 自动生成 |
| `DSH_LLM_API_KEY` | LLM API 密钥 (DeepSeek/OpenAI) | 无 |
| `DSH_QWEN_API_KEY` | 通义千问 API 密钥 | 无 |
| `DSH_CLAUDE_API_KEY` | Claude API 密钥 | 无 |
| `DSH_CORS_ORIGINS` | CORS 白名单 (逗号分隔) | `http://localhost:3000` |
| `DSH_ENCRYPTION_KEY` | 数据加密密钥 | 无 |
| `DSH_DATABASE_URL` | 数据库连接字符串 | `sqlite:///./data/dsh.db` |
| `DSH_REDIS_URL` | Redis 连接字符串 | 无 |
| `DSH_LOG_LEVEL` | 日志级别 | `INFO` |

### .env.example

```bash
# JWT 配置
DSH_JWT_SECRET=your-secret-key-here

# LLM API 密钥
DSH_LLM_API_KEY=sk-...
DSH_QWEN_API_KEY=sk-...
DSH_CLAUDE_API_KEY=sk-...

# CORS 配置
DSH_CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

# 数据库 (生产环境)
DSH_DATABASE_URL=postgresql://user:pass@localhost:5432/dsh

# 缓存 (生产环境)
DSH_REDIS_URL=redis://localhost:6379/0

# 加密密钥 (32 字节)
DSH_ENCRYPTION_KEY=your-32-byte-encryption-key-here

# 日志级别
DSH_LOG_LEVEL=INFO
```

---

## 架构概览

### 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      客户端层                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │  Web UI  │  │  CLI     │  │  MCP     │  │  A2A     │    │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘    │
└───────┼──────────────┼──────────────┼──────────────┼─────────┘
        │              │              │              │
        ▼              ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────┐
│                      API 网关层                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  FastAPI (RESTful) + Middleware (Auth/Security)      │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────┐
│                      服务层                                  │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────┐ │
│  │ LLM Router │ │ 工作流引擎  │ │ 探索引擎   │ │ 插件系统 │ │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └────┬─────┘ │
│        │              │              │              │       │
│  ┌─────┴──────────────┴──────────────┴──────────────┴─────┐ │
│  │              核心服务 (Core Services)                   │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │ │
│  │  │ 认证授权  │ │ 缓存管理  │ │ 消息总线  │ │ 存储管理 │  │ │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘  │ │
│  └──────────────────────────────────────────────────────┘ │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────┐
│                      基础设施层                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │ PostgreSQL│ │   Redis  │ │   NATS   │ │  MinIO   │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
└─────────────────────────────────────────────────────────────┘
```

### 模块依赖关系

```
dsh_core/
├── auth          # 认证授权 (无外部依赖)
├── llm           # LLM 供应商 (依赖 auth, cache)
├── cache         # 缓存 (依赖 db)
├── db            # 数据库 (无外部依赖)
├── bus           # 消息总线 (无外部依赖)
├── plugins       # 插件系统 (无外部依赖)
├── storage       # 存储管理 (无外部依赖)
├── workflow      # 工作流 (依赖 llm, plugins)
├── exploration   # 探索引擎 (依赖 llm, workflow)
├── api           # API 路由 (依赖所有服务层)
├── mcp           # MCP Server (依赖 api, llm)
├── audit         # 审计日志 (依赖 db)
├── notify        # 通知服务 (依赖 bus)
├── metrics       # 指标收集 (依赖 db)
└── utils         # 工具库 (无外部依赖)
```

---

## 核心模块文档

### 认证授权 (auth)

#### JWT 双 Token 机制

```python
from dsh_core.auth.jwt import JWTManager, TokenPair

# 初始化
jwt_manager = JWTManager(
    secret_key="your-secret",
    access_token_expiry=900,    # 15 分钟
    refresh_token_expiry=604800  # 7 天
)

# 生成令牌
token_pair = jwt_manager.generate_tokens(user_id="user123")
print(token_pair.access_token)
print(token_pair.refresh_token)

# 验证令牌
payload = jwt_manager.verify_token(access_token)

# 刷新令牌
new_access_token = jwt_manager.refresh_token(refresh_token)

# 撤销令牌
jwt_manager.revoke_token(token_id)
```

#### RBAC 权限模型

```python
from dsh_core.auth.rbac import RBACManager, Permission

# 初始化 RBAC
rbac = RBACManager()

# 定义权限
rbac.add_permission("admin", Permission.ALL)
rbac.add_permission("editor", ["read", "write"])
rbac.add_permission("viewer", ["read"])

# 检查权限
assert rbac.check_permission("admin", "delete") == True
assert rbac.check_permission("viewer", "write") == False
```

#### API 密钥管理

```python
from dsh_core.auth.apikey import APIKeyManager

# 初始化
key_manager = APIKeyManager()

# 生成 API 密钥
api_key = key_manager.generate_key(
    name="my-key",
    user_id="user123",
    permissions=["read", "write"],
    rate_limit=100  # 每分钟 100 次
)
print(api_key)  # 格式: dsh_xxxxx_xxxxx

# 验证 API 密钥
result = key_manager.verify_key("dsh_xxxxx_xxxxx")
print(result.valid)  # True/False
```

### LLM 供应商 (llm)

#### 供应商基类

```python
from dsh_core.llm.base import LLMProvider

# 所有供应商继承自 LLMProvider
class MyProvider(LLMProvider):
    name = "my-provider"
    default_model = "my-model"
    base_url = "https://api.example.com"

    async def chat(self, messages, model=None, **kwargs):
        # 实现聊天接口
        pass

    async def stream_chat(self, messages, model=None, **kwargs):
        # 实现流式聊天接口
        pass
```

#### LLM 路由器

```python
from dsh_core.llm.router import build_llm_router

# 构建路由器
router = build_llm_router(
    default_provider="deepseek",
    providers=["deepseek", "openai", "qwen", "claude", "simulated"]
)

# 调用聊天
response = await router.chat(
    messages=[{"role": "user", "content": "你好"}],
    model="deepseek"
)

# 流式调用
async for chunk in router.stream_chat(
    messages=[{"role": "user", "content": "写一首诗"}]
):
    print(chunk)
```

#### 支持的供应商

| 供应商 | 模块 | 默认模型 | 环境变量 |
|--------|------|----------|----------|
| DeepSeek | `dsh_core.llm.deepseek` | deepseek-chat | `DSH_LLM_API_KEY` |
| OpenAI | `dsh_core.llm.openai` | gpt-4o | `DSH_LLM_API_KEY` |
| 通义千问 | `dsh_core.llm.qwen` | qwen-turbo | `DSH_QWEN_API_KEY` |
| Claude | `dsh_core.llm.claude` | claude-3-5-sonnet-20241022 | `DSH_CLAUDE_API_KEY` |
| Simulated | `dsh_core.llm.simulated` | - | 无 |

### 缓存 (cache)

#### 多级缓存 + 布隆过滤器

```python
from dsh_core.cache.multi_layer import MultiLayerCache

# 初始化
cache = MultiLayerCache(
    l1_ttl=300,      # L1 内存缓存 5 分钟
    l2_ttl=3600,     # L2 Redis 缓存 1 小时
    bloom_filter_capacity=10000,
    bloom_filter_error_rate=0.01
)

# 设置缓存
await cache.set("key:1", {"data": "value"})

# 获取缓存
value = await cache.get("key:1")

# 删除缓存
await cache.delete("key:1")

# 批量操作
await cache.mset([("k1", "v1"), ("k2", "v2")])
values = await cache.mget(["k1", "k2"])
```

### 数据库 (db)

#### 连接池

```python
from dsh_core.db.connection import ConnectionPool

# 初始化连接池
pool = ConnectionPool(
    min_connections=2,
    max_connections=10,
    health_check_interval=60
)

# 获取连接
async with pool.acquire() as conn:
    result = await conn.fetch("SELECT * FROM users")

# 监控指标
metrics = pool.get_metrics()
print(f"活跃连接: {metrics['active_connections']}")
```

### 异步任务队列 (utils)

```python
from dsh_core.utils.asyncq import AsyncTaskQueue

# 初始化任务队列
queue = AsyncTaskQueue(
    max_workers=10,
    default_timeout=300,
    max_retries=3
)

# 提交任务
task_id = await queue.submit(
    func=my_async_function,
    args=(arg1, arg2),
    priority=1,
    timeout=60
)

# 获取结果
result = await queue.get_result(task_id)

# 取消任务
await queue.cancel(task_id)
```

### 工作流引擎 (workflow)

```python
from dsh_core.workflow.engine import WorkflowEngine
from dsh_core.workflow.nodes import TextGenerateNode

# 初始化引擎
engine = WorkflowEngine()

# 创建工作流
workflow = {
    "nodes": [
        {
            "id": "node1",
            "type": "text_generate",
            "config": {
                "prompt": "写一篇关于AI的文章",
                "model": "deepseek"
            }
        }
    ],
    "edges": []
}

# 执行工作流
result = await engine.execute(workflow)
```

### 探索引擎 (exploration)

```python
from dsh_core.exploration.engine import ExplorationEngine

# 初始化引擎
engine = ExplorationEngine()

# 执行探索
result = await engine.explore(
    task="优化数据库查询性能",
    context={"database": "PostgreSQL", "scale": "1000万条记录"}
)

print(result.solutions)  # 解决方案列表
print(result.experiments)  # 实验方案
```

---

## 配置指南

### 数据库配置

#### SQLite (开发环境)

```bash
# 默认配置
DSH_DATABASE_URL=sqlite:///./data/dsh.db
```

#### PostgreSQL (生产环境)

```bash
# 生产环境配置
DSH_DATABASE_URL=postgresql://user:password@localhost:5432/dsh

# 连接池配置 (可选)
DSH_DB_POOL_SIZE=10
DSH_DB_MAX_OVERFLOW=20
DSH_DB_POOL_TIMEOUT=30
```

### 缓存配置

#### 内存缓存 (开发环境)

```bash
# 默认使用内存缓存
DSH_CACHE_TYPE=memory
```

#### Redis (生产环境)

```bash
# Redis 配置
DSH_CACHE_TYPE=redis
DSH_REDIS_URL=redis://localhost:6379/0

# 缓存 TTL (秒)
DSH_CACHE_TTL=3600
```

### LLM 配置

#### 单供应商

```bash
# 使用 DeepSeek
DSH_LLM_PROVIDER=deepseek
DSH_LLM_API_KEY=sk-...
```

#### 多供应商 (自动降级)

```bash
# 供应商链 (按优先级)
DSH_LLM_FALLBACK_CHAIN=deepseek,openai,qwen,claude,simulated

# 各供应商 API 密钥
DSH_LLM_API_KEY=sk-...
DSH_QWEN_API_KEY=sk-...
DSH_CLAUDE_API_KEY=sk-...
```

### 安全配置

```bash
# JWT 密钥 (必须修改)
DSH_JWT_SECRET=your-very-secret-key-change-me

# 加密密钥 (32 字节)
DSH_ENCRYPTION_KEY=your-32-byte-encryption-key

# CORS 白名单
DSH_CORS_ORIGINS=https://yourdomain.com,https://app.yourdomain.com

# 速率限制
DSH_RATE_LIMIT_ENABLED=true
DSH_RATE_LIMIT_RPS=100
```

---

## 插件开发指南

### 插件接口

```python
from dsh_core.plugins.base import PluginBase

class MyPlugin(PluginBase):
    name = "my-plugin"
    version = "1.0.0"
    description = "我的插件"
    author = "Your Name"

    async def initialize(self, context):
        """插件初始化"""
        self.context = context
        print(f"插件 {self.name} 已加载")

    async def execute(self, input_data):
        """插件执行逻辑"""
        return {
            "result": "ok",
            "data": input_data
        }

    async def cleanup(self):
        """插件清理"""
        print(f"插件 {self.name} 已卸载")
```

### 注册插件

```python
from dsh_core.plugins.registry import plugin_registry

# 注册插件
plugin_registry.register(MyPlugin())

# 从目录加载插件
plugin_registry.load_from_directory("plugins/")
```

### 插件市场

```python
from dsh_core.plugins.marketplace import PluginMarketplace

marketplace = PluginMarketplace()

# 发布插件
await marketplace.publish(MyPlugin())

# 安装插件
await marketplace.install("my-plugin")

# 更新插件
await marketplace.update("my-plugin", "1.1.0")
```

---

## 工作流节点开发指南

### 节点接口

```python
from dsh_core.workflow.base import WorkflowNode

class MyNode(WorkflowNode):
    node_code = "my-node"
    node_type = "tool"
    description = "我的工作流节点"

    async def execute(self, context):
        """执行逻辑"""
        input_data = context.get("input")
        
        # 处理逻辑
        result = {
            "output": f"处理结果: {input_data}"
        }
        
        return result

    async def validate(self, config):
        """验证配置"""
        return True
```

### 预定义节点

| 节点类型 | 描述 |
|----------|------|
| `text_query` | 文本查询 |
| `text_generate` | 文本生成 |
| `ocr_extract` | OCR 识别 |
| `audio_transcribe` | 语音转文字 |
| `video_analyze` | 视频分析 |
| `rag_query` | RAG 检索 |
| `approval` | 审批节点 |
| `http_request` | HTTP 请求 |
| `code_execute` | 代码执行 |

---

## 测试编写指南

### 单元测试

```python
import pytest
from dsh_core.utils import RateLimiter

def test_rate_limiter():
    """测试速率限制器"""
    limiter = RateLimiter(rps=10, burst=20)
    
    # 正常请求
    assert limiter.acquire() == True
    
    # 超过限制
    for _ in range(20):
        limiter.acquire()
    
    assert limiter.acquire() == False
```

### 异步测试

```python
import pytest
from dsh_core.llm.router import build_llm_router

@pytest.mark.asyncio
async def test_llm_router_chat():
    """测试 LLM 路由器"""
    router = build_llm_router()
    
    response = await router.chat(
        messages=[{"role": "user", "content": "你好"}]
    )
    
    assert response is not None
    assert "content" in response
```

### 测试固件

```python
# conftest.py
import pytest
import pytest_asyncio
from dsh_core.db.connection import ConnectionPool

@pytest_asyncio.fixture
async def db_pool():
    """数据库连接池固件"""
    pool = ConnectionPool(min_connections=1, max_connections=5)
    yield pool
    await pool.close()

@pytest.fixture
def sample_data():
    """测试数据固件"""
    return {
        "users": [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"}
        ]
    }
```

### 运行测试

```bash
# 所有测试
pytest

# 覆盖率报告
pytest --cov=dsh_core --cov-report=html

# 特定测试文件
pytest tests/test_m8_auth.py

# 特定测试用例
pytest tests/test_m8_auth.py::test_jwt_token_generation

# 并行测试
pytest -n auto
```

---

## API 使用示例

### 认证

```python
from fastapi import FastAPI, Depends, HTTPException
from dsh_core.auth.dependencies import require_auth

app = FastAPI()

@app.get("/protected")
async def protected_route(user=Depends(require_auth)):
    return {"user": user, "message": "认证成功"}
```

### LLM 调用

```python
from dsh_core.llm.router import get_llm_router

@app.post("/api/v1/chat")
async def chat(request: ChatRequest):
    router = get_llm_router()
    
    response = await router.chat(
        messages=request.messages,
        model=request.model
    )
    
    return {"response": response}
```

### 流式响应

```python
from fastapi import Response
from dsh_core.llm.router import get_llm_router

@app.post("/api/v1/chat/stream")
async def chat_stream(request: ChatRequest):
    router = get_llm_router()
    
    async def event_stream():
        async for chunk in router.stream_chat(
            messages=request.messages
        ):
            yield f"data: {chunk.json()}\n\n"
    
    return Response(
        content=event_stream(),
        media_type="text/event-stream"
    )
```

---

## 故障排查

### 服务无法启动

1. 检查日志: `docker logs dsh-api`
2. 检查端口: `netstat -tlnp | grep 8000`
3. 检查环境变量: `docker-compose config`

### 数据库连接失败

1. 检查 SQLite 文件权限
2. 检查磁盘空间: `df -h`
3. 检查 PostgreSQL 连接字符串

### 缓存连接失败

1. 检查 Redis 是否运行
2. 检查网络连通性
3. 检查 Redis 密码配置

### LLM API 调用失败

1. 检查 API 密钥是否正确
2. 检查 API 配额是否充足
3. 检查网络连通性
4. 查看降级日志确认是否切换到备用供应商

---

## 相关文档

- [API 文档](openapi.md)
- [部署运维指南](../ops/deploy.md)
- [架构设计](../architecture/)
- [技术设计白皮书](../../多模态AI-Agent中台-技术设计白皮书.md)
