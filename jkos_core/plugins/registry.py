"""
DSH Core - 增强插件注册表

支持可见域 (Visibility Domain): CORE / INDUSTRY / CUSTOM
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from jkos_core.plugins.base import (
    PluginBase,
    PluginCategory,
    PluginMetadata,
    PluginRegistry as BasePluginRegistry,
)

logger = logging.getLogger("dsh.plugins.registry")


# ─── 可见域枚举 ───

class VisibilityDomain(str, Enum):
    """插件可见域"""
    CORE = "core"           # 平台核心插件，所有租户可见
    INDUSTRY = "industry"   # 行业插件，按租户类型可见
    CUSTOM = "custom"       # 自定义插件，仅创建者可见


# ─── 插件市场元数据 ───

@dataclass
class MarketplaceMetadata:
    """插件市场元数据"""
    # 评分与下载
    rating: float = 0.0
    rating_count: int = 0
    downloads: int = 0
    
    # 版本历史
    versions: List[Dict[str, Any]] = field(default_factory=list)
    
    # 展示信息
    screenshots: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    
    # 作者信息
    author_url: str = ""
    support_email: str = ""
    
    # 市场状态
    featured: bool = False
    verified: bool = False
    
    # 时间戳
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class PluginInfo:
    """插件信息（包含市场元数据）"""
    plugin: PluginBase
    metadata: PluginMetadata
    marketplace: MarketplaceMetadata
    visibility: VisibilityDomain
    industry_tags: List[str] = field(default_factory=list)
    tenant_id: Optional[str] = None  # 自定义插件的所属租户


# ─── 增强插件注册表 ───

class EnhancedPluginRegistry:
    """增强插件注册表
    
    支持:
    - 可见域过滤
    - 插件市场元数据
    - 租户隔离
    """
    
    def __init__(self):
        self._plugins: Dict[str, PluginInfo] = {}
        self._categories: Dict[PluginCategory, List[str]] = {}
        self._visibility_index: Dict[VisibilityDomain, List[str]] = {
            VisibilityDomain.CORE: [],
            VisibilityDomain.INDUSTRY: [],
            VisibilityDomain.CUSTOM: [],
        }
        self._industry_index: Dict[str, List[str]] = {}  # industry_tag -> plugin_ids
        self._tenant_index: Dict[str, List[str]] = {}    # tenant_id -> plugin_ids
    
    def register(
        self,
        plugin: PluginBase,
        visibility: VisibilityDomain = VisibilityDomain.CORE,
        marketplace: Optional[MarketplaceMetadata] = None,
        industry_tags: Optional[List[str]] = None,
        tenant_id: Optional[str] = None,
    ) -> None:
        """注册插件
        
        Args:
            plugin: 插件实例
            visibility: 可见域
            marketplace: 市场元数据
            industry_tags: 行业标签
            tenant_id: 租户ID（自定义插件必填）
        """
        plugin_id = plugin.metadata.id
        
        if plugin_id in self._plugins:
            raise ValueError(f"插件 {plugin_id} 已注册")
        
        # 创建插件信息
        info = PluginInfo(
            plugin=plugin,
            metadata=plugin.metadata,
            marketplace=marketplace or MarketplaceMetadata(),
            visibility=visibility,
            industry_tags=industry_tags or [],
            tenant_id=tenant_id,
        )
        
        self._plugins[plugin_id] = info
        
        # 更新索引
        cat = plugin.metadata.category
        self._categories.setdefault(cat, []).append(plugin_id)
        self._visibility_index[visibility].append(plugin_id)
        
        for tag in (industry_tags or []):
            self._industry_index.setdefault(tag, []).append(plugin_id)
        
        if tenant_id:
            self._tenant_index.setdefault(tenant_id, []).append(plugin_id)
        
        logger.info(
            f"插件已注册: {plugin_id} "
            f"(visibility={visibility.value}, category={cat.value})"
        )
    
    def unregister(self, plugin_id: str) -> bool:
        """卸载插件"""
        if plugin_id not in self._plugins:
            return False
        
        info = self._plugins[plugin_id]
        
        # 从各索引中移除
        cat = info.metadata.category
        if plugin_id in self._categories.get(cat, []):
            self._categories[cat].remove(plugin_id)
        
        if plugin_id in self._visibility_index.get(info.visibility, []):
            self._visibility_index[info.visibility].remove(plugin_id)
        
        for tag in info.industry_tags:
            if plugin_id in self._industry_index.get(tag, []):
                self._industry_index[tag].remove(plugin_id)
        
        if info.tenant_id:
            if plugin_id in self._tenant_index.get(info.tenant_id, []):
                self._tenant_index[info.tenant_id].remove(plugin_id)
        
        # 清理插件
        import asyncio
        try:
            asyncio.run(info.plugin.cleanup())
        except Exception as e:
            logger.warning(f"清理插件 {plugin_id} 失败: {e}")
        
        del self._plugins[plugin_id]
        logger.info(f"插件已卸载: {plugin_id}")
        return True
    
    def get(self, plugin_id: str) -> Optional[PluginBase]:
        """获取插件"""
        info = self._plugins.get(plugin_id)
        return info.plugin if info else None
    
    def get_info(self, plugin_id: str) -> Optional[PluginInfo]:
        """获取插件信息"""
        return self._plugins.get(plugin_id)
    
    def list_visible(
        self,
        tenant_id: Optional[str] = None,
        industry: Optional[str] = None,
        category: Optional[PluginCategory] = None,
    ) -> List[PluginBase]:
        """列出可见插件
        
        Args:
            tenant_id: 租户ID（用于过滤自定义插件）
            industry: 行业标签
            category: 分类
        
        Returns:
            可见插件列表
        """
        result = []
        
        for plugin_id, info in self._plugins.items():
            # 可见域过滤
            if info.visibility == VisibilityDomain.CUSTOM:
                if tenant_id and info.tenant_id != tenant_id:
                    continue
            elif info.visibility == VisibilityDomain.INDUSTRY:
                if industry and industry not in info.industry_tags:
                    continue
            
            # 行业标签过滤
            if industry and industry not in info.industry_tags:
                continue
            
            # 分类过滤
            if category and info.metadata.category != category:
                continue
            
            result.append(info.plugin)
        
        return result
    
    def list_by_category(self, category: PluginCategory) -> List[PluginBase]:
        """按分类列出插件"""
        return [
            self._plugins[pid].plugin
            for pid in self._categories.get(category, [])
            if pid in self._plugins
        ]
    
    def list_by_visibility(self, visibility: VisibilityDomain) -> List[PluginBase]:
        """按可见域列出插件"""
        return [
            self._plugins[pid].plugin
            for pid in self._visibility_index.get(visibility, [])
            if pid in self._plugins
        ]
    
    def list_by_industry(self, industry_tag: str) -> List[PluginBase]:
        """按行业标签列出插件"""
        return [
            self._plugins[pid].plugin
            for pid in self._industry_index.get(industry_tag, [])
            if pid in self._plugins
        ]
    
    def list_by_tenant(self, tenant_id: str) -> List[PluginBase]:
        """列出租户的自定义插件"""
        return [
            self._plugins[pid].plugin
            for pid in self._tenant_index.get(tenant_id, [])
            if pid in self._plugins
        ]
    
    def all(self) -> Dict[str, PluginBase]:
        """所有插件"""
        return {pid: info.plugin for pid, info in self._plugins.items()}
    
    def list_ids(self) -> List[str]:
        """列出所有插件ID"""
        return list(self._plugins.keys())
    
    def count(self) -> int:
        """插件数量"""
        return len(self._plugins)
    
    def search(self, query: str) -> List[PluginBase]:
        """搜索插件"""
        query = query.lower()
        result = []
        
        for info in self._plugins.values():
            if (
                query in info.metadata.id.lower()
                or query in info.metadata.name.lower()
                or query in info.metadata.description.lower()
                or any(query in tag.lower() for tag in info.marketplace.tags)
            ):
                result.append(info.plugin)
        
        return result
    
    def get_marketplace_info(self, plugin_id: str) -> Optional[MarketplaceMetadata]:
        """获取插件市场信息"""
        info = self._plugins.get(plugin_id)
        return info.marketplace if info else None
    
    def update_marketplace_info(
        self,
        plugin_id: str,
        **kwargs,
    ) -> bool:
        """更新插件市场信息"""
        info = self._plugins.get(plugin_id)
        if not info:
            return False
        
        for key, value in kwargs.items():
            if hasattr(info.marketplace, key):
                setattr(info.marketplace, key, value)
        
        info.marketplace.updated_at = datetime.now()
        return True
    
    def increment_downloads(self, plugin_id: str) -> bool:
        """增加下载次数"""
        info = self._plugins.get(plugin_id)
        if not info:
            return False
        
        info.marketplace.downloads += 1
        return True
    
    def add_rating(self, plugin_id: str, rating: float) -> bool:
        """添加评分"""
        if not 1 <= rating <= 5:
            return False
        
        info = self._plugins.get(plugin_id)
        if not info:
            return False
        
        # 更新平均评分
        total = info.marketplace.rating * info.marketplace.rating_count + rating
        info.marketplace.rating_count += 1
        info.marketplace.rating = round(total / info.marketplace.rating_count, 2)
        
        return True
    
    def get_featured(self) -> List[PluginBase]:
        """获取推荐插件"""
        return [
            info.plugin
            for info in self._plugins.values()
            if info.marketplace.featured
        ]
    
    def get_verified(self) -> List[PluginBase]:
        """获取已验证插件"""
        return [
            info.plugin
            for info in self._plugins.values()
            if info.marketplace.verified
        ]
    
    def get_categories(self) -> List[str]:
        """获取所有分类"""
        return [cat.value for cat in PluginCategory]
    
    def get_industry_tags(self) -> List[str]:
        """获取所有行业标签"""
        return list(self._industry_index.keys())


# ─── 全局注册表实例 ───

_global_registry: Optional[EnhancedPluginRegistry] = None


def get_registry() -> EnhancedPluginRegistry:
    """获取全局注册表"""
    global _global_registry
    if _global_registry is None:
        _global_registry = EnhancedPluginRegistry()
    return _global_registry


def set_registry(registry: EnhancedPluginRegistry) -> None:
    """设置全局注册表"""
    global _global_registry
    _global_registry = registry
