"""DSH 插件模块

M5 功能：插件市场 + 多语言支持
"""

from jkos_core.plugins.base import (
    PluginBase,
    PluginCategory,
    PluginConfig,
    PluginContext,
    PluginMetadata,
    PluginRegistry,
    PluginResult,
    PluginStatus,
    PluginType,
    PluginLoader,
    DeterministicPlugin,
    AIPlugin,
)

from jkos_core.plugins.registry import (
    EnhancedPluginRegistry,
    VisibilityDomain,
    MarketplaceMetadata,
    PluginInfo,
    get_registry,
)

from jkos_core.plugins.marketplace import (
    PluginMarketplace,
    PluginPackage,
    PluginSource,
    get_marketplace,
)

from jkos_core.plugins.i18n import (
    I18nManager,
    LanguageCode,
    LanguageDetector,
    TranslationFile,
    get_i18n,
    _,
    detect_language,
)

__all__ = [
    # Base
    "PluginBase",
    "PluginCategory",
    "PluginConfig",
    "PluginContext",
    "PluginMetadata",
    "PluginRegistry",
    "PluginResult",
    "PluginStatus",
    "PluginType",
    "PluginLoader",
    "DeterministicPlugin",
    "AIPlugin",
    # Enhanced Registry
    "EnhancedPluginRegistry",
    "VisibilityDomain",
    "MarketplaceMetadata",
    "PluginInfo",
    "get_registry",
    # Marketplace
    "PluginMarketplace",
    "PluginPackage",
    "PluginSource",
    "get_marketplace",
    # i18n
    "I18nManager",
    "LanguageCode",
    "LanguageDetector",
    "TranslationFile",
    "get_i18n",
    "_",
    "detect_language",
]
