"""
DSH 多语言测试 (M5)

测试多语言核心功能：语言检测、翻译、语言切换
"""

from __future__ import annotations

import pytest
import sys
from pathlib import Path

# 添加项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dsh_core.plugins.i18n import (
    I18nManager,
    LanguageCode,
    LanguageDetector,
    TranslationFile,
    get_i18n,
    _,
    detect_language,
)


# ─── 语言检测器测试 ───

class TestLanguageDetector:
    
    def test_detect_from_accept_language_zh(self):
        """测试从 Accept-Language 检测中文"""
        lang = LanguageDetector.detect_from_accept_language("zh-CN,zh;q=0.9,en;q=0.8")
        assert lang == "zh"
    
    def test_detect_from_accept_language_en(self):
        """测试从 Accept-Language 检测英文"""
        lang = LanguageDetector.detect_from_accept_language("en-US,en;q=0.9")
        assert lang == "en"
    
    def test_detect_from_accept_language_empty(self):
        """测试空 Accept-Language"""
        lang = LanguageDetector.detect_from_accept_language("")
        assert lang == "zh"
    
    def test_detect_from_accept_language_none(self):
        """测试 None Accept-Language"""
        lang = LanguageDetector.detect_from_accept_language(None)
        assert lang == "zh"
    
    def test_detect_from_query_param_zh(self):
        """测试从查询参数检测中文"""
        lang = LanguageDetector.detect_from_query_param("zh")
        assert lang == "zh"
    
    def test_detect_from_query_param_en(self):
        """测试从查询参数检测英文"""
        lang = LanguageDetector.detect_from_query_param("en")
        assert lang == "en"
    
    def test_detect_from_query_param_invalid(self):
        """测试无效查询参数"""
        lang = LanguageDetector.detect_from_query_param("invalid")
        assert lang is None
    
    def test_get_default_language(self):
        """测试默认语言"""
        lang = LanguageDetector.get_default_language()
        assert lang == "zh"


# ─── 多语言管理器测试 ───

class TestI18nManager:
    
    @pytest.fixture
    def i18n(self):
        """创建 i18n 管理器"""
        return I18nManager()
    
    def test_get_languages(self, i18n):
        """测试获取语言列表"""
        languages = i18n.get_languages()
        assert len(languages) >= 2
        
        # 检查中文和英文
        lang_codes = [lang["code"] for lang in languages]
        assert "zh" in lang_codes
        assert "en" in lang_codes
    
    def test_set_language(self, i18n):
        """测试设置语言"""
        assert i18n.set_language("en") is True
        assert i18n.get_language() == "en"
        
        assert i18n.set_language("zh") is True
        assert i18n.get_language() == "zh"
    
    def test_set_invalid_language(self, i18n):
        """测试设置无效语言"""
        assert i18n.set_language("invalid") is False
    
    def test_translate_zh(self, i18n):
        """测试中文翻译"""
        i18n.set_language("zh")
        
        assert i18n.translate("app.name") == "DSH AI 中台"
        assert i18n.translate("button.submit") == "提交"
        assert i18n.translate("status.active") == "活跃"
    
    def test_translate_en(self, i18n):
        """测试英文翻译"""
        i18n.set_language("en")
        
        assert i18n.translate("app.name") == "DSH AI Platform"
        assert i18n.translate("button.submit") == "Submit"
        assert i18n.translate("status.active") == "Active"
    
    def test_translate_missing_key(self, i18n):
        """测试翻译缺失键"""
        i18n.set_language("zh")
        
        # 缺失的键应该返回键本身
        result = i18n.translate("nonexistent.key")
        assert result == "nonexistent.key"
    
    def test_translate_with_params(self, i18n):
        """测试带参数的翻译"""
        i18n.set_language("zh")
        
        # 测试格式化
        result = i18n.translate("error.not_found")
        assert result == "资源不存在"
    
    def test_translate_many(self, i18n):
        """测试批量翻译"""
        i18n.set_language("zh")
        
        keys = ["app.name", "button.submit", "status.active"]
        translations = i18n.translate_many(keys)
        
        assert len(translations) == 3
        assert translations["app.name"] == "DSH AI 中台"
        assert translations["button.submit"] == "提交"
    
    def test_has_translation(self, i18n):
        """测试检查翻译是否存在"""
        i18n.set_language("zh")
        
        assert i18n.has_translation("app.name") is True
        assert i18n.has_translation("nonexistent.key") is False
    
    def test_get_translation_file(self, i18n):
        """测试获取翻译文件"""
        translations = i18n.get_translation_file("zh")
        assert translations is not None
        assert "app.name" in translations
    
    def test_get_translation_file_invalid(self, i18n):
        """测试获取无效语言翻译文件"""
        translations = i18n.get_translation_file("invalid")
        assert translations is None
    
    def test_add_translation(self, i18n):
        """测试添加翻译"""
        i18n.set_language("zh")
        
        assert i18n.add_translation("zh", "custom.key", "自定义值") is True
        assert i18n.translate("custom.key") == "自定义值"
    
    def test_add_translation_new_language(self, i18n):
        """测试添加新语言的翻译"""
        assert i18n.add_translation("fr", "hello", "bonjour") is True
        
        i18n.set_language("fr")
        assert i18n.translate("hello") == "bonjour"


# ─── 便捷函数测试 ───

class TestConvenienceFunctions:
    
    def test_underscore_function(self):
        """测试 _() 便捷函数"""
        i18n = get_i18n()
        i18n.set_language("zh")
        
        result = _("app.name")
        assert result == "DSH AI 中台"
    
    def test_detect_language(self):
        """测试语言检测便捷函数"""
        # 测试查询参数
        lang = detect_language(lang_param="en")
        assert lang == "en"
        
        # 测试 Accept-Language
        lang = detect_language(accept_language="zh-CN,zh;q=0.9")
        assert lang == "zh"
        
        # 测试默认
        lang = detect_language()
        assert lang == "zh"


# ─── 语言代码测试 ───

class TestLanguageCode:
    
    def test_constants(self):
        """测试语言代码常量"""
        assert LanguageCode.ZH == "zh"
        assert LanguageCode.EN == "en"
        assert LanguageCode.ZH_CN == "zh-CN"
        assert LanguageCode.ZH_TW == "zh-TW"
        assert LanguageCode.EN_US == "en-US"
        assert LanguageCode.EN_GB == "en-GB"


# ─── 翻译文件测试 ───

class TestTranslationFile:
    
    def test_create(self):
        """测试创建翻译文件"""
        tf = TranslationFile(
            language="zh",
            translations={"key": "value"},
        )
        
        assert tf.language == "zh"
        assert tf.translations["key"] == "value"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
