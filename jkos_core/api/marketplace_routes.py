"""
DSH API - 插件市场与多语言路由

M5 功能：插件市场 API + i18n API
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from jkos_core.auth.dependencies import get_tenant_context
from jkos_core.plugins.marketplace import (
    PluginMarketplace,
    VisibilityDomain,
    get_marketplace,
)
from jkos_core.plugins.i18n import (
    I18nManager,
    LanguageCode,
    detect_language,
    get_i18n,
)

logger = logging.getLogger("dsh.api.marketplace")


# ─── Pydantic 模型 ───

class PluginInstallRequest(BaseModel):
    """插件安装请求"""
    plugin_id: str = Field(..., description="插件ID")
    version: Optional[str] = Field(None, description="版本")
    config: Optional[Dict[str, Any]] = Field(default_factory=dict, description="配置")
    visibility: str = Field("custom", description="可见域: core/industry/custom")


class PluginUninstallRequest(BaseModel):
    """插件卸载请求"""
    plugin_id: str = Field(..., description="插件ID")


class PluginRateRequest(BaseModel):
    """插件评分请求"""
    rating: float = Field(..., ge=1, le=5, description="评分 (1-5)")


class I18nTranslateRequest(BaseModel):
    """翻译请求"""
    key: str = Field(..., description="翻译键")
    params: Optional[Dict[str, str]] = Field(default_factory=dict, description="格式化参数")


# ─── 插件市场路由 ───

def create_plugin_router(marketplace: Optional[PluginMarketplace] = None) -> APIRouter:
    """创建插件市场路由"""
    router = APIRouter(prefix="/api/v1/plugins", tags=["Plugins"])
    mp = marketplace or get_marketplace()
    
    @router.get("/categories")
    async def list_categories():
        """获取插件分类列表"""
        return {"categories": mp.get_categories()}
    
    @router.get("/statistics")
    async def get_statistics():
        """获取市场统计"""
        return mp.get_statistics()
    
    @router.get("/featured")
    async def get_featured():
        """获取推荐插件"""
        return {"plugins": mp.get_featured()}
    
    @router.get("")
    async def list_plugins(
        category: Optional[str] = Query(None, description="分类过滤"),
        visibility: Optional[str] = Query(None, description="可见域过滤"),
        industry: Optional[str] = Query(None, description="行业标签过滤"),
        search: Optional[str] = Query(None, description="搜索关键词"),
        ctx: Any = Depends(get_tenant_context),
    ):
        """列出插件"""
        if search:
            return {"plugins": mp.search(search), "total": len(mp.search(search))}
        
        # 根据可见域过滤
        plugins = []
        for plugin_id, info in mp.registry._plugins.items():
            # 分类过滤
            if category and info.metadata.category.value != category:
                continue
            
            # 可见域过滤
            if visibility and info.visibility.value != visibility:
                continue
            
            # 行业过滤
            if industry and industry not in info.industry_tags:
                continue
            
            # 租户隔离
            if info.visibility == VisibilityDomain.CUSTOM:
                if ctx.tenant_id and info.tenant_id != ctx.tenant_id:
                    continue
            
            plugins.append({
                "plugin_id": plugin_id,
                "name": info.metadata.name,
                "version": info.metadata.version,
                "description": info.metadata.description,
                "category": info.metadata.category.value,
                "visibility": info.visibility.value,
                "author": info.metadata.author,
                "rating": info.marketplace.rating,
                "downloads": info.marketplace.downloads,
                "tags": info.marketplace.tags,
            })
        
        return {"plugins": plugins, "total": len(plugins)}
    
    @router.get("/{plugin_id}")
    async def get_plugin(plugin_id: str):
        """获取插件详情"""
        details = mp.get_details(plugin_id)
        if not details:
            raise HTTPException(status_code=404, detail="插件不存在")
        return details
    
    @router.post("/install")
    async def install_plugin(
        req: PluginInstallRequest,
        ctx: Any = Depends(get_tenant_context),
    ):
        """安装插件"""
        try:
            visibility = VisibilityDomain(req.visibility)
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的可见域")
        
        success = mp.install(
            plugin_id=req.plugin_id,
            version=req.version,
            config=req.config,
            visibility=visibility,
            tenant_id=ctx.tenant_id if visibility == VisibilityDomain.CUSTOM else None,
        )
        
        if not success:
            raise HTTPException(status_code=400, detail="插件安装失败")
        
        return {"plugin_id": req.plugin_id, "status": "installed"}
    
    @router.post("/uninstall")
    async def uninstall_plugin(
        req: PluginUninstallRequest,
        ctx: Any = Depends(get_tenant_context),
    ):
        """卸载插件"""
        success = mp.uninstall(
            plugin_id=req.plugin_id,
            tenant_id=ctx.tenant_id,
        )
        
        if not success:
            raise HTTPException(status_code=400, detail="插件卸载失败")
        
        return {"plugin_id": req.plugin_id, "status": "uninstalled"}
    
    @router.post("/{plugin_id}/rate")
    async def rate_plugin(
        plugin_id: str,
        req: PluginRateRequest,
    ):
        """评分插件"""
        success = mp.rate(plugin_id, req.rating)
        if not success:
            raise HTTPException(status_code=400, detail="评分失败")
        return {"plugin_id": plugin_id, "rating": req.rating}
    
    @router.post("/{plugin_id}/featured")
    async def set_featured(
        plugin_id: str,
        featured: bool = True,
    ):
        """设置推荐插件"""
        success = mp.set_featured(plugin_id, featured)
        if not success:
            raise HTTPException(status_code=400, detail="设置失败")
        return {"plugin_id": plugin_id, "featured": featured}
    
    @router.post("/{plugin_id}/verified")
    async def set_verified(
        plugin_id: str,
        verified: bool = True,
    ):
        """设置已验证插件"""
        success = mp.set_verified(plugin_id, verified)
        if not success:
            raise HTTPException(status_code=400, detail="设置失败")
        return {"plugin_id": plugin_id, "verified": verified}
    
    return router


# ─── 多语言路由 ───

def create_i18n_router(i18n: Optional[I18nManager] = None) -> APIRouter:
    """创建多语言路由"""
    router = APIRouter(prefix="/api/v1/i18n", tags=["i18n"])
    i18n_mgr = i18n or get_i18n()
    
    @router.get("/languages")
    async def list_languages():
        """获取可用语言列表"""
        return {"languages": i18n_mgr.get_languages()}
    
    @router.get("/{language}")
    async def get_translations(language: str):
        """获取翻译文件"""
        translations = i18n_mgr.get_translation_file(language)
        if not translations:
            raise HTTPException(status_code=404, detail="语言不存在")
        return {"language": language, "translations": translations}
    
    @router.get("")
    async def get_current_translations(
        accept_language: Optional[str] = Header(None),
        lang: Optional[str] = Query(None),
    ):
        """获取当前语言翻译"""
        # 检测语言
        language = detect_language(accept_language, lang)
        i18n_mgr.set_language(language)
        
        translations = i18n_mgr.get_translation_file(language)
        return {"language": language, "translations": translations}
    
    @router.post("/translate")
    async def translate(
        req: I18nTranslateRequest,
        accept_language: Optional[str] = Header(None),
        lang: Optional[str] = Query(None),
    ):
        """翻译文本"""
        language = detect_language(accept_language, lang)
        i18n_mgr.set_language(language)
        
        text = i18n_mgr.translate(req.key, **(req.params or {}))
        return {"key": req.key, "text": text, "language": language}
    
    @router.post("/translate/batch")
    async def translate_batch(
        keys: List[str] = Body(...),
        accept_language: Optional[str] = Header(None),
        lang: Optional[str] = Query(None),
    ):
        """批量翻译"""
        language = detect_language(accept_language, lang)
        i18n_mgr.set_language(language)
        
        translations = i18n_mgr.translate_many(keys)
        return {"translations": translations, "language": language}
    
    @router.post("/set-language")
    async def set_language(
        language: str = Query(..., description="语言代码"),
    ):
        """设置当前语言"""
        success = i18n_mgr.set_language(language)
        if not success:
            raise HTTPException(status_code=400, detail="无效的语言代码")
        return {"language": language, "status": "set"}
    
    return router


# ─── 导入依赖 ───

from fastapi import Header, Body


# ─── 统一路由注册 ───

def register_marketplace_routes(app, marketplace: Optional[PluginMarketplace] = None):
    """注册插件市场路由"""
    router = create_plugin_router(marketplace)
    app.include_router(router)
    logger.info("插件市场路由已注册")


def register_i18n_routes(app, i18n: Optional[I18nManager] = None):
    """注册多语言路由"""
    router = create_i18n_router(i18n)
    app.include_router(router)
    logger.info("多语言路由已注册")
