"""test_freshness_v2.py — Round 6: 时效性维度的回归测试

目标:
1. scan_all 必须给每个 topic 带可解析的 timestamp
2. 时间戳未来 1h 内 → 应被过滤 (避免误传)
3. 时间戳 < 6h → freshness_score >= 80
4. extract_timestamp 支持 "X 分钟前" / "X 小时前" 格式
"""

import time as _time
import unittest
from pathlib import Path
from datetime import datetime, timedelta


class TestFreshnessV2(unittest.TestCase):

    def test_scan_all_topics_have_timestamp(self):
        """scan_all 必须每条带 timestamp (int)"""
        from info_gap_pipeline.research import TopicResearcher
        r = TopicResearcher()
        topics = r.scan_all(use_cache=False)
        if not topics:
            self.skipTest("无 topics (可能离线)")
        bad = [t for t in topics if not isinstance(t.get("timestamp"), int)]
        # 容许 ≤50% 缺失 (平台 API 部分不返)
        self.assertLess(len(bad), len(topics) * 0.5,
                        f"过多 topic 缺 timestamp: {len(bad)}/{len(topics)}")

    def test_recent_topic_passes_freshness(self):
        """6h 内话题 freshness_score ≥ 80"""
        sys_path = __import__('sys').path
        sys_path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from tests.test_quality import TestNewsFreshness as T
        now_ts = int(_time.time())
        topic = {
            "title": "刚刚",
            "timestamp": now_ts - 3 * 3600,  # 3h 前
        }
        score, _ = T._score_freshness([topic], now=datetime.fromtimestamp(now_ts))
        self.assertGreaterEqual(score, 80)

    def test_relative_time_parsing(self):
        """extract_timestamp 支持相对时间"""
        from info_gap_pipeline.research import extract_timestamp
        now = int(_time.time())
        # 5 分钟前
        ts = extract_timestamp({"title": "刚刚发生 (5分钟前)"})
        # 容许 ±60s 容差
        self.assertAlmostEqual(ts, now - 5 * 60, delta=60)


class TestRelativeTimeParsing(unittest.TestCase):

    def test_hours_ago(self):
        from info_gap_pipeline.research import extract_timestamp
        now = int(_time.time())
        ts = extract_timestamp({"title": "新闻 (2小时前)"})
        self.assertAlmostEqual(ts, now - 2 * 3600, delta=60)

    def test_days_ago(self):
        from info_gap_pipeline.research import extract_timestamp
        now = int(_time.time())
        ts = extract_timestamp({"title": "新闻 (1天前)"})
        self.assertAlmostEqual(ts, now - 86400, delta=60)


if __name__ == "__main__":
    unittest.main()
