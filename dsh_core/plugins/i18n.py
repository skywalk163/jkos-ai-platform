"""
DSH Core - 多语言支持 (i18n)

提供语言检测、翻译加载、翻译函数等功能
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dsh.plugins.i18n")


# �── 语言代码 ───

class LanguageCode:
    """语言代码常量"""
    ZH = "zh"      # 中文
    EN = "en"      # 英文
    ZH_CN = "zh-CN"  # 简体中文
    ZH_TW = "zh-TW"  # 繁体中文
    EN_US = "en-US"  # 美式英语
    EN_GB = "en-GB"  # 英式英语


# ─── 翻译文件结构 ───

@dataclass
class TranslationFile:
    """翻译文件"""
    language: str
    translations: Dict[str, str]
    metadata: Dict[str, Any] = field(default_factory=dict)
    loaded_at: datetime = field(default_factory=datetime.now)


# ─── 语言检测器 ───

class LanguageDetector:
    """语言检测器"""
    
    # 语言优先级映射
    LANGUAGE_PRIORITY = {
        LanguageCode.ZH: 100,
        LanguageCode.ZH_CN: 100,
        LanguageCode.ZH_TW: 90,
        LanguageCode.EN: 80,
        LanguageCode.EN_US: 80,
        LanguageCode.EN_GB: 70,
    }
    
    @classmethod
    def detect_from_accept_language(cls, accept_language: str) -> str:
        """从 Accept-Language 头检测语言
        
        Args:
            accept_language: Accept-Language 头值
            
        Returns:
            检测到的语言代码
        """
        if not accept_language:
            return LanguageCode.ZH
        
        # 解析 Accept-Language 头
        languages = []
        for part in accept_language.split(","):
            part = part.strip()
            if ";" in part:
                lang, q = part.split(";", 1)
                q = q.replace("q=", "").strip()
                try:
                    quality = float(q)
                except ValueError:
                    quality = 1.0
            else:
                lang = part
                quality = 1.0
            languages.append((lang.strip().lower(), quality))
        
        # 按质量排序
        languages.sort(key=lambda x: -x[1])
        
        # 返回第一个支持的语言
        for lang, _ in languages:
            if lang in cls.LANGUAGE_PRIORITY:
                return lang
            # 检查语言前缀
            for supported in cls.LANGUAGE_PRIORITY:
                if lang.startswith(supported[:2]):
                    return supported
        
        return LanguageCode.ZH
    
    @classmethod
    def detect_from_query_param(cls, lang_param: Optional[str]) -> Optional[str]:
        """从查询参数检测语言
        
        Args:
            lang_param: lang 查询参数值
            
        Returns:
            语言代码，如果参数无效则返回 None
        """
        if not lang_param:
            return None
        
        lang = lang_param.lower().strip()
        if lang in cls.LANGUAGE_PRIORITY:
            return lang
        
        # 检查前缀
        for supported in cls.LANGUAGE_PRIORITY:
            if lang.startswith(supported[:2]):
                return supported
        
        return None
    
    @classmethod
    def get_default_language(cls) -> str:
        """获取默认语言"""
        return LanguageCode.ZH


# ─── 多语言管理器 ───

class I18nManager:
    """多语言管理器
    
    提供:
    - 翻译文件加载
    - 语言切换
    - 翻译函数
    """
    
    def __init__(
        self,
        locales_dir: Optional[str] = None,
        default_language: str = LanguageCode.ZH,
    ):
        self.locales_dir = Path(locales_dir) if locales_dir else Path("./locales")
        self.default_language = default_language
        self._translations: Dict[str, TranslationFile] = {}
        self._current_language: str = default_language
        
        # 加载默认翻译
        self._load_default_translations()
    
    def _load_default_translations(self) -> None:
        """加载默认翻译"""
        # 加载内置翻译
        self._load_builtin_translations()
        
        # 加载自定义翻译文件
        self.load_from_directory(self.locales_dir)
    
    def _load_builtin_translations(self) -> None:
        """加载内置翻译"""
        # 中文翻译
        zh_translations = {
            # 通用
            "app.name": "DSH AI 中台",
            "app.description": "企业级多模态 AI Agent 中台",
            
            # 通用按钮
            "button.submit": "提交",
            "button.cancel": "取消",
            "button.save": "保存",
            "button.delete": "删除",
            "button.edit": "编辑",
            "button.search": "搜索",
            "button.filter": "筛选",
            "button.reset": "重置",
            "button.confirm": "确认",
            "button.back": "返回",
            "button.next": "下一步",
            "button.prev": "上一步",
            
            # 通用标签
            "label.name": "名称",
            "label.description": "描述",
            "label.status": "状态",
            "label.created_at": "创建时间",
            "label.updated_at": "更新时间",
            "label.search": "搜索",
            "label.filter": "筛选",
            "label.language": "语言",
            
            # 状态
            "status.active": "活跃",
            "status.inactive": "未活跃",
            "status.pending": "待处理",
            "status.running": "运行中",
            "status.completed": "已完成",
            "status.failed": "失败",
            "status.cancelled": "已取消",
            
            # 插件相关
            "plugin.title": "插件",
            "plugin.marketplace": "插件市场",
            "plugin.install": "安装插件",
            "plugin.uninstall": "卸载插件",
            "plugin.enable": "启用插件",
            "plugin.disable": "禁用插件",
            "plugin.config": "插件配置",
            
            # 工作流相关
            "workflow.title": "工作流",
            "workflow.run": "运行工作流",
            "workflow.history": "历史记录",
            "workflow.instances": "实例列表",
            
            # 错误消息
            "error.unknown": "未知错误",
            "error.not_found": "资源不存在",
            "error.unauthorized": "未授权",
            "error.forbidden": "禁止访问",
            "error.invalid_param": "参数无效",
            "error.internal": "服务器内部错误",
            "error.timeout": "请求超时",
            "error.rate_limit": "请求频率超限",
            
            # 认证相关
            "auth.login": "登录",
            "auth.logout": "退出",
            "auth.register": "注册",
            "auth.email": "邮箱",
            "auth.password": "密码",
            "auth.confirm_password": "确认密码",
            
            # 租户相关
            "tenant.title": "租户",
            "tenant.switch": "切换租户",
            "tenant.create": "创建租户",
            
            # 通知相关
            "notification.title": "通知",
            "notification.mark_read": "标记已读",
            "notification.clear": "清空通知",
        }
        
        self._translations[LanguageCode.ZH] = TranslationFile(
            language=LanguageCode.ZH,
            translations=zh_translations,
            metadata={"name": "简体中文", "native_name": "简体中文"},
        )
        
        # 英文翻译
        en_translations = {
            # General
            "app.name": "DSH AI Platform",
            "app.description": "Enterprise-grade Multi-modal AI Agent Platform",
            
            # Buttons
            "button.submit": "Submit",
            "button.cancel": "Cancel",
            "button.save": "Save",
            "button.delete": "Delete",
            "button.edit": "Edit",
            "button.search": "Search",
            "button.filter": "Filter",
            "button.reset": "Reset",
            "button.confirm": "Confirm",
            "button.back": "Back",
            "button.next": "Next",
            "button.prev": "Previous",
            
            # Labels
            "label.name": "Name",
            "label.description": "Description",
            "label.status": "Status",
            "label.created_at": "Created At",
            "label.updated_at": "Updated At",
            "label.search": "Search",
            "label.filter": "Filter",
            "label.language": "Language",
            
            # Status
            "status.active": "Active",
            "status.inactive": "Inactive",
            "status.pending": "Pending",
            "status.running": "Running",
            "status.completed": "Completed",
            "status.failed": "Failed",
            "status.cancelled": "Cancelled",
            
            # Plugin
            "plugin.title": "Plugins",
            "plugin.marketplace": "Plugin Marketplace",
            "plugin.install": "Install Plugin",
            "plugin.uninstall": "Uninstall Plugin",
            "plugin.enable": "Enable Plugin",
            "plugin.disable": "Disable Plugin",
            "plugin.config": "Plugin Config",
            
            # Workflow
            "workflow.title": "Workflows",
            "workflow.run": "Run Workflow",
            "workflow.history": "History",
            "workflow.instances": "Instances",
            
            # Error messages
            "error.unknown": "Unknown error",
            "error.not_found": "Resource not found",
            "error.unauthorized": "Unauthorized",
            "error.forbidden": "Forbidden",
            "error.invalid_param": "Invalid parameter",
            "error.internal": "Internal server error",
            "error.timeout": "Request timeout",
            "error.rate_limit": "Rate limit exceeded",
            
            # Auth
            "auth.login": "Login",
            "auth.logout": "Logout",
            "auth.register": "Register",
            "auth.email": "Email",
            "auth.password": "Password",
            "auth.confirm_password": "Confirm Password",
            
            # Tenant
            "tenant.title": "Tenant",
            "tenant.switch": "Switch Tenant",
            "tenant.create": "Create Tenant",
            
            # Notification
            "notification.title": "Notifications",
            "notification.mark_read": "Mark as Read",
            "notification.clear": "Clear All",
        }
        
        self._translations[LanguageCode.EN] = TranslationFile(
            language=LanguageCode.EN,
            translations=en_translations,
            metadata={"name": "English", "native_name": "English"},
        )
    
    def load_from_directory(self, directory: Path) -> int:
        """从目录加载翻译文件
        
        Args:
            directory: 翻译文件目录
            
        Returns:
            加载的翻译文件数量
        """
        if not directory.exists():
            return 0
        
        count = 0
        for lang_dir in directory.iterdir():
            if not lang_dir.is_dir():
                continue
            
            lang = lang_dir.name
            for json_file in lang_dir.glob("*.json"):
                try:
                    translations = json.loads(json_file.read_text(encoding="utf-8"))
                    
                    if lang in self._translations:
                        # 合并翻译
                        self._translations[lang].translations.update(translations)
                    else:
                        self._translations[lang] = TranslationFile(
                            language=lang,
                            translations=translations,
                        )
                    
                    count += 1
                    logger.info(f"加载翻译文件: {json_file}")
                except Exception as e:
                    logger.error(f"加载翻译文件失败 {json_file}: {e}")
        
        return count
    
    def set_language(self, language: str) -> bool:
        """设置当前语言
        
        Args:
            language: 语言代码
            
        Returns:
            是否设置成功
        """
        if language not in self._translations:
            # 尝试匹配前缀
            for lang in self._translations:
                if language.startswith(lang[:2]):
                    self._current_language = lang
                    return True
            return False
        
        self._current_language = language
        return True
    
    def get_language(self) -> str:
        """获取当前语言"""
        return self._current_language
    
    def get_languages(self) -> List[Dict[str, str]]:
        """获取可用语言列表"""
        return [
            {
                "code": lang,
                "name": trans.metadata.get("name", lang),
                "native_name": trans.metadata.get("native_name", lang),
            }
            for lang, trans in self._translations.items()
        ]
    
    def translate(self, key: str, **kwargs) -> str:
        """翻译文本
        
        Args:
            key: 翻译键
            **kwargs: 格式化参数
            
        Returns:
            翻译后的文本
        """
        # 获取当前语言的翻译
        trans = self._translations.get(self._current_language)
        if not trans:
            trans = self._translations.get(self.default_language)
        
        if not trans:
            return key
        
        # 获取翻译文本
        text = trans.translations.get(key, key)
        
        # 格式化
        if kwargs:
            try:
                text = text.format(**kwargs)
            except (KeyError, ValueError):
                pass
        
        return text
    
    def translate_many(self, keys: List[str]) -> Dict[str, str]:
        """批量翻译
        
        Args:
            keys: 翻译键列表
            
        Returns:
            翻译结果字典
        """
        return {key: self.translate(key) for key in keys}
    
    def has_translation(self, key: str) -> bool:
        """检查是否存在翻译"""
        trans = self._translations.get(self._current_language)
        if not trans:
            return False
        return key in trans.translations
    
    def get_translation_file(self, language: str) -> Optional[Dict[str, str]]:
        """获取翻译文件内容"""
        trans = self._translations.get(language)
        if not trans:
            return None
        return trans.translations
    
    def add_translation(self, language: str, key: str, value: str) -> bool:
        """添加翻译
        
        Args:
            language: 语言代码
            key: 翻译键
            value: 翻译值
            
        Returns:
            是否添加成功
        """
        if language not in self._translations:
            self._translations[language] = TranslationFile(
                language=language,
                translations={},
            )
        
        self._translations[language].translations[key] = value
        return True
    
    def save_translation_file(self, language: str, directory: Optional[Path] = None) -> bool:
        """保存翻译文件
        
        Args:
            language: 语言代码
            directory: 保存目录
            
        Returns:
            是否保存成功
        """
        trans = self._translations.get(language)
        if not trans:
            return False
        
        save_dir = directory or self.locales_dir / language
        save_dir.mkdir(parents=True, exist_ok=True)
        
        # 保存为 JSON 文件
        file_path = save_dir / "common.json"
        file_path.write_text(
            json.dumps(trans.translations, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        
        return True


# ─── 全局管理器 ───

_global_i18n: Optional[I18nManager] = None


def get_i18n() -> I18nManager:
    """获取全局 i18n 管理器"""
    global _global_i18n
    if _global_i18n is None:
        _global_i18n = I18nManager()
    return _global_i18n


def set_i18n(manager: I18nManager) -> None:
    """设置全局 i18n 管理器"""
    global _global_i18n
    _global_i18n = manager


# ─── 便捷函数 ───

def _(key: str, **kwargs) -> str:
    """翻译便捷函数
    
    Usage:
        from dsh_core.plugins.i18n import _
        
        print(_("app.name"))  # DSH AI 中台
        print(_("button.submit"))  # 提交
        print(_("error.not_found"))  # 资源不存在
    """
    return get_i18n().translate(key, **kwargs)


def detect_language(
    accept_language: Optional[str] = None,
    lang_param: Optional[str] = None,
) -> str:
    """检测语言
    
    Args:
        accept_language: Accept-Language 头
        lang_param: lang 查询参数
        
    Returns:
        检测到的语言代码
    """
    # 查询参数优先级最高
    if lang_param:
        detected = LanguageDetector.detect_from_query_param(lang_param)
        if detected:
            return detected
    
    # 其次 Accept-Language 头
    if accept_language:
        return LanguageDetector.detect_from_accept_language(accept_language)
    
    # 默认
    return LanguageDetector.get_default_language()
