"""OCR 插件测试"""

import pytest
import base64
from unittest.mock import Mock, patch

from jkos_core.plugins import PluginConfig, PluginContext
from jkos_core.plugins.ocr.plugin import OCRPlugin, OCRConfig


class TestOCRPlugin:
    """OCR 插件测试"""

    @pytest.fixture
    def plugin_config(self):
        """OCR 插件配置"""
        return PluginConfig(
            enabled=True,
            config={
                "lang": "ch",
                "use_gpu": False,
            },
        )

    @pytest.fixture
    def plugin(self, plugin_config):
        """OCR 插件实例"""
        return OCRPlugin(plugin_config)

    @pytest.fixture
    def context(self):
        """插件上下文"""
        return PluginContext(
            request_id="test-ocr-001",
            user_id="test-user",
        )

    def test_metadata(self, plugin):
        """测试插件元数据"""
        metadata = plugin.metadata
        assert metadata.id == "dsh.multimodal.image.ocr"
        assert metadata.version == "1.0.0"
        assert "OCR" in metadata.name
        assert metadata.plugin_type.value == "deterministic"  # DeterministicPlugin base sets this

    def test_config_validation(self, plugin):
        """测试配置验证"""
        # 默认配置应该有效
        assert plugin._config.lang == "ch"

    @pytest.mark.asyncio
    async def test_health_check_not_initialized(self, plugin):
        """测试未初始化时的健康检查"""
        assert not await plugin.health_check()

    @pytest.mark.asyncio
    async def test_parse_input_bytes(self, plugin):
        """测试解析 bytes 输入"""
        test_data = b"fake image data"
        result = await plugin._parse_input(test_data)
        assert result == test_data

    @pytest.mark.asyncio
    async def test_parse_input_base64(self, plugin):
        """测试解析 base64 输入"""
        test_data = b"fake image data"
        b64_data = base64.b64encode(test_data).decode()
        result = await plugin._parse_input({"type": "base64", "data": b64_data})
        assert result == test_data

    @pytest.mark.asyncio
    async def test_parse_input_file(self, plugin, tmp_path):
        """测试解析文件输入"""
        test_data = b"fake image data"
        file_path = tmp_path / "test.png"
        file_path.write_bytes(test_data)

        result = await plugin._parse_input({"type": "file", "path": str(file_path)})
        assert result == test_data

    @pytest.mark.asyncio
    async def test_parse_input_invalid(self, plugin):
        """测试解析无效输入"""
        result = await plugin._parse_input({"type": "unknown", "data": "xxx"})
        assert result is None

    @pytest.mark.asyncio
    async def test_execute_invalid_input(self, plugin, context):
        """测试执行无效输入"""
        result = await plugin.execute({"type": "invalid"}, context)
        assert not result.success
        assert result.error_code == "INVALID_INPUT"

    def test_ocr_config_to_dict(self):
        """测试 OCR 配置转字典"""
        config = OCRConfig(lang="en", use_gpu=False)
        assert config.lang == "en"
        assert not config.use_gpu


class TestOCRResult:
    """OCR 结果测试"""

    def test_result_to_dict(self):
        """测试结果转字典"""
        from jkos_core.plugins.ocr.plugin import OCRResult

        result = OCRResult(
            text="测试文字",
            confidence=0.95,
            bbox=[[0, 0], [100, 0], [100, 50], [0, 50]],
        )
        data = result.to_dict()
        assert data["text"] == "测试文字"
        assert data["confidence"] == 0.95
        assert len(data["bbox"]) == 4


class TestOCRConvenience:
    """OCR 便捷函数测试"""

    @pytest.mark.asyncio
    async def test_ocr_image_invalid(self):
        """测试便捷函数处理无效数据"""
        with pytest.raises(Exception):
            from jkos_core.plugins.ocr.plugin import ocr_image
            await ocr_image(b"invalid data")
