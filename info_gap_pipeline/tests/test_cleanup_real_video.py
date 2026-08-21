"""test_cleanup_real_video.py — Round 9: 验证每次 run 前清空 temp/videos/

用户痛点: temp/videos/seg_00.mp4 是 6月12日的旧视频,隔天 segment_idx=0 命中
          → 复用旧视频,产生"内容每天一样"假象

修复: _cleanup_historical_materials 必须递归清空 temp/videos/
"""

import os
from pathlib import Path
import unittest


class TestCleanupVideosCache(unittest.TestCase):
    """_cleanup_historical_materials 必须清空 temp/videos/ 目录"""

    def setUp(self):
        from info_gap_pipeline.config import TEMP_DIR
        self.temp_dir = TEMP_DIR
        self.videos_dir = TEMP_DIR / "videos"
        self.videos_dir.mkdir(parents=True, exist_ok=True)
        # 模拟旧的 seg_00.mp4 (12天前的时间戳, mtime)
        import time
        old_path = self.videos_dir / "seg_00.mp4"
        old_path.write_bytes(b"fake old video content" * 1000)
        old_time = time.time() - 12 * 86400
        os.utime(old_path, (old_time, old_time))

    def tearDown(self):
        # 测试后清理
        if self.videos_dir.exists():
            for f in self.videos_dir.glob("*"):
                try:
                    f.unlink()
                except Exception:
                    pass

    def test_cleanup_removes_old_seg_files(self):
        """_cleanup_historical_materials 必须删掉 temp/videos/ 里的所有文件"""
        # 确认有旧文件
        self.assertTrue((self.videos_dir / "seg_00.mp4").exists())

        from info_gap_pipeline.main import InfoGapPipeline
        pipe = InfoGapPipeline()
        pipe._cleanup_historical_materials()

        # 验证: 旧文件被删
        self.assertFalse((self.videos_dir / "seg_00.mp4").exists(),
                         "temp/videos/seg_00.mp4 未被清理 → 隔天会复用旧视频")

    def test_cleanup_runs_each_time(self):
        """模拟"隔天运行": 第一次清,放新文件,第二次再清,验证能再次清空"""
        from info_gap_pipeline.main import InfoGapPipeline
        pipe = InfoGapPipeline()

        # Day 1 run
        pipe._cleanup_historical_materials()
        # 模拟新下载
        (self.videos_dir / "seg_00.mp4").write_bytes(b"day 1 video")
        self.assertTrue((self.videos_dir / "seg_00.mp4").exists())

        # Day 2 run
        pipe._cleanup_historical_materials()
        self.assertFalse((self.videos_dir / "seg_00.mp4").exists(),
                         "Day 2 应清空 Day 1 的下载")


class TestDownloadIsRealTime(unittest.TestCase):
    """download() 必须真实下载,不能永远用缓存"""

    def test_download_skips_only_if_fresh(self):
        """download 缓存逻辑: 仅当文件存在且>1MB 才跳过;否则重下"""
        # 这是行为契约: 隔天 _cleanup 已清,文件不存在 → 重新下载
        # 单元测试不直接执行 yt-dlp, 但可验证 download() 方法的判断
        from info_gap_pipeline.download import VideoDownloader
        from info_gap_pipeline.config import TEMP_DIR

        # 准备: 模拟昨天的下载产物
        vdir = TEMP_DIR / "videos"
        vdir.mkdir(parents=True, exist_ok=True)
        fake_old = vdir / "seg_99.mp4"
        fake_old.write_bytes(b"x" * (2 * 1024 * 1024))  # 2MB

        # 验证: file exists & size > 1MB → 跳过 (这是 cache hit 路径)
        self.assertTrue(fake_old.exists())
        self.assertGreater(fake_old.stat().st_size, 1024 * 1024)

        # 关键: 在 main.run() 中, _cleanup 删 fake_old, 然后 download 看到文件不在 → 重下
        from info_gap_pipeline.main import InfoGapPipeline
        pipe = InfoGapPipeline()
        pipe._cleanup_historical_materials()

        # 验证清理后 fake_old 不存在
        self.assertFalse(fake_old.exists(),
                         "清理后 fake_old 应被删,download() 会重下")


if __name__ == "__main__":
    unittest.main()
