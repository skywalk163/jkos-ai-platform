"""
DSH Plugin - 图像 OCR 识别插件
确定性插件 - 使用 PaddleOCR 进行文字识别，无 AI 参与
"""

from __future__ import annotations
import base64
import io
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dsh_core.plugins.base import (
    PluginBase,
    PluginCategory,
    PluginConfig,
    PluginContext,
    PluginMetadata,
    PluginResult,
    DeterministicPlugin,
    PluginType,
)

logger = logging.getLogger("dsh.plugins.ocr")


# ─── 配置 ───

@dataclass
class OCRConfig:
    """OCR 配置"""
    use_gpu: bool = False
    lang: str = "ch"  # ch: 中文, en: 英文, ch_en: 中英混合
    det_db_thresh: float = 0.3
    det_db_unclip_ratio: float = 2.0
    rec_batch_num: int = 6
    drop_score: float = 0.5
    visualization: bool = False


# ─── OCR 结果 ───

@dataclass
class OCRResult:
    """OCR 识别结果"""
    text: str
    confidence: float
    bbox: List[List[float]]  # 4个角点坐标
    page_index: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "bbox": self.bbox,
            "page_index": self.page_index,
        }


# ─── OCR 插件 ───

class OCRPlugin(DeterministicPlugin):
    """
    图像 OCR 识别插件
    
    确定性插件 - 使用 PaddleOCR 进行文字识别
    无 AI 参与，纯工具调用，相同输入永远得到相同输出
    """

    def __init__(self, config: Optional[PluginConfig] = None):
        super().__init__(config)
        self._ocr_engine = None
        self._config = OCRConfig()
        self._load_config()

    def _load_config(self) -> None:
        """从插件配置加载 OCR 配置"""
        if self.config.config:
            cfg = self.config.config
            self._config = OCRConfig(
                use_gpu=cfg.get("use_gpu", False),
                lang=cfg.get("lang", "ch"),
                det_db_thresh=cfg.get("det_db_thresh", 0.3),
                det_db_unclip_ratio=cfg.get("det_db_unclip_ratio", 2.0),
                rec_batch_num=cfg.get("rec_batch_num", 6),
                drop_score=cfg.get("drop_score", 0.5),
            )

    def _get_plugin_id(self) -> str:
        return "dsh.multimodal.image.ocr"

    def _get_version(self) -> str:
        return "1.0.0"

    def _get_name(self) -> str:
        return "图像 OCR 识别"

    def _get_description(self) -> str:
        return "使用 PaddleOCR 从图片中提取文字，支持中英文混合识别"

    def _get_category(self) -> PluginCategory:
        return PluginCategory.IMAGE

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id=self._get_plugin_id(),
            version=self._get_version(),
            name=self._get_name(),
            description=self._get_description(),
            category=self._get_category(),
            plugin_type=PluginType.DETERMINISTIC,
            author="DSH Team",
            license="Apache-2.0",
            dependencies=["paddleocr>=2.7.0", "paddlepaddle>=2.6.0"],
            config_schema={
                "use_gpu": {"type": "boolean", "default": False},
                "lang": {"type": "string", "enum": ["ch", "en", "ch_en"], "default": "ch"},
                "det_db_thresh": {"type": "number", "default": 0.3},
                "det_db_unclip_ratio": {"type": "number", "default": 2.0},
            },
        )

    async def initialize(self) -> None:
        """初始化 OCR 引擎"""
        try:
            from paddleocr import PaddleOCR

            self._ocr_engine = PaddleOCR(
                use_gpu=self._config.use_gpu,
                lang=self._config.lang,
                det_db_thresh=self._config.det_db_thresh,
                det_db_unclip_ratio=self._config.det_db_unclip_ratio,
                rec_batch_num=self._config.rec_batch_num,
                drop_score=self._config.drop_score,
                show_log=False,
            )
            self.status = PluginStatus.ACTIVE
            logger.info(f"OCR 引擎初始化完成 (lang={self._config.lang})")
        except ImportError as e:
            self.status = PluginStatus.ERROR
            error_msg = f"PaddleOCR 未安装: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        except Exception as e:
            self.status = PluginStatus.ERROR
            error_msg = f"OCR 引擎初始化失败: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    async def health_check(self) -> bool:
        """健康检查"""
        if self._ocr_engine is None:
            return False
        try:
            # 简单测试：检查引擎是否可用
            return True
        except Exception:
            return False

    async def execute(
        self,
        input_data: Any,
        context: PluginContext,
    ) -> PluginResult:
        """
        执行 OCR 识别
        
        输入格式:
        - 文件路径: {"type": "file", "path": "/path/to/image.jpg"}
        - Base64: {"type": "base64", "data": "base64_string"}
        - URL: {"type": "url", "url": "http://..."}
        - 直接 bytes: b'...'
        
        输出格式:
        {
            "text": "识别出的完整文字",
            "lines": [
                {"text": "行文字", "confidence": 0.95, "bbox": [[x1,y1],...]},
                ...
            ],
            "page_count": 1,
            "language": "ch"
        }
        """
        import time
        start = time.time()

        try:
            # 解析输入
            image_data = await self._parse_input(input_data)
            if image_data is None:
                return PluginResult(
                    success=False,
                    error="无法解析输入数据",
                    error_code="INVALID_INPUT",
                    duration_ms=self._measure_duration(start),
                )

            # 执行 OCR
            results = self._ocr_engine.ocr(image_data, cls=True)

            # 处理结果
            all_text = []
            lines = []

            for page_idx, page_result in enumerate(results):
                if page_result is None:
                    continue
                for line in page_result:
                    if line and len(line) >= 2:
                        text = line[1][0] if isinstance(line[1], (list, tuple)) else str(line[1])
                        confidence = line[1][1] if isinstance(line[1], (list, tuple)) and len(line[1]) > 1 else 0.0
                        bbox = line[0] if len(line) > 0 else []

                        all_text.append(text)
                        lines.append({
                            "text": text,
                            "confidence": float(confidence),
                            "bbox": bbox,
                            "page_index": page_idx,
                        })

            full_text = "\n".join(all_text)

            return PluginResult(
                success=True,
                data={
                    "text": full_text,
                    "lines": lines,
                    "page_count": len(results) if results else 1,
                    "language": self._config.lang,
                    "total_lines": len(lines),
                },
                duration_ms=self._measure_duration(start),
                metadata={
                    "request_id": context.request_id,
                    "plugin_id": self.metadata.id,
                },
            )

        except Exception as e:
            logger.error(f"OCR 执行失败: {e}")
            return PluginResult(
                success=False,
                error=str(e),
                error_code="EXECUTION_ERROR",
                duration_ms=self._measure_duration(start),
            )

    async def _parse_input(self, input_data: Any) -> Optional[bytes]:
        """解析输入数据，返回图片 bytes"""
        if isinstance(input_data, bytes):
            return input_data

        if isinstance(input_data, str):
            # 可能是文件路径或 base64
            if os.path.exists(input_data):
                with open(input_data, "rb") as f:
                    return f.read()
            # 尝试 base64 解码
            try:
                return base64.b64decode(input_data)
            except Exception:
                pass

        if isinstance(input_data, dict):
            input_type = input_data.get("type")
            if input_type == "file":
                path = input_data.get("path")
                if path and os.path.exists(path):
                    with open(path, "rb") as f:
                        return f.read()
            elif input_type == "base64":
                data = input_data.get("data")
                if data:
                    return base64.b64decode(data)
            elif input_type == "url":
                import httpx
                url = input_data.get("url")
                if url:
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(url)
                        return resp.content

        return None

    async def cleanup(self) -> None:
        """清理"""
        self._ocr_engine = None
        self.status = PluginStatus.DISABLED
        logger.info("OCR 插件已清理")

    async def validate_config(self) -> bool:
        """验证配置"""
        if self._config.lang not in ["ch", "en", "ch_en"]:
            return False
        if not 0 <= self._config.det_db_thresh <= 1:
            return False
        return True


# ─── 便捷函数 ───

async def ocr_image(
    image_data: bytes,
    lang: str = "ch",
    config: Optional[OCRConfig] = None,
) -> Dict[str, Any]:
    """
    便捷函数：对图片进行 OCR 识别
    
    Args:
        image_data: 图片 bytes
        lang: 语言 (ch/en/ch_en)
        config: OCR 配置
    
    Returns:
        OCR 结果字典
    """
    plugin_config = PluginConfig(
        enabled=True,
        config={"lang": lang} if not config else {
            "use_gpu": config.use_gpu,
            "lang": config.lang,
            "det_db_thresh": config.det_db_thresh,
        },
    )

    plugin = OCRPlugin(plugin_config)
    await plugin.initialize()

    context = PluginContext()
    result = await plugin.execute(image_data, context)
    await plugin.cleanup()

    if result.success:
        return result.data
    else:
        raise RuntimeError(f"OCR 失败: {result.error}")


# ─── 测试入口 ───

def test_ocr():
    """测试 OCR 插件"""
    import asyncio

    async def _run():
        plugin = OCRPlugin()
        await plugin.initialize()

        # 创建一个简单的测试图片（实际使用时替换为真实图片）
        test_data = {"type": "file", "path": "test_image.png"}

        context = PluginContext()
        result = await plugin.execute(test_data, context)

        print(f"OCR 结果: {result.to_json()}")
        await plugin.cleanup()

    asyncio.run(_run())


if __name__ == "__main__":
    test_ocr()
