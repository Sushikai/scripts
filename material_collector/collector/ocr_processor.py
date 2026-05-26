#!/usr/bin/env python3
"""
OCR 处理器 - PaddleOCR 本地 + 百度 OCR 备用
支持：文字识别、区域裁剪、坐标映射
"""

from __future__ import annotations

import base64
import io
import time
import logging
from pathlib import Path
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)


# ============ PaddleOCR 初始化 ============

_paddle_ocr = None

def _get_paddle_ocr(use_gpu: bool = True, use_slim: bool = True, lang: str = "ch"):
    """延迟初始化 PaddleOCR（懒加载，避免启动慢）"""
    global _paddle_ocr
    if _paddle_ocr is None:
        try:
            from paddleocr import PaddleOCR
            _paddle_ocr = PaddleOCR(
                use_angle_cls=True,
                use_gpu=use_gpu,
                lang=lang,
                slim=use_slim,
                show_log=False,
            )
            logger.info("PaddleOCR 初始化成功")
            return _paddle_ocr
        except ImportError:
            logger.error("请安装 PaddleOCR: pip install paddlepaddle paddleocr")
            raise
    return _paddle_ocr


# ============ 百度 OCR 客户端 ============

class BaiduOCRClient:
    """百度 OCR API 客户端（需要配置 API Key）"""

    def __init__(self, api_key: str, secret_key: str, endpoint: str = None):
        self.api_key = api_key
        self.secret_key = secret_key
        self.endpoint = endpoint or "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic"
        self._token = None
        self._token_expires = 0

    def _get_token(self) -> str:
        """获取 access_token"""
        import requests
        if time.time() < self._token_expires:
            return self._token

        url = "https://aip.baidubce.com/oauth/2.0/token"
        params = {
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key,
        }
        resp = requests.post(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = time.time() + data.get("expires_in", 3600) - 60
        return self._token

    def recognize(self, image_data: bytes | Image.Image) -> list:
        """识别图片中的文字"""
        import requests

        if isinstance(image_data, Image.Image):
            buf = io.BytesIO()
            image_data.save(buf, format="JPEG")
            image_data = buf.getvalue()

        img_base64 = base64.b64encode(image_data).decode("utf-8")
        token = self._get_token()
        url = f"{self.endpoint}?access_token={token}"

        resp = requests.post(url, data={"image": img_base64}, timeout=15)
        resp.raise_for_status()
        result = resp.json()

        texts = []
        for item in result.get("words_result", []):
            texts.append({
                "text": item["words"],
                "confidence": item.get("probability", {}).get("average", 1.0),
                "location": item.get("location", {}),
            })
        return texts


# ============ OCR 处理器（统一接口）============

class OCRProcessor:
    """
    OCR 处理器 - 自动选择引擎
    优先级：PaddleOCR（本地）> 百度 OCR
    """

    def __init__(
        self,
        engine: str = "paddle",
        paddle_use_gpu: bool = True,
        paddle_lang: str = "ch",
        baidu_api_key: str = "",
        baidu_secret_key: str = "",
        min_text_height: int = 15,
        confidence_threshold: float = 0.6,
    ):
        self.engine = engine
        self.min_text_height = min_text_height
        self.confidence_threshold = confidence_threshold
        self._paddle_ocr = None
        self._baidu_client = None

        if engine == "paddle":
            self._paddle_ocr = _get_paddle_ocr(use_gpu=paddle_use_gpu, lang=paddle_lang)
        elif engine == "baidu":
            if not baidu_api_key:
                raise ValueError("百度 OCR 需要配置 API Key")
            self._baidu_client = BaiduOCRClient(baidu_api_key, baidu_secret_key)

    def recognize_image_file(self, image_path: str | Path) -> list[dict]:
        """
        识别本地图片文件
        Returns:
            [{"text": "文字", "confidence": 0.95, "bbox": [x1,y1,x2,y2]}, ...]
        """
        with Image.open(image_path) as img:
            return self.recognize_image(img)

    def recognize_image(self, image: Image.Image) -> list[dict]:
        """
        识别 PIL.Image 对象
        Returns:
            [{"text": "文字", "confidence": 0.95, "bbox": [x1,y1,x2,y2]}, ...]
        """
        if self.engine == "paddle":
            return self._recognize_paddle(image)
        elif self.engine == "baidu":
            return self._recognize_baidu(image)
        else:
            raise ValueError(f"Unknown OCR engine: {self.engine}")

    def _recognize_paddle(self, image: Image.Image) -> list[dict]:
        """PaddleOCR 识别"""
        try:
            result = self._paddle_ocr.ocr(image, cls=True)
            if not result or not result[0]:
                return []

            texts = []
            for line in result[0]:
                if not line:
                    continue
                box, (text, confidence) = line
                # 过滤低置信度
                if confidence < self.confidence_threshold:
                    continue
                # 过滤太高或太小的文字（通常是水印、按钮）
                if self._is_valid_text(box, image.height):
                    texts.append({
                        "text": text.strip(),
                        "confidence": float(confidence),
                        "bbox": [int(p) for p in box[0] + box[2]],  # [x1,y1,x2,y2]
                    })
            return texts

        except Exception as e:
            logger.error(f"PaddleOCR 识别失败: {e}")
            return []

    def _recognize_baidu(self, image: Image.Image) -> list[dict]:
        """百度 OCR 识别"""
        try:
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=85)
            results = self._baidu_client.recognize(buf.getvalue())

            texts = []
            for item in results:
                if item["confidence"] < self.confidence_threshold:
                    continue
                loc = item.get("location", {})
                bbox = [
                    loc.get("left", 0), loc.get("top", 0),
                    loc.get("left", 0) + loc.get("width", 0),
                    loc.get("top", 0) + loc.get("height", 0),
                ]
                if self._is_valid_text(bbox, image.height):
                    texts.append({
                        "text": item["text"].strip(),
                        "confidence": item["confidence"],
                        "bbox": bbox,
                    })
            return texts

        except Exception as e:
            logger.error(f"百度 OCR 识别失败: {e}")
            return []

    def _is_valid_text(self, bbox: list, image_height: int, ratio: float = 0.005) -> bool:
        """
        判断文字是否有效（过滤水印、按钮文字等）
        - 文字高度占画面比例 > 0.5%
        - 文字位置不在边缘（水印区）
        """
        if not bbox:
            return False
        x1, y1, x2, y2 = bbox
        h = y2 - y1

        # 高度过滤
        if h < self.min_text_height and h < image_height * ratio:
            return False

        # 位置过滤（底部水印区、顶部状态栏）
        y_ratio = y1 / image_height if image_height else 0
        if y_ratio > 0.95 or y_ratio < 0.02:
            return False

        return True

    def recognize_and_merge_lines(
        self,
        image: Image.Image,
        merge_gap: int = 10,
        merge_y_threshold: int = 20,
    ) -> list[str]:
        """
        识别并合并成行
        - 垂直方向相邻且接近的文字合并成一行
        """
        results = self.recognize_image(image)
        if not results:
            return []

        # 按 y 坐标排序
        sorted_results = sorted(results, key=lambda r: r["bbox"][1])

        lines = []
        current_line = []

        for item in sorted_results:
            if not current_line:
                current_line.append(item)
                continue

            last = current_line[-1]
            y_diff = abs(item["bbox"][1] - last["bbox"][1])
            # 同行使 x 接近或 y 接近
            if y_diff < merge_y_threshold:
                current_line.append(item)
            else:
                # 保存当前行
                merged = " ".join(r["text"] for r in sorted(current_line, key=lambda r: r["bbox"][0]))
                lines.append(merged)
                current_line = [item]

        if current_line:
            merged = " ".join(r["text"] for r in sorted(current_line, key=lambda r: r["bbox"][0]))
            lines.append(merged)

        return lines


# ---- 快速 OCR 函数 ----
_ocr_global = None

def quick_ocr(image_path: str | Path, engine: str = "paddle") -> list[str]:
    """一行函数：快速 OCR，返回文字列表"""
    global _ocr_global
    if _ocr_global is None:
        _ocr_global = OCRProcessor(engine=engine)
    processor = _ocr_global
    results = processor.recognize_image_file(image_path)
    return [r["text"] for r in results if r["text"].strip()]


# ---- 单元测试 ----
if __name__ == "__main__":
    import sys

    # 测试 OCR
    test_image = Path(__file__).parent.parent / "materials" / "raw" / ".gitkeep"
    if test_image.exists():
        results = quick_ocr(test_image)
        print(f"识别结果: {results}")
    else:
        print("无测试图片，跳过 OCR 测试")
        print("OCR 处理器模块已加载成功")