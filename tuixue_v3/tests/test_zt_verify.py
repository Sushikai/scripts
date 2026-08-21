"""
tests/test_zt_verify.py — ZT 验证循环单元测试
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from tuixue_v3 import zt_verify as verify
from tuixue_v3 import zt_config as cfg


class TestCheckTargets:
    """达标检查"""

    def test_pass_all_targets(self):
        """所有目标达标 → passed=True
        需要日均 >= 5.65% 才能月化 >= 200%
        6% daily → 60天后 total = (1.06^60-1)*100 ≈ 3299%, 月化≈220%
        """
        total_6pct_60d = round((1.06 ** 60 - 1) * 100, 2)  # ~3299%
        summary = {
            "total_return_pct": total_6pct_60d,
            "win_rate_pct": 60.0,
            "max_drawdown_pct": -15.0,
            "trades": 100,
        }
        raw = {"trade_dates_total": 60}  # 60个交易日
        passed, reasons = verify._check_targets(summary, raw)
        assert passed, f"应达标但未通过: {reasons}"
        assert len(reasons) == 0

    def test_fail_daily_return(self):
        """日均收益不足 5%"""
        summary = {
            "total_return_pct": 50.0,
            "win_rate_pct": 60.0,
            "max_drawdown_pct": -10.0,
            "trades": 100,
        }
        raw = {"trade_dates_total": 60}
        passed, reasons = verify._check_targets(summary, raw)
        assert not passed
        assert any("日均" in r for r in reasons)

    def test_fail_win_rate(self):
        """胜率不足 50%"""
        summary = {
            "total_return_pct": 5000.0,
            "win_rate_pct": 40.0,
            "max_drawdown_pct": -15.0,
            "trades": 100,
        }
        raw = {"trade_dates_total": 60}
        passed, reasons = verify._check_targets(summary, raw)
        assert not passed
        assert any("胜率" in r for r in reasons)

    def test_fail_drawdown(self):
        """回撤超过 -30%"""
        summary = {
            "total_return_pct": 5000.0,
            "win_rate_pct": 60.0,
            "max_drawdown_pct": -40.0,
            "trades": 100,
        }
        raw = {"trade_dates_total": 60}
        passed, reasons = verify._check_targets(summary, raw)
        assert not passed
        assert any("回撤" in r for r in reasons)

    def test_fail_few_trades(self):
        """交易太少"""
        summary = {
            "total_return_pct": 5000.0,
            "win_rate_pct": 100.0,
            "max_drawdown_pct": 0.0,
            "trades": 5,
        }
        raw = {"trade_dates_total": 60}
        passed, reasons = verify._check_targets(summary, raw)
        assert not passed
        assert any("太少" in r for r in reasons)

    def test_edge_near_target(self):
        """接近但未达到目标"""
        # daily_ret 4.9% (close to 5%)
        total_ret = ((1 + 0.049) ** 60 - 1) * 100  # 4.9% daily for 60 days
        summary = {
            "total_return_pct": round(total_ret, 2),
            "win_rate_pct": 50.0,
            "max_drawdown_pct": -20.0,
            "trades": 200,
        }
        raw = {"trade_dates_total": 60}
        passed, reasons = verify._check_targets(summary, raw)
        # compound_daily = (1+total_ret/100)^(1/60)-1 ≈ 4.9% < 5%
        assert not passed


@pytest.mark.slow
class TestVerifyCycle:
    """验证循环"""

    def test_verify_short_run(self):
        """跑1轮完整验证循环（20次迭代，快速验证能跑通）"""
        old_max = cfg.ZT_VERIFY_MAX_ITERATIONS
        try:
            r = verify.verify(iterations=10, max_loops=1)
            assert "passed" in r
            assert "best_params" in r
            assert "holdout_result" in r
        finally:
            pass

    def test_verify_returns_structure(self):
        """验证返回结构完整"""
        r = verify.verify(iterations=5, max_loops=1)
        for key in ["passed", "best_params", "final_result", "holdout_result", "elapsed_sec"]:
            assert key in r, f"缺少 key: {key}"
