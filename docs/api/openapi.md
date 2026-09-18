# 极快AI操作系统 (JKOS) API 文档 (OpenAPI 3.0)

## 版本信息

- **版本**: 0.1.0
- **描述**: 多模态 AI Agent 中台 API
- **基础路径**: `/api/v1`
- **协议**: HTTPS (生产环境)
- **格式**: JSON

## 认证

### Bearer Token

所有 API 请求需要在 Header 中携带 Bearer Token：

```
Authorization: Bearer <access_token>
```

### 刷新令牌

当 Access Token 过期时，使用 Refresh Token 获取新的 Access Token：

```
X-Refresh-Token: <refresh_token>
```

### API 密钥

对于非交互式访问，可以使用 API 密钥：

```
X-API-Key: <api_key>
```

## 通用请求头

| Header | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `Authorization` | string | 否 | Bearer Token |
| `X-API-Key` | string | 否 | API 密钥 |
| `X-Refresh-Token` | string | 否 | 刷新令牌 |
| `Content-Type` | string | 是 | `application/json` |
| `Accept` | string | 否 | `application/json` 或 `text/event-stream` |

## 通用响应格式

### 成功响应

```json
{
  "code": 200,
  "message": "success",
  "data": {},
  "timestamp": "2026-03-13T10:00:00Z"
}
```

### 错误响应

```json
{
  "code": 400,
  "message": "请求参数错误",
  "error": {
    "type": "ValidationError",
    "details": [
      {
        "field": "prompt",
        "message": "该字段不能为空"
      }
    ]
  },
  "timestamp": "2026-03-13T10:00:00Z"
}
```

## 错误码

| 状态码 | 含义 | 说明 |
|--------|------|------|
| 200 | 成功 | 请求成功 |
| 201 | 创建成功 | 资源创建成功 |
| 204 | 无内容 | 删除成功 |
| 400 | 请求参数错误 | 参数格式或值错误 |
| 401 | 未认证 | 缺少或无效的认证信息 |
| 403 | 权限不足 | 无访问权限 |
| 404 | 资源不存在 | 请求的资源不存在 |
| 409 | 冲突 | 资源已存在或状态冲突 |
| 422 | 验证失败 | 数据验证失败 |
| 429 | 请求过于频繁 | 超过速率限制 |
| 500 | 服务器内部错误 | 服务器异常 |
| 502 | 网关错误 | 上游服务错误 |
| 503 | 服务不可用 | 服务维护或过载 |
| 504 | 网关超时 | 上游服务响应超时 |

---

## API 端点

### 健康检查

#### GET /healthz

健康检查端点，检查各组件连通性。

**响应**:

```json
{
  "status": "ok",
  "service": "dsh-api",
  "version": "0.1.0",
  "checks": {
    "database": "ok",
    "cache": "ok",
    "llm": "ok"
  },
  "timestamp": "2026-03-13T10:00:00Z"
}
```

**状态码**:
- `200`: 服务正常
- `503`: 服务异常

#### GET /readyz

就绪检查端点，检查服务是否就绪可接受请求。

**响应**:

```json
{
  "ready": true,
  "checks": {
    "database": true,
    "cache": true,
    "llm": true
  }
}
```

---

### 认证 API

#### POST /auth/login

用户登录，获取访问令牌。

**请求**:

```json
{
  "username": "admin",
  "password": "password123"
}
```

**响应**:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 900,
  "user": {
    "id": "user123",
    "username": "admin",
    "role": "admin"
  }
}
```

#### POST /auth/refresh

刷新访问令牌。

**请求头**:
```
X-Refresh-Token: <refresh_token>
```

**响应**:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 900
}
```

#### POST /auth/logout

登出，撤销令牌。

**请求**:

```json
{
  "token": "eyJhbGciOiJIUzI1NiIs..."
}
```

#### POST /auth/api-key

创建 API 密钥。

**请求**:

```json
{
  "name": "my-api-key",
  "permissions": ["read", "write"],
  "rate_limit": 100
}
```

**响应**:

```json
{
  "id": "key123",
  "name": "my-api-key",
  "key": "dsh_abc123_xyz789",
  "permissions": ["read", "write"],
  "rate_limit": 100,
  "created_at": "2026-03-13T10:00:00Z"
}
```

> ⚠️ 注意：API 密钥仅返回一次，请妥善保存。

#### GET /auth/api-keys

列出 API 密钥。

**查询参数**:
- `page`: 页码 (默认 1)
- `limit`: 每页数量 (默认 20)

**响应**:

```json
{
  "items": [
    {
      "id": "key123",
      "name": "my-api-key",
      "permissions": ["read", "write"],
      "created_at": "2026-03-13T10:00:00Z"
    }
  ],
  "total": 1,
  "page": 1,
  "limit": 20
}
```

#### DELETE /auth/api-keys/{key_id}

删除 API 密钥。

---

### LLM API

#### POST /llm/chat

发送聊天消息。

**请求**:

```json
{
  "messages": [
    {"role": "system", "content": "你是一个助手"},
    {"role": "user", "content": "你好"}
  ],
  "model": "deepseek",
  "temperature": 0.7,
  "max_tokens": 1024,
  "stream": false
}
```

**参数说明**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `messages` | array | 是 | 消息列表 |
| `messages.role` | string | 是 | 角色: system/user/assistant |
| `messages.content` | string | 是 | 消息内容 |
| `model` | string | 否 | 模型名称 (默认 deepseek) |
| `temperature` | float | 否 | 温度 (0-2, 默认 0.7) |
| `max_tokens` | int | 否 | 最大生成 token 数 |
| `stream` | bool | 否 | 是否流式返回 (默认 false) |

**响应**:

```json
{
  "id": "chat-123",
  "model": "deepseek",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "你好！有什么可以帮助你的？"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 20,
    "total_tokens": 30
  }
}
```

#### POST /llm/chat/stream

流式聊天 (SSE)。

**请求**: 同 `/llm/chat`，`stream: true`

**响应** (Server-Sent Events):

```
data: {"type": "start", "id": "chat-123"}

data: {"type": "content", "delta": "你好"}

data: {"type": "content", "delta": "！"}

data: {"type": "done", "finish_reason": "stop"}
```

#### GET /llm/models

列出可用模型。

**响应**:

```json
{
  "models": [
    {
      "id": "deepseek-chat",
      "provider": "deepseek",
      "context_length": 32768
    },
    {
      "id": "gpt-4o",
      "provider": "openai",
      "context_length": 128000
    },
    {
      "id": "qwen-turbo",
      "provider": "qwen",
      "context_length": 8192
    },
    {
      "id": "claude-3-5-sonnet-20241022",
      "provider": "claude",
      "context_length": 200000
    }
  ]
}
```

---

### 文本 API

#### POST /text/query

文本数据查询。

**请求**:

```json
{
  "table": "users",
  "conditions": {"status": "active"},
  "limit": 100,
  "offset": 0,
  "order_by": "created_at DESC"
}
```

**响应**:

```json
{
  "table": "users",
  "conditions": {"status": "active"},
  "results": [
    {
      "id": 1,
      "name": "Alice",
      "status": "active"
    }
  ],
  "total": 1,
  "limit": 100,
  "offset": 0
}
```

#### POST /text/generate

文本生成。

**请求**:

```json
{
  "prompt": "请写一篇关于AI的文章",
  "model": "deepseek",
  "max_tokens": 1024,
  "temperature": 0.7
}
```

**响应**:

```json
{
  "prompt": "请写一篇关于AI的文章",
  "model": "deepseek",
  "result": "人工智能是...",
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 500
  }
}
```

---

### 多模态 API

#### POST /ocr/extract

OCR 文字识别。

**请求**:

```json
{
  "image_url": "https://example.com/image.png",
  "image_base64": "base64-encoded-data",
  "language": "ch",
  "plugin": "tesseract"
}
```

> 提供 `image_url` 或 `image_base64` 之一

**响应**:

```json
{
  "text": "识别结果...",
  "lines": [
    {
      "text": "第一行",
      "confidence": 0.95,
      "bbox": [0, 0, 100, 20]
    }
  ],
  "language": "ch",
  "plugin": "tesseract"
}
```

#### POST /asr/transcribe

语音转文字。

**请求**:

```json
{
  "audio_url": "https://example.com/audio.mp3",
  "audio_base64": "base64-encoded-data",
  "language": "zh",
  "plugin": "whisper"
}
```

**响应**:

```json
{
  "text": "转写结果...",
  "segments": [
    {
      "start": 0.0,
      "end": 5.0,
      "text": "第一句话"
    }
  ],
  "language": "zh",
  "plugin": "whisper"
}
```

#### POST /video/analyze

视频内容分析。

**请求**:

```json
{
  "video_url": "https://example.com/video.mp4",
  "analysis_type": "summary",
  "model": "deepseek"
}
```

**响应**:

```json
{
  "video_url": "https://example.com/video.mp4",
  "analysis_type": "summary",
  "result": "视频内容摘要...",
  "frames": 100
}
```

---

### RAG API

#### POST /rag/query

RAG 检索增强生成。

**请求**:

```json
{
  "query": "什么是AI中台？",
  "top_k": 5,
  "sources": ["docs", "knowledge_base"],
  "model": "deepseek"
}
```

**响应**:

```json
{
  "query": "什么是AI中台？",
  "answer": "AI中台是...",
  "sources": [
    {
      "id": "doc1",
      "title": "AI中台白皮书",
      "content": "...",
      "score": 0.95
    }
  ],
  "model": "deepseek"
}
```

#### POST /rag/ingest

向知识库添加文档。

**请求**:

```json
{
  "collection": "docs",
  "documents": [
    {
      "id": "doc1",
      "title": "文档标题",
      "content": "文档内容..."
    }
  ]
}
```

**响应**:

```json
{
  "collection": "docs",
  "ingested": 1,
  "failed": 0
}
```

---

### 会话 API

#### POST /sessions

创建会话。

**请求**:

```json
{
  "title": "我的会话",
  "metadata": {
    "category": "general"
  }
}
```

**响应**:

```json
{
  "id": "abc12345",
  "title": "我的会话",
  "metadata": {
    "category": "general"
  },
  "created_at": "2026-03-13T10:00:00Z",
  "updated_at": "2026-03-13T10:00:00Z"
}
```

#### GET /sessions

列出会话。

**查询参数**:
- `user_id`: 用户 ID
- `limit`: 返回条数 (默认 20)
- `offset`: 偏移量 (默认 0)

**响应**:

```json
{
  "items": [
    {
      "id": "abc12345",
      "title": "我的会话",
      "created_at": "2026-03-13T10:00:00Z"
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

#### GET /sessions/{session_id}

获取会话详情。

**响应**:

```json
{
  "id": "abc12345",
  "title": "我的会话",
  "messages": [],
  "created_at": "2026-03-13T10:00:00Z"
}
```

#### DELETE /sessions/{session_id}

删除会话。

---

### 消息 API

#### POST /sessions/{session_id}/messages

发送消息。

**请求**:

```json
{
  "role": "user",
  "content": "你好",
  "metadata": {}
}
```

**响应**:

```json
{
  "id": "msg123",
  "session_id": "abc12345",
  "role": "user",
  "content": "你好",
  "metadata": {},
  "created_at": "2026-03-13T10:00:00Z"
}
```

#### GET /sessions/{session_id}/messages

列出消息。

**查询参数**:
- `limit`: 返回条数 (默认 50)
- `offset`: 偏移量 (默认 0)

**响应**:

```json
{
  "items": [
    {
      "id": "msg123",
      "role": "user",
      "content": "你好",
      "created_at": "2026-03-13T10:00:00Z"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

---

### 审批 API

#### GET /approvals/pending

待审批任务列表。

**查询参数**:
- `tenant_code`: 租户代码
- `limit`: 返回条数 (默认 20)
- `offset`: 偏移量 (默认 0)

**响应**:

```json
{
  "items": [
    {
      "id": "task123",
      "type": "data_access",
      "requester": "user1",
      "reason": "需要访问敏感数据",
      "created_at": "2026-03-13T10:00:00Z"
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

#### POST /approvals/{task_id}/decision

提交审批决策。

**请求**:

```json
{
  "decision": "approve",
  "approver": "user1",
  "reason": "同意"
}
```

**响应**:

```json
{
  "task_id": "task123",
  "decision": "approve",
  "decided_by": "user1",
  "reason": "同意",
  "decided_at": "2026-03-13T10:00:00Z"
}
```

#### POST /approvals/sweep-timeouts

审批超时扫描 (管理员)。

**响应**:

```json
{
  "scanned": 10,
  "timed_out": 2,
  "auto_approved": 0
}
```

---

### 插件 API

#### GET /plugins

列出已安装插件。

**响应**:

```json
{
  "items": [
    {
      "id": "ocr",
      "name": "OCR 识别",
      "version": "1.0.0",
      "enabled": true
    }
  ]
}
```

#### POST /plugins/{plugin_id}/install

安装插件。

**请求**:

```json
{
  "version": "1.0.0",
  "source": "marketplace"
}
```

#### POST /plugins/{plugin_id}/enable

启用插件。

#### POST /plugins/{plugin_id}/disable

禁用插件。

#### DELETE /plugins/{plugin_id}

卸载插件。

---

### 监控指标

#### GET /metrics

Prometheus 格式指标暴露。

**响应** (文本格式):

```
# HELP dsh_requests_total Total number of requests
# TYPE dsh_requests_total counter
dsh_requests_total{method="GET",path="/healthz",status="200"} 100

# HELP dsh_request_duration_seconds Request duration
# TYPE dsh_request_duration_seconds histogram
dsh_request_duration_seconds_bucket{le="0.1"} 50
```

#### GET /stats

服务统计信息。

**响应**:

```json
{
  "uptime": 3600,
  "requests_total": 1000,
  "requests_active": 5,
  "llm_calls": 200,
  "cache_hits": 150,
  "cache_misses": 50
}
```

---

### 工作流 API

#### POST /workflows/execute

执行工作流。

**请求**:

```json
{
  "workflow": {
    "nodes": [
      {
        "id": "node1",
        "type": "text_generate",
        "config": {
          "prompt": "写一首诗"
        }
      }
    ],
    "edges": []
  },
  "input": {}
}
```

**响应**:

```json
{
  "id": "exec123",
  "status": "completed",
  "results": {
    "node1": {
      "output": "生成的诗歌..."
    }
  },
  "started_at": "2026-03-13T10:00:00Z",
  "completed_at": "2026-03-13T10:00:05Z"
}
```

#### GET /workflows/executions/{execution_id}

获取工作流执行结果。

---

### 探索引擎 API

#### POST /exploration/explore

执行探索任务。

**请求**:

```json
{
  "task": "优化数据库查询性能",
  "context": {
    "database": "PostgreSQL",
    "scale": "1000万条记录"
  },
  "options": {
    "max_solutions": 5,
    "max_experiments": 3
  }
}
```

**响应**:

```json
{
  "id": "exp123",
  "task": "优化数据库查询性能",
  "status": "completed",
  "solutions": [
    {
      "id": "sol1",
      "title": "添加索引",
      "description": "为常用查询字段添加索引",
      "confidence": 0.9
    }
  ],
  "experiments": [
    {
      "id": "exp1",
      "solution_id": "sol1",
      "status": "pending"
    }
  ],
  "created_at": "2026-03-13T10:00:00Z"
}
```

#### GET /exploration/executions/{execution_id}

获取探索执行结果。

---

## 权限矩阵

| 端点 | 权限 | 说明 |
|------|------|------|
| `/healthz` | 公开 | 健康检查 |
| `/auth/login` | 公开 | 用户登录 |
| `/auth/refresh` | 公开 | 刷新令牌 |
| `/llm/*` | `llm:read` | LLM 调用 |
| `/text/*` | `text:read` | 文本查询 |
| `/ocr/*` | `ocr:read` | OCR 识别 |
| `/asr/*` | `asr:read` | 语音转文字 |
| `/rag/*` | `rag:read` | RAG 检索 |
| `/sessions/*` | `session:read` | 会话管理 |
| `/approvals/*` | `approval:read` | 审批管理 |
| `/plugins/*` | `plugin:admin` | 插件管理 |
| `/workflows/*` | `workflow:execute` | 工作流执行 |
| `/exploration/*` | `exploration:execute` | 探索执行 |

---

## 速率限制

| 端点 | 限制 | 说明 |
|------|------|------|
| `/auth/login` | 10/min | 登录尝试 |
| `/llm/chat` | 60/min | LLM 调用 |
| `/text/query` | 100/min | 文本查询 |
| 其他 | 1000/min | 默认限制 |

---

## 变更日志

### v0.1.0 (2026-03-13)

- 初始版本发布
- 支持 LLM 多供应商 (DeepSeek/OpenAI/Qwen/Claude)
- 支持多模态 (OCR/ASR/视频分析)
- 支持 RAG 检索增强生成
- 支持会话管理
- 支持审批工作流
- 支持插件系统
- 支持探索引擎
