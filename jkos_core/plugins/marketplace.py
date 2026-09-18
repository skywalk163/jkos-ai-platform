"""
DSH Core - 插件市场

提供插件发现、安装、卸载、更新等功能
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from jkos_core.plugins.base import (
    PluginBase,
    PluginCategory,
    PluginConfig,
    PluginContext,
    PluginMetadata,
    PluginResult,
    PluginStatus,
    DeterministicPlugin,
)
from jkos_core.plugins.registry import (
    EnhancedPluginRegistry,
    MarketplaceMetadata,
    VisibilityDomain,
    get_registry,
)

logger = logging.getLogger("dsh.plugins.marketplace")


# ─── 插件包信息 ───

@dataclass
class PluginPackage:
    """插件包信息"""
    name: str
    version: str
    plugin_id: str
    description: str
    category: PluginCategory
    author: str
    license: str
    dependencies: List[str]
    config_schema: Dict[str, Any]
    visibility: VisibilityDomain
    industry_tags: List[str]
    download_url: str
    checksum: str
    size: int
    created_at: datetime = field(default_factory=datetime.now)


# ─── 远程插件源 ───

@dataclass
class PluginSource:
    """远程插件源"""
    name: str
    url: str
    description: str
    priority: int = 100
    enabled: bool = True


# ─── 插件市场 ───

class PluginMarketplace:
    """插件市场
    
    提供:
    - 本地插件管理
    - 远程插件发现
    - 插件安装/卸载
    - 插件更新
    """
    
    def __init__(
        self,
        registry: Optional[EnhancedPluginRegistry] = None,
        plugins_dir: Optional[str] = None,
    ):
        self.registry = registry or get_registry()
        self.plugins_dir = Path(plugins_dir) if plugins_dir else Path("./plugins")
        self.sources: List[PluginSource] = []
        self._installed_packages: Dict[str, PluginPackage] = {}
    
    def add_source(self, source: PluginSource) -> None:
        """添加远程插件源"""
        self.sources.append(source)
        logger.info(f"添加插件源: {source.name} ({source.url})")
    
    def remove_source(self, name: str) -> bool:
        """移除远程插件源"""
        for i, source in enumerate(self.sources):
            if source.name == name:
                self.sources.pop(i)
                logger.info(f"移除插件源: {name}")
                return True
        return False
    
    def list_local(self) -> List[Dict[str, Any]]:
        """列出本地插件"""
        result = []
        for plugin_id, info in self.registry._plugins.items():
            result.append({
                "plugin_id": plugin_id,
                "name": info.metadata.name,
                "version": info.metadata.version,
                "description": info.metadata.description,
                "category": info.metadata.category.value,
                "visibility": info.visibility.value,
                "author": info.metadata.author,
                "license": info.metadata.license,
                "rating": info.marketplace.rating,
                "downloads": info.marketplace.downloads,
                "tags": info.marketplace.tags,
                "industry_tags": info.industry_tags,
                "tenant_id": info.tenant_id,
            })
        return result
    
    def list_remote(self) -> List[PluginPackage]:
        """列出远程插件（从源获取）"""
        packages = []
        for source in self.sources:
            if not source.enabled:
                continue
            try:
                remote_packages = self._fetch_from_source(source)
                packages.extend(remote_packages)
            except Exception as e:
                logger.error(f"从源 {source.name} 获取插件失败: {e}")
        return packages
    
    def _fetch_from_source(self, source: PluginSource) -> List[PluginPackage]:
        """从源获取插件列表"""
        # 实际实现中，这里会从远程 API 获取
        # 目前返回空列表，等待远程源配置
        logger.info(f"从源 {source.name} 获取插件列表...")
        return []
    
    def search(self, query: str) -> List[Dict[str, Any]]:
        """搜索插件"""
        results = []
        for plugin_id, info in self.registry._plugins.items():
            if (
                query.lower() in plugin_id.lower()
                or query.lower() in info.metadata.name.lower()
                or query.lower() in info.metadata.description.lower()
            ):
                results.append({
                    "plugin_id": plugin_id,
                    "name": info.metadata.name,
                    "version": info.metadata.version,
                    "description": info.metadata.description,
                    "category": info.metadata.category.value,
                    "visibility": info.visibility.value,
                    "rating": info.marketplace.rating,
                    "downloads": info.marketplace.downloads,
                    "tags": info.marketplace.tags,
                })
        return results
    
    def get_details(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """获取插件详情"""
        info = self.registry.get_info(plugin_id)
        if not info:
            return None
        
        return {
            "plugin_id": plugin_id,
            "name": info.metadata.name,
            "version": info.metadata.version,
            "description": info.metadata.description,
            "category": info.metadata.category.value,
            "plugin_type": info.metadata.plugin_type.value,
            "author": info.metadata.author,
            "license": info.metadata.license,
            "dependencies": info.metadata.dependencies,
            "config_schema": info.metadata.config_schema,
            "visibility": info.visibility.value,
            "industry_tags": info.industry_tags,
            "tenant_id": info.tenant_id,
            "rating": info.marketplace.rating,
            "rating_count": info.marketplace.rating_count,
            "downloads": info.marketplace.downloads,
            "screenshots": info.marketplace.screenshots,
            "tags": info.marketplace.tags,
            "featured": info.marketplace.featured,
            "verified": info.marketplace.verified,
            "created_at": info.marketplace.created_at.isoformat(),
            "updated_at": info.marketplace.updated_at.isoformat(),
        }
    
    def install(
        self,
        plugin_id: str,
        version: Optional[str] = None,
        source: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        visibility: VisibilityDomain = VisibilityDomain.CUSTOM,
        tenant_id: Optional[str] = None,
    ) -> bool:
        """安装插件
        
        Args:
            plugin_id: 插件ID
            version: 版本（可选）
            source: 源URL（可选）
            config: 配置（可选）
            visibility: 可见域
            tenant_id: 租户ID（自定义插件必填）
        
        Returns:
            是否安装成功
        """
        # 检查是否已安装
        if self.registry.get(plugin_id):
            logger.warning(f"插件 {plugin_id} 已安装")
            return False
        
        # 自定义插件需要租户ID
        if visibility == VisibilityDomain.CUSTOM and not tenant_id:
            logger.error("自定义插件需要指定租户ID")
            return False
        
        # 实际安装逻辑
        # 1. 从源下载插件包
        # 2. 解压到 plugins 目录
        # 3. 加载插件
        # 4. 注册到注册表
        
        logger.info(f"安装插件: {plugin_id}")
        
        # 模拟安装（实际实现需要下载和解压）
        try:
            # 创建插件目录
            plugin_dir = self.plugins_dir / plugin_id
            plugin_dir.mkdir(parents=True, exist_ok=True)
            
            # 创建 plugin.yaml
            plugin_yaml = plugin_dir / "plugin.yaml"
            plugin_yaml.write_text(json.dumps({
                "id": plugin_id,
                "version": version or "1.0.0",
                "visibility": visibility.value,
                "tenant_id": tenant_id,
            }, indent=2))
            
            # 增加下载次数
            self.registry.increment_downloads(plugin_id)
            
            logger.info(f"插件 {plugin_id} 安装成功")
            return True
            
        except Exception as e:
            logger.error(f"安装插件 {plugin_id} 失败: {e}")
            return False
    
    def uninstall(self, plugin_id: str, tenant_id: Optional[str] = None) -> bool:
        """卸载插件
        
        Args:
            plugin_id: 插件ID
            tenant_id: 租户ID（自定义插件必填）
        
        Returns:
            是否卸载成功
        """
        info = self.registry.get_info(plugin_id)
        if not info:
            logger.warning(f"插件 {plugin_id} 未安装")
            return False
        
        # 自定义插件需要验证租户
        if info.visibility == VisibilityDomain.CUSTOM:
            if tenant_id and info.tenant_id != tenant_id:
                logger.error(f"无权卸载插件 {plugin_id}")
                return False
        
        # 从注册表卸载
        self.registry.unregister(plugin_id)
        
        # 删除插件目录
        plugin_dir = self.plugins_dir / plugin_id
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir)
        
        logger.info(f"插件 {plugin_id} 已卸载")
        return True
    
    def update(self, plugin_id: str, version: str) -> bool:
        """更新插件"""
        logger.info(f"更新插件 {plugin_id} 到版本 {version}")
        # 实际实现需要下载新版本并替换
        return True
    
    def rate(self, plugin_id: str, rating: float) -> bool:
        """评分"""
        return self.registry.add_rating(plugin_id, rating)
    
    def set_featured(self, plugin_id: str, featured: bool) -> bool:
        """设置推荐"""
        return self.registry.update_marketplace_info(plugin_id, featured=featured)
    
    def set_verified(self, plugin_id: str, verified: bool) -> bool:
        """设置已验证"""
        return self.registry.update_marketplace_info(plugin_id, verified=verified)
    
    def get_featured(self) -> List[Dict[str, Any]]:
        """获取推荐插件"""
        results = []
        for info in self.registry._plugins.values():
            if info.marketplace.featured:
                results.append({
                    "plugin_id": info.metadata.id,
                    "name": info.metadata.name,
                    "description": info.metadata.description,
                    "category": info.metadata.category.value,
                    "rating": info.marketplace.rating,
                    "downloads": info.marketplace.downloads,
                })
        return results
    
    def get_categories(self) -> List[Dict[str, Any]]:
        """获取分类统计"""
        stats = {}
        for info in self.registry._plugins.values():
            cat = info.metadata.category.value
            stats[cat] = stats.get(cat, 0) + 1
        
        return [
            {"category": cat, "count": count}
            for cat, count in sorted(stats.items(), key=lambda x: -x[1])
        ]
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取市场统计"""
        total = self.registry.count()
        core_count = len(self.registry.list_by_visibility(VisibilityDomain.CORE))
        industry_count = len(self.registry.list_by_visibility(VisibilityDomain.INDUSTRY))
        custom_count = len(self.registry.list_by_visibility(VisibilityDomain.CUSTOM))
        
        total_downloads = sum(
            info.marketplace.downloads
            for info in self.registry._plugins.values()
        )
        
        return {
            "total_plugins": total,
            "core_plugins": core_count,
            "industry_plugins": industry_count,
            "custom_plugins": custom_count,
            "total_downloads": total_downloads,
            "categories": self.get_categories(),
            "industry_tags": self.registry.get_industry_tags(),
        }


# ─── 全局市场实例 ───

_global_marketplace: Optional[PluginMarketplace] = None


def get_marketplace() -> PluginMarketplace:
    """获取全局市场实例"""
    global _global_marketplace
    if _global_marketplace is None:
        _global_marketplace = PluginMarketplace()
    return _global_marketplace


def set_marketplace(marketplace: PluginMarketplace) -> None:
    """设置全局市场实例"""
    global _global_marketplace
    _global_marketplace = marketplace
