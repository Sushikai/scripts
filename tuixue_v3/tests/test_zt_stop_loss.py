"""tests/test_zt_stop_loss.py — zt_backtest._simulate_trade 硬止损 + 真实 OHLC 退场 TDD

覆盖用户硬要求:
  "回测不算损失吗 如果买入第二天股票在水下呢 要用真实的第二天的股票来算"
  "T+1 买入只能 T+2 卖出啊 你没考虑?" (A 股 T+1 制度)

修复:
  - stop_loss_pct 强制作为 exit (不再是 informational)
  - 所有 exit 触发日必须是 T+2 (T+1 锁仓不能卖)
  - trail pullback 用 t2_low 验证可成交价 (不能高于 low)
  - 移除 buy_price * 0.97 假地板
  - 移除 "保本出" 假退出 (允许水下真实退出)
  - 止损实际可执行价: T+2 open ≤ stop → 按 open 集合竞价卖; 否则按 stop_price (盘中触发滑点 0)
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


# ── 1. T+2 触发止损 (开盘已破 stop → 集合竞价卖出) ──

def test_stop_loss_fires_when_t2_open_below_stop():
    """T+2 09:30 集合竞价开盘就 ≤ stop_price → 按 t2_open 集合竞价卖 (真实可成交)."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        # buy=10.5, stop_price=10.5×0.95=9.975. T+1 没触发 (high=10.5<gap_level 10.53).
        # T+2 open=9.7 ≤ stop 9.975 → 集合竞价 9.7 卖 (真实成交).
        {"日期": "20260102", "开盘": 10.5, "收盘": 10.4, "最高": 10.5, "最低": 10.3},
        {"日期": "20260105", "开盘": 9.7,  "收盘": 9.5, "最高": 9.8,  "最低": 9.4},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0)
    assert r["trigger"] == "stop_loss", f"期望 stop_loss, 实际 {r['trigger']}"
    assert r["buy_price"] == 10.5
    assert r["sell_price"] == pytest.approx(9.7, abs=0.01), f"集合竞价 9.7 卖, 实际 {r['sell_price']}"
    assert r["sell_date"] == "20260105"


# ── 2. T+2 触发止损 (盘中破 stop → 按 stop_price 卖, 滑点 0) ──

def test_stop_loss_fires_when_t2_low_below_stop_but_open_above():
    """T+2 开盘没破 stop, 但盘中跌穿 → exit at stop_price (假设滑点 0)."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        # buy=10.0, stop_price=10.0×0.95=9.5. T+2 open=9.55 (未破 9.5), low=9.3 (盘中破).
        # exit = stop_price=9.5 (你盘中在 9.5 触发瞬间卖出, 滑点 0).
        {"日期": "20260102", "开盘": 10.0, "收盘": 9.9, "最高": 10.0, "最低": 9.85},
        {"日期": "20260105", "开盘": 9.55, "收盘": 9.3, "最高": 9.6,  "最低": 9.3},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0)
    assert r["trigger"] == "stop_loss"
    assert r["sell_price"] == pytest.approx(9.5, abs=0.01), f"盘中触发 exit=9.5, 实际 {r['sell_price']}"
    assert r["sell_date"] == "20260105"


# ── 3. T+2 触发 gap_high (开盘就高开 → 按 T+2 high 卖) ──

def test_gap_high_captures_t2_high_when_t2_opens_high():
    """T+2 09:30 集合竞价高开 ≥ buy×1.003 → gap_high 触发, exit at t2_high (T+2 盘中最高)."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        # buy=10.0, gap_level=10.03. T+1 平. T+2 open=10.5 ≥ 10.03 → gap_high 触发.
        # exit at t2_high=11.0 → +10%.
        {"日期": "20260102", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        {"日期": "20260105", "开盘": 10.5, "收盘": 10.8, "最高": 11.0, "最低": 10.4},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0)
    assert r["trigger"] == "gap_high", f"期望 gap_high, 实际 {r['trigger']}"
    assert r["sell_price"] == pytest.approx(11.0, abs=0.01)
    assert r["return_pct"] > 5.0, f"应盈利, 实际 {r['return_pct']}%"
    assert r["sell_date"] == "20260105"


# ── 4. T+2 都没触发 → T+2 close 退出 (兜底路径) ──

def test_t2_close_when_neither_activated_no_stop():
    """T+1/T+2 high 都 < gap_level 也 < activate, T+2 low 没破 stop → T+2 close 退出."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        # buy=10.0. T+1/T+2 high=10.02 < gap_level 10.03 也 < activate 10.1. low=9.95 > stop 9.5.
        {"日期": "20260102", "开盘": 10.0, "收盘": 9.98, "最高": 10.02, "最低": 9.95},
        {"日期": "20260105", "开盘": 9.98, "收盘": 10.0, "最高": 10.02, "最低": 9.9},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0)
    assert r["trigger"] == "trail_t2", f"期望 trail_t2 (close 兜底), 实际 {r['trigger']}"
    assert r["return_pct"] != 0


# ── 5. T+2 暴跌触发 stop_loss (开盘就崩 → 集合竞价卖) ──

def test_stop_loss_fires_on_t2_when_t1_did_not_activate():
    """T+1 平开未触发, 但 T+2 暴跌 → T+2 止损 (开盘就破 stop_price)."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        # buy=10.0. T+1 high=10.02 < gap 10.03 → 不触发.
        {"日期": "20260102", "开盘": 10.0, "收盘": 9.9, "最高": 10.02, "最低": 9.7},
        # T+2 open=9.4 < stop 9.5 → 集合竞价 9.4 卖 (开盘已破).
        {"日期": "20260105", "开盘": 9.4,  "收盘": 9.3, "最高": 9.6,  "最低": 9.2},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0)
    assert r["trigger"] == "stop_loss"
    assert r["sell_price"] == pytest.approx(9.4, abs=0.01), f"开盘 9.4 卖, 实际 {r['sell_price']}"


# ── 6. gap_high 在 T+2 仍然按 high 出 (T+2 盘中可执行) ──

def test_gap_high_uses_t2_high_as_executable():
    """T+2 09:30 集合竞价高开, 然后盘中更高 → exit at t2_high (T+2 当天可执行).

    旧版 bug 修复: 旧版用 t1_high (lookahead, T+1 锁仓). 现在用 t2_high (合规).
    """
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        # buy=10.0. T+1 震荡. T+2 open=10.5 ≥ 10.03 → gap_high 触发, exit at t2_high=11.0.
        {"日期": "20260102", "开盘": 10.0, "收盘": 9.9, "最高": 10.05, "最低": 9.85},
        {"日期": "20260105", "开盘": 10.5, "收盘": 10.7, "最高": 11.0, "最低": 10.4},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0)
    assert r["trigger"] == "gap_high"
    assert r["sell_price"] == pytest.approx(11.0, abs=0.01), f"按 t2_high=11.0 卖, 实际 {r['sell_price']}"
    assert r["return_pct"] > 8.0


# ── 7. 极端水下 - T+2 暴跌 (开盘就崩, 集合竞价按 t2_open 卖) ──

def test_extreme_underwater_loss_now_capped_at_open():
    """T+1/T+2 high 从未涨过 gap_level, T+2 暴跌 -30% → 按 t2_open 集合竞价卖 (开盘就崩)."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        # buy=9.8, gap_level=9.8294. T+1 high=9.82<9.83 → 不触发. low=9.4>stop 9.31.
        {"日期": "20260102", "开盘": 9.8, "收盘": 9.5, "最高": 9.82, "最低": 9.4},
        # T+2 open=8.0 < stop 9.31 → 集合竞价 8.0 卖 (开盘崩).
        {"日期": "20260105", "开盘": 8.0, "收盘": 7.0, "最高": 8.5, "最低": 6.5},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0)
    assert r["trigger"] == "stop_loss"
    assert r["sell_price"] == pytest.approx(8.0, abs=0.01)
    # 真实水下 -18.6% (含成本)
    assert r["return_pct"] < -15.0, f"开盘崩应深亏, 实际 {r['return_pct']}%"


# ── 8. stop_loss=0 → T+2 开盘低于 buy 就触发 ──

def test_no_stop_loss_when_pct_is_zero():
    """stop_loss=0 → stop_price=buy, T+2 开盘低于 buy 就集合竞价卖."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        # buy=10.5, stop_price=10.5 (0%). T+2 open=10.4 < 10.5 → 集合竞价 10.4 卖.
        {"日期": "20260102", "开盘": 10.5, "收盘": 10.5, "最高": 10.5, "最低": 10.4},
        {"日期": "20260105", "开盘": 10.4, "收盘": 10.4, "最高": 10.5, "最低": 10.3},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, 0.0)
    assert r["trigger"] == "stop_loss"
    assert r["sell_price"] == pytest.approx(10.4, abs=0.01)


# ── 9. T+1 暴跌但不触发 (锁仓日) → T+2 集合竞价卖出 ──

def test_t1_crash_does_not_trigger_due_to_lockup():
    """T+1 暴跌 -15% → 你不能卖 (锁仓), 必须等 T+2. T+2 开盘就破 stop → 集合竞价卖."""
    zt = _zt()
    df = _df([
        {"日期": "20260101", "开盘": 10.0, "收盘": 10.0, "最高": 10.0, "最低": 10.0},
        # buy=11.0, stop_price=10.45. T+1 low=9.4 ≤ stop → 旧版会触发 (lookahead).
        # 修复后: T+1 锁仓, 不能卖. exit 日 = T+2.
        {"日期": "20260102", "开盘": 11.0, "收盘": 9.4, "最高": 11.0, "最低": 9.4},
        # T+2 open=10.0 < stop 10.45 → 集合竞价 10.0 卖 (开盘已破 stop).
        {"日期": "20260105", "开盘": 10.0, "收盘": 9.9, "最高": 10.1, "最低": 9.7},
    ])
    dates = ["20260101", "20260102", "20260105"]
    r = _simulate_trade(zt, df, dates, "20260102", "open_t1", 1.0, 0.5, -5.0)
    assert r["trigger"] == "stop_loss"
    assert r["sell_date"] == "20260105", f"退出日必须是 T+2, 实际 {r['sell_date']}"
    assert r["sell_price"] == pytest.approx(10.0, abs=0.01), f"开盘已破, 集合竞价 10.0 卖, 实际 {r['sell_price']}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])