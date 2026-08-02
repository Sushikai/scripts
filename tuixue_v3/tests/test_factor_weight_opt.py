#!/usr/bin/env python3
"""
test_factor_weight_opt.py
Ship 45 单元测试 — 多因子权重优化
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tuixue_v3.factor_weight_opt import (
    FactorIR, WeightOptResult,
    _ir_from_series, compute_irs,
    equal_weight, ir_weighted, ic_ir_blend, diversification_aware,
    optimize, to_dict, summarize, validate_weights,
)


class TestIR(unittest.TestCase):
    def test_basic(self):
        f = _ir_from_series([0.05, 0.06, 0.07, 0.04])
        self.assertAlmostEqual(f.ic_mean, 0.055, places=4)
        self.assertGreater(f.ir, 0)

    def test_negative_ir(self):
        f = _ir_from_series([-0.05, -0.06, -0.04])
        self.assertLess(f.ir, 0)

    def test_ir_zero(self):
        f = _ir_from_series([0.05, 0.05, 0.05, 0.05])
        self.assertEqual(f.ic_std, 0.0)
        self.assertEqual(f.ir, 0.0)

    def test_compute_irs(self):
        ic_dict = {
            "a": [0.05, 0.06, 0.07],
            "b": [-0.05, -0.06, -0.04],
        }
        irs = compute_irs(ic_dict)
        self.assertEqual(len(irs), 2)
        self.assertEqual({f.factor for f in irs}, {"a", "b"})


class TestEqual(unittest.TestCase):
    def test_3_factors(self):
        w = equal_weight(["a", "b", "c"])
        self.assertAlmostEqual(w["a"], 1.0 / 3)
        self.assertAlmostEqual(w["b"], 1.0 / 3)
        self.assertAlmostEqual(w["c"], 1.0 / 3)

    def test_empty(self):
        self.assertEqual(equal_weight([]), {})


class TestIRWeighted(unittest.TestCase):
    def test_basic(self):
        from tuixue_v3.factor_weight_opt import FactorIR
        irs = [
            FactorIR("a", 0.05, 0.02, 2.5, 100),
            FactorIR("b", 0.03, 0.02, 1.5, 100),
            FactorIR("c", 0.01, 0.02, 0.5, 100),
        ]
        w = ir_weighted(irs, abs_weight=True)
        # a 比例最大
        self.assertEqual(max(w, key=lambda k: w[k]), "a")
        # 总和 = 1
        total = sum(w.values())
        self.assertAlmostEqual(total, 1.0)

    def test_negative_ir(self):
        from tuixue_v3.factor_weight_opt import FactorIR
        irs = [
            FactorIR("a", 0.05, 0.02, 2.5, 100),
            FactorIR("bad", -0.05, 0.02, -2.5, 100),
        ]
        w = ir_weighted(irs, abs_weight=False)
        # bad 不分配
        self.assertEqual(w["bad"], 0.0)
        self.assertAlmostEqual(w["a"], 1.0)


class TestICIRBlend(unittest.TestCase):
    def test_basic(self):
        from tuixue_v3.factor_weight_opt import FactorIR
        irs = [
            FactorIR("a", 0.05, 0.02, 2.5, 100),
            FactorIR("b", 0.10, 0.05, 2.0, 100),
        ]
        w = ic_ir_blend(irs, alpha=0.5)
        self.assertAlmostEqual(sum(w.values()), 1.0)

    def test_alpha_one_uses_only_ic(self):
        from tuixue_v3.factor_weight_opt import FactorIR
        irs = [
            FactorIR("a", 0.05, 0.02, 2.5, 100),  # high IR
            FactorIR("b", 0.10, 0.05, 2.0, 100),  # high IC
        ]
        w = ic_ir_blend(irs, alpha=1.0)
        # b 的 IC 更高 → b 应该权重大
        self.assertGreater(w["b"], w["a"])


class TestDiversification(unittest.TestCase):
    def test_basic(self):
        from tuixue_v3.factor_weight_opt import FactorIR
        irs = [
            FactorIR("a", 0.05, 0.02, 2.5, 100),
            FactorIR("b", 0.03, 0.02, 1.5, 100),
        ]
        w = diversification_aware(irs)
        self.assertAlmostEqual(sum(w.values()), 1.0)

    def test_corr_penalty(self):
        from tuixue_v3.factor_weight_opt import FactorIR
        irs = [
            FactorIR("a", 0.05, 0.02, 2.0, 100),
            FactorIR("b", 0.05, 0.02, 2.0, 100),
        ]
        # a-b 高相关 0.9
        corr = [[1.0, 0.9], [0.9, 1.0]]
        w = diversification_aware(irs, correlation_matrix=corr, penalty=0.5)
        self.assertAlmostEqual(sum(w.values()), 1.0)


class TestOptimize(unittest.TestCase):
    def test_method_equal(self):
        ic_dict = {
            "a": [0.05, 0.06, 0.07],
            "b": [0.04, 0.05, 0.06],
            "c": [0.03, 0.04, 0.05],
        }
        r = optimize(ic_dict, method="equal")
        self.assertEqual(r.method, "equal")
        self.assertAlmostEqual(sum(r.weights.values()), 1.0)

    def test_method_ir(self):
        ic_dict = {
            "a": [0.05, 0.06, 0.07, 0.04],
            "b": [0.01, 0.02, 0.015, 0.025],
        }
        r = optimize(ic_dict, method="ir")
        self.assertAlmostEqual(sum(r.weights.values()), 1.0)

    def test_method_ic_ir(self):
        ic_dict = {"a": [0.05, 0.06], "b": [0.03, 0.04]}
        r = optimize(ic_dict, method="ic_ir")
        self.assertAlmostEqual(sum(r.weights.values()), 1.0)

    def test_method_diversification(self):
        ic_dict = {"a": [0.05, 0.06], "b": [0.03, 0.04]}
        r = optimize(ic_dict, method="diversification")
        self.assertAlmostEqual(sum(r.weights.values()), 1.0)


class TestToDict(unittest.TestCase):
    def test_basic(self):
        ic_dict = {"a": [0.05, 0.06], "b": [0.03, 0.04]}
        r = optimize(ic_dict)
        d = to_dict(r)
        self.assertIn("weights", d)
        self.assertIn("factor_irs", d)
        self.assertEqual(len(d["factor_irs"]), 2)


class TestSummarize(unittest.TestCase):
    def test_basic(self):
        ic_dict = {"a": [0.05, 0.06]}
        r = optimize(ic_dict)
        s = summarize(r)
        self.assertIn("a:", s)


class TestValidateWeights(unittest.TestCase):
    def test_sum_to_one(self):
        self.assertTrue(validate_weights({"a": 0.5, "b": 0.5}))
        self.assertTrue(validate_weights({"a": 1.0}))
        self.assertFalse(validate_weights({"a": 0.5, "b": 0.3}))
        self.assertFalse(validate_weights({"a": -0.5}))
        self.assertFalse(validate_weights({}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
