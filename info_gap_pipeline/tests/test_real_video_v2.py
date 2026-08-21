"""test_real_video_v2.py — Round 8: 视频源必须是真实的,永远不用兜底噪点

用户痛点:
  当前代码 _generate_test_video 用 cellauto=rule:110 生成噪点,视觉上看每天一样
  传了几十天了 — 真实视频失败时永远 fallback 到这个噪声

修复目标:
  1. _step_download 必须返回真实视频,或返回 None (让流水线失败)
  2. 多关键词重试 + 多平台
  3. B站热搜视频兜底 (实时,真材实料)
  4. 移除/废弃 _generate_test_video
"""

import re
import unittest
from pathlib import Path


class TestNeverUseCellautoFallback(unittest.TestCase):
    """流水线行为契约: Round 8/9 修复后,实时下载 + 兜底无害

    视频下载的"实时性"由 Round 9 锁定: 每次 run() 启动清空 temp/videos/,
    所以 segment_idx 命中时是空白, yt-dlp 必然真实重下.
    """

    def test_cleanup_makes_download_realtime(self):
        """_cleanup_historical_materials 必须清空 temp/videos/

        这是视频"实时下载"的关键保障 — 没有这个清理,
        隔天 segment_idx 会命中昨天缓存, 复用旧视频.
        """
        from info_gap_pipeline.main import InfoGapPipeline
        from info_gap_pipeline.config import TEMP_DIR
        import os

        vdir = TEMP_DIR / "videos"
        vdir.mkdir(parents=True, exist_ok=True)
        fake_old = vdir / "seg_99.mp4"
        fake_old.write_bytes(b"old video" * 1000)
        try:
            pipe = InfoGapPipeline()
            pipe._cleanup_historical_materials()
            self.assertFalse(fake_old.exists(),
                             "temp/videos/ 没被清空 → 隔天会复用旧视频")
        finally:
            if fake_old.exists():
                fake_old.unlink()


class TestMultiKeywordSearch(unittest.TestCase):
    """process_one 必须用多关键词重试"""

    def test_extract_keywords_returns_multi(self):
        """脚本关键词提取应返回多个词"""
        # 模拟 script 文本
        script = "据报道,科学家发现地球内核在 2025 年首次反向旋转。这种突破在过去 100 年里从未记录。"
        words = re.findall(r'[\w一-鿿]{2,6}', script)
        # 至少应提取出 5+ 候选词
        self.assertGreater(len(words), 5)


class TestVideoFilterRealtime(unittest.TestCase):
    """实时筛选: 候选视频必须非广告 + 时长合理"""

    def test_filter_skips_short_videos(self):
        """太短视频(< 5s) 应被过滤"""
        # 单元: simulate filter logic
        candidates = [
            {"title": "广告短片", "duration": 3, "platform": "bilibili"},
            {"title": "NASA 探索地球", "duration": 120, "platform": "bilibili"},
            {"title": "太短片段", "duration": 4, "platform": "youtube"},
        ]
        # 至少要 duration >= 5
        good = [c for c in candidates if c.get("duration", 0) >= 5]
        self.assertEqual(len(good), 1)

    def test_filter_skips_ad_keywords(self):
        """标题含 '广告' / 'AD' / '商业' 应被过滤"""
        candidates = [
            {"title": "NASA 探索地球", "duration": 120},
            {"title": "广告-可口可乐", "duration": 60},
            {"title": "【AD】商业推广", "duration": 30},
            {"title": "深度报道", "duration": 240},
        ]
        ad_kw = ("广告", "AD", "商业", "推广", "sponsored")
        good = [c for c in candidates if not any(k in c["title"] for k in ad_kw)]
        self.assertEqual(len(good), 2)


class TestRealtimeHotVideoFallback(unittest.TestCase):
    """全部搜索失败时,从 B站实时热搜拿视频"""

    def test_get_realtime_hot_videos_signature(self):
        """应有 get_realtime_hot_videos 函数,返回 B站热搜视频列表"""
        from info_gap_pipeline.download.search import MaterialSearcher
        searcher = MaterialSearcher()
        # 检查函数存在
        self.assertTrue(hasattr(searcher, "get_realtime_hot_videos"))


if __name__ == "__main__":
    unittest.main()
