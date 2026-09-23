"""DSH API 路由"""

from __future__ import annotations
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from jkos_core.auth.dependencies import get_optional_context, get_tenant_context
from jkos_core.db import (
    RESOURCE_ACTIVE,
    RESOURCE_DISABLED,
    RESOURCE_KINDS,
    DatabaseConnector,
    ResourceRepo,
)
from jkos_core.workflow.engine import WorkflowEngine, WorkflowError

logger = logging.getLogger("dsh.api")

# ─── Pydantic 模型 ───

class ApprovalDecisionRequest(BaseModel):
    """审批决策请求（M1 任务 1.4）"""
    decision: str = Field(..., description="决策: approve/reject")
    approver: Optional[str] = Field(None, description="审批人（缺省取当前登录用户）")
    reason: Optional[str] = Field(None, description="决策理由")

class TextQueryRequest(BaseModel):
    """文本查询请求"""
    table: str = Field(..., description="表名")
    conditions: Dict[str, Any] = Field(default_factory=dict, description="查询条件")
    limit: int = Field(100, ge=1, le=1000, description="返回条数限制")


class TextGenerateRequest(BaseModel):
    """文本生成请求"""
    prompt: str = Field(..., description="输入提示词", min_length=1, max_length=10000)
    model: str = Field("deepseek", description="模型名称")
    max_tokens: int = Field(1024, ge=1, description="最大生成长度")


class OCRRequest(BaseModel):
    """OCR 请求"""
    image_url: str = Field(..., description="图片 URL 或 base64")
    language: str = Field("ch", description="语言代码")


class ASRRequest(BaseModel):
    """ASR 请求"""
    audio_url: str = Field(..., description="音频 URL 或 base64")
    language: str = Field("zh", description="语言代码")


class RAGQueryRequest(BaseModel):
    """RAG 查询请求"""
    query: str = Field(..., description="查询问题", min_length=1)
    top_k: int = Field(5, ge=1, le=50, description="检索条数")
    sources: List[str] = Field(default_factory=list, description="限定检索来源")


class SessionCreateRequest(BaseModel):
    """创建会话请求"""
    title: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MessageSendRequest(BaseModel):
    """发送消息请求"""
    role: str = Field(..., description="消息角色: user/assistant")
    content: str = Field(..., description="消息内容")


class ResourceCreateRequest(BaseModel):
    """资源配置创建（V3 §8.3.4）"""
    name: str = Field(..., description="资源名称（租户内唯一）")
    kind: str = Field(..., description="资源类型")
    config: Dict[str, Any] = Field(default_factory=dict, description="连接参数")


class ResourceUpdateRequest(BaseModel):
    """资源配置更新（V3 §8.3.4）"""
    config: Optional[Dict[str, Any]] = Field(None, description="连接参数")
    status: Optional[str] = Field(None, description="状态 active/disabled")


# ─── API 路由 ───

def _engine_provider_of(engine: Any, explicit: Any) -> Any:
    """解析「按租户取引擎」的提供者（M18.2 运行时路由）

    显式参数优先；否则从 `engine.comps`（真实 AppComponents）取 `engine_for`。
    仅接受 AppComponents 实例，避免测试中的 Mock/SimpleNamespace 被误判。
    """
    if explicit is not None:
        return explicit
    if engine is None:
        return None
    from jkos_core.bootstrap import AppComponents

    comps = getattr(engine, "comps", None)
    return comps.engine_for if isinstance(comps, AppComponents) else None


def create_api_router(engine: Optional[WorkflowEngine] = None, *,
                      engine_provider: Any = None) -> APIRouter:
    """创建 API 路由（engine 缺省时审批路由返回 503）

    engine_provider：可选的 `f(tenant_code) -> Optional[WorkflowEngine]`；
    返回引擎即按租户路由（L2 物理隔离），返回 None 则回落单引擎。
    """
    router = APIRouter(prefix="/api/v1", tags=["API"])

    # ─── 健康检查 ───
    @router.get("/health")
    async def health():
        return {"status": "ok", "service": "dsh-api", "version": "0.1.0"}

    # ─── 文本查询 ───
    @router.post("/text/query")
    async def text_query(req: TextQueryRequest):
        """文本数据查询"""
        # TODO: 实现实际查询
        return {"table": req.table, "conditions": req.conditions, "results": []}

    # ─── 文本生成 ───
    @router.post("/text/generate")
    async def text_generate(req: TextGenerateRequest):
        """文本生成"""
        # TODO: 实现实际生成
        return {"prompt": req.prompt, "model": req.model, "result": "[生成中...]"}

    # ─── OCR ───
    @router.post("/ocr/extract")
    async def ocr_extract(req: OCRRequest):
        """OCR 文字识别"""
        # TODO: 实现实际 OCR
        return {"text": "[OCR 识别结果...]", "lines": [], "language": req.language}

    # ─── ASR ───
    @router.post("/asr/transcribe")
    async def asr_transcribe(req: ASRRequest):
        """语音转文字"""
        # TODO: 实现实际 ASR
        return {"text": "[转写结果...]", "language": req.language}

    # ─── RAG ───
    @router.post("/rag/query")
    async def rag_query(req: RAGQueryRequest):
        """RAG 检索"""
        # TODO: 实现实际 RAG
        return {"query": req.query, "answer": "[RAG 回答...]", "sources": []}

    # ─── 会话管理 ───
    @router.post("/sessions")
    async def create_session(req: SessionCreateRequest):
        """创建会话"""
        session_id = uuid.uuid4().hex[:16]
        return {"id": session_id, "title": req.title, "created_at": datetime.now().isoformat()}

    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str):
        """获取会话"""
        return {"id": session_id, "title": "", "messages": []}

    @router.get("/sessions")
    async def list_sessions(user_id: Optional[str] = None, limit: int = 20):
        """列出会话"""
        return {"sessions": [], "total": 0}

    # ─── 消息 ───
    @router.post("/sessions/{session_id}/messages")
    async def send_message(session_id: str, req: MessageSendRequest):
        """发送消息"""
        msg_id = uuid.uuid4().hex[:16]
        return {"id": msg_id, "role": req.role, "content": req.content}

    @router.get("/sessions/{session_id}/messages")
    async def list_messages(session_id: str, limit: int = 50):
        """列出消息"""
        return {"messages": [], "total": 0}

    # ─── 资源配置与数据库连通（V3 §8.3.4）───

    def _resource_repo(ctx: Any = None) -> ResourceRepo:
        """解析资源配置仓储；未装配时 503"""
        eng = _require_engine(ctx)
        comps = getattr(eng, "comps", None)
        repo = getattr(comps, "resources", None) if comps is not None else None
        if repo is None:
            raise HTTPException(status_code=503, detail="资源配置仓储未装配")
        return repo

    def _resource_tenant(ctx: Any = None) -> str:
        """解析资源所属租户；匿名回落 system"""
        tid = getattr(ctx, "tenant_id", None) if ctx is not None else None
        return tid or "system"

    @router.post("/resources")
    async def create_resource(
        req: ResourceCreateRequest,
        ctx: Any = Depends(get_optional_context),
    ):
        """创建资源配置"""
        if req.kind not in RESOURCE_KINDS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的资源类型: {req.kind}（可选 {', '.join(RESOURCE_KINDS)}）",
            )
        repo = _resource_repo(ctx)
        tenant_id = _resource_tenant(ctx)
        if repo.get_by_name(tenant_id, req.name) is not None:
            raise HTTPException(status_code=409, detail="资源配置已存在")
        resource = repo.create(
            tenant_id, req.name, req.kind,
            config=req.config or {}, created_by=_resource_tenant(ctx),
        )
        return {"resource": resource}

    @router.get("/resources")
    async def list_resources(
        kind: Optional[str] = None,
        limit: int = Query(100, ge=1, le=1000),
        ctx: Any = Depends(get_optional_context),
    ):
        """列出资源配置"""
        repo = _resource_repo(ctx)
        items = repo.list_all(tenant_id=_resource_tenant(ctx), kind=kind, limit=limit)
        return {"resources": items, "total": len(items)}

    @router.get("/resources/stats")
    async def resource_stats(ctx: Any = Depends(get_optional_context)):
        """资源配置统计"""
        repo = _resource_repo(ctx)
        return {"stats": repo.stats(tenant_id=_resource_tenant(ctx))}

    @router.get("/resources/{resource_id}")
    async def get_resource(
        resource_id: str,
        ctx: Any = Depends(get_optional_context),
    ):
        """获取配置详情"""
        repo = _resource_repo(ctx)
        resource = repo.get(resource_id)
        if resource is None:
            raise HTTPException(status_code=404, detail="资源配置不存在")
        return {"resource": resource}

    @router.put("/resources/{resource_id}")
    async def update_resource(
        resource_id: str,
        req: ResourceUpdateRequest,
        ctx: Any = Depends(get_optional_context),
    ):
        """更新配置（config / status）"""
        repo = _resource_repo(ctx)
        if repo.get(resource_id) is None:
            raise HTTPException(status_code=404, detail="资源配置不存在")
        resource = None
        if req.status is not None:
            if req.status not in (RESOURCE_ACTIVE, RESOURCE_DISABLED):
                raise HTTPException(status_code=400, detail="非法的资源状态")
            resource = repo.set_status(resource_id, req.status)
        if req.config is not None:
            resource = repo.update_config(resource_id, req.config)
        if resource is None:
            resource = repo.get(resource_id)
        return {"resource": resource}

    @router.delete("/resources/{resource_id}")
    async def delete_resource(
        resource_id: str,
        ctx: Any = Depends(get_optional_context),
    ):
        """删除配置（级联清理连通记录）"""
        repo = _resource_repo(ctx)
        repo.delete(resource_id)
        return {"id": resource_id, "status": "deleted"}

    @router.post("/resources/{resource_id}/check")
    async def check_resource(
        resource_id: str,
        ctx: Any = Depends(get_optional_context),
    ):
        """连通性探测（V3 §8.3.4）"""
        repo = _resource_repo(ctx)
        resource = repo.get(resource_id)
        if resource is None:
            raise HTTPException(status_code=404, detail="资源配置不存在")
        result = DatabaseConnector().check(resource["kind"], resource.get("config") or {})
        detail = result["detail"]
        connected = result["connected"]
        cid = repo.record_check(
            _resource_tenant(ctx), resource_id, resource["kind"],
            connected, result["latency_ms"], detail=detail,
        )
        out: Dict[str, Any] = {
            "connected": connected,
            "latency_ms": result["latency_ms"],
            "check_id": cid,
        }
        if not connected:
            out["error"] = detail.get("error", "连接失败")
        return out

    @router.get("/resources/{resource_id}/checks")
    async def list_resource_checks(
        resource_id: str,
        limit: int = Query(20, ge=1, le=500),
        ctx: Any = Depends(get_optional_context),
    ):
        """连通记录"""
        repo = _resource_repo(ctx)
        if repo.get(resource_id) is None:
            raise HTTPException(status_code=404, detail="资源配置不存在")
        checks = repo.list_checks(resource_id, limit=limit)
        return {"total": len(checks), "checks": checks}

    # ─── Agent 管理 ───
    @router.get("/agents")
    async def list_agents():
        """列出已注册 Agent"""
        return {"agents": [], "total": 0}

    @router.post("/agents/register")
    async def register_agent(
        name: str,
        agent_type: str,
        endpoint: str,
    ):
        """注册 Agent"""
        agent_id = uuid.uuid4().hex[:16]
        return {"id": agent_id, "name": name, "status": "registered"}

    @router.delete("/agents/{agent_id}")
    async def unregister_agent(agent_id: str):
        """注销 Agent"""
        return {"id": agent_id, "status": "unregistered"}

    # ─── 审批（M1 任务 1.4，§8.2）───

    def _tenant_engine(ctx: Any) -> Optional[WorkflowEngine]:
        """按租户解析引擎；未启用 L2 隔离（或该租户未隔离）时返回 None"""
        provider = _engine_provider_of(engine, engine_provider)
        if provider is None or ctx is None:
            return None
        tenant_code = getattr(ctx, "tenant_code", None)
        if not tenant_code:
            return None
        return provider(tenant_code)

    def _require_engine(ctx: Any = None) -> WorkflowEngine:
        tenant_engine = _tenant_engine(ctx)
        if tenant_engine is not None:
            return tenant_engine
        if engine is None:
            raise HTTPException(status_code=503, detail="工作流引擎未装配")
        return engine

    @router.get("/approvals/pending")
    async def list_pending_approvals(
        tenant_code: Optional[str] = None,
        limit: int = 20,
        ctx: Any = Depends(get_tenant_context),
    ):
        """待审批任务列表（仅返回当前租户可见范围）"""
        eng = _require_engine(ctx)
        # 租户隔离：非 admin 只能看本租户任务
        code = tenant_code or ctx.tenant_code
        if _tenant_engine(ctx) is not None and code != ctx.tenant_code:
            # L2 物理隔离：租户数据落在独立库，不支持跨租户查询
            raise HTTPException(status_code=403, detail="L2 隔离模式下不支持跨租户查询")
        if "admin" not in (ctx.roles or ()) and tenant_code and tenant_code != ctx.tenant_code:
            raise HTTPException(status_code=403, detail="无权查看其他租户的审批任务")
        try:
            tasks = eng.list_pending_approvals(tenant_code=code, limit=min(limit, 100))
        except WorkflowError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"tasks": tasks, "total": len(tasks)}

    @router.post("/approvals/{task_id}/decision")
    async def decide_approval(
        task_id: str,
        req: ApprovalDecisionRequest,
        ctx: Any = Depends(get_tenant_context),
    ):
        """提交审批决策：通过 → 流程继续；驳回 → 流程取消（§8.2.1）"""
        eng = _require_engine(ctx)
        if req.decision not in ("approve", "reject"):
            raise HTTPException(status_code=400, detail="decision 必须是 approve 或 reject")
        approver = req.approver or ctx.user_id
        try:
            status = await eng.approve_task(
                task_id, decision=(req.decision == "approve"),
                approver=approver, reason=req.reason,
            )
        except WorkflowError as e:
            msg = str(e)
            if msg.startswith("审批任务不存在"):
                raise HTTPException(status_code=404, detail=msg)
            raise HTTPException(status_code=400, detail=msg)
        return {"task_id": task_id, "decision": req.decision, "decided_by": approver,
                "instance": status}

    @router.post("/approvals/sweep-timeouts")
    async def sweep_approval_timeouts(ctx: Any = Depends(get_tenant_context)):
        """审批超时扫描（§8.2.2）：low 自动通过 / medium 升级 / high 自动驳回。
        由运维定时器周期调用。"""
        eng = _require_engine(ctx)
        handled = await eng.sweep_timeouts()
        return {"handled": handled}

    return router


def create_app(engine: Optional[WorkflowEngine] = None, *,
               engine_provider: Any = None) -> FastAPI:
    """创建 FastAPI 应用（engine 缺省时审批路由返回 503）"""
    from jkos_core.auth.dependencies import configure_auth

    app = FastAPI(
        title="极快AI操作系统 API",
        version="0.1.0",
        description="多模态 AI Agent 中台 API",
    )
    # 装配认证（engine 提供 JWTManager）
    if engine is not None:
        jwt_mgr = getattr(engine, "jwt", None)
        if jwt_mgr is not None:
            configure_auth(jwt_mgr)
    else:
        # 无 engine 时，审批路由返回 503（由 _require_engine 处理）
        pass
    router = create_api_router(engine=engine, engine_provider=engine_provider)
    app.include_router(router)
    # M13: 工具注册 / 发现 / 版本管理 / 市场路由
    from jkos_core.api.tool_routes import register_tool_routes
    register_tool_routes(app, registry=None)
    # M16: 智能中枢（harness sidecar）—— 默认关闭，由 JKOS_HARNESS_ENABLED 控制
    from jkos_core.harness.config import HarnessConfig
    harness_config = HarnessConfig.from_env()
    if harness_config.enabled:
        from jkos_core.harness.gateway import get_gateway
        from jkos_core.harness.proxy import create_ui_proxy_router
        from jkos_core.harness.routes import create_harness_router
        app.include_router(create_harness_router(get_gateway(harness_config)))
        app.include_router(create_ui_proxy_router(harness_config))
    return app
