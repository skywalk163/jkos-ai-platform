"""
DSH Core - 工具注册表 (M13 工具标准化)

支持可见域 (Visibility Domain): CORE / INDUSTRY / CUSTOM
支持工具版本历史与工具市场元数据
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from jkos_core.mcp.server import MCPTool

logger = logging.getLogger("dsh.mcp.registry")


# ─── 工具分类枚举 ───

class ToolCategory(str, Enum):
    """工具分类"""
    TEXT = "text"                       # 文本处理
    AUDIO = "audio"                     # 音频处理
    IMAGE = "image"                     # 图像处理
    VIDEO = "video"                     # 视频处理
    RAG = "rag"                         # RAG 检索
    SESSION = "session"                 # 会话工具
    INTERCONNECT = "interconnect"       # 互联互通
    GOVERNANCE = "governance"           # 治理与审批
    CUSTOM = "custom"                   # 自定义


# ─── 可见域枚举 ───

class ToolVisibility(str, Enum):
    """工具可见域"""
    CORE = "core"           # 平台核心工具，所有租户可见
    INDUSTRY = "industry"   # 行业工具，按租户类型可见
    CUSTOM = "custom"       # 自定义工具，仅创建者可见


# ─── 工具市场元数据 ───

@dataclass
class ToolMarketplaceMetadata:
    """工具市场元数据"""
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
class ToolInfo:
    """工具信息（包含市场元数据）"""
    tool: "MCPTool"                                    # 工具实例
    marketplace: ToolMarketplaceMetadata
    visibility: ToolVisibility
    category: ToolCategory
    version: str = "1.0.0"                             # 当前激活版本
    industry_tags: List[str] = field(default_factory=list)
    tenant_id: Optional[str] = None                    # 自定义工具的所属租户


# ─── 工具注册表 ───

class ToolRegistry:
    """工具注册表

    支持:
    - 可见域过滤
    - 工具市场元数据
    - 租户隔离
    - 工具版本管理
    """

    def __init__(self):
        self._tools: Dict[str, ToolInfo] = {}
        self._categories: Dict[ToolCategory, List[str]] = {}
        self._visibility_index: Dict[ToolVisibility, List[str]] = {
            ToolVisibility.CORE: [],
            ToolVisibility.INDUSTRY: [],
            ToolVisibility.CUSTOM: [],
        }
        self._industry_index: Dict[str, List[str]] = {}  # industry_tag -> tool_names
        self._tenant_index: Dict[str, List[str]] = {}    # tenant_id -> tool_names

    # ─── 注册与注销 ───

    def register(
        self,
        tool: "MCPTool",
        visibility: ToolVisibility = ToolVisibility.CORE,
        category: ToolCategory = ToolCategory.CUSTOM,
        marketplace: Optional[ToolMarketplaceMetadata] = None,
        industry_tags: Optional[List[str]] = None,
        tenant_id: Optional[str] = None,
        version: str = "1.0.0",
    ) -> None:
        """注册工具

        Args:
            tool: MCPTool 实例
            visibility: 可见域
            category: 工具分类
            marketplace: 市场元数据
            industry_tags: 行业标签
            tenant_id: 租户ID（自定义工具必填）
            version: 初始版本
        """
        tool_name = tool.name

        if tool_name in self._tools:
            raise ValueError(f"工具 {tool_name} 已注册")

        # 创建工具信息
        info = ToolInfo(
            tool=tool,
            marketplace=marketplace or ToolMarketplaceMetadata(),
            visibility=visibility,
            category=category,
            version=version,
            industry_tags=industry_tags or [],
            tenant_id=tenant_id,
        )

        # 记录初始版本
        info.marketplace.versions.append(
            self._make_version_snapshot(tool, version)
        )

        self._tools[tool_name] = info

        # 更新索引
        self._categories.setdefault(category, []).append(tool_name)
        self._visibility_index[visibility].append(tool_name)

        for tag in (industry_tags or []):
            self._industry_index.setdefault(tag, []).append(tool_name)

        if tenant_id:
            self._tenant_index.setdefault(tenant_id, []).append(tool_name)

        logger.info(
            f"工具已注册: {tool_name} "
            f"(visibility={visibility.value}, category={category.value}, version={version})"
        )

    def unregister(self, tool_name: str) -> bool:
        """注销工具"""
        if tool_name not in self._tools:
            return False

        info = self._tools[tool_name]

        # 从各索引中移除
        if tool_name in self._categories.get(info.category, []):
            self._categories[info.category].remove(tool_name)

        if tool_name in self._visibility_index.get(info.visibility, []):
            self._visibility_index[info.visibility].remove(tool_name)

        for tag in info.industry_tags:
            if tool_name in self._industry_index.get(tag, []):
                self._industry_index[tag].remove(tool_name)

        if info.tenant_id:
            if tool_name in self._tenant_index.get(info.tenant_id, []):
                self._tenant_index[info.tenant_id].remove(tool_name)

        del self._tools[tool_name]
        logger.info(f"工具已注销: {tool_name}")
        return True

    # ─── 版本管理 ───

    def _make_version_snapshot(
        self,
        tool: "MCPTool",
        version: str,
        changelog: str = "",
    ) -> Dict[str, Any]:
        """生成工具版本快照"""
        return {
            "version": version,
            "name": tool.name,
            "description": getattr(tool, "description", ""),
            "input_schema": getattr(tool, "input_schema", {}),
            "output_schema": getattr(tool, "output_schema", {}),
            "released_at": datetime.now().isoformat(),
            "changelog": changelog,
        }

    def publish_version(
        self,
        tool_name: str,
        version: str,
        changelog: str = "",
        **changes,
    ) -> bool:
        """发布工具新版本

        Args:
            tool_name: 工具名
            version: 新版本号
            changelog: 变更说明
            **changes: 可选更新项 (name/description/input_schema/output_schema)

        Returns:
            是否成功
        """
        info = self._tools.get(tool_name)
        if not info:
            return False

        current = info.tool
        updates = {}
        for key, value in changes.items():
            if hasattr(current, key):
                updates[key] = value

        # 基于当前工具创建新实例
        if hasattr(current, "model_copy"):
            new_tool = current.model_copy(update=updates)
        else:
            new_tool = current
        info.tool = new_tool
        info.version = version

        # 追加版本历史
        info.marketplace.versions.append(
            self._make_version_snapshot(new_tool, version, changelog)
        )
        info.marketplace.updated_at = datetime.now()

        logger.info(f"工具版本已发布: {tool_name} (version={version})")
        return True

    def get_versions(self, tool_name: str) -> List[Dict[str, Any]]:
        """获取工具版本历史"""
        info = self._tools.get(tool_name)
        return list(info.marketplace.versions) if info else []

    def set_active_version(self, tool_name: str, version: str) -> bool:
        """切换工具激活版本"""
        info = self._tools.get(tool_name)
        if not info:
            return False

        snapshot = None
        for v in info.marketplace.versions:
            if v.get("version") == version:
                snapshot = v
                break
        if not snapshot:
            return False

        info.tool = self._rebuild_tool(snapshot)
        info.version = snapshot["version"]
        info.marketplace.updated_at = datetime.now()
        logger.info(f"工具激活版本已切换: {tool_name} (version={version})")
        return True

    def _rebuild_tool(self, snapshot: Dict[str, Any]) -> "MCPTool":
        """从版本快照重建工具实例（延迟导入避免循环依赖）"""
        from jkos_core.mcp.server import MCPTool  # 惰性导入

        return MCPTool(
            name=snapshot["name"],
            description=snapshot.get("description", ""),
            input_schema=snapshot.get("input_schema", {}),
            output_schema=snapshot.get("output_schema", {}),
        )

    # ─── 查询 ───

    def get(self, tool_name: str) -> Optional["MCPTool"]:
        """获取工具"""
        info = self._tools.get(tool_name)
        return info.tool if info else None

    def get_info(self, tool_name: str) -> Optional[ToolInfo]:
        """获取工具信息"""
        return self._tools.get(tool_name)

    def list_visible(
        self,
        tenant_id: Optional[str] = None,
        industry: Optional[str] = None,
        category: Optional[ToolCategory] = None,
    ) -> List["MCPTool"]:
        """列出可见工具

        Args:
            tenant_id: 租户ID（用于过滤自定义工具）
            industry: 行业标签
            category: 分类

        Returns:
            可见工具列表
        """
        result = []

        for tool_name, info in self._tools.items():
            # 可见域过滤
            if info.visibility == ToolVisibility.CUSTOM:
                if tenant_id and info.tenant_id != tenant_id:
                    continue
            elif info.visibility == ToolVisibility.INDUSTRY:
                if industry and industry not in info.industry_tags:
                    continue

            # 行业标签过滤
            if industry and industry not in info.industry_tags:
                continue

            # 分类过滤
            if category and info.category != category:
                continue

            result.append(info.tool)

        return result

    def list_by_category(self, category: ToolCategory) -> List["MCPTool"]:
        """按分类列出工具"""
        return [
            self._tools[tool_name].tool
            for tool_name in self._categories.get(category, [])
            if tool_name in self._tools
        ]

    def list_by_visibility(self, visibility: ToolVisibility) -> List["MCPTool"]:
        """按可见域列出工具"""
        return [
            self._tools[tool_name].tool
            for tool_name in self._visibility_index.get(visibility, [])
            if tool_name in self._tools
        ]

    def list_by_industry(self, industry_tag: str) -> List["MCPTool"]:
        """按行业标签列出工具"""
        return [
            self._tools[tool_name].tool
            for tool_name in self._industry_index.get(industry_tag, [])
            if tool_name in self._tools
        ]

    def list_by_tenant(self, tenant_id: str) -> List["MCPTool"]:
        """列出租户的自定义工具"""
        return [
            self._tools[tool_name].tool
            for tool_name in self._tenant_index.get(tenant_id, [])
            if tool_name in self._tools
        ]

    def all(self) -> Dict[str, "MCPTool"]:
        """所有工具"""
        return {tool_name: info.tool for tool_name, info in self._tools.items()}

    def list_ids(self) -> List[str]:
        """列出所有工具名"""
        return list(self._tools.keys())

    def count(self) -> int:
        """工具数量"""
        return len(self._tools)

    def search(self, query: str) -> List["MCPTool"]:
        """搜索工具"""
        query = query.lower()
        result = []

        for info in self._tools.values():
            if (
                query in info.tool.name.lower()
                or query in info.tool.description.lower()
                or any(query in tag.lower() for tag in info.marketplace.tags)
            ):
                result.append(info.tool)

        return result

    # ─── 市场元数据 ───

    def get_marketplace_info(self, tool_name: str) -> Optional[ToolMarketplaceMetadata]:
        """获取工具市场信息"""
        info = self._tools.get(tool_name)
        return info.marketplace if info else None

    def update_marketplace_info(
        self,
        tool_name: str,
        **kwargs,
    ) -> bool:
        """更新工具市场信息"""
        info = self._tools.get(tool_name)
        if not info:
            return False

        for key, value in kwargs.items():
            if hasattr(info.marketplace, key):
                setattr(info.marketplace, key, value)

        info.marketplace.updated_at = datetime.now()
        return True

    def increment_downloads(self, tool_name: str) -> bool:
        """增加下载次数"""
        info = self._tools.get(tool_name)
        if not info:
            return False

        info.marketplace.downloads += 1
        return True

    def add_rating(self, tool_name: str, rating: float) -> bool:
        """添加评分"""
        if not 1 <= rating <= 5:
            return False

        info = self._tools.get(tool_name)
        if not info:
            return False

        # 更新平均评分
        total = info.marketplace.rating * info.marketplace.rating_count + rating
        info.marketplace.rating_count += 1
        info.marketplace.rating = round(total / info.marketplace.rating_count, 2)

        return True

    def get_featured(self) -> List["MCPTool"]:
        """获取推荐工具"""
        return [
            info.tool
            for info in self._tools.values()
            if info.marketplace.featured
        ]

    def get_verified(self) -> List["MCPTool"]:
        """获取已验证工具"""
        return [
            info.tool
            for info in self._tools.values()
            if info.marketplace.verified
        ]

    def get_categories(self) -> List[str]:
        """获取所有分类"""
        return [cat.value for cat in ToolCategory]

    def get_industry_tags(self) -> List[str]:
        """获取所有行业标签"""
        return list(self._industry_index.keys())


# ─── 全局注册表实例 ───

_global_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    """获取全局注册表"""
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
    return _global_registry


def set_registry(registry: ToolRegistry) -> None:
    """设置全局注册表"""
    global _global_registry
    _global_registry = registry