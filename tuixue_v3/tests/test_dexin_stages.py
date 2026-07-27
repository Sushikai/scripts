"""
tests/test_dexin_stages.py — 得鑫量变术 时序链条判定核心逻辑单测 (TDD, 先写)。

被测 (web/dexin_screener.py):
  _detect_box(df, lookback=20)
  _detect_gaps(df, lookback=30)
  _gap_filled(df, gap)
  _classify_stage(df, modules=None)        # 向后兼容入口
  DexinTrendAgent.detect(df, modules=None)  # 新时序链条主入口

阶段 (phase): no_uptrend / none / cang_zha / xu_sha / clearing / de_xin
虚杀 variant: benign / dangerous
时序链条: 上升趋势(前置) → 藏诈诱多 → 虚杀洗盘 → 抛压清零 → 得鑫突破
"""
import numpy as np
import pandas as pd
import pytest

from web import dexin_screener as dx


# ── df 构造助手 ──────────────────────────────────────────
def _mk_df(closes, opens=None, highs=None, lows=None, vols=None):
    n = len(closes)
    closes = [float(c) for c in closes]
    if opens is None:
        opens = [closes[0]] + closes[:-1]
    if highs is None:
        highs = [max(o, c) * 1.005 for o, c in zip(opens, closes)]
    if lows is None:
        lows = [min(o, c) * 0.995 for o, c in zip(opens, closes)]
    if vols is None:
        vols = [10000.0] * n
    dates = pd.bdate_range("2026-05-01", periods=n)
    df = pd.DataFrame({
        "日期": dates,
        "开盘": [float(x) for x in opens],
        "最高": [float(x) for x in highs],
        "最低": [float(x) for x in lows],
        "收盘": closes,
        "成交量": [float(x) for x in vols],
    })
    df["涨跌幅"] = df["收盘"].pct_change().fillna(0) * 100
    return df


def _mk_uptrend_then_fraud_no_kill():
    """30日: 缓慢上升 + 末尾一次藏诈日(创10日新高+阳线+实体饱满) + 但次日只有小幅阴线(跌幅<3%,不满足虚杀)。

    期望: cang_zha (无虚杀匹配)。
    """
    closes = []
    # 0-19: 缓慢上升 (确保 ma20 斜率 > 0)
    base = 10.0
    for i in range(20):
        base += 0.05
        closes.append(round(base, 2))
    # 20: 藏诈日. 要满足: close ≥ close_high_n.shift(1) AND chg ≥ 3% AND 阳线 AND body/range ≥ 70%
    #    前 10 日最高 ≈ 10.95 (idx 19=10.99). 前 1 日 close=10.99. 涨幅 3% → close ≥ 10.99 * 1.03 = 11.32.
    #    open = 11.00 (实体 0.32, body/range 0.32/0.40=0.80 ✓)
    closes.append(11.40)   # idx 20
    # 21-29: 小幅震荡 (跌幅均 < 3%, 不触发虚杀)
    closes.extend([11.35, 11.42, 11.30, 11.45, 11.42, 11.48, 11.43, 11.50, 11.55])
    opens  = [closes[0]] + closes[:-1]
    highs  = [max(o, c) * 1.002 for o, c in zip(opens, closes)]
    lows   = [min(o, c) * 0.998 for o, c in zip(opens, closes)]
    # 藏诈日单独放宽 high/low 让 body_ratio = (11.40-11.00)/(11.50-10.95) = 0.40/0.55 = 0.73 ✓
    highs[20] = 11.50; lows[20] = 10.95
    vols = [10000.0] * 30
    vols[20] = 30000  # 藏诈放量
    return _mk_df(closes, opens=opens, highs=highs, lows=lows, vols=vols)


def _mk_uptrend_with_full_chain_de_xin():
    """完整链条: 上升 → 藏诈 → 虚杀(回撤≤15%) → 抛压清零(3日不创新低+振幅收缩) → 得鑫突破(突破藏诈高点+涨幅≥4%+阳线+量能1.2倍)。

    设计原则:
    - 虚杀日 close > ma20 (避免 dangerous_break 误判).
    - 清零窗口 27-29 的 low 全部 ≥ kill_low (避免未清零).
    - 藏诈阶段振幅足够大, 让清零振幅收缩条件满足 (avg_amp ≤ fraud_amp × 0.6).
    """
    # 25 天前置: 上升, ma20 倾斜向上
    pre = []
    base = 10.0
    for i in range(25):
        base += 0.08
        pre.append(round(base, 2))
    # idx 24 close ≈ 11.92, ma20 ≈ 11.16
    # 25 藏诈: 涨幅 5%, close ≈ 12.52, 阳线
    fraud_high = pre[-1] * 1.05      # 12.52
    # 26 虚杀: 跌 3%, close = 12.52*0.97 = 12.14 > ma20(11.42) ✓
    kill_close = fraud_high * 0.97   # 12.14
    kill_low   = kill_close * 0.99   # 12.02
    # 27-28 清零: low >= kill_low (12.02), 振幅小
    clear_low   = kill_low           # 12.02
    clear_high  = clear_low * 1.005  # 12.08
    # 29 得鑫: close >= fraud_high (12.52), 涨幅 ≥ 4%, 阳线, 量能 1.2x
    gain_close = fraud_high * 1.06   # 13.27

    closes = pre + [fraud_high, kill_close, clear_high, clear_high * 1.001, gain_close]
    # 让 opens 让 27-28 的 low ≥ kill_low (不要 open 太低)
    opens  = pre[:25] + [
        fraud_high * 0.94,        # 25 阳线
        fraud_high,               # 26 阴线
        clear_low,                # 27 阳线 (open = low, 保证 low=clear_low)
        clear_low * 1.001,        # 28 阳线
        gain_close * 0.96,        # 29 阳线
    ]
    highs = pre[:25] + [
        fraud_high * 1.005,       # 25 high (藏诈振幅 ≈ 0.5%)
        fraud_high,               # 26 high
        clear_high * 1.001,       # 27 high (振幅小)
        clear_high * 1.001,       # 28 high
        gain_close * 1.005,       # 29 high
    ]
    # 关键: 27-28 的 low = clear_low (等于 kill_low), 不能 < kill_low
    lows  = pre[:25] + [
        fraud_high * 0.97,        # 25 low
        kill_low,                 # 26 low (= kill_low)
        clear_low,                # 27 low (>= kill_low)
        clear_low,                # 28 low (>= kill_low)
        gain_close * 0.985,       # 29 low
    ]
    vols = [10000.0] * 30
    vols[25] = 30000  # 藏诈放量
    vols[29] = 40000  # 得鑫放量 (5日均量 = (10000*4+30000)/5 = 14000, ×1.2 = 16800; 40000 ✓)
    return _mk_df(closes, opens=opens, highs=highs, lows=lows, vols=vols)


def _mk_clearing_no_breakout():
    """30 日: 完整藏诈+虚杀+清零, 但末尾未突破(收盘 < fraud_high)。期望: clearing。"""
    pre = []
    base = 10.0
    for i in range(25):
        base += 0.08
        pre.append(round(base, 2))
    fraud_high = pre[-1] * 1.05
    kill_close = fraud_high * 0.97
    kill_low   = kill_close * 0.995
    # 27-28 清零: low == kill_low (不创新低), 振幅小
    # 29: 收盘 < fraud_high, 涨幅小, 不突破
    no_break_close = fraud_high * 0.99  # 收在 fraud_high 下方约 1%

    closes = pre + [fraud_high, kill_close, kill_low * 1.005, kill_low * 1.008, no_break_close]
    opens  = pre[:25] + [
        fraud_high * 0.94,
        fraud_high,
        kill_low,        # 27 open = kill_low (避免 low < kill_low)
        kill_low * 1.003,
        no_break_close * 0.99,
    ]
    highs = pre[:25] + [
        fraud_high * 1.005,
        fraud_high,
        kill_low * 1.008,
        kill_low * 1.012,
        no_break_close * 1.005,
    ]
    lows  = pre[:25] + [
        fraud_high * 0.97,
        kill_low,
        kill_low,        # 27 low = kill_low (不创新低)
        kill_low * 1.001,
        no_break_close * 0.985,
    ]
    vols = [10000.0] * 30
    vols[25] = 30000
    return _mk_df(closes, opens=opens, highs=highs, lows=lows, vols=vols)


def _mk_dangerous_xu_sha():
    """30 日: 上升 + 藏诈 + 虚杀 (回撤 > 15%) → 危险剔除。

    设计: 末端企稳在 ma20 上方(避免触发 no_uptrend 前置过滤), 仅靠 drawdown > 15% 触发 dangerous.
    """
    pre = []
    base = 10.0
    for i in range(25):
        base += 0.08
        pre.append(round(base, 2))
    fraud_high = pre[-1] * 1.05  # 12.52
    # 虚杀回撤 16% (> 危险阈值 15%)
    kill_low = fraud_high * 0.84  # 10.52
    # 后续几天企稳在 ma20 上方
    # ma20 在 idx 29 约为 11.40 (要看实际计算). 设 settle close = 11.80 (大幅高于 ma20)
    settle_close = 11.80
    closes = pre + [
        fraud_high,
        kill_low * 1.02,       # 26 虚杀阴线, close ≈ 10.73
        settle_close - 0.10,   # 27
        settle_close,           # 28
        settle_close - 0.05,   # 29
    ]
    opens  = pre[:25] + [
        fraud_high * 0.94,
        fraud_high,
        kill_low * 1.05,
        settle_close - 0.15,
        settle_close - 0.10,
    ]
    highs = pre[:25] + [
        fraud_high * 1.005,
        fraud_high,
        kill_low * 1.06,
        settle_close + 0.02,
        settle_close + 0.01,
    ]
    lows  = pre[:25] + [
        fraud_high * 0.97,
        kill_low,
        settle_close - 0.15,
        settle_close - 0.05,
        settle_close - 0.10,
    ]
    vols = [10000.0] * 30
    return _mk_df(closes, opens=opens, highs=highs, lows=lows, vols=vols)


def _mk_xu_sha_benign_in_progress():
    """30 日: 上升 + 藏诈 + 虚杀 + 但末端仍在跌(未清零)。期望: xu_sha benign。

    关键: 末端 close > ma20 (避免 dangerous_break/no_uptrend).
    末端 low 跌穿 kill_low 之下, 触发"未清零".
    """
    pre = []
    base = 10.0
    for i in range(25):
        base += 0.08
        pre.append(round(base, 2))
    fraud_high = pre[-1] * 1.05   # 12.52
    kill_close = fraud_high * 0.97  # 12.14
    kill_low   = kill_close * 0.99  # 12.02
    # ma20 在 29 日约为 11.50. 末端 close 必须 > ma20.
    # 末端 (27-29) low 创 kill_low 新低, 同时 close > ma20
    settle_high = 12.30  # 末端高 close, 保证 > ma20 + 创新低
    settle_low_falling = kill_low * 0.97  # 11.66 < kill_low=12.02 (创 kill_low 新低)
    closes = pre + [
        fraud_high,                  # 25 藏诈
        kill_close,                  # 26 虚杀
        settle_high - 0.20,          # 27 close ≈ 12.10, low=11.66 (创 kill_low 新低)
        settle_high - 0.10,          # 28 close ≈ 12.20
        settle_high,                  # 29 close ≈ 12.30 (> ma20)
    ]
    opens  = pre[:25] + [
        fraud_high * 0.94,
        fraud_high,
        settle_high - 0.30,
        settle_high - 0.20,
        settle_high - 0.15,
    ]
    highs = pre[:25] + [
        fraud_high * 1.005,
        fraud_high,
        settle_high - 0.05,
        settle_high,
        settle_high + 0.05,
    ]
    lows  = pre[:25] + [
        fraud_high * 0.97,
        kill_low,
        settle_low_falling,   # 27 low < kill_low
        settle_low_falling * 0.99,  # 28 继续
        settle_low_falling * 0.98,  # 29 继续
    ]
    vols = [10000.0] * 30
    return _mk_df(closes, opens=opens, highs=highs, lows=lows, vols=vols)


def _mk_no_uptrend():
    """30 日: 持续下跌, ma20 向下, 斜率 ≤ 0 → 期望 no_uptrend。"""
    closes = []
    base = 20.0
    for i in range(30):
        base -= 0.5
        closes.append(base)
    opens  = [closes[0]] + closes[:-1]
    highs  = [max(o, c) * 1.005 for o, c in zip(opens, closes)]
    lows   = [min(o, c) * 0.995 for o, c in zip(opens, closes)]
    return _mk_df(closes, opens=opens, highs=highs, lows=lows)


def _mk_flat_30d():
    """30 日: 横盘无趋势, 涨幅 0, 但 ma20 斜率 = 0 → 期望 no_uptrend (斜率必须 > 0)。"""
    closes = [10.0] * 30
    opens  = [10.0] + closes[:-1]
    highs  = [max(o, c) * 1.005 for o, c in zip(opens, closes)]
    lows   = [min(o, c) * 0.995 for o, c in zip(opens, closes)]
    return _mk_df(closes, opens=opens, highs=highs, lows=lows)


def _mk_uptrend_no_fraud():
    """30 日: 上升趋势, 但没有藏诈形态(无创新高阳线 + 实体饱满)。期望: none。"""
    closes = []
    base = 10.0
    for i in range(30):
        base += 0.06
        closes.append(round(base, 2))
    opens  = [closes[0]] + closes[:-1]
    highs  = [max(o, c) * 1.002 for o, c in zip(opens, closes)]   # 影线小, 无藏诈形态
    lows   = [min(o, c) * 0.998 for o, c in zip(opens, closes)]
    return _mk_df(closes, opens=opens, highs=highs, lows=lows)


# ═══════════════ 箱体 / 缺口 检测 (旧 case 保留) ═══════════════
def test_detect_box_basic():
    df = _mk_df([10, 10.2, 9.9, 10.1, 10.3, 9.8, 10.0, 10.2, 9.9, 10.1] * 2)
    box = dx._detect_box(df, lookback=20)
    assert box["low"] < box["high"]
    assert box["low"] <= 10.0 <= box["high"]
    assert 0.0 <= box["pos"] <= 1.0


def test_detect_box_pos_near_high():
    df = _mk_df([10, 10, 10, 10, 10, 11, 11.5, 11.8, 11.9, 12.0])
    box = dx._detect_box(df, lookback=10)
    assert box["pos"] > 0.7


def test_detect_up_gap_and_not_filled():
    highs = [10.2, 10.4, 10.5, 11.6, 11.8, 12.0, 12.2, 12.4]
    lows  = [10.0, 10.2, 10.3, 11.0, 11.3, 11.5, 11.8, 12.0]
    closes= [10.1, 10.3, 10.4, 11.4, 11.6, 11.8, 12.0, 12.2]
    df = _mk_df(closes, highs=highs, lows=lows)
    gaps = dx._detect_gaps(df, lookback=30)
    up = [g for g in gaps if g["type"] == "up"]
    assert len(up) >= 1
    g = up[0]
    assert g["low"] == pytest.approx(10.5, abs=1e-6)
    assert g["high"] == pytest.approx(11.0, abs=1e-6)
    assert dx._gap_filled(df, g) is False


def test_up_gap_filled_when_price_retraces():
    highs = [10.2, 10.4, 10.5, 11.6, 11.8, 11.0, 10.6, 10.3]
    lows  = [10.0, 10.2, 10.3, 11.0, 11.2, 10.4, 10.2, 10.0]
    closes= [10.1, 10.3, 10.4, 11.4, 11.5, 10.5, 10.3, 10.1]
    df = _mk_df(closes, highs=highs, lows=lows)
    gaps = dx._detect_gaps(df, lookback=30)
    up = [g for g in gaps if g["type"] == "up"]
    assert len(up) >= 1
    assert dx._gap_filled(df, up[0]) is True


# ═══════════════ 时序链条判定 7 个 case ═══════════════

def test_chain_no_uptrend():
    """持续下跌 30 日 → no_uptrend (前置过滤不过)。"""
    r = dx.DexinTrendAgent().detect(_mk_no_uptrend())
    assert r["phase"] == "no_uptrend"
    assert r["stage"] == "none"
    assert "观望" in r["advice"]
    assert "phase_dates" in r


def test_chain_flat_is_no_uptrend():
    """横盘 30 日, ma20 斜率 = 0 → no_uptrend (斜率必须 > 0)。"""
    r = dx.DexinTrendAgent().detect(_mk_flat_30d())
    assert r["phase"] == "no_uptrend"


def test_chain_uptrend_no_fraud():
    """上升 30 日但无藏诈形态 → none。"""
    r = dx.DexinTrendAgent().detect(_mk_uptrend_no_fraud())
    assert r["phase"] == "none"
    assert r["stage"] == "none"
    assert r["signals"].get("fraud_count_20d") == 0


def test_chain_cang_zha_no_kill():
    """上升 + 藏诈日 + 后续无 ≥3% 阴线 → cang_zha (无虚杀匹配)。"""
    r = dx.DexinTrendAgent().detect(_mk_uptrend_then_fraud_no_kill())
    assert r["phase"] == "cang_zha"
    assert r["stage"] == "cang_zha"
    assert "观望" in r["advice"]
    assert r["phase_dates"].get("藏诈日")
    assert "虚杀日" not in r["phase_dates"]


def test_chain_full_de_xin():
    """完整链条 → 突破藏诈高点 + 涨幅 ≥ 4% + 阳线 + 量比 ≥ 1.2 → de_xin, signal=True。"""
    r = dx.DexinTrendAgent().detect(_mk_uptrend_with_full_chain_de_xin())
    assert r["phase"] == "de_xin"
    assert r["stage"] == "de_xin"
    assert "核心持仓" in r["advice"]
    pd_ = r["phase_dates"]
    assert pd_.get("藏诈日")
    assert pd_.get("虚杀日")
    assert pd_.get("洗盘区间")
    assert pd_.get("得鑫日")
    assert pd_.get("cycle_days") and pd_["cycle_days"] > 0


def test_chain_clearing_no_breakout():
    """完整洗盘末端但未突破藏诈高点 → clearing。"""
    r = dx.DexinTrendAgent().detect(_mk_clearing_no_breakout())
    assert r["phase"] == "clearing"
    assert r["stage"] == "clearing"
    assert "重点跟踪" in r["advice"]
    pd_ = r["phase_dates"]
    assert pd_.get("藏诈日")
    assert pd_.get("虚杀日")
    assert "得鑫日" not in pd_


def test_chain_xu_sha_in_progress():
    """上升 + 藏诈 + 虚杀 + 末端还在跌 (未清零) → xu_sha benign。"""
    r = dx.DexinTrendAgent().detect(_mk_xu_sha_benign_in_progress())
    assert r["phase"] == "xu_sha"
    assert r["stage"] == "xu_sha"
    assert r["variant"] == "benign"
    assert "低吸" in r["advice"]
    pd_ = r["phase_dates"]
    assert pd_.get("藏诈日")
    assert pd_.get("虚杀日")


def test_chain_dangerous_xu_sha():
    """上升 + 藏诈 + 虚杀回撤 > 15% → xu_sha dangerous。"""
    r = dx.DexinTrendAgent().detect(_mk_dangerous_xu_sha())
    assert r["phase"] == "xu_sha"
    assert r["stage"] == "xu_sha"
    assert r["variant"] == "dangerous"
    assert "剔除" in r["advice"]


# ═══════════════ 兼容性 + 兜底 ═══════════════

def test_classify_stage_backward_compat():
    """_classify_stage 旧入口仍可调用, 字段保持向后兼容 (stage/stage_label/quote/advice)。"""
    r = dx._classify_stage(_mk_uptrend_with_full_chain_de_xin(),
                           modules={"sector_strong": True, "dragon_net_yi": 1.0})
    # de_xin 在有模块门控支持下应该保住
    assert r["stage"] in {"de_xin", "clearing"}
    assert isinstance(r["quote"], str)
    assert isinstance(r["advice"], str)
    assert "signals" in r
    # 新增字段
    assert "phase" in r
    assert "phase_dates" in r


def test_short_df_safe():
    """数据不足不应崩溃。"""
    df = _mk_df([10, 10.1, 10.2])
    r = dx._classify_stage(df)
    assert r["stage"] == "none"
    assert r["phase"] == "none"


def test_every_phase_has_quote_and_advice():
    """每个 phase 都必须返回 quote + advice (供前端卡片溯源展示)。"""
    fixtures = [
        (_mk_no_uptrend(),          ["no_uptrend"]),
        (_mk_uptrend_no_fraud(),    ["none"]),
        (_mk_uptrend_then_fraud_no_kill(), ["cang_zha"]),
        (_mk_xu_sha_benign_in_progress(),  ["xu_sha"]),
        (_mk_dangerous_xu_sha(),           ["xu_sha"]),
        (_mk_clearing_no_breakout(),       ["clearing"]),
        (_mk_uptrend_with_full_chain_de_xin(), ["de_xin", "clearing"]),  # 视模块门控
    ]
    for df, expected_phases in fixtures:
        r = dx._classify_stage(df)
        assert isinstance(r["quote"], str)
        assert isinstance(r["advice"], str) and r["advice"]
        assert r["phase"] in expected_phases
        assert "signals" in r
        assert "phase_dates" in r