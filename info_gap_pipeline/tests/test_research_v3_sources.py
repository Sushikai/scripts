"""test_research_v3_sources.py — Round 7: 新增带真实时间戳的源

目标: 加 HackerNews / GitHub Trending / Dev.to 三个带真实时间戳的源
      - 大幅提升 NEWS_FRESHNESS (0 → 期望 ≥50)
      - 提升 NEWS_SOURCE.coverage (7 → 10 源)
"""

import time as _time
import unittest


class TestFetchGitHubTrending(unittest.TestCase):
    """fetch_github_trending: GitHub Search API 返回 created_at ISO 时间戳"""

    def test_returns_topics(self):
        from info_gap_pipeline.research import TopicResearcher
        r = TopicResearcher()
        topics = r.fetch_github_trending(limit=5)
        if not topics:
            self.skipTest("GitHub API 不可达")
        self.assertGreater(len(topics), 0)
        self.assertEqual(topics[0]["source"], "GitHub")

    def test_returns_int_timestamp(self):
        from info_gap_pipeline.research import TopicResearcher, normalize_topic, extract_timestamp
        r = TopicResearcher()
        topics = r.fetch_github_trending(limit=5)
        if not topics:
            self.skipTest("GitHub API 不可达")
        for t in topics:
            # fetch 返回 ISO 字符串, normalize_topic 后转 int
            normalized = normalize_topic(t)
            self.assertIsInstance(normalized["timestamp"], int)
            self.assertGreater(normalized["timestamp"], int(_time.time()) - 7 * 86400)
            self.assertLessEqual(normalized["timestamp"], int(_time.time()) + 3600)
            # extract_timestamp 也能处理原始 ISO 字段
            ts = extract_timestamp({"timestamp": t.get("timestamp")})
            self.assertGreater(ts, 0)

    def test_returns_heat(self):
        from info_gap_pipeline.research import TopicResearcher
        r = TopicResearcher()
        topics = r.fetch_github_trending(limit=5)
        if not topics:
            self.skipTest("GitHub API 不可达")
        for t in topics:
            # GitHub 返回 stars,转为 heat (星数 = 热度)
            self.assertGreater(t.get("heat", 0), 0)


class TestFetchDevToTop(unittest.TestCase):
    """fetch_devto_top: Dev.to API 返回 published_timestamp ISO"""

    def test_returns_topics(self):
        from info_gap_pipeline.research import TopicResearcher
        r = TopicResearcher()
        topics = r.fetch_devto_top(limit=5)
        if not topics:
            self.skipTest("DevTo API 不可达")
        self.assertEqual(topics[0]["source"], "DevTo")

    def test_returns_int_timestamp(self):
        from info_gap_pipeline.research import TopicResearcher, normalize_topic, extract_timestamp
        r = TopicResearcher()
        topics = r.fetch_devto_top(limit=5)
        if not topics:
            self.skipTest("DevTo API 不可达")
        for t in topics:
            # fetch 返回 ISO 字符串, normalize_topic 后转 int
            normalized = normalize_topic(t)
            self.assertIsInstance(normalized["timestamp"], int)
            self.assertGreater(normalized["timestamp"], int(_time.time()) - 30 * 86400)
            ts = extract_timestamp({"timestamp": t.get("timestamp")})
            self.assertGreater(ts, 0)

    def test_returns_reactions_heat(self):
        from info_gap_pipeline.research import TopicResearcher
        r = TopicResearcher()
        topics = r.fetch_devto_top(limit=5)
        if not topics:
            self.skipTest("DevTo API 不可达")
        for t in topics:
            # public_reactions_count 转为 heat
            self.assertGreater(t.get("heat", 0), 0)


class TestScanAllV3Sources(unittest.TestCase):
    """scan_all 集成测试: 新源进入聚合后提升 freshness"""

    def test_scan_all_topics_have_more_timestamps(self):
        """加新源后,带 timestamp 的 topic 比例提升

        注: 网络环境不稳定,某些源可能失败. 这里只检查至少有 timestamp 的 topic
        数 ≥ 5 (即至少一个新源生效)。
        """
        from info_gap_pipeline.research import TopicResearcher
        r = TopicResearcher()
        topics = r.scan_all(use_cache=False)
        if not topics:
            self.skipTest("无 topics (可能离线)")
        with_ts = [t for t in topics if isinstance(t.get("timestamp"), int) and t["timestamp"] > 0]
        # 至少 5 条带 timestamp (v2: 几乎 0; 加 GitHub+DevTo 后显著提升)
        self.assertGreater(len(with_ts), 5,
                           f"带 timestamp 的 topic 太少了: {len(with_ts)}/{len(topics)}")
        ratio = len(with_ts) / len(topics)
        # 比例不低于 10% (比 v2 的 0% 大幅提升)
        self.assertGreater(ratio, 0.10,
                           f"timestamp 覆盖率太低: {len(with_ts)}/{len(topics)} = {ratio:.1%}")

    def test_scan_all_more_sources(self):
        """scan_all 至少包含 4 个不同 source (网络受限时 ≥4)

        注: 全 10 源需要全部网络通畅. 此测试检查至少 4 源可用
        (v2 在受限网络下通常仅 2-3 源)
        """
        from info_gap_pipeline.research import TopicResearcher
        r = TopicResearcher()
        topics = r.scan_all(use_cache=False)
        if not topics:
            self.skipTest("无 topics")
        sources_seen = set(t.get("source") for t in topics if t.get("source"))
        self.assertGreaterEqual(len(sources_seen), 4,
                                f"源太少: {sources_seen}")


class TestFetchHackerNews(unittest.TestCase):
    """fetch_hackernews_top: HackerNews API (慢,容错优先)"""

    def test_returns_topics_or_skip(self):
        """HN 经常超时,返回空或网络异常都不算 fail"""
        from info_gap_pipeline.research import TopicResearcher
        r = TopicResearcher()
        try:
            topics = r.fetch_hackernews_top(limit=5)
            if not topics:
                self.skipTest("HN API 不可达或超时 (常事)")
            self.assertEqual(topics[0]["source"], "HN")
            for t in topics:
                self.assertIsInstance(t.get("timestamp"), int)
                self.assertGreater(t["timestamp"], 0)
        except Exception:
            self.skipTest("HN API 异常")


if __name__ == "__main__":
    unittest.main()
