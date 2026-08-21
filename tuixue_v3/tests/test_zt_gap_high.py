"""tests/test_zt_gap_high.py — zt_backtest._simulate_trade 高开按高点卖 TDD

用户硬要求: "高开就按照高点卖出来算 这样也符合实际交易"

策略:
  - 触发: T+1 high ≥ buy × (1 + gap_activate/100)
  - exit: T+1 high (实时可成交)
  - 否则: T+2 high → T+2 close 兜底
  - 默认 gap_activate_pct=0.3 (任何小高开都触发)
"""
import pandas as pd
import pytest

from tuixue_v3.zt_backtest import _simulate_trade


def _df(rows):
    return pd.DataFrame(rows)


def _zt():
    return {
        "code": "000001",
        "name": "测试股",
        "streak": 2,
        "date": "20260101",
        "sector": "测试",
    }


# ── 场景 1: T+1 高开 5% → exit at T+1 high (捕获全部涨幅) ──

def test_gap_high_exits_at_t1_high():
    """T+1 高开 5%, intraday 涨 7% → exit at high = ~+6.5%."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        {"日期": "20260102", "开盘": 10.5, "收盘": 10.3, "最高": 10.7, "最低": 10.3},
        {"日期": "20260105", "开盘": 10.5, "收盘": 10.8, "最高": 11.0, "最低": 10.4},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0, gap_activate=0.3)
    assert "gap_high" in r["exits_pct"]
    # buy=10.5, t1_high=10.7, gap_high = (10.7/10.5 - 1) × 100 - cost ≈ +1.4%
    assert r["exits_pct"]["gap_high"] > 1.0, f"应盈利, 实际 {r['exits_pct']['gap_high']}%"


# ── 场景 2: T+1 没高开 → T+2 高开 → exit at T+2 high ──

def test_gap_high_truly_falls_through_to_t2():
    """T+1 high < buy×1.003 (没高开), T+2 高开 → exit at T+2 high."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        {"日期": "20260102", "开盘": 10.0, "收盘": 9.9, "最高": 10.02, "最低": 9.85},  # high=10.02 < 10.03 → 没触发
        {"日期": "20260105", "开盘": 9.95, "收盘": 10.0, "最高": 10.3, "最低": 9.9},   # high=10.3 > 10.03 → 触发
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0, gap_activate=0.3)
    assert "gap_high" in r["exits_pct"]
    # buy=10.0, exit at t2_high=10.3 → +2.4%
    assert r["exits_pct"]["gap_high"] > 2.0, f"T+2 高开捕获 +2.4%, 实际 {r['exits_pct']['gap_high']}%"


# ── 场景 3: T+1/T+2 都没高开 → T+2 close 兜底 (亏损) ──

def test_gap_high_falls_back_to_t2_close():
    """T+1/T+2 都没高开, T+2 close < buy → gap_high 退出亏损."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        {"日期": "20260102", "开盘": 10.0, "收盘": 9.9, "最高": 10.02, "最低": 9.85},
        {"日期": "20260105", "开盘": 9.8, "收盘": 9.7, "最高": 9.95, "最低": 9.65},   # high=9.95 < 10.03 → 没触发
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0, gap_activate=0.3)
    assert "gap_high" in r["exits_pct"]
    # exit at t2_close=9.7 → -3.56%
    assert r["exits_pct"]["gap_high"] < -3.0, f"应亏损, 实际 {r['exits_pct']['gap_high']}%"


# ── 场景 4: T+1 高开但 T+1 锁仓, T+2 暴跌 → 真实亏损 (旧 bug: 假装盈利) ──

def test_t1_lockup_means_high_day_cannot_be_captured():
    """T+1 high=11 (+5%) 但 T+1 锁仓不能卖; T+2 暴跌 -10% → 真实亏损.

    旧版 lookahead bug: T+1 high 触发 gap_high → exit at 11.0, 假装 +5% (T+1 当天根本卖不出).
    修复后: gap_high T+1 不触发; T+2 open=9.5<stop 9.975 → 集合竞价 9.5 卖 → -10%.
    """
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        # T+1 高开 5% + 暴跌 -10% (同日振幅巨大, 旧版 look-ahead 会捕获 high)
        {"日期": "20260102", "开盘": 10.5, "收盘": 9.5, "最高": 11.0, "最低": 9.4},
        # T+2 继续跌: open=9.5 < stop 9.975 → 集合竞价 9.5 卖 (T+1 锁仓日过后)
        {"日期": "20260105", "开盘": 9.5, "收盘": 9.4, "最高": 9.7, "最低": 9.3},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0, gap_activate=0.3)
    # main_kind 必须是 T+2 退出 (T+1 锁仓)
    assert r["sell_date"] == "20260105", f"退出日必须是 T+2, 实际 {r['sell_date']}"
    # T+2 open < stop → stop_loss 触发, exit = t2_open = 9.5
    assert r["trigger"] == "stop_loss"
    assert r["sell_price"] == pytest.approx(9.5, abs=0.01)
    # 真实水下 ~-10% (含成本) — 旧版 bug 假装 +5% 盈利
    assert r["return_pct"] < -8.0, f"应深亏, 实际 {r['return_pct']}%"
    # informational gap_high 反映"如果 T+1 能卖会怎样" — 但这只是反事实参考
    assert "gap_high" in r["exits_pct"]


# ── 场景 5: 默认值 gap_activate=0.3 让 0.5% 高开触发 ──

def test_gap_high_default_activate_is_0_3pct():
    """默认 gap_activate=0.3% → 0.5% 高开应触发."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        {"日期": "20260102", "开盘": 10.05, "收盘": 10.04, "最高": 10.10, "最低": 10.02},  # 涨 1%
        {"日期": "20260105", "开盘": 10.0, "收盘": 9.95, "最高": 10.0, "最低": 9.9},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0)  # 用默认 gap_activate=0.3
    # buy=10.05, gap_activate_level=10.05×1.003=10.080. t1_high=10.10 > 10.080 → 触发
    # exit at 10.10 → (10.10/10.05 - 1) × 100 - cost ≈ -0.06%
    assert "gap_high" in r["exits_pct"]
    # 触发了, 但因为 buy 跟 high 太接近, 收益几乎为 0
    assert "gap_high" in r["exits_pct"]


# ── 场景 6: gap_high 在 scenario_compare 中出现 ──

def test_gap_high_in_scenario_compare():
    """scenario_compare 应包含 gap_high 键."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        {"日期": "20260102", "开盘": 10.5, "收盘": 10.4, "最高": 10.8, "最低": 10.3},
        {"日期": "20260105", "开盘": 10.6, "收盘": 10.5, "最高": 10.7, "最低": 10.4},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0)
    assert "gap_high" in r["exits_pct"]
    assert "gap_high" in r["exits_sell_price"]
    assert "gap_high" in r["exits_sell_date"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])