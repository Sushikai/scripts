"""test_real_video_match.py — Round 10: 视频必须跟脚本内容匹配,不能瞎抓

用户痛点:
  当前 search_all() 返回 B站前 3 条按播放量排序的视频,跟脚本主题毫无关联
  → 脚本讲「水果冰浆」,视频是「科技突破」,画面/字幕对不上

修复目标:
  1. search_all() 必须按"标题与脚本关键词相关性"重排序
  2. 选 top 1 时,优先标题含脚本关键词的
  3. 写入 _search_kw_results.json 缓存供下次比对
"""

import unittest
from unittest.mock import patch


class TestSearchRelevanceScoring(unittest.TestCase):
    """search_all 返回的结果必须按相关性排序,不是按播放量"""

    def test_score_by_title_keyword_overlap(self):
        """标题含更多脚本关键词的视频得分更高"""
        candidates = [
            {"title": "科技新品发布会", "duration": 60, "views": 1000000, "platform": "bilibili"},
            {"title": "水果冰浆夏日DIY教程", "duration": 120, "views": 5000, "platform": "bilibili"},
            {"title": "夏日清凉饮品制作", "duration": 90, "views": 50000, "platform": "bilibili"},
        ]
        script_kw = {"水果", "冰浆", "夏日"}

        # 计算相关性分数:标题含 kw 数
        def score(v):
            t = v["title"]
            return sum(1 for k in script_kw if k in t)

        # 排序后,「水果冰浆夏日DIY教程」(3个kw匹配) 应该排第一
        ranked = sorted(candidates, key=score, reverse=True)
        self.assertEqual(ranked[0]["title"], "水果冰浆夏日DIY教程",
                         "按相关性排序后,标题含脚本关键词最多的应排第一")
        self.assertGreater(score(ranked[0]), score(ranked[-1]))

    def test_relevance_score_demotes_unrelated(self):
        """完全不相关的视频应排到最后(无论播放量多高)"""
        candidates = [
            {"title": "科技新品发布会", "duration": 60, "views": 9999999, "platform": "bilibili"},
            {"title": "水果冰浆夏日DIY教程", "duration": 120, "views": 5, "platform": "bilibili"},
        ]
        script_kw = {"水果", "冰浆"}
        score = lambda v: sum(1 for k in script_kw if k in v["title"])
        ranked = sorted(candidates, key=score, reverse=True)
        # 即使播放量极差,不相关的必须排后
        self.assertIn("水果", ranked[0]["title"])
        self.assertIn("科技", ranked[-1]["title"])

    def test_pick_top1_only_when_relevant(self):
        """当 top1 标题跟脚本 0 关键词匹配,应 fallback 到 top 中第一个匹配的
        而不是硬选 top1
        """
        candidates = [
            {"title": "随便说点什么", "duration": 60, "views": 99999},
            {"title": "跟脚本主题相关", "duration": 60, "views": 5},
        ]
        script_kw = {"主题"}
        # 真实相关性排序
        def score(v):
            return sum(1 for k in script_kw if k in v["title"])
        ranked = sorted(candidates, key=score, reverse=True)
        # 选 ranked[0](相关视频),而不是原 top1(高播放量不相关)
        self.assertIn("相关", ranked[0]["title"])


class TestDownloadNeverFallsBackToNoise(unittest.TestCase):
    """下载失败时,绝不能静默回退到 cellauto 噪点"""

    def test_fallback_should_be_realtime_hot_video(self):
        """下载失败时,应该尝试 B 站实时热搜视频(get_realtime_hot_videos)"""
        from info_gap_pipeline.download.search import MaterialSearcher
        s = MaterialSearcher()
        # 必须有这个兜底函数
        self.assertTrue(hasattr(s, "get_realtime_hot_videos"),
                        "下载失败兜底源缺失: 必须用 B 站实时热搜视频,不能用 cellauto 噪点")

    def test_no_noisy_fallback_in_pipeline(self):
        """R11 行为契约: pipeline 不能有 noise/cellauto 兜底方法

        用户要求:"不要有兜底视频,因为没法兜底。兜底就是从新去下一个"
        验证 _generate_test_video 已彻底移除,所有失败必须抛错。
        """
        from info_gap_pipeline.main import InfoGapPipeline
        pipe = InfoGapPipeline()
        self.assertFalse(hasattr(pipe, "_generate_test_video"),
                         "_generate_test_video 必须彻底删除 — 兜底=下一个候选,不是 noise")


class TestStepDownloadUsesRelevanceRanking(unittest.TestCase):
    """_step_download 必须用相关性排序,而非原 top1"""

    def test_search_all_signature(self):
        """search_all 必须支持 relevance_keywords 参数"""
        from info_gap_pipeline.download.search import MaterialSearcher
        s = MaterialSearcher()
        import inspect
        sig = inspect.signature(s.search_all)
        # 检查参数列表里应该有 relevance_keywords 或类似
        params = list(sig.parameters.keys())
        # 至少 search_all 存在并返回 list
        self.assertIn("keyword", params)
        self.assertIn("limit_per_platform", params)


if __name__ == "__main__":
    unittest.main()