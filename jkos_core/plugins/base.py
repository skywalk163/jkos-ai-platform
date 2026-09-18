"""DSH Core - 插件 SDK

所有 DSH 插件必须继承 PluginBase 并实现标准接口。
"""

from __future__ import annotations
import abc
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dsh.plugins")


# ─── 枚举定义 ───

class PluginCategory(str, Enum):
    """插件分类"""
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    INTERCONNECT = "interconnect"
    GOVERNANCE = "governance"


class PluginType(str, Enum):
    """插件类型"""
    DETERMINISTIC = "deterministic"
    AI_POWERED = "ai_powered"
    INTERCONNECT = "interconnect"
    GOVERNANCE = "governance"


class PluginStatus(str, Enum):
    """插件状态"""
    LOADING = "loading"
    ACTIVE = "active"
    ERROR = "error"
    DISABLED = "disabled"


# ─── 数据类 ───

@dataclass
class PluginMetadata:
    id: str
    version: str
    name: str
    description: str
    category: PluginCategory
    plugin_type: PluginType
    author: str = "DSH Team"
    license: str = "Apache-2.0"
    dependencies: List[str] = field(default_factory=list)
    config_schema: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class PluginConfig:
    enabled: bool = True
    config: Dict[str, Any] = field(default_factory=dict)
    rate_limit: int = 100
    timeout: int = 300
    retries: int = 3


@dataclass
class PluginContext:
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    start_time: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginResult:
    success: bool
    data: Any = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    duration_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "error_code": self.error_code,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


# ─── 插件基类 ───

class PluginBase(abc.ABC):
    def __init__(self, config: Optional[PluginConfig] = None):
        self.config = config or PluginConfig()
        self.status = PluginStatus.LOADING
        self._logger = logging.getLogger(f"dsh.plugins.{self.metadata.id}")

    @property
    @abc.abstractmethod
    def metadata(self) -> PluginMetadata:
        pass

    @abc.abstractmethod
    async def initialize(self) -> None:
        pass

    @abc.abstractmethod
    async def health_check(self) -> bool:
        pass

    @abc.abstractmethod
    async def execute(self, input_data: Any, context: PluginContext) -> PluginResult:
        pass

    async def cleanup(self) -> None:
        pass

    async def validate_config(self) -> bool:
        return True

    def get_config(self, key: str, default: Any = None) -> Any:
        return self.config.config.get(key, default)

    def set_config(self, key: str, value: Any) -> None:
        self.config.config[key] = value

    def _measure_duration(self, start: float) -> float:
        return round((time.time() - start) * 1000, 2)

    def _log_execution(self, context: PluginContext, result: PluginResult) -> None:
        self._logger.info(
            "plugin_exec", extra={
                "request_id": context.request_id,
                "plugin_id": self.metadata.id,
                "duration_ms": result.duration_ms,
                "success": result.success,
                "error_code": result.error_code,
            }
        )


class DeterministicPlugin(PluginBase):
    """确定性插件基类 - 无 AI 参与"""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id=self._get_plugin_id(),
            version=self._get_version(),
            name=self._get_name(),
            description=self._get_description(),
            category=self._get_category(),
            plugin_type=PluginType.DETERMINISTIC,
        )

    @abc.abstractmethod
    def _get_plugin_id(self) -> str: pass

    @abc.abstractmethod
    def _get_version(self) -> str: pass

    @abc.abstractmethod
    def _get_name(self) -> str: pass

    @abc.abstractmethod
    def _get_description(self) -> str: pass

    @abc.abstractmethod
    def _get_category(self) -> PluginCategory: pass


class AIPlugin(PluginBase):
    """AI 插件基类 - 大模型驱动"""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id=self._get_plugin_id(),
            version=self._get_version(),
            name=self._get_name(),
            description=self._get_description(),
            category=self._get_category(),
            plugin_type=PluginType.AI_POWERED,
        )

    @abc.abstractmethod
    def _get_plugin_id(self) -> str: pass

    @abc.abstractmethod
    def _get_version(self) -> str: pass

    @abc.abstractmethod
    def _get_name(self) -> str: pass

    @abc.abstractmethod
    def _get_description(self) -> str: pass

    @abc.abstractmethod
    def _get_category(self) -> PluginCategory: pass

    @abc.abstractmethod
    async def get_model_client(self) -> Any:
        pass


# ─── 插件注册表 ───

class PluginRegistry:
    def __init__(self):
        self._plugins: Dict[str, PluginBase] = {}
        self._categories: Dict[PluginCategory, List[str]] = {}

    def register(self, plugin: PluginBase) -> None:
        plugin_id = plugin.metadata.id
        if plugin_id in self._plugins:
            raise ValueError(f"插件 {plugin_id} 已注册")
        self._plugins[plugin_id] = plugin
        cat = plugin.metadata.category
        self._categories.setdefault(cat, []).append(plugin_id)
        logger.info(f"插件已注册: {plugin_id} ({cat.value})")

    def unregister(self, plugin_id: str) -> None:
        if plugin_id in self._plugins:
            plugin = self._plugins[plugin_id]
            cat = plugin.metadata.category
            if plugin_id in self._categories.get(cat, []):
                self._categories[cat].remove(plugin_id)
            del self._plugins[plugin_id]

    def get(self, plugin_id: str) -> Optional[PluginBase]:
        return self._plugins.get(plugin_id)

    def get_by_category(self, category: PluginCategory) -> List[PluginBase]:
        return [self._plugins[pid] for pid in self._categories.get(category, [])]

    def all(self) -> Dict[str, PluginBase]:
        return self._plugins.copy()

    def list_ids(self) -> List[str]:
        return list(self._plugins.keys())

    def count(self) -> int:
        return len(self._plugins)


# ─── 插件加载器 ───

class PluginLoader:
    def __init__(self, registry: PluginRegistry):
        self.registry = registry
        self._loaded_paths: Dict[str, str] = {}

    def load_from_directory(self, directory: str) -> int:
        import os
        from pathlib import Path
        dir_path = Path(directory)
        if not dir_path.exists():
            logger.warning(f"插件目录不存在: {directory}")
            return 0
        count = 0
        for plugin_dir in dir_path.iterdir():
            if plugin_dir.is_dir():
                try:
                    if self._load_plugin_from_dir(plugin_dir):
                        count += 1
                except Exception as e:
                    logger.error(f"加载插件失败 {plugin_dir}: {e}")
        return count

    def _load_plugin_from_dir(self, plugin_dir) -> bool:
        from pathlib import Path
        plugin_yaml = plugin_dir / "plugin.yaml"
        if not plugin_yaml.exists():
            return False
        logger.info(f"发现插件目录: {plugin_dir.name}")
        return True

    def unload(self, plugin_id: str) -> bool:
        plugin = self.registry.get(plugin_id)
        if plugin:
            import asyncio
            asyncio.run(plugin.cleanup())
            self.registry.unregister(plugin_id)
            return True
        return False
