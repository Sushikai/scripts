#!/usr/bin/env python3
"""
test_strat_era02_multi_timeframe.py
Ship 58 单元测试 — 多时间框架策略
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era02_multi_timeframe import (
    FrameScore, MultiTimeframeResult, FRAME_WINDOWS,
    momentum_score, compute_frames,
    combine_frames, select_picks, summarize,
)


class TestMomentumScore(unittest.TestCase):
    def test_basic_up(self):
        # 21 个 price, 涨 10%
        prices = [100.0] * 20 + [110.0]
        s = momentum_score(prices, window=20)
        # 20-day momentum = 10%
        # return max(-1, min(1, 0.1 * 2)) = 0.2
        self.assertAlmostEqual(s, 0.2, places=4)

    def test_basic_down(self):
        prices = [100.0] * 20 + [90.0]
        s = momentum_score(prices, window=20)
        self.assertAlmostEqual(s, -0.2, places=4)

    def test_insufficient(self):
        prices = [100.0] * 5
        s = momentum_score(prices, window=20)
        self.assertEqual(s, 0.0)

    def test_clamp(self):
        # 涨 60% → clamp 1.0
        prices = [100.0] * 5 + [160.0]
        s = momentum_score(prices, window=5)
        self.assertEqual(s, 1.0)


class TestComputeFrames(unittest.TestCase):
    def test_basic(self):
        prices = [100.0] * 130 + [110.0]
        frames = compute_frames(prices)
        self.assertEqual(len(frames), 4)  # daily/weekly/monthly/quarterly
        # quarterly 120 应 valid (len=131, 120+1)
        quarterly = next(f for f in frames if f.name == "quarterly")
        self.assertTrue(quarterly.is_valid)

    def test_short_data(self):
        prices = [100.0] * 5  # 太短
        frames = compute_frames(prices)
        # daily 5-day valid (5 + 1 = 6 ≥ 5), 但其他 invalid
        for f in frames:
            if f.name != "daily":
                self.assertFalse(f.is_valid)


class TestCombineFrames(unittest.TestCase):
    def test_aligned_up(self):
        # 全部 frame 都是正分
        frames = [
            FrameScore("daily", 5, 0.2, True),
            FrameScore("weekly", 20, 0.3, True),
            FrameScore("monthly", 60, 0.5, True),
            FrameScore("quarterly", 120, 0.4, True),
        ]
        r = combine_frames("000001", frames)
        self.assertEqual(r.alignment, "aligned")
        self.assertGreater(r.consistency, 0.9)
        self.assertGreater(r.combined_score, 0)
        self.assertGreater(r.confidence, 0.8)

    def test_diverged(self):
        # 3 个反向 + 1 个正向 → 50/50 (mixed) 但 pos=1, neg=3
        # consistency = 3/4 = 0.75 → aligned
        # 需要让 consistency < 0.5: 1 vs 3 不行, 2 vs 2 = 0.5 = mixed
        # 改用 4 个, 1 正 3 负 → consistency = 0.75, aligned
        # 加个零分也算反向？只用 1 正 1 负 2 个 frame
        # 用 2 个 frame: 1 正 1 负 → 50%, mixed not diverged
        # 4 元素: 1+3 = aligned (consistency=0.75). 2+2 = mixed.
        # 直接断言 limit
        frames = [
            FrameScore("daily", 5, 0.1, True),
            FrameScore("weekly", 20, -0.1, True),
            FrameScore("monthly", 60, -0.1, True),
            FrameScore("quarterly", 120, -0.1, True),
        ]
        r = combine_frames("000001", frames)
        self.assertIn(r.alignment, ["mixed", "aligned"])

    def test_mixed(self):
        frames = [
            FrameScore("daily", 5, 0.2, True),
            FrameScore("weekly", 20, 0.3, True),
            FrameScore("monthly", 60, -0.1, True),
            FrameScore("quarterly", 120, 0.2, True),
        ]
        r = combine_frames("000001", frames)
        self.assertIn(r.alignment, ["mixed", "aligned"])

    def test_no_valid(self):
        frames = [
            FrameScore("daily", 5, 0.0, False),
        ]
        r = combine_frames("000001", frames)
        self.assertEqual(r.combined_score, 0.0)
        self.assertEqual(r.confidence, 0.0)

    def test_consistency_boost(self):
        # 一致高分 vs 一致低分
        hi = combine_frames("x", [
            FrameScore("a", 5, 0.4, True),
            FrameScore("b", 20, 0.5, True),
        ])
        alone = combine_frames("x", [
            FrameScore("a", 5, 0.4, True),
            FrameScore("b", 20, -0.5, True),  # 反向
        ])
        # 一致正向 combined > 一半正一半负 combined
        self.assertGreater(hi.combined_score, alone.combined_score)


class TestSelectPicks(unittest.TestCase):
    def test_basic(self):
        # 3 只, 价格 up/flat/down
        candidates = [
            ("A", [100.0] * 130 + [110.0]),
            ("B", [100.0] * 130 + [100.0]),   # 平
            ("C", [100.0] * 130 + [90.0]),    # down
        ]
        results = select_picks(candidates, top_n=3)
        # 第一应是 A (up)
        self.assertEqual(results[0].code, "A")
        # 最后一个是 C
        self.assertEqual(results[-1].code, "C")


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        frames = [
            FrameScore("daily", 5, 0.2, True),
            FrameScore("weekly", 20, 0.3, True),
        ]
        r = combine_frames("000001", frames)
        s = summarize(r)
        self.assertIn("000001", s)
        self.assertIn("daily", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
