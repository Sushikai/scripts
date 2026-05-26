"""
collector - 素材采集模块
包含：ADB控制器、OCR处理器、采集器核心
"""

from .collector_core import create_collector, BaseCollector, DouyinCollector, BilibiliCollector, XiaohongshuCollector
from .adb_controller import ADBController, ADBError
from .ocr_processor import OCRProcessor, BaiduOCRClient

__all__ = [
    "create_collector",
    "BaseCollector",
    "DouyinCollector",
    "BilibiliCollector",
    "XiaohongshuCollector",
    "ADBController",
    "ADBError",
    "OCRProcessor",
    "BaiduOCRClient",
]
