"""
DSH API - 工具市场与注册路由

M13 功能：工具注册 / 发现 / 版本管理 / 市场 API
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from jkos_core.auth.dependencies import get_tenant_context
from jkos_core.mcp.registry import (
    ToolCategory,
    ToolMarketplaceMetadata,
    ToolRegistry,
    ToolVisibility,
    get_registry,
)

logger = logging.getLogger("dsh.api.tool")


# ─── Pydantic 模型 ───

class ToolRegisterRequest(BaseModel):
    """工具注册请求"""
    name: str = Field(..., description="工具名称")
    description: str = Field("", description="工具描述")
    input_schema: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="输入 schema"
    )
    output_schema: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="输出 schema"
    )
    visibility: str = Field("core", description="可见域: core/industry/custom")
    category: str = Field(
        "custom",
        description="工具分类: text/audio/image/video/rag/session/"
                    "interconnect/governance/custom",
    )
    industry_tags: Optional[List[str]] = Field(
        default_factory=list, description="行业标签"
    )
    version: str = Field("1.0.0", description="版本号")


class ToolVersionRequest(BaseModel):
    """发布工具版本请求"""
    version: str = Field(..., description="新版本号")
    changelog: str = Field("", description="变更说明")
    changes: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="变更项: name/description/input_schema/output_schema",
    )


class ToolRatingRequest(BaseModel):
    """工具评分请求"""
    rating: float = Field(..., ge=1, le=5, description="评分 (1-5)")


class ToolMarketplaceUpdateRequest(BaseModel):
    """更新工具市场信息请求"""
    tags: Optional[List[str]] = Field(None, description="标签")
    screenshots: Optional[List[str]] = Field(None, description="截图")
    author_url: Optional[str] = Field(None, description="作者链接")
    support_email: Optional[str] = Field(None, description="支持邮箱")
    featured: Optional[bool] = Field(None, description="是否推荐")
    verified: Optional[bool] = Field(None, description="是否已验证")


# ─── 序列化辅助 ───

def _serialize_tool(tool: Any) -> Dict[str, Any]:
    """序列化工具摘要 (MCPTool)"""
    return {
        "name": getattr(tool, "name", ""),
        "description": getattr(tool, "description", ""),
        "input_schema": getattr(tool, "input_schema", {}),
        "output_schema": getattr(tool, "output_schema", {}),
    }


def _serialize_marketplace(mp: ToolMarketplaceMetadata) -> Dict[str, Any]:
    """序列化工具市场元数据"""
    return {
        "rating": mp.rating,
        "rating_count": mp.rating_count,
        "downloads": mp.downloads,
        "versions": mp.versions,
        "screenshots": mp.screenshots,
        "tags": mp.tags,
        "author_url": mp.author_url,
        "support_email": mp.support_email,
        "featured": mp.featured,
        "verified": mp.verified,
        "created_at": mp.created_at.isoformat(),
        "updated_at": mp.updated_at.isoformat(),
    }


def _serialize_info(info: Any) -> Dict[str, Any]:
    """序列化工具详情 (ToolInfo)"""
    return {
        "name": info.tool.name,
        "description": getattr(info.tool, "description", ""),
        "input_schema": getattr(info.tool, "input_schema", {}),
        "output_schema": getattr(info.tool, "output_schema", {}),
        "version": info.version,
        "visibility": info.visibility.value,
        "category": info.category.value,
        "industry_tags": info.industry_tags,
        "tenant_id": info.tenant_id,
        "marketplace": _serialize_marketplace(info.marketplace),
    }


def _parse_visibility(value: Optional[str]) -> Optional[ToolVisibility]:
    """解析可见域，非法值抛 400"""
    if not value:
        return None
    try:
        return ToolVisibility(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的可见域")


def _parse_category(value: Optional[str]) -> Optional[ToolCategory]:
    """解析工具分类，非法值抛 400"""
    if not value:
        return None
    try:
        return ToolCategory(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的工具分类")


# ─── 工具路由 ───

def create_tool_router(registry: Optional[ToolRegistry] = None) -> APIRouter:
    """创建工具路由"""
    router = APIRouter(prefix="/api/v1/tools", tags=["Tools"])
    reg = registry or get_registry()

    @router.get("/categories")
    async def list_categories():
        """获取工具分类列表"""
        return {"categories": reg.get_categories()}

    @router.get("/industry-tags")
    async def list_industry_tags():
        """获取行业标签列表"""
        return {"industry_tags": reg.get_industry_tags()}

    @router.get("/featured")
    async def get_featured():
        """获取推荐工具"""
        tools = reg.get_featured()
        return {"tools": [_serialize_tool(t) for t in tools], "total": len(tools)}

    @router.get("/verified")
    async def get_verified():
        """获取已验证工具"""
        tools = reg.get_verified()
        return {"tools": [_serialize_tool(t) for t in tools], "total": len(tools)}

    @router.get("")
    async def list_tools(
        visibility: Optional[str] = Query(None, description="可见域过滤"),
        category: Optional[str] = Query(None, description="分类过滤"),
        industry: Optional[str] = Query(None, description="行业标签过滤"),
        search: Optional[str] = Query(None, description="搜索关键词"),
        ctx: Any = Depends(get_tenant_context),
    ):
        """列出工具（支持搜索与过滤）"""
        if search:
            tools = reg.search(search)
            return {"tools": [_serialize_tool(t) for t in tools], "total": len(tools)}

        cat = _parse_category(category)
        vis = _parse_visibility(visibility)

        tools = reg.list_visible(
            tenant_id=ctx.tenant_id,
            industry=industry,
            category=cat,
        )

        # 可见域过滤
        if vis:
            tools = [t for t in tools if (reg.get_info(t.name).visibility == vis)]

        return {"tools": [_serialize_tool(t) for t in tools], "total": len(tools)}

    @router.post("/register")
    async def register_tool(
        req: ToolRegisterRequest,
        ctx: Any = Depends(get_tenant_context),
    ):
        """注册工具"""
        visibility = _parse_visibility(req.visibility)
        category = _parse_category(req.category)

        # 延迟导入，避免与 server 的循环依赖
        from jkos_core.mcp.server import MCPTool

        tool = MCPTool(
            name=req.name,
            description=req.description,
            input_schema=req.input_schema,
            output_schema=req.output_schema,
        )

        try:
            reg.register(
                tool=tool,
                visibility=visibility or ToolVisibility.CORE,
                category=category or ToolCategory.CUSTOM,
                industry_tags=req.industry_tags,
                tenant_id=(
                    ctx.tenant_id
                    if (visibility or ToolVisibility.CORE) == ToolVisibility.CUSTOM
                    else None
                ),
                version=req.version,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        return {
            "tool_name": req.name,
            "status": "registered",
            "version": req.version,
        }

    @router.get("/{tool_name}")
    async def get_tool(tool_name: str):
        """获取工具详情"""
        info = reg.get_info(tool_name)
        if not info:
            raise HTTPException(status_code=404, detail="工具不存在")
        return _serialize_info(info)

    @router.get("/{tool_name}/versions")
    async def get_versions(tool_name: str):
        """获取工具版本历史"""
        versions = reg.get_versions(tool_name)
        return {"tool_name": tool_name, "versions": versions}

    @router.post("/{tool_name}/versions")
    async def publish_version(
        tool_name: str,
        req: ToolVersionRequest,
    ):
        """发布工具新版本"""
        success = reg.publish_version(
            tool_name=tool_name,
            version=req.version,
            changelog=req.changelog,
            **req.changes,
        )
        if not success:
            raise HTTPException(status_code=404, detail="工具不存在")
        return {"tool_name": tool_name, "version": req.version, "status": "published"}

    @router.post("/{tool_name}/versions/{version}/activate")
    async def activate_version(
        tool_name: str,
        version: str,
    ):
        """激活工具指定版本"""
        success = reg.set_active_version(tool_name, version)
        if not success:
            raise HTTPException(status_code=404, detail="版本激活失败")
        return {"tool_name": tool_name, "version": version, "status": "active"}

    @router.delete("/{tool_name}")
    async def unregister_tool(tool_name: str):
        """注销工具"""
        success = reg.unregister(tool_name)
        if not success:
            raise HTTPException(status_code=404, detail="工具不存在")
        return {"tool_name": tool_name, "status": "unregistered"}

    @router.post("/{tool_name}/rate")
    async def rate_tool(
        tool_name: str,
        req: ToolRatingRequest,
    ):
        """评分工具"""
        success = reg.add_rating(tool_name, req.rating)
        if not success:
            raise HTTPException(status_code=400, detail="评分失败")
        return {"tool_name": tool_name, "rating": req.rating}

    @router.post("/{tool_name}/downloads")
    async def increment_downloads(tool_name: str):
        """增加下载次数"""
        if not reg.increment_downloads(tool_name):
            raise HTTPException(status_code=404, detail="工具不存在")
        info = reg.get_marketplace_info(tool_name)
        downloads = info.downloads if info else 0
        return {"tool_name": tool_name, "downloads": downloads, "status": "incremented"}

    @router.patch("/{tool_name}/marketplace")
    async def update_marketplace(
        tool_name: str,
        req: ToolMarketplaceUpdateRequest,
    ):
        """更新工具市场信息"""
        if hasattr(req, "model_dump"):
            updates = req.model_dump(exclude_none=True)
        else:
            updates = req.dict(exclude_none=True)

        if not updates:
            raise HTTPException(status_code=400, detail="无更新字段")

        success = reg.update_marketplace_info(tool_name, **updates)
        if not success:
            raise HTTPException(status_code=404, detail="工具不存在")

        info = reg.get_marketplace_info(tool_name)
        return {
            "tool_name": tool_name,
            "marketplace": _serialize_marketplace(info) if info else None,
        }

    return router


# ─── 统一路由注册 ───

def register_tool_routes(app, registry: Optional[ToolRegistry] = None):
    """注册工具路由"""
    router = create_tool_router(registry)
    app.include_router(router)
    logger.info("工具路由已注册")