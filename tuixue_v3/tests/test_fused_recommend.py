"""
tests/test_fused_recommend.py — 融合模块单元测试

R-2026-08-16: 验证 (1) 归一化函数, (2) 动态权重 sum=1 + 先验下限, (3) 路径 A 诚实守卫。

跑法: pytest tests/test_fused_recommend.py -v
"""
from web.fused_recommend import (
    _norm_zt, _norm_dragons, _norm_dexin,
    dynamic_weight, HONEST_WR_CEILING, OOS_MIN_WR,
)


class TestNormalize:
    def test_zt_norm_basic(self):
        # zt score 50→0, 70→50, 90→100, 低于50当0, 高于90当100
        assert _norm_zt(50.0) == 0.0
        assert _norm_zt(70.0) == 50.0
        assert _norm_zt(90.0) == 100.0
        assert _norm_zt(40.0) == 0.0   # 钳下界
        assert _norm_zt(100.0) == 100.0  # 钳上界
        assert _norm_zt(None) == 0.0
        assert _norm_zt(0) == 0.0

    def test_dragons_norm_passthrough(self):
        # dragons 已经是 0-100
        assert _norm_dragons(80.5) == 80.5
        assert _norm_dragons(None) == 0.0
        assert _norm_dragons(-10) == 0.0
        assert _norm_dragons(150) == 100.0

    def test_dexin_norm_stage(self):
        # stage 0-4 → 0-100, de_xin/clearing 加成
        assert _norm_dexin("none") == 0.0
        assert _norm_dexin("cang_zha") > 0
        assert _norm_dexin("xu_sha") > _norm_dexin("cang_zha")
        assert _norm_dexin("clearing") > _norm_dexin("xu_sha")  # clearing 有 +10 bonus
        assert _norm_dexin("de_xin") >= 100.0  # de_xin stage 4 + 20 bonus, 钳到 100
        assert _norm_dexin("no_uptrend") == 0.0
        # dangerous variant 减分
        assert _norm_dexin("xu_sha", "dangerous_break") < _norm_dexin("xu_sha")
        # unknown stage
        assert _norm_dexin("foo") == 0.0


class TestDynamicWeight:
    def test_sum_invariant(self):
        # 三路权重 sum 必须 == 1 (容差 1e-3 处理 round 累加误差)
        zt = [60, 70, 80, 90]
        dr = [55, 65, 75, 85]
        dx = [40, 50, 60, 70]
        w = dynamic_weight(zt, dr, dx)
        assert abs(sum(w) - 1.0) < 1e-3, f"sum={sum(w)}, weights={w}"

    def test_floor(self):
        # 先验下限: 任一路 0 方差不能霸榜
        zt = [80, 80, 80, 80]  # var=0 → 默认 100 → 反方差 1/100=0.01
        dr = [10, 30, 70, 90]  # var 大
        dx = [20, 50, 50, 80]
        w = dynamic_weight(zt, dr, dx)
        assert w[0] >= 0.25 - 1e-3, f"zt floor violated: {w}"
        assert w[1] >= 0.20 - 1e-3, f"dragons floor violated: {w}"
        assert w[2] >= 0.15 - 1e-3, f"dexin floor violated: {w}"
        assert abs(sum(w) - 1.0) < 1e-3

    def test_single_array_no_crash(self):
        # 极端: 单元素数组 (走 fallback default=100)
        w = dynamic_weight([80], [70], [60])
        assert abs(sum(w) - 1.0) < 1e-3


class TestHonestCeilingConstants:
    """R-2026-08-16 路径 A: 诚实上限常量必须守住记忆里的 58-63%。"""

    def test_honest_ceiling_below_80(self):
        # 记忆 [[zt-honest-wr-ceiling]] 实测 58-63%
        # 我们的上限是 65 (留 2pp 安全垫), 绝不能等于或超过 80 (cheating)
        assert HONEST_WR_CEILING < 70, f"诚实上限 {HONEST_WR_CEILING} 应 < 70"
        assert HONEST_WR_CEILING >= 60, f"诚实上限 {HONEST_WR_CEILING} 应 ≥ 60"

    def test_oos_min_below_honest(self):
        # OOS 硬门槛 应 ≤ 诚实上限 (允许一定保守)
        assert OOS_MIN_WR <= HONEST_WR_CEILING
