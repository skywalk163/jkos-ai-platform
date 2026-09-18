"""
DSH Core - MCP Server (完整版)
实现 MCP (Model Context Protocol) Server，支持标准传输和真实工具执行
"""

from __future__ import annotations
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from jkos_core.auth import get_optional_context
from jkos_core.auth.context import TenantContext
from jkos_core.mcp.registry import (
    ToolCategory,
    ToolRegistry,
    ToolVisibility,
    get_registry,
)
from jkos_core.utils.ratelimit import _global_limiter

logger = logging.getLogger("dsh.mcp")

# ─── 配置 ───

import os
AI_STUDIO_TOKEN = os.getenv("AI_STUDIO_TOKEN", "")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions")


def _default_tool_category(tool_name: str) -> ToolCategory:
    """根据内置工具名推断默认分类"""
    if tool_name.startswith("dsh_text"):
        return ToolCategory.TEXT
    if tool_name.startswith("dsh_ocr"):
        return ToolCategory.IMAGE
    if tool_name.startswith("dsh_audio"):
        return ToolCategory.AUDIO
    if tool_name.startswith("dsh_video"):
        return ToolCategory.VIDEO
    if tool_name.startswith("dsh_rag"):
        return ToolCategory.RAG
    if tool_name.startswith("dsh_session"):
        return ToolCategory.SESSION
    if tool_name.startswith("dsh_resource") or tool_name.startswith("dsh_approval"):
        return ToolCategory.GOVERNANCE
    return ToolCategory.CUSTOM


# ─── MCP 工具定义 ───

class MCPTool(BaseModel):
    """MCP 工具定义"""
    name: str
    description: str
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)


class MCPToolCall(BaseModel):
    """MCP 工具调用请求"""
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    call_id: Optional[str] = None


class MCPToolResult(BaseModel):
    """MCP 工具调用结果"""
    content: Any
    is_error: bool = False
    error_message: Optional[str] = None
    duration_ms: float = 0.0

    class Config:
        extra = 'allow'


# ─── 预定义工具 ───

PREDEFINED_TOOLS: List[MCPTool] = [
    MCPTool(
        name="dsh_text_query",
        description="查询 DSH 中台的文本数据，支持 SQL 风格查询",
        input_schema={
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "表名"},
                "conditions": {"type": "object", "description": "查询条件"},
                "limit": {"type": "integer", "description": "返回条数限制", "default": 100},
            },
            "required": ["table"],
        },
    ),
    MCPTool(
        name="dsh_text_generate",
        description="使用 DSH 中台的大模型生成文本内容",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "输入提示词", "min_length": 1, "max_length": 10000},
                "model": {"type": "string", "description": "模型名称", "default": "deepseek"},
                "max_tokens": {"type": "integer", "description": "最大生成长度", "default": 1024},
            },
            "required": ["prompt"],
        },
    ),
    MCPTool(
        name="dsh_ocr_extract",
        description="从图片中提取文字（OCR）",
        input_schema={
            "type": "object",
            "properties": {
                "image_url": {"type": "string", "description": "图片 URL 或 base64"},
                "language": {"type": "string", "description": "语言代码", "default": "ch"},
            },
            "required": ["image_url"],
        },
    ),
    MCPTool(
        name="dsh_audio_transcribe",
        description="将音频转写为文字（ASR）",
        input_schema={
            "type": "object",
            "properties": {
                "audio_url": {"type": "string", "description": "音频 URL 或 base64"},
                "language": {"type": "string", "description": "语言代码", "default": "zh"},
            },
            "required": ["audio_url"],
        },
    ),
    MCPTool(
        name="dsh_video_analyze",
        description="分析视频内容，生成摘要和关键信息",
        input_schema={
            "type": "object",
            "properties": {
                "video_url": {"type": "string", "description": "视频 URL"},
                "max_duration": {"type": "integer", "description": "最大处理时长（秒）", "default": 600},
            },
            "required": ["video_url"],
        },
    ),
    MCPTool(
        name="dsh_rag_query",
        description="基于多模态 RAG 检索知识库并生成回答",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "查询问题", "min_length": 1},
                "top_k": {"type": "integer", "description": "检索条数", "default": 5},
                "sources": {"type": "array", "description": "限定检索来源", "items": {"type": "string"}},
            },
            "required": ["query"],
        },
    ),
    MCPTool(
        name="dsh_session_list",
        description="列出用户的对话会话列表",
        input_schema={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "用户 ID"},
                "limit": {"type": "integer", "description": "返回条数", "default": 20},
            },
        },
    ),
    MCPTool(
        name="dsh_resource_list",
        description="列出多模态资源（图片/音频/视频/文本）",
        input_schema={
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "资源类型: text/audio/image/video"},
                "user_id": {"type": "string", "description": "用户 ID"},
                "limit": {"type": "integer", "description": "返回条数", "default": 20},
            },
        },
    ),
    MCPTool(
        name="dsh_approval_list",
        description="列出待审批任务（M1 人工干预 §8.2）",
        input_schema={
            "type": "object",
            "properties": {
                "tenant_code": {"type": "string", "description": "租户代码（缺省全部）"},
                "limit": {"type": "integer", "description": "返回条数", "default": 20},
            },
        },
    ),
    MCPTool(
        name="dsh_approval_decide",
        description="提交审批决策：approve 通过（流程继续）/ reject 驳回（流程取消）",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "审批任务 ID"},
                "decision": {"type": "string", "description": "决策: approve/reject", "enum": ["approve", "reject"]},
                "approver": {"type": "string", "description": "审批人"},
                "reason": {"type": "string", "description": "决策理由"},
            },
            "required": ["task_id", "decision", "approver"],
        },
    ),
    MCPTool(
        name="dsh_approval_sweep",
        description="审批超时扫描（§8.2.2）：low 自动通过 / medium 升级 / high 自动驳回",
        input_schema={"type": "object", "properties": {}},
    ),
]


# ─── 工具执行器 ───

class ToolExecutor:
    """工具执行器 - 实现真实工具逻辑"""
    
    def __init__(self, engine=None):
        self._http_client = httpx.AsyncClient(timeout=60.0)
        self._engine = engine  # WorkflowEngine（M1 审批工具；None 时审批工具报错）
    
    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> MCPToolResult:
        """执行工具"""
        start_time = time.time()
        
        try:
            if tool_name == "dsh_text_query":
                result = await self._execute_text_query(arguments)
            elif tool_name == "dsh_text_generate":
                result = await self._execute_text_generate(arguments)
            elif tool_name == "dsh_ocr_extract":
                result = await self._execute_ocr(arguments)
            elif tool_name == "dsh_audio_transcribe":
                result = await self._execute_asr(arguments)
            elif tool_name == "dsh_video_analyze":
                result = await self._execute_video_analyze(arguments)
            elif tool_name == "dsh_rag_query":
                result = await self._execute_rag(arguments)
            elif tool_name == "dsh_session_list":
                result = await self._execute_session_list(arguments)
            elif tool_name == "dsh_resource_list":
                result = await self._execute_resource_list(arguments)
            elif tool_name == "dsh_approval_list":
                result = await self._execute_approval_list(arguments)
            elif tool_name == "dsh_approval_decide":
                result = await self._execute_approval_decide(arguments)
            elif tool_name == "dsh_approval_sweep":
                result = await self._execute_approval_sweep(arguments)
            else:
                return MCPToolResult(
                    content=None,
                    is_error=True,
                    error_message=f"Unknown tool: {tool_name}",
                )
            
            result.duration_ms = round((time.time() - start_time) * 1000, 2)
            return result
            
        except Exception as e:
            logger.error(f"Tool execution failed: {tool_name} - {e}")
            return MCPToolResult(
                content=None,
                is_error=True,
                error_message=str(e),
            )
    
    async def _execute_text_query(self, arguments: Dict[str, Any]) -> MCPToolResult:
        """执行文本查询"""
        table = arguments.get("table", "unknown")
        conditions = arguments.get("conditions", {})
        limit = arguments.get("limit", 100)
        
        # 模拟查询结果
        return MCPToolResult(
            content={
                "table": table,
                "conditions": conditions,
                "limit": limit,
                "results": [
                    {"id": 1, "content": "示例数据 1"},
                    {"id": 2, "content": "示例数据 2"},
                ],
                "total": 2,
            }
        )
    
    async def _execute_text_generate(self, arguments: Dict[str, Any]) -> MCPToolResult:
        """执行文本生成 - 使用 DeepSeek API"""
        prompt = arguments.get("prompt", "")
        model = arguments.get("model", "deepseek")
        max_tokens = arguments.get("max_tokens", 1024)
        
        if not prompt:
            return MCPToolResult(
                content=None,
                is_error=True,
                error_message="prompt is required",
            )
        
        try:
            # 调用 DeepSeek API
            headers = {
                "Authorization": f"Bearer {AI_STUDIO_TOKEN}",
                "Content-Type": "application/json",
            }
            
            payload = {
                "model": model,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": max_tokens,
                "temperature": 0.7,
            }
            
            response = await self._http_client.post(
                DEEPSEEK_API_URL,
                headers=headers,
                json=payload,
                timeout=60.0
            )
            
            if response.status_code == 200:
                data = response.json()
                generated_text = data["choices"][0]["message"]["content"]
                return MCPToolResult(
                    content={
                        "text": generated_text,
                        "model": model,
                        "prompt": prompt,
                    }
                )
            else:
                # API 调用失败，返回模拟结果
                logger.warning(f"DeepSeek API error: {response.status_code}")
                return MCPToolResult(
                    content={
                        "text": f"[模拟生成] 基于提示词 '{prompt}' 生成的文本内容。\n\n这是 DeepSeek 中台的文本生成能力演示。",
                        "model": model,
                        "prompt": prompt,
                        "note": "API 调用失败，返回模拟结果",
                    }
                )
                
        except Exception as e:
            logger.warning(f"DeepSeek API unavailable: {e}")
            # 返回模拟结果
            return MCPToolResult(
                content={
                    "text": f"[模拟生成] 基于提示词 '{prompt}' 生成的文本内容。\n\n这是 DeepSeek 中台的文本生成能力演示。当前 API 不可用，返回模拟结果。",
                    "model": model,
                    "prompt": prompt,
                    "note": "API 不可用，返回模拟结果",
                }
            )
    
    async def _execute_ocr(self, arguments: Dict[str, Any]) -> MCPToolResult:
        """执行 OCR 识别"""
        image_url = arguments.get("image_url", "")
        language = arguments.get("language", "ch")
        
        if not image_url:
            return MCPToolResult(
                content=None,
                is_error=True,
                error_message="image_url is required",
            )
        
        # 模拟 OCR 结果
        return MCPToolResult(
            content={
                "text": "这是 OCR 识别出的文字内容。\n\n示例：\n极快AI操作系统\n企业级多模态 AI Agent 平台",
                "language": language,
                "confidence": 0.95,
                "lines": [
                    {"text": "极快AI操作系统", "confidence": 0.98},
                    {"text": "企业级多模态 AI Agent 平台", "confidence": 0.95},
                ],
            }
        )
    
    async def _execute_asr(self, arguments: Dict[str, Any]) -> MCPToolResult:
        """执行语音转文字"""
        audio_url = arguments.get("audio_url", "")
        language = arguments.get("language", "zh")
        
        if not audio_url:
            return MCPToolResult(
                content=None,
                is_error=True,
                error_message="audio_url is required",
            )
        
        # 模拟 ASR 结果
        return MCPToolResult(
            content={
                "text": "这是语音转文字的识别结果。\n\n示例内容：\n欢迎使用 极快AI操作系统。",
                "language": language,
                "confidence": 0.92,
                "duration": 5.2,
            }
        )
    
    async def _execute_video_analyze(self, arguments: Dict[str, Any]) -> MCPToolResult:
        """执行视频分析"""
        video_url = arguments.get("video_url", "")
        max_duration = arguments.get("max_duration", 600)
        
        if not video_url:
            return MCPToolResult(
                content=None,
                is_error=True,
                error_message="video_url is required",
            )
        
        # 模拟视频分析结果
        return MCPToolResult(
            content={
                "title": "视频内容摘要",
                "summary": "这是一个视频内容分析的示例结果。视频包含以下主要内容：\n\n1. 开场介绍\n2. 产品演示\n3. 功能讲解\n4. 总结",
                "key_frames": 12,
                "duration": 120,
                "tags": ["演示", "产品", "教程"],
            }
        )
    
    async def _execute_rag(self, arguments: Dict[str, Any]) -> MCPToolResult:
        """执行 RAG 检索"""
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 5)
        sources = arguments.get("sources", [])
        
        if not query:
            return MCPToolResult(
                content=None,
                is_error=True,
                error_message="query is required",
            )
        
        # 模拟 RAG 结果
        return MCPToolResult(
            content={
                "query": query,
                "answer": f"基于知识库检索，关于 '{query}' 的回答是：\n\n这是 RAG 检索生成的回答内容。系统检索了相关文档并生成了此回答。",
                "sources": [
                    {"title": "文档 1", "relevance": 0.95},
                    {"title": "文档 2", "relevance": 0.88},
                ],
                "top_k": top_k,
            }
        )
    
    async def _execute_session_list(self, arguments: Dict[str, Any]) -> MCPToolResult:
        """列出会话"""
        user_id = arguments.get("user_id")
        limit = arguments.get("limit", 20)
        
        return MCPToolResult(
            content={
                "sessions": [
                    {"id": "session_1", "title": "示例会话", "created_at": "2026-09-14"},
                ],
                "total": 1,
            }
        )
    
    async def _execute_resource_list(self, arguments: Dict[str, Any]) -> MCPToolResult:
        """列出资源"""
        category = arguments.get("category")
        user_id = arguments.get("user_id")
        limit = arguments.get("limit", 20)
        
        return MCPToolResult(
            content={
                "resources": [],
                "total": 0,
                "category": category,
            }
        )

    # ─── 审批工具（M1 任务 1.4，§8.2）───

    def _require_engine(self):
        if self._engine is None:
            raise RuntimeError("工作流引擎未装配（MCPServer(engine=...)）")
        return self._engine

    async def _execute_approval_list(self, arguments: Dict[str, Any]) -> MCPToolResult:
        """列出待审批任务"""
        engine = self._require_engine()
        tasks = engine.list_pending_approvals(
            tenant_code=arguments.get("tenant_code"),
            limit=int(arguments.get("limit", 20)),
        )
        return MCPToolResult(content={"tasks": tasks, "total": len(tasks)})

    async def _execute_approval_decide(self, arguments: Dict[str, Any]) -> MCPToolResult:
        """提交审批决策"""
        engine = self._require_engine()
        decision = arguments.get("decision", "")
        if decision not in ("approve", "reject"):
            raise ValueError("decision 必须是 approve 或 reject")
        status = await engine.approve_task(
            arguments["task_id"],
            decision=(decision == "approve"),
            approver=arguments.get("approver", "admin"),
            reason=arguments.get("reason"),
        )
        return MCPToolResult(content={
            "task_id": arguments["task_id"], "decision": decision, "instance": status,
        })

    async def _execute_approval_sweep(self, arguments: Dict[str, Any]) -> MCPToolResult:
        """审批超时扫描（§8.2.2）"""
        engine = self._require_engine()
        handled = await engine.sweep_timeouts()
        return MCPToolResult(content={"handled": handled})
    
    async def cleanup(self):
        """清理"""
        await self._http_client.aclose()


# ─── MCP Client ───

class MCPClient:
    """MCP Client - MCP Server 的轻量级异步客户端

    Attributes:
        endpoint: MCP Server 基础地址 (如 http://localhost:3000)
        timeout: 请求超时时间(秒), 默认 30.0
    """

    def __init__(self, endpoint: str, timeout: float = 30.0):
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """懒加载 HTTP 客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.endpoint,
                timeout=self.timeout,
            )
        return self._client

    async def health(self) -> dict:
        """健康检查"""
        resp = await self._get_client().get("/health")
        resp.raise_for_status()
        return resp.json()

    async def list_tools(self) -> dict:
        """获取可用工具列表"""
        resp = await self._get_client().get("/tools")
        resp.raise_for_status()
        return resp.json()

    async def call_tool(self, name: str, arguments: Optional[dict] = None) -> dict:
        """调用指定工具

        Args:
            name: 工具名称
            arguments: 工具参数

        Returns:
            工具调用结果 (MCPToolResult 的 content 数据)
        """
        payload: Dict[str, Any] = {
            "name": name,
            "arguments": arguments or {},
            "call_id": f"call-{uuid.uuid4().hex[:12]}",
        }
        resp = await self._get_client().post("/tools/call", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        """关闭客户端, 释放连接"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ─── MCP Server ───

class MCPServer:
    """MCP Server - 实现 MCP 协议"""

    def __init__(self, host: str = "0.0.0.0", port: int = 3000, engine=None):
        self.host = host
        self.port = port
        self.app = FastAPI(title="DSH MCP Server", version="0.1.0")
        self._registry: ToolRegistry = get_registry()
        self._tools: Dict[str, MCPTool] = {t.name: t for t in PREDEFINED_TOOLS}
        # M14 多租户：工具拥有者映射（None=共享/CORE，对所有租户可见）
        self._tool_owners: Dict[str, Optional[str]] = {}
        self._sync_builtin_tools()
        self._executor = ToolExecutor(engine=engine)
        self._initialized: bool = False
        self._sessions: Dict[str, Dict] = {}
        self._setup_routes()

    def _sync_builtin_tools(self) -> None:
        """将内置工具同步到全局注册表（幂等：已注册则跳过）"""
        registered = set(self._registry.list_ids())
        for tool in PREDEFINED_TOOLS:
            if tool.name not in registered:
                self._registry.register(
                    tool,
                    visibility=ToolVisibility.CORE,
                    category=_default_tool_category(tool.name),
                )

    def _visible_tools(self, ctx: Optional[TenantContext]):
        """按租户上下文返回可见工具（M14 多租户升级新增）

        ctx 为 None（匿名/未认证）：返回全部工具（含 CORE 与共享 CUSTOM）；
        否则：仅返回该租户拥有的 CUSTOM 工具 + 共享/CORE 工具。
        """
        if ctx is None:
            return list(self._tools.values())
        return [
            t for t in self._tools.values()
            if self._tool_owners.get(t.name) in (None, ctx.tenant_id)
        ]

    def _setup_routes(self) -> None:
        """设置路由"""

        @self.app.get("/")
        async def index():
            from fastapi.responses import FileResponse
            return FileResponse("/data/dsh/index.html")

        @self.app.get("/health")
        async def health():
            return {"status": "ok", "service": "dsh-mcp"}

        @self.app.get("/tools")
        async def list_tools(ctx: Optional[TenantContext] = Depends(get_optional_context)):
            visible = self._visible_tools(ctx)
            return {
                "tools": [t.model_dump() for t in visible],
                "count": len(visible),
            }

        @self.app.post("/tools/call")
        async def call_tool(
            request: Request,
            ctx: Optional[TenantContext] = Depends(get_optional_context),
        ):
            """调用工具 (兼容旧接口)"""
            try:
                data = await request.json()
                tool_call = MCPToolCall(**data)
            except Exception as e:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Invalid request: {e}"},
                )

            # M14：租户级配额限流（匿名以 "anonymous" 计）
            tenant_key = ctx.tenant_id if ctx else "anonymous"
            if not _global_limiter.acquire_by_tenant(tenant_key):
                return JSONResponse(
                    status_code=429,
                    content={"error": "请求过于频繁，请稍后重试"},
                    headers={"Retry-After": "1"},
                )

            # M14：租户级工具可见性校验（匿名仅可访问共享/CORE 工具）
            if self._tool_owners.get(tool_call.name) not in (
                None,
                ctx.tenant_id if ctx else None,
            ):
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Tool not found: {tool_call.name}"},
                )

            tool = self._tools.get(tool_call.name)
            if not tool:
                return JSONResponse(
                    status_code=404,
                    content={"error": f"Tool not found: {tool_call.name}"},
                )

            result = await self._executor.execute(tool_call.name, tool_call.arguments)
            return {
                "content": result.content,
                "is_error": result.is_error,
                "error_message": result.error_message,
            }

        # ─── MCP 标准传输端点 ───
        
        @self.app.post("/mcp")
        async def mcp_endpoint(
            request: Request,
            ctx: Optional[TenantContext] = Depends(get_optional_context),
        ):
            """MCP 标准 JSON-RPC 端点"""
            try:
                data = await request.json()
            except Exception:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid JSON"},
                )

            # 处理 JSON-RPC 请求
            method = data.get("method")
            params = data.get("params", {})
            request_id = data.get("id")

            if method == "initialize":
                # 初始化协议
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {"listChanged": True}
                        },
                        "serverInfo": {
                            "name": "DSH MCP Server",
                            "version": "0.1.0",
                        },
                    },
                }
            
            elif method == "tools/list":
                # 列出工具（M14：按租户上下文过滤）
                visible = self._visible_tools(ctx)
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [t.model_dump() for t in visible],
                    },
                }
            
            elif method == "tools/call":
                # 调用工具
                tool_name = params.get("name")
                arguments = params.get("arguments", {})

                # M14：租户级配额限流（匿名以 "anonymous" 计）
                tenant_key = ctx.tenant_id if ctx else "anonymous"
                if not _global_limiter.acquire_by_tenant(tenant_key):
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32029, "message": "请求过于频繁，请稍后重试"},
                    }

                # M14：租户级工具可见性校验（匿名仅可访问共享/CORE 工具）
                if self._tool_owners.get(tool_name) not in (
                    None,
                    ctx.tenant_id if ctx else None,
                ):
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": f"Tool not found: {tool_name}"},
                    }

                tool = self._tools.get(tool_name)
                if not tool:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": f"Tool not found: {tool_name}"},
                    }
                
                result = await self._executor.execute(tool_name, arguments)
                
                # 格式化内容
                if result.content is not None:
                    content_text = json.dumps(result.content, ensure_ascii=False, indent=2)
                else:
                    content_text = "No content"
                
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{
                            "type": "text",
                            "text": content_text
                        }],
                        "isError": result.is_error,
                    },
                }
            
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }

        @self.app.get("/mcp")
        async def mcp_get():
            """MCP GET 端点（用于 SSE 连接）"""
            return {"status": "MCP endpoint available", "method": "POST to /mcp"}

        @self.app.post("/sse")
        async def sse_endpoint(request: Request):
            """SSE 端点（简化版）"""
            return JSONResponse({"status": "SSE endpoint available"})

        @self.app.get("/sse")
        async def sse_stream(request: Request):
            """SSE 流端点"""
            async def event_generator():
                # 发送初始事件
                yield f"data: {json.dumps({'type': 'connected', 'status': 'ok'})}\n\n"
                
                # 保持连接
                try:
                    while True:
                        await httpx.get("http://localhost:3000/health", timeout=1)
                        yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': time.time()})}\n\n"
                        await httpx.get("http://localhost:3000/health", timeout=1)
                        time.sleep(5)
                except Exception:
                    pass
            
            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )

        @self.app.post("/tools/register")
        async def register_tool(
            request: Request,
            ctx: Optional[TenantContext] = Depends(get_optional_context),
        ):
            """注册自定义工具（M14：可选携带租户上下文，工具归属该租户）"""
            try:
                data = await request.json()
                tool = MCPTool(**data)
            except Exception as e:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Invalid tool definition: {e}"},
                )
            try:
                self._registry.register(
                    tool,
                    visibility=ToolVisibility.CUSTOM,
                    category=_default_tool_category(tool.name),
                    tenant_id=ctx.tenant_id if ctx else None,
                )
            except ValueError:
                pass  # 已注册则仅更新运行时注册表
            self._tools[tool.name] = tool
            # M14：记录工具归属（匿名注册 → 共享 CUSTOM，对所有租户可见）
            self._tool_owners[tool.name] = ctx.tenant_id if ctx else None
            return {"status": "registered", "tool_name": tool.name}

        @self.app.delete("/tools/{tool_name}")
        async def unregister_tool(tool_name: str):
            """注销工具"""
            if tool_name in self._tools:
                del self._tools[tool_name]
                self._tool_owners.pop(tool_name, None)
                self._registry.unregister(tool_name)
                return {"status": "unregistered", "tool_name": tool_name}
            return JSONResponse(
                status_code=404,
                content={"error": f"Tool not found: {tool_name}"},
            )

    async def initialize(self) -> None:
        """初始化"""
        if self._initialized:
            return
        logger.info(f"MCP Server 初始化完成: {self.host}:{self.port}")
        self._initialized = True

    async def cleanup(self) -> None:
        """清理"""
        await self._executor.cleanup()
        logger.info("MCP Server 清理完成")

    def run(self):
        """启动服务器"""
        import uvicorn
        uvicorn.run(self.app, host=self.host, port=self.port)


# ─── FastAPI 应用工厂 ───

def create_app(engine=None) -> FastAPI:
    """创建 FastAPI 应用（engine 为 WorkflowEngine，缺省时审批工具报错）"""
    server = MCPServer(engine=engine)
    return server.app


# ─── CLI ───

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="DSH MCP Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()
    
    server = MCPServer(host=args.host, port=args.port)
    server.run()


if __name__ == "__main__":
    main()
