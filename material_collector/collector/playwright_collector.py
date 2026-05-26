#!/usr/bin/env python3
"""
Playwright Web 采集器 - 直接抓取网页版抖音/B站
不依赖模拟器，用于快速采集或无头环境
支持：抖音网页版、B站网页版
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ============ 数据模型 ============

@dataclass
class WebCollectedItem:
    """Web 采集的数据项"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    platform: str = ""
    keyword: str = ""
    content_type: str = "subtitle"  # subtitle | title | comment | danmu
    raw_text: str = ""
    video_title: str = ""
    video_url: str = ""
    author: str = ""
    timestamp: str = ""
    ad_score: float = 0.0
    hash: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
        if self.raw_text:
            self.hash = hashlib.md5(self.raw_text.encode()).hexdigest()


# ============ 广告检测 ============

def _is_ad(texts: list[str]) -> float:
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


# ============ 抖音 Web 采集器 ============

class DouyinWebCollector:
    """抖音网页版采集器"""

    PLATFORM = "douyin_web"

    def __init__(
        self,
        output_dir: str | Path = "materials/raw",
        max_per_keyword: int = 50,
        headless: bool = True,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_per_keyword = max_per_keyword
        self.headless = headless
        self._page = None
        self._browser = None

    async def _init_browser(self):
        """初始化 Playwright 浏览器"""
        if self._browser is None:
            try:
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=self.headless)
            except ImportError:
                raise ImportError("请安装 playwright: pip install playwright && playwright install chromium")

    async def _ensure_page(self):
        """确保页面已创建"""
        if self._page is None:
            await self._init_browser()
            self._page = await self._browser.new_page()
            # 设置 User-Agent
            await self._page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })

    async def close(self):
        """关闭浏览器"""
        if self._page:
            await self._page.close()
            self._page = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if hasattr(self, "_playwright"):
            await self._playwright.stop()
            delattr(self, "_playwright")

    def run(self, keywords: list[str], auto_scroll: int = 20) -> list[WebCollectedItem]:
        """同步入口：执行采集"""
        return asyncio.run(self._run_async(keywords, auto_scroll))

    async def _run_async(self, keywords: list[str], auto_scroll: int = 20) -> list[WebCollectedItem]:
        """异步执行采集"""
        collected = []

        try:
            await self._ensure_page()

            for keyword in keywords:
                logger.info(f"[抖音Web] 开始采集关键词: {keyword}")
                items = await self._search_and_collect(keyword, auto_scroll)
                collected.extend(items)
                logger.info(f"[抖音Web] 关键词 [{keyword}] 采集完成: {len(items)} 条")
                await asyncio.sleep(2)

            return collected
        finally:
            await self.close()

    async def _search_and_collect(self, keyword: str, auto_scroll: int) -> list[WebCollectedItem]:
        """搜索并采集"""
        items = []

        # 访问抖音搜索页
        search_url = f"https://www.douyin.com/search/{keyword}?type=video"
        await self._page.goto(search_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        # 滚动加载更多
        for i in range(auto_scroll):
            # 提取页面文本
            texts = await self._extract_texts()

            for text in texts:
                if len(text) < 3:
                    continue
                item = WebCollectedItem(
                    platform=self.PLATFORM,
                    keyword=keyword,
                    content_type="subtitle",
                    raw_text=text,
                )
                item.ad_score = _is_ad([text])
                if item.ad_score < 0.6:
                    items.append(item)
                    self._save_item(item)

            # 滚动
            await self._page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(1.5)

            if len(items) >= self.max_per_keyword:
                break

        return items

    async def _extract_texts(self) -> list[str]:
        """提取页面文本"""
        try:
            # 获取视频标题和描述
            texts = await self._page.evaluate("""
                () => {
                    const results = [];
                    // 视频标题
                    document.querySelectorAll('h2, .title, [data-e2e="search-card-title"]').forEach(el => {
                        const t = el.innerText.trim();
                        if (t.length > 3) results.push(t);
                    });
                    // 搜索结果中的文字
                    document.querySelectorAll('[data-e2e="search-card-desc"]').forEach(el => {
                        const t = el.innerText.trim();
                        if (t.length > 3) results.push(t);
                    });
                    return [...new Set(results)];
                }
            """)
            return texts
        except Exception as e:
            logger.warning(f"文本提取失败: {e}")
            return []

    def _save_item(self, item: WebCollectedItem) -> Path:
        """保存单条采集数据"""
        path = self.output_dir / f"{item.platform}_{item.content_type}_{item.id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(item), f, ensure_ascii=False, indent=2)
        return path


# ============ B站 Web 采集器 ============

class BilibiliWebCollector:
    """B站网页版采集器"""

    PLATFORM = "bilibili_web"

    def __init__(
        self,
        output_dir: str | Path = "materials/raw",
        max_per_keyword: int = 50,
        headless: bool = True,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_per_keyword = max_per_keyword
        self.headless = headless
        self._page = None
        self._browser = None

    async def _init_browser(self):
        """初始化 Playwright 浏览器"""
        if self._browser is None:
            try:
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=self.headless)
            except ImportError:
                raise ImportError("请安装 playwright: pip install playwright && playwright install chromium")

    async def _ensure_page(self):
        """确保页面已创建"""
        if self._page is None:
            await self._init_browser()
            self._page = await self._browser.new_page()
            await self._page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })

    async def close(self):
        """关闭浏览器"""
        if self._page:
            await self._page.close()
            self._page = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if hasattr(self, "_playwright"):
            await self._playwright.stop()
            delattr(self, "_playwright")

    def run(self, keywords: list[str], auto_scroll: int = 20) -> list[WebCollectedItem]:
        """同步入口：执行采集"""
        return asyncio.run(self._run_async(keywords, auto_scroll))

    async def _run_async(self, keywords: list[str], auto_scroll: int = 20) -> list[WebCollectedItem]:
        """异步执行采集"""
        collected = []

        try:
            await self._ensure_page()

            for keyword in keywords:
                logger.info(f"[B站Web] 开始采集关键词: {keyword}")
                items = await self._search_and_collect(keyword, auto_scroll)
                collected.extend(items)
                logger.info(f"[B站Web] 关键词 [{keyword}] 采集完成: {len(items)} 条")
                await asyncio.sleep(2)

            return collected
        finally:
            await self.close()

    async def _search_and_collect(self, keyword: str, auto_scroll: int) -> list[WebCollectedItem]:
        """搜索并采集"""
        items = []

        # 访问B站搜索页
        search_url = f"https://search.bilibili.com/all?keyword={keyword}&order=totalrank&duration=0&tids_1=0"
        await self._page.goto(search_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        # 滚动加载更多
        for i in range(auto_scroll):
            texts = await self._extract_texts()

            for text in texts:
                if len(text) < 3:
                    continue
                item = WebCollectedItem(
                    platform=self.PLATFORM,
                    keyword=keyword,
                    content_type="subtitle",
                    raw_text=text,
                )
                item.ad_score = _is_ad([text])
                if item.ad_score < 0.6:
                    items.append(item)
                    self._save_item(item)

            # 滚动
            await self._page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(1.5)

            if len(items) >= self.max_per_keyword:
                break

        return items

    async def _extract_texts(self) -> list[str]:
        """提取页面文本"""
        try:
            texts = await self._page.evaluate("""
                () => {
                    const results = [];
                    const seen = new Set();
                    // 导航关键词（过滤）
                    const navWords = [
                        '精彩推荐', '专栏投稿', '音频投稿', '贴纸投稿', '视频投稿',
                        '全部分区', '排行榜', '搜索历史', '猜你想搜', '默认排序',
                        '综合排序', '最近搜索', '清除历史', '游戏中心', '我的',
                        '10分钟以下', '10-30分钟', '30-60分钟', '60分钟以上',
                        '番剧', '影视', '点评', '开播时间', '声优', '风格'
                    ];
                    // 从 .video-item 提取视频标题
                    document.querySelectorAll('.video-item').forEach(el => {
                        const t = el.innerText.trim().split('\\n').filter(l => l.trim()).join(' ');
                        if (t.length > 5 && !seen.has(t)) {
                            // 过滤导航词
                            if (!navWords.some(w => t.includes(w))) {
                                seen.add(t);
                                results.push(t);
                            }
                        }
                    });
                    return results;
                }
            """)
            return texts
        except Exception as e:
            logger.warning(f"文本提取失败: {e}")
            return []

    def _save_item(self, item: WebCollectedItem) -> Path:
        """保存单条采集数据"""
        path = self.output_dir / f"{item.platform}_{item.content_type}_{item.id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(item), f, ensure_ascii=False, indent=2)
        return path


# ============ 工厂函数 ============

def create_web_collector(
    platform: str,
    output_dir: str | Path = "materials/raw",
    **kwargs,
) -> BilibiliWebCollector | DouyinWebCollector:
    """创建 Web 采集器"""
    collectors = {
        "douyin": DouyinWebCollector,
        "douyin_web": DouyinWebCollector,
        "bilibili": BilibiliWebCollector,
        "bilibili_web": BilibiliWebCollector,
    }

    if platform not in collectors:
        raise ValueError(f"不支持的平台: {platform}，可选: {list(collectors.keys())}")

    return collectors[platform](output_dir=output_dir, **kwargs)


# ---- 单元测试 ----
if __name__ == "__main__":
    print("Playwright Web 采集器模块已加载")
    print("支持平台: douyin_web, bilibili_web")
    print()
    print("提示: 请确保已安装 playwright")
    print("  pip install playwright")
    print("  playwright install chromium")
    print()
    print("测试导入...")
    try:
        from playwright.async_api import async_playwright
        print("✅ Playwright 已安装")
    except ImportError:
        print("⚠️ Playwright 未安装，请运行: pip install playwright && playwright install chromium")
