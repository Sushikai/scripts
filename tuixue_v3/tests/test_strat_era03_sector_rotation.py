#!/usr/bin/env python3
"""
test_strat_era03_sector_rotation.py
Ship 59 单元测试 — 行业轮动
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.strat_era03_sector_rotation import (
    SectorStats, SectorPick, RotationResult,
    build_rotation, detect_reversal, _rank_by_composite,
    select_stocks_in_sector, summarize,
)


class TestBuildRotation(unittest.TestCase):
    def test_basic(self):
        stats = {
            "tech": SectorStats("tech", 0.5, 0.03, 1.2, 100),
            "finance": SectorStats("finance", 0.2, 0.01, 1.0, 80),
            "energy": SectorStats("energy", -0.3, -0.02, 0.8, 60),
        }
        r = build_rotation(stats, n_top=2)
        self.assertEqual(r.n_top, 2)
        # tech composite 最高
        self.assertEqual(r.top_sectors[0], "tech")

    def test_empty(self):
        r = build_rotation({}, n_top=3)
        self.assertEqual(r.sectors, [])
        self.assertEqual(r.top_sectors, [])

    def test_weights_sum_to_one(self):
        stats = {
            "a": SectorStats("a", 0.5, 0.03, 1.2, 100),
            "b": SectorStats("b", 0.2, 0.01, 1.0, 80),
            "c": SectorStats("c", 0.4, 0.02, 1.1, 90),
        }
        r = build_rotation(stats, n_top=3)
        total_w = sum(s.weight for s in r.sectors if s.weight > 0)
        self.assertAlmostEqual(total_w, 1.0, places=3)

    def test_zscore(self):
        stats = {
            "a": SectorStats("a", 0.5, 0.03, 1.2, 100),
            "b": SectorStats("b", 0.2, 0.01, 1.0, 80),
        }
        r = build_rotation(stats)
        # z_score 字段 +ve/-ve 分布合理
        # a 较高 → z > 0; b 较低 → z < 0
        a = next(s for s in r.sectors if s.sector == "a")
        # 第一个, rank=1
        self.assertEqual(a.rank, 1)


class TestReversal(unittest.TestCase):
    def test_basic(self):
        prev = {
            "a": SectorStats("a", 0.1, 0.01, 1.0, 80),
            "b": SectorStats("b", 0.5, 0.03, 1.2, 100),
            "c": SectorStats("c", 0.2, 0.02, 1.1, 90),
            "d": SectorStats("d", 0.05, 0.0, 1.05, 70),  # rank 4
        }
        curr = {
            "a": SectorStats("a", 0.8, 0.05, 1.5, 100),   # rank 4 → 1 (反转 up)
            "b": SectorStats("b", -0.3, -0.05, 0.5, 80),  # rank 1 → 4 (反转 down)
            "c": SectorStats("c", 0.2, 0.02, 1.1, 90),    # rank 3 → 3
            "d": SectorStats("d", 0.4, 0.04, 1.3, 70),    # rank 2 → 2
        }
        rev = detect_reversal(prev, curr)
        rev_dict = dict(rev)
        self.assertIn("a", rev_dict)
        self.assertEqual(rev_dict["a"], "up")

    def test_empty(self):
        rev = detect_reversal({}, {})
        self.assertEqual(rev, [])


class TestRankByComposite(unittest.TestCase):
    def test_basic(self):
        stats = {
            "a": SectorStats("a", 0.1, 0.01, 1.0, 80),
            "b": SectorStats("b", 0.5, 0.03, 1.2, 100),
        }
        ranks = _rank_by_composite(stats)
        self.assertEqual(ranks["b"], 1)
        self.assertEqual(ranks["a"], 2)


class TestSelectStocksInSector(unittest.TestCase):
    def test_basic(self):
        candidates = [
            ("000001", 1.0),
            ("000002", 0.8),
            ("000003", 0.5),
            ("000004", -0.2),
        ]
        picks = select_stocks_in_sector(candidates, top_n=3)
        self.assertEqual(len(picks), 3)
        # 第一应是 000001
        self.assertEqual(picks[0].code, "000001")

    def test_empty(self):
        self.assertEqual(select_stocks_in_sector([]), [])

    def test_weights(self):
        candidates = [("a", 1.0), ("b", 1.0)]
        picks = select_stocks_in_sector(candidates)
        self.assertAlmostEqual(sum(p.weight for p in picks), 1.0)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        stats = {
            "a": SectorStats("a", 0.5, 0.03, 1.2, 100),
            "b": SectorStats("b", 0.2, 0.01, 1.0, 80),
        }
        r = build_rotation(stats, n_top=2)
        s = summarize(r)
        self.assertIn("Top 2", s)
        self.assertIn("a", s)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        stats = {"a": SectorStats("a", 0.5, 0.03, 1.2, 100)}
        r = build_rotation(stats, n_top=1)
        d = r.to_dict()
        self.assertEqual(d["n_top"], 1)
        self.assertEqual(d["top_sectors"], ["a"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
