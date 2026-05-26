#!/usr/bin/env python3
"""
视频采集器核心模块
功能：控制模拟器自动搜索、滚动、截图、OCR 识别
平台：抖音 / B站 / 小红书
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image

from .adb_controller import ADBController, ADBError
from .ocr_processor import OCRProcessor

logger = logging.getLogger(__name__)


# ============ 数据模型 ============

@dataclass
class CollectedItem:
    """采集的数据项"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    platform: str = ""
    keyword: str = ""
    content_type: str = "subtitle"  # subtitle | comment | danmu | description
    raw_text: str = ""
    video_title: str = ""
    video_bvid: str = ""
    timestamp: str = ""
    source_url: str = ""
    ad_score: float = 0.0  # 广告分（越高越可能是广告）
    hash: str = ""  # 内容hash，用于去重

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
        if self.raw_text:
            self.hash = hashlib.md5(self.raw_text.encode()).hexdigest()


# ============ 平台采集器基类 ============

class BaseCollector:
    """采集器基类"""

    PLATFORM = "base"

    def __init__(
        self,
        adb: ADBController,
        ocr: OCRProcessor,
        output_dir: Path,
        max_per_keyword: int = 50,
    ):
        self.adb = adb
        self.ocr = ocr
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_per_keyword = max_per_keyword
        self._collected: dict[str, int] = {}  # keyword -> count

    def run(self, keywords: list[str], auto_scroll: int = 20) -> list[CollectedItem]:
        """执行采集流程"""
        raise NotImplementedError

    def _screencap_and_ocr(self) -> tuple[Path, list[str]]:
        """截图 + OCR，返回（图片路径, 文字列表）"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        img_path = self.output_dir / f"{self.PLATFORM}_{ts}_{uuid.uuid4().hex[:6]}.jpg"
        self.adb.screencap(str(img_path))

        lines = self.ocr.recognize_and_merge_lines(Image.open(img_path))
        return img_path, lines

    def _save_item(self, item: CollectedItem) -> Path:
        """保存单条采集数据"""
        path = self.output_dir / f"{item.platform}_{item.content_type}_{item.id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(item), f, ensure_ascii=False, indent=2)
        return path

    def _is_ad(self, texts: list[str]) -> float:
        """
        简单广告检测（基于关键词）
        返回 0.0-1.0 的广告分数
        """
        ad_keywords = [
            "广告", "推广", "限时优惠", "立即购买", "点击链接",
            "招商", "合作", "加微", "vx", "二维码", "扫码",
            "专属客服", "秒杀", "折扣", "领取优惠券",
        ]
        score = 0.0
        for text in texts:
            t = text.lower()
            for kw in ad_keywords:
                if kw in t:
                    score += 0.3
        return min(score, 1.0)


# ============ 抖音采集器 ============

class DouyinCollector(BaseCollector):
    """抖音采集器"""

    PLATFORM = "douyin"

    def run(self, keywords: list[str], auto_scroll: int = 20) -> list[CollectedItem]:
        """执行抖音采集"""
        collected = []

        # 启动抖音
        self._ensure_app_running("com.ss.android.ugc.aweme", ".main.MainActivity")
        self.adb.wait(3)

        for keyword in keywords:
            logger.info(f"[抖音] 开始采集关键词: {keyword}")
            items = self._search_and_collect(keyword, auto_scroll)
            collected.extend(items)
            self._collected[keyword] = len(items)
            self.adb.wait(2)

        return collected

    def _ensure_app_running(self, package: str, activity: str):
        """确保应用在运行"""
        current = self.adb.get_current_app()
        if self.PLATFORM not in current.lower():
            logger.info(f"启动 {package}")
            self.adb.launch_app(package, activity)
            self.adb.wait(4)

    def _search_and_collect(self, keyword: str, auto_scroll: int) -> list[CollectedItem]:
        """搜索关键词并采集"""
        items = []

        # 1. 点击搜索按钮（通常在顶部）
        self._click_search_tab()
        self.adb.wait(2)

        # 2. 输入搜索词
        self._input_search_keyword(keyword)
        self.adb.wait(2)

        # 3. 点击搜索按钮/回车
        self.adb.press_enter()
        self.adb.wait(3)

        # 4. 切换到"视频"标签
        self._click_video_tab()
        self.adb.wait(2)

        # 5. 滚动采集
        for i in range(auto_scroll):
            img_path, texts = self._screencap_and_ocr()

            # 提取字幕/文字
            for text in texts:
                if len(text) < 3:
                    continue
                item = CollectedItem(
                    platform=self.PLATFORM,
                    keyword=keyword,
                    content_type="subtitle",
                    raw_text=text,
                )
                item.ad_score = self._is_ad([text])
                if item.ad_score < 0.6:  # 过滤广告
                    items.append(item)
                    self._save_item(item)
                    logger.debug(f"  采集 [{keyword}]: {text[:30]}...")

            # 滑动到下一个
            self.adb.swipe_up(duration=300)
            self.adb.wait(1.5)

            if len(items) >= self.max_per_keyword:
                break

        # 6. 返回搜索页
        self.adb.press_back()
        self.adb.wait(1)
        self.adb.press_back()
        self.adb.wait(1)

        logger.info(f"[抖音] 关键词 [{keyword}] 采集完成: {len(items)} 条")
        return items

    def _click_search_tab(self):
        """点击搜索 Tab"""
        w, h = self.adb.get_screen_size()
        # 搜索图标通常在顶部中间偏右
        self.adb.tap(int(w * 0.85), int(h * 0.05))
        self.adb.wait(2)

    def _input_search_keyword(self, keyword: str):
        """输入搜索关键词"""
        # 清空输入框
        self.adb.tap(int(540), int(300))  # 点击输入框
        self.adb.wait(1)
        # 全选删除
        self.adb.swipe(int(200), int(300), int(800), int(300), duration=200)
        self.adb.wait(0.5)

        # 输入文字（特殊字符需 URL 编码）
        safe_text = keyword.replace(" ", "%s")
        self.adb.input_text(safe_text)

    def _click_video_tab(self):
        """点击视频 tab"""
        w, h = self.adb.get_screen_size()
        # 视频 tab 通常在综合/用户之后
        self.adb.tap(int(w * 0.66), int(h * 0.15))
        self.adb.wait(1)


# ============ B站采集器 ============

class BilibiliCollector(BaseCollector):
    """B站采集器（支持弹幕采集）"""

    PLATFORM = "bilibili"

    def run(self, keywords: list[str], auto_scroll: int = 20) -> list[CollectedItem]:
        collected = []

        self._ensure_app_running("tv.danmaku.bili", ".MainActivity")
        self.adb.wait(3)

        for keyword in keywords:
            logger.info(f"[B站] 开始采集关键词: {keyword}")
            items = self._search_and_collect(keyword, auto_scroll)
            collected.extend(items)
            self._collected[keyword] = len(items)
            self.adb.wait(2)

        return collected

    def _ensure_app_running(self, package: str, activity: str):
        current = self.adb.get_current_app()
        if "bilibili" not in current.lower():
            logger.info(f"启动 {package}")
            self.adb.launch_app(package, activity)
            self.adb.wait(4)

    def _search_and_collect(self, keyword: str, auto_scroll: int) -> list[CollectedItem]:
        items = []

        # 1. 进入搜索
        self._click_search()
        self.adb.wait(2)

        # 2. 输入搜索词
        self._input_keyword(keyword)
        self.adb.wait(1)

        # 3. 搜索
        self.adb.press_enter()
        self.adb.wait(3)

        # 4. 切换到视频
        self._click_video_tab()
        self.adb.wait(2)

        # 5. 滚动采集（视频标题 + 弹幕）
        for i in range(auto_scroll):
            img_path, texts = self._screencap_and_ocr()

            for text in texts:
                if len(text) < 3:
                    continue
                item = CollectedItem(
                    platform=self.PLATFORM,
                    keyword=keyword,
                    content_type="subtitle",
                    raw_text=text,
                )
                item.ad_score = self._is_ad([text])
                if item.ad_score < 0.6:
                    items.append(item)
                    self._save_item(item)

            self.adb.swipe_up(duration=300)
            self.adb.wait(1.5)

            if len(items) >= self.max_per_keyword:
                break

        # 返回
        self.adb.press_back()
        self.adb.wait(1)
        self.adb.press_back()
        self.adb.wait(1)

        logger.info(f"[B站] 关键词 [{keyword}] 采集完成: {len(items)} 条")
        return items

    def _click_search(self):
        w, h = self.adb.get_screen_size()
        self.adb.tap(int(w * 0.85), int(h * 0.05))
        self.adb.wait(2)

    def _input_keyword(self, keyword: str):
        self.adb.tap(int(540), int(300))
        self.adb.wait(1)
        safe_text = keyword.replace(" ", "%s")
        self.adb.input_text(safe_text)

    def _click_video_tab(self):
        w, h = self.adb.get_screen_size()
        self.adb.tap(int(w * 0.5), int(h * 0.15))
        self.adb.wait(1)


# ============ 小红书采集器 ============

class XiaohongshuCollector(BaseCollector):
    """小红书采集器"""

    PLATFORM = "xiaohongshu"

    def run(self, keywords: list[str], auto_scroll: int = 20) -> list[CollectedItem]:
        collected = []

        self._ensure_app_running("com.xingin.xhs", ".index.v2.IndexActivityV2")
        self.adb.wait(3)

        for keyword in keywords:
            logger.info(f"[小红书] 开始采集关键词: {keyword}")
            items = self._search_and_collect(keyword, auto_scroll)
            collected.extend(items)
            self._collected[keyword] = len(items)
            self.adb.wait(2)

        return collected

    def _ensure_app_running(self, package: str, activity: str):
        current = self.adb.get_current_app()
        if "xhs" not in current.lower() and "xiaohongshu" not in current.lower():
            logger.info(f"启动 {package}")
            self.adb.launch_app(package, activity)
            self.adb.wait(4)

    def _search_and_collect(self, keyword: str, auto_scroll: int) -> list[CollectedItem]:
        items = []

        # 1. 点击搜索入口
        self._click_search_icon()
        self.adb.wait(2)

        # 2. 输入搜索词
        self._input_keyword(keyword)
        self.adb.wait(1)

        # 3. 搜索
        self.adb.press_enter()
        self.adb.wait(3)

        # 4. 滚动采集
        for i in range(auto_scroll):
            img_path, texts = self._screencap_and_ocr()

            for text in texts:
                if len(text) < 3:
                    continue
                item = CollectedItem(
                    platform=self.PLATFORM,
                    keyword=keyword,
                    content_type="subtitle",
                    raw_text=text,
                )
                item.ad_score = self._is_ad([text])
                if item.ad_score < 0.6:
                    items.append(item)
                    self._save_item(item)

            self.adb.swipe_up(duration=300)
            self.adb.wait(1.5)

            if len(items) >= self.max_per_keyword:
                break

        # 返回
        self.adb.press_back()
        self.adb.wait(1)
        self.adb.press_back()
        self.adb.wait(1)

        logger.info(f"[小红书] 关键词 [{keyword}] 采集完成: {len(items)} 条")
        return items

    def _click_search_icon(self):
        w, h = self.adb.get_screen_size()
        self.adb.tap(int(w * 0.8), int(h * 0.06))
        self.adb.wait(2)

    def _input_keyword(self, keyword: str):
        self.adb.tap(int(540), int(300))
        self.adb.wait(1)
        safe_text = keyword.replace(" ", "%s")
        self.adb.input_text(safe_text)


# ============ 工厂函数 ============

def create_collector(
    platform: str,
    adb_host: str = "127.0.0.1",
    adb_port: int = 16384,
    output_dir: str | Path = "materials/raw",
    **kwargs,
) -> BaseCollector:
    """创建指定平台的采集器"""
    adb = ADBController(host=adb_host, port=adb_port)
    ocr = OCRProcessor(**kwargs)
    output = Path(output_dir)

    collectors = {
        "douyin": DouyinCollector,
        "bilibili": BilibiliCollector,
        "xiaohongshu": XiaohongshuCollector,
    }

    if platform not in collectors:
        raise ValueError(f"不支持的平台: {platform}，可选: {list(collectors.keys())}")

    return collectors[platform](adb, ocr, output, **kwargs)


# ---- 单元测试 ----
if __name__ == "__main__":
    from PIL import Image

    print("视频采集器核心模块已加载")
    print("支持平台: douyin, bilibili, xiaohongshu")
    print("直接运行此文件可测试设备连接")
    print()
    print("提示: 请确保 MuMu Player Pro 已启动并开启 ADB")
    print("  adb connect 127.0.0.1:16384")

    # 尝试连接
    try:
        adb = ADBController()
        print(f"\n✅ 设备连接成功: {adb.serial}")
        print(f"   屏幕: {adb.get_screen_size()}")
        print(f"   当前应用: {adb.get_current_app()}")
    except ADBError as e:
        print(f"\n⚠️ 设备未连接: {e}")
        print("请启动 MuMu Player Pro 后重试")