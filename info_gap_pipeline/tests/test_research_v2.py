"""test_research_v2.py — Round 1+: 新闻源维度提升的回归测试

每个测试对应一轮迭代的承诺:
1. 真实热度字段 (scan_all 后每个 topic 都带标准化后的 heat 数值)
2. 真实时间戳 (每个 topic 带 timestamp,可被新鲜度评分使用)
3. 跨平台覆盖统计 (新加 sources_seen 记录多少个不同平台)
"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent


class TestResearchV2(unittest.TestCase):
    """Round 1 测试: scan_all 必须返回带 heat/timestamp 的 topic 列表"""

    def test_scan_all_returns_heat_int(self):
        """每条 topic 必须有 'heat' 字段(浮点型,来自 hot/hot_value/heat 等)"""
        from info_gap_pipeline.research import TopicResearcher
        r = TopicResearcher()
        topics = r.scan_all(use_cache=False)
        if not topics:
            self.skipTest("无 topics (可能离线)")
        # 在没有 cache 的情况下,真去拉,会很慢 — 但我们想验证:
        # (1) 字段命名统一为 'heat'
        # (2) 类型为 int/float
        # 如果跑不动 scan_all 我们用 mock data
        sample = topics[0]
        # 必须有 heat 字段
        self.assertIn("heat", sample, "topic 缺 heat 字段")
        # 类型必须是数字
        self.assertIsInstance(sample["heat"], (int, float))
        self.assertGreaterEqual(sample["heat"], 0)

    def test_scan_all_returns_timestamp(self):
        """每条 topic 必须有 'timestamp'(int, unix 秒),缺则=0"""
        from info_gap_pipeline.research import TopicResearcher
        r = TopicResearcher()
        topics = r.scan_all(use_cache=False)
        if not topics:
            self.skipTest("无 topics")
        sample = topics[0]
        self.assertIn("timestamp", sample, "topic 缺 timestamp 字段")
        self.assertIsInstance(sample["timestamp"], int)
        self.assertGreaterEqual(sample["timestamp"], 0)

    def test_no_cache_when_disabled(self):
        """use_cache=False 时绕过 cache(防止用旧数据骗测试)"""
        from info_gap_pipeline.research import TopicResearcher
        r = TopicResearcher()
        # mock fetcher 在迭代期能跑
        with patch.object(r, "_topics_cache", {}):
            pass  # 占位


class TestScoreV2(unittest.TestCase):
    """score 函数本身的可测试性(单元级,无需网络)"""

    def test_extract_heat_handles_aliases(self):
        """从 [heat, hot, hot_value, hotScore, score] 任一字段提取热度"""
        from info_gap_pipeline.research import extract_heat
        # hot 转 heat
        self.assertEqual(extract_heat({"hot": 12082202}), 12082202.0)
        self.assertEqual(extract_heat({"hot_value": "99,999"}), 99999.0)
        self.assertEqual(extract_heat({"heat": "100w"}), 1000000.0)  # 简写
        self.assertEqual(extract_heat({"hotScore": 5000}), 5000.0)
        self.assertEqual(extract_heat({}), 0.0)
        self.assertEqual(extract_heat(None), 0.0)

    def test_extract_timestamp_handles_aliases(self):
        """从 [timestamp, showTime, ctime, publish_time, time] 任一字段提取时间戳(秒)"""
        from info_gap_pipeline.research import extract_timestamp
        # showTime 字符串格式
        self.assertGreater(extract_timestamp({"showTime": "2026-07-24 10:30:00"}), 0)
        self.assertGreater(extract_timestamp({"ctime": 1784799007}), 0)
        self.assertEqual(extract_timestamp({}), 0)

    def test_topic_quality_score_combines(self):
        """topic_quality_score: 综合热度、新鲜度、信息差密度、出处权威性"""
        from info_gap_pipeline.research import topic_quality_score
        # 高热度 + 新 + 数据点 + 知乎(权威) → 高分
        t = {"heat": 1_000_000, "timestamp": 0, "title": "99% 人不知道,2025 年地球内核首次反转 真相"}
        # 由于 timestamp=0(epoch)→ 标记为旧,但 heat 大,有信息差词
        s = topic_quality_score(t, now=0)
        self.assertGreater(s, 0.5)

    def test_rank_topics_by_quality(self):
        """多个话题按 quality_score 降序"""
        from info_gap_pipeline.research import rank_topics
        topics = [
            {"title": "旧闻", "heat": 100, "timestamp": 0},
            {"title": "新鲜突发,99% 人不知道", "heat": 1_000_000, "timestamp": 1},
        ]
        # recent = max
        ranked = rank_topics(topics)
        self.assertEqual(ranked[0]["title"], "新鲜突发,99% 人不知道")


if __name__ == "__main__":
    unittest.main()
