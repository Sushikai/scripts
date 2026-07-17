"""
尾盘战法回测 v4 (2026-07-15 重写)
══════════════════════════════════════════════════════════════════
"顶级" 优化:
  1) 完全向量化 — 1 次拉全集日线, 1 次广播到所有候选, 1 次算所有 9 套退场
  2) 不依赖 screen.py 4 层管线 (历史模式 L4 必软通, 浪费 100+ms × N)
  3) 直接复用 backtest._compute_overall / _compute_monthly / _compute_scenario_compare
  4) 复用 data_layer.batch_fetch_daily + 日级缓存 (磁盘 mtime 7d TTL)
  5) Top-N 每日选股: 全员向量化评分 + argsort, 一次性选出 Top N
  6) 9 套退场 vs 7 套退场 = 同时算 9+7=16 种策略, 输出交叉胜率表

性能目标: 半年回测 (≈ 120 交易日 × 5000 只)  < 30s (冷启),  < 5s (缓存命中)

数据流:
  prefetch_daily(universe) → master_panel (multi-index df 一次拼完)
  → daily_metrics(MA5/10/20/60, 量比, 换手%) computed in place
  → vectorized_screen(master_panel) → candidates dict[date] → list of code+tier+score
  → top_n + trade_simulate → trades list (含 exits_pct 9 套 + exits_d + exits_sell)
  → stats.compute_overall / monthly / scenario_compare / exit_breakdown / sector
"""
from __future__ import annotations

import logging
import math
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger("tuixue_v3.backtest_screener")

# 并行池: 拉日线 30 worker 接近瞬时; 200+ stock 时 IO 还没打满
_EXECUTOR = ThreadPoolExecutor(max_workers=40, thread_name_prefix="bt-preload")

# ═════════════════════════════════════════════════════════════════
# 规则阈值 (与 web/screener.py 同步)
# ═════════════════════════════════════════════════════════════════
LIMIT_UP_PCT = 9.5

# 候选池静态预筛 (规则 2/3/5/6/7 全可向量化判; 规则 4/8 在向量化层软通)
#   规则 2: 主板 (code 前缀, 在向量化之前 filter)
#   规则 3: change_pct ∈ [3, 5]
#   规则 5: vol_ratio ≥ 1.0
#   规则 6: mcap_yi ∈ [40, 300] (回测无 mcap 列, 留接口软通)
#   规则 7: turnover ∈ [2, 30] (回测放松: 5-10 在大牛/大熊太严, 几乎无 trade)
#   规则 4 (20d 涨停 ≥ 1): 向量化 (rolling 20 max-ret >= 9.5%)
#   规则 8 (全天 VWAP 上): 软通 — 历史分时不可得, 标注软通不计入 fail

TH = {
    "change_pct_min": 3.0,
    "change_pct_max": 5.0,
    "vol_ratio_min": 1.0,
    "mcap_yi_min": 40.0,
    "mcap_yi_max": 300.0,
    # 🔧 回测专属放宽: 直播 live 5-10%, 历史多数日换手率=0 (akshare 这列空),
    # 用 amount_proxy (成交额 / 流通市值的代理) 替代真实 turnover
    # 命中区间: 成交额在近 60 日均量的 0.6×~3.0× 之间 → 当日不算极端放量也不算地量
    "amount_ratio_min": 0.6,
    "amount_ratio_max": 3.0,
    "zt_20d_min": 1,
    "above_vwap_min_pct": 100.0,
}


# ═════════════════════════════════════════════════════════════════
# 0) 周期窗常量 (server.py 别名解析要用)
# ═════════════════════════════════════════════════════════════════
WINDOWS = [("1周", 5), ("2周", 10), ("1月", 21), ("2月", 42), ("半年", 120), ("1年", 250)]
PERIOD_DAYS_MAP = dict(WINDOWS)


# ═════════════════════════════════════════════════════════════════
# 1) 主板 only — vectorized via str prefix (向量化前提)
# ═════════════════════════════════════════════════════════════════
def _is_main_board(code: str) -> bool:
    if not code or len(code) != 6 or not code.isdigit():
        return False
    return not code.startswith(("300", "301", "688", "8", "9"))


def _board_fallback_sector(code: str) -> str:
    """板块归类兜底: 主板(60/00) → 主板; 创业(300) → 创业板; 科创(688) → 科创板; 北交(8/9) → 北交所"""
    if not code or len(code) != 6:
        return "其他"
    if code.startswith(("60", "00")): return "主板"
    if code.startswith(("300", "301")): return "创业板"
    if code.startswith("688"): return "科创板"
    if code.startswith(("8", "9", "43", "83", "87")): return "北交所"
    return "其他"


# ═════════════════════════════════════════════════════════════════
# 2) 全市场日线预加载 + 拼接为一张大表 (vectorized 关键)
# ═════════════════════════════════════════════════════════════════
def _is_main_board_codes(codes: list[str]) -> list[str]:
    return [c for c in codes if _is_main_board(c)]


def _prefetch_daily(universe_codes: list[str], days: int = 400, progress_cb=None) -> dict[str, pd.DataFrame]:
    """并行拉日线 (40 worker)，复用 data_layer 的 Redis/SQLite 缓存层。

    命中缓存: 3000 只 < 1s
    冷启:     ≈ 60-90s (东财/腾讯限流, 12s 总闸)
    部分命中:  hit 数倍速
    """
    from .. import data_layer as _dl

    out: dict[str, pd.DataFrame] = {}
    t0 = _time.time()
    total = len(universe_codes)

    futs = {_EXECUTOR.submit(_dl.fetch_daily, code, days): code for code in universe_codes}
    done = 0
    try:
        for f in as_completed(futs, timeout=90):
            try:
                df = f.result(timeout=0.1)
                if df is not None and not df.empty:
                    code = futs[f]
                    out[code] = df
            except Exception:
                pass
            done += 1
            if done % 200 == 0 and progress_cb:
                progress_cb(f"日线拉取 {done}/{total}, 命中 {len(out)} ({_time.time()-t0:.0f}s)")
    except Exception as e:
        # R52: as_completed timeout= 仍可能抛 — 兜底不让上层整个 BT 失败
        if progress_cb:
            progress_cb(f"日线 90s 闸到, 完成 {done}/{total}, 命中 {len(out)}")

    # R52: 显式 cancel 未完成的 future (避免线程池卡住后续 submit)
    unfinished = [f for f in futs if not f.done()]
    if unfinished:
        for f in unfinished:
            f.cancel()
    if progress_cb:
        suffix = f" (放弃 {len(unfinished)} 只)" if unfinished else ""
        progress_cb(f"日线完成 {len(out)}/{total}{suffix} ({_time.time()-t0:.1f}s)")
    return out


def _build_master_panel(daily_cache: dict[str, pd.DataFrame], names: dict[str, str]) -> pd.DataFrame:
    """所有股票的日线拼成 1 张大表 (MultiIndex: (code, date))

    计算 yield 列 (n+1 / n - 1)
    """
    dfs = []
    cols_required = ["日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额"]
    for code, df in daily_cache.items():
        if df is None or df.empty:
            continue
        # 列容错 (akshare 老版本/新版本/有些 fetch 渠道列名不同)
        df_cols = set(df.columns)
        if not all(c in df_cols for c in ["日期", "开盘", "最高", "最低", "收盘"]):
            continue
        keep = [c for c in cols_required if c in df_cols]
        if "换手率" not in df_cols:
            df = df.copy()
            df["换手率"] = 0.0
            keep.append("换手率")
        d = df[keep].copy()
        d["code"] = code
        d["name"] = names.get(code, code)
        dfs.append(d)
    if not dfs:
        return pd.DataFrame()
    panel = pd.concat(dfs, ignore_index=True)
    panel["日期"] = panel["日期"].astype(str)
    panel = panel.sort_values(["code", "日期"]).reset_index(drop=True)
    # 涨幅 %
    panel["prev_close"] = panel.groupby("code")["收盘"].shift(1)
    panel["change_pct"] = (panel["收盘"] / panel["prev_close"] - 1.0) * 100.0
    panel["prev_volume"] = panel.groupby("code")["成交量"].shift(1)
    panel["vol_ratio"] = panel["成交量"] / panel["prev_volume"]
    return panel


def _add_metrics(panel: pd.DataFrame) -> pd.DataFrame:
    """在 master panel 上加 MA5/10/20/60 + 量比滚动 + 20d max-return (找涨停)。

    全部按 code 分组一次性 rolling, 而不是 per-stock 循环。

    amount_ratio 代理 turnover: 成交额 / 近 60 日均额 (无换手率数据时退而求其次)
    """
    if panel.empty:
        return panel
    g = panel.groupby("code", sort=False)
    for w in (5, 10, 20, 60):
        panel[f"ma{w}"] = g["收盘"].transform(lambda s: s.rolling(w, min_periods=max(2, w // 2)).mean())
    panel["vol_ma5"] = g["成交量"].transform(lambda s: s.rolling(5, min_periods=2).mean())
    panel["vol_ma20"] = g["成交量"].transform(lambda s: s.rolling(20, min_periods=4).mean())
    panel["amt_ma60"] = g["成交额"].transform(lambda s: s.rolling(60, min_periods=8).mean())
    # amount_ratio = 今日成交额 / 近 60 日均成交额 (proxy for turnover-ratio)
    panel["amount_ratio"] = panel["成交额"] / panel["amt_ma60"].replace(0, float("nan"))
    # 20 日内最大涨幅 (含今日) — 找涨停
    panel["ret_20d_max"] = g["change_pct"].transform(lambda s: s.rolling(20, min_periods=2).max())
    # 量比 (今日量 / 近 5 日均量) — 兼容 数据源缺失
    panel["vol_ratio_calc"] = panel["成交量"] / panel["vol_ma5"]
    panel["amount_ratio"] = panel["amount_ratio"].fillna(1.0)
    return panel


# ═════════════════════════════════════════════════════════════════
# 3) 向量化筛股 — 一次性给所有 (date, code) 打标签
# ═════════════════════════════════════════════════════════════════
def _vectorized_screen(panel: pd.DataFrame) -> pd.DataFrame:
    """返回 panel 加 rules_pass_count / fails / score / pass_all 列

    全部 boolean mask & | ~ 向量化
    """
    if panel.empty:
        return panel
    cp = panel["change_pct"]
    vr = panel["vol_ratio"].fillna(panel["vol_ratio_calc"]).fillna(0)
    ar = panel["amount_ratio"]  # 成交额代理换手率
    rets_20d_max = panel["ret_20d_max"].fillna(0)

    rules = {
        "change_pct":    (cp >= TH["change_pct_min"]) & (cp <= TH["change_pct_max"]),
        "vol_ratio":     vr >= TH["vol_ratio_min"],
        "amount_ratio":  (ar >= TH["amount_ratio_min"]) & (ar <= TH["amount_ratio_max"]),
        "zt_20d":        rets_20d_max >= LIMIT_UP_PCT,
        # mcap (规则 6) — 总市值在日线无列, 软通不计入 fail
        # above_vwap: 软通 (历史分时不可得), 不计入 fail
    }
    panel["rules_pass_count"] = sum(rules.values()).astype(int)
    panel["fail_change_pct"] = (~rules["change_pct"]).astype(int)
    panel["fail_vol_ratio"]  = (~rules["vol_ratio"]).astype(int)
    panel["fail_amount_ratio"] = (~rules["amount_ratio"]).astype(int)
    panel["fail_zt_20d"]     = (~rules["zt_20d"]).astype(int)
    panel["n_fails"] = (
        panel["fail_change_pct"] + panel["fail_vol_ratio"]
        + panel["fail_amount_ratio"] + panel["fail_zt_20d"]
    )
    panel["pass_all"] = (panel["n_fails"] == 0).astype(int)
    # 综合得分 — change_pct 居中 + 量比高 + 成交活跃 + 涨停
    panel["score"] = (
        (5.0 - (cp - 4.0).abs()) * 10         # 4% 为甜点, 越近越高
        + vr.fillna(0).clip(0, 10)
        + ar.fillna(0).clip(0, 5)
        + rets_20d_max.clip(0, 30) * 0.5
    )
    return panel


# ═════════════════════════════════════════════════════════════════
# 4) Top N 选股 — 每日按 score 排序取 Top N
# ═════════════════════════════════════════════════════════════════
def _pick_top_per_date(panel: pd.DataFrame, top_n: int = 1,
                       require_pass_all: bool = True) -> dict[str, list[dict]]:
    """按 日期 group, 每组选 score 前 top_n 只

    require_pass_all: True → 只取 8 条规则都过的; False → 按 score 排名即可
    返回 {date_str: [{code, name, score, change_pct, vol_ratio, turnover, close, passes_count}, ...]}
    """
    if panel.empty:
        return {}
    sub = panel
    if require_pass_all:
        sub = sub[sub["pass_all"] == 1]
    sub = sub.dropna(subset=["score"])
    if sub.empty:
        return {}
    sub = sub.sort_values(["日期", "score"], ascending=[True, False])
    grouped = sub.groupby("日期", sort=True)
    out: dict[str, list[dict]] = {}
    for date, g in grouped:
        picks = []
        for _, row in g.head(top_n).iterrows():
            picks.append({
                "code":         row["code"],
                "name":         row.get("name", row["code"]),
                "score":        round(float(row["score"]), 2),
                "change_pct":   round(float(row["change_pct"]), 3),
                "vol_ratio":    round(float(row.get("vol_ratio_calc") or 0), 3),
                "turnover":     round(float(row.get("换手率") or 0), 3),
                "close":        float(row["收盘"]),
                "buy_price":    float(row["收盘"]),  # T 日收盘 (≈ 14:30 的市价)
                "passes_count": int(row["rules_pass_count"]),
                "_regime":      "—",
            })
        if picks:
            out[str(date)] = picks
    return out


# ═════════════════════════════════════════════════════════════════
# 5) 模拟退场 — 9 套 T+1 退场场景 (含续涨停延后)
# ═════════════════════════════════════════════════════════════════
def _simulate_from_cache_row(row_t1: dict, buy_price: float, actual_10_close: float | None = None) -> dict | None:
    """从 cache 单行 dict 模拟 18 套退场 (+ actual_10 系列) — O(1) 时间

    row_t1: {open, high, low, close, date_str}
    actual_10_close: 真实 10:00 K 线 close (若有), 用于对比 actual_10 系列场景
                     若不传/取不到, actual_10 系列回退到 ret_open (与原规则一致)

    统一规则: 9:30 不翻红 (open ≤ buy) → 全部10:00水下均价 (open+low)/2 止损
             9:30 翻红   (open > buy) → 各策略自有退出逻辑
    """
    open_p = row_t1["open"]; high_p = row_t1["high"]
    low_p = row_t1["low"]; close_p = row_t1["close"]
    if buy_price <= 0 or open_p <= 0:
        return None
    _p = lambda p: (p / buy_price - 1.0) * 100.0
    ret_open = _p(open_p); ret_close = _p(close_p)
    ret_high = _p(high_p); ret_low = _p(low_p)
    ret_avg_up = _p((open_p + high_p) / 2.0)

    # 续涨停延后: T+1 开盘涨停→交给 T+2
    if open_p >= buy_price * 1.095:
        return None

    # ═══════════════════════════════════════════════════════════
    # 统一不翻红退出: 10:00 实际价格
    # 用户铁律: 9:30 没翻红→10:00 必卖, 不等收盘低点
    # 用 open 近似 (9:30 → 10:00 仅 30 分钟, 无精确分时日线不可得)
    # ═══════════════════════════════════════════════════════════
    ret_10oclock = ret_open
    # actual_10 系列: 若有真实 10:00 close, 用它替代 ret_open
    ret_actual_10 = _p(actual_10_close) if (actual_10_close and actual_10_close > 0) else ret_open

    if open_p > buy_price:
        # ── 翻红 (9:30 在买入价以上) → 各策略自有退出逻辑 ──
        ret_S1 = ret_open
        ret_tp2 = 2.0 if high_p >= buy_price * 1.02 else ret_close
        ret_twap = (ret_open + ret_close) / 2.0
        ret_half = (ret_open + ret_avg_up) / 2.0
        ret_avg_up_strat = ret_avg_up
        ret_max95 = _p(high_p * 0.95) if high_p > buy_price else ret_close
        ret_trail80 = round(ret_high * 0.8, 3) if ret_high > 0 else ret_close
        ret_trail50 = round(ret_high * 0.5, 3) if ret_high > 0 else ret_close
        ret_gap_target = ret_avg_up if open_p >= buy_price * 1.02 else ret_close
        ret_bull_candle = ret_avg_up if close_p > open_p else ret_open
        ret_gap_cut = ret_close  # 翻红日不可能低开≥2%
        # S2 系列
        ret_S2 = ret_open
        ret_S2_trail80 = ret_trail80
        ret_S2_tp2 = ret_tp2
        ret_S2_avg_up = ret_avg_up_strat
        # actual_10 系列: 翻红时跟原策略一致 (trail80/close)
        ret_S3_actual10_trail80 = ret_trail80
        ret_S_actual10_close = ret_close
    else:
        # ── 不翻红 → 统一 10:00 水下均价 (用户铁律) ──
        ret_S1 = ret_10oclock
        ret_tp2 = ret_10oclock
        ret_twap = ret_10oclock
        ret_half = ret_10oclock
        ret_avg_up_strat = ret_10oclock
        ret_max95 = ret_10oclock
        ret_trail80 = ret_10oclock
        ret_trail50 = ret_10oclock
        ret_gap_target = ret_10oclock
        ret_bull_candle = ret_10oclock
        ret_gap_cut = ret_10oclock
        ret_S2 = ret_10oclock
        ret_S2_trail80 = ret_10oclock
        ret_S2_tp2 = ret_10oclock
        ret_S2_avg_up = ret_10oclock
        # actual_10 系列: 翻红逻辑无意义 (此时已经不翻红), 用真实 10:00 close
        # S3_actual10_trail80: 不翻红→真实10:00 close; 翻红→trail80
        ret_S3_actual10_trail80 = ret_actual_10
        ret_S_actual10_close = ret_actual_10

    return {
        "open": round(ret_open, 3), "S1": round(ret_S1, 3),
        "avg_up": round(ret_avg_up_strat, 3), "max95": round(ret_max95, 3),
        "low": round(ret_low, 3), "close": round(ret_close, 3),
        "twap": round(ret_twap, 3), "tp2": round(ret_tp2, 3),
        "half": round(ret_half, 3),
        "S2": round(ret_S2, 3),
        "S2_trail80": round(ret_S2_trail80, 3),
        "S2_tp2": round(ret_S2_tp2, 3),
        "S2_avg_up": round(ret_S2_avg_up, 3),
        "S3_trail80": round(ret_trail80, 3),
        "trail50": round(ret_trail50, 3),
        "gap_target": round(ret_gap_target, 3),
        "gap_cut_2pct": round(ret_gap_cut, 3),
        "bull_candle": round(ret_bull_candle, 3),
        # actual_10 系列: 对比"真实 10:00 close" vs "open 代理" 的差异
        "S3_actual10_trail80": round(ret_S3_actual10_trail80, 3),
        "S_actual10_close": round(ret_S_actual10_close, 3),
        "exits_pct": {
            "best": round(ret_high, 3),
            "trail_3pct": round(min(ret_high, 3.0), 3) if high_p >= buy_price * 1.03 else ret_close,
            "trail_5pct": round(min(ret_high, 5.0), 3) if high_p >= buy_price * 1.05 else ret_close,
            "trail_8pct": round(min(ret_high, 8.0), 3) if high_p >= buy_price * 1.08 else ret_close,
            "stop_3pct": round(min(ret_low, -3.0), 3) if low_p <= buy_price * 0.97 else ret_close,
            "close": round(ret_close, 3),
            "rule_pri": round(ret_S1, 3),
        },
        "recovered": high_p > buy_price,
        "trigger": "S1_recover" if open_p > buy_price else ("tp2_hit" if high_p >= buy_price * 1.02 else "time_exit"),
    }


def _simulate_batch(rows: list[dict], buy_prices: list[float],
                    actual_10_closes: list[float | None] | None = None) -> list[dict | None]:
    """向量化: 一次算 N 笔 trade 的 18 套退场 (~500x 快于 for-loop)

    rows: [{open, high, low, close, date_str}, ...]
    buy_prices: [float, ...]
    actual_10_closes: [float|None, ...] (并行 list, None 表示用 open 代理)

    Returns: 与 _simulate_from_cache_row 同 schema 的 list[dict], 失败位 None
    """
    if not rows:
        return []
    n = len(rows)
    actual_10_closes = actual_10_closes or [None] * n
    op = np.array([r["open"]  for r in rows], dtype=np.float64)
    hp = np.array([r["high"]  for r in rows], dtype=np.float64)
    lp = np.array([r["low"]   for r in rows], dtype=np.float64)
    cp = np.array([r["close"] for r in rows], dtype=np.float64)
    bp = np.array(buy_prices, dtype=np.float64)
    a10 = np.array([x if (x and x > 0) else 0.0 for x in actual_10_closes], dtype=np.float64)
    has_a10 = a10 > 0

    # 基础 % 收益
    ret_open = (op / bp - 1.0) * 100.0
    ret_close = (cp / bp - 1.0) * 100.0
    ret_high = (hp / bp - 1.0) * 100.0
    ret_low = (lp / bp - 1.0) * 100.0
    ret_avg_up = ((op + hp) / 2.0 / bp - 1.0) * 100.0
    ret_actual_10 = np.where(has_a10, (a10 / bp - 1.0) * 100.0, ret_open)

    # 续涨停 mask: open >= buy*1.095 → 该笔返回 None
    up_limit_mask = op >= bp * 1.095
    invalid_mask = (bp <= 0) | (op <= 0) | up_limit_mask

    # 翻红 mask: open > buy
    green_mask = op > bp
    underwater_mask = ~green_mask & ~invalid_mask

    # ── 翻红分支 (green_mask) ──
    tp2_hit = hp >= (bp * 1.02)
    ret_tp2_g = np.where(tp2_hit, 2.0, ret_close)
    ret_twap_g = (ret_open + ret_close) / 2.0
    ret_half_g = (ret_open + ret_avg_up) / 2.0
    ret_max95_g = np.where(hp > bp, (hp * 0.95 / bp - 1.0) * 100.0, ret_close)
    ret_trail80_g = np.where(ret_high > 0, np.round(ret_high * 0.8, 3), ret_close)
    ret_trail50_g = np.where(ret_high > 0, np.round(ret_high * 0.5, 3), ret_close)
    ret_gap_target_g = np.where(op >= bp * 1.02, ret_avg_up, ret_close)
    ret_bull_candle_g = np.where(cp > op, ret_avg_up, ret_open)
    ret_S3_actual10_g = ret_trail80_g
    ret_S_actual10_g = ret_close

    # ── 不翻红分支 (underwater) ── 全部走 ret_open (= 10:00 代理)
    ret_S_uw = ret_open
    ret_S3_actual10_uw = ret_actual_10
    ret_S_actual10_uw = ret_actual_10

    # 合并: 用 where + mask
    def pick(green, uw):
        return np.where(invalid_mask, 0.0,
               np.where(green_mask, green, uw))

    out_open       = np.where(invalid_mask, 0.0, ret_open)
    out_S1         = pick(ret_open, ret_S_uw)
    out_tp2        = pick(ret_tp2_g, ret_S_uw)
    out_twap       = pick(ret_twap_g, ret_S_uw)
    out_half       = pick(ret_half_g, ret_S_uw)
    out_avg_up     = pick(ret_avg_up, ret_S_uw)
    out_max95      = pick(ret_max95_g, ret_S_uw)
    out_trail80    = pick(ret_trail80_g, ret_S_uw)
    out_trail50    = pick(ret_trail50_g, ret_S_uw)
    out_gap_target = pick(ret_gap_target_g, ret_S_uw)
    out_bull_candle= pick(ret_bull_candle_g, ret_S_uw)
    out_gap_cut    = pick(ret_close, ret_S_uw)
    out_S2         = pick(ret_open, ret_S_uw)
    out_S2_trail80 = pick(ret_trail80_g, ret_S_uw)
    out_S2_tp2     = pick(ret_tp2_g, ret_S_uw)
    out_S2_avg_up  = pick(ret_avg_up, ret_S_uw)
    out_S3_actual10 = pick(ret_S3_actual10_g, ret_S3_actual10_uw)
    out_S_actual10  = pick(ret_S_actual10_g, ret_S_actual10_uw)

    # exits_pct 子字典
    trail3 = np.where(hp >= bp * 1.03, np.minimum(ret_high, 3.0), ret_close)
    trail5 = np.where(hp >= bp * 1.05, np.minimum(ret_high, 5.0), ret_close)
    trail8 = np.where(hp >= bp * 1.08, np.minimum(ret_high, 8.0), ret_close)
    stop3 = np.where(lp <= bp * 0.97, np.minimum(ret_low, -3.0), ret_close)

    out: list[dict | None] = []
    for i in range(n):
        if invalid_mask[i]:
            out.append(None); continue
        g = bool(green_mask[i])
        recovered = bool(hp[i] > bp[i])
        if g:
            trig = "S1_recover"
        elif hp[i] >= bp[i] * 1.02:
            trig = "tp2_hit"
        else:
            trig = "time_exit"
        out.append({
            "open": round(float(ret_open[i]), 3),
            "S1": round(float(out_S1[i]), 3),
            "avg_up": round(float(out_avg_up[i]), 3),
            "max95": round(float(out_max95[i]), 3),
            "low": round(float(ret_low[i]), 3),
            "close": round(float(ret_close[i]), 3),
            "twap": round(float(out_twap[i]), 3),
            "tp2": round(float(out_tp2[i]), 3),
            "half": round(float(out_half[i]), 3),
            "S2": round(float(out_S2[i]), 3),
            "S2_trail80": round(float(out_S2_trail80[i]), 3),
            "S2_tp2": round(float(out_S2_tp2[i]), 3),
            "S2_avg_up": round(float(out_S2_avg_up[i]), 3),
            "S3_trail80": round(float(out_trail80[i]), 3),
            "trail50": round(float(out_trail50[i]), 3),
            "gap_target": round(float(out_gap_target[i]), 3),
            "gap_cut_2pct": round(float(out_gap_cut[i]), 3),
            "bull_candle": round(float(out_bull_candle[i]), 3),
            "S3_actual10_trail80": round(float(out_S3_actual10[i]), 3),
            "S_actual10_close": round(float(out_S_actual10[i]), 3),
            "exits_pct": {
                "best": round(float(ret_high[i]), 3),
                "trail_3pct": round(float(trail3[i]), 3),
                "trail_5pct": round(float(trail5[i]), 3),
                "trail_8pct": round(float(trail8[i]), 3),
                "stop_3pct": round(float(stop3[i]), 3),
                "close": round(float(ret_close[i]), 3),
                "rule_pri": round(float(out_S1[i]), 3),
            },
            "recovered": recovered,
            "trigger": trig,
        })
    return out


def _simulate_hold_from_idx(cm: dict, buy_date: str, sell_date: str, buy_price: float) -> dict | None:
    """从 {date: row} 索引模拟持仓 N 天的 7 套退场。

    cm: {date_str: row}  where row has open/high/low/close
    """
    if not cm:
        return None
    # 收集 [buy_date, sell_date] 之间所有交易日的 row (按日期排序)
    keys = sorted(k for k in cm if buy_date <= k <= sell_date)
    if len(keys) < 2:
        return None
    rows = [cm[k] for k in keys]
    n = len(rows)
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    closes = [r["close"] for r in rows]
    opens = [r["open"] for r in rows]
    dates = keys
    last_i = n - 1

    def _ret(p):
        return (p / buy_price - 1.0) * 100.0 if buy_price > 0 else 0.0

    best_sell = max(highs)
    best_i = highs.index(best_sell)

    def _trail(thresh, pullback):
        for i in range(1, n):
            if highs[i] >= buy_price * (1 + thresh):
                target = highs[i] * (1 - pullback)
                return max(target, opens[i]), i
        return closes[last_i], last_i

    t3, i3 = _trail(0.03, 0.015)
    t5, i5 = _trail(0.05, 0.02)
    t8, i8 = _trail(0.08, 0.03)

    s3, i3s = closes[last_i], last_i
    for i in range(1, n):
        if lows[i] <= buy_price * 0.97:
            s3, i3s = lows[i], i
            break

    last_close = closes[last_i]
    last_date = dates[last_i]

    if i3s < last_i:
        main_sell, main_kind = s3, "stop_3pct"
    elif i8 < last_i:
        main_sell, main_kind = t8, "trail_8pct"
    else:
        main_sell, main_kind = last_close, "time_exit"

    exits_pct = {
        "best": round(_ret(best_sell), 3),
        "trail_3pct": round(_ret(t3), 3),
        "trail_5pct": round(_ret(t5), 3),
        "trail_8pct": round(_ret(t8), 3),
        "stop_3pct": round(_ret(s3), 3),
        "close": round(_ret(last_close), 3),
        "rule_pri": round(_ret(main_sell), 3),
    }
    return {
        "buy_date": buy_date, "sell_date": last_date,
        "buy_price": round(buy_price, 3),
        "sell_price": round(main_sell, 3),
        "return_pct": round(_ret(main_sell), 3),
        "trigger": main_kind,
        "hold_days": last_i,
        "exits_pct": exits_pct,
        "best_exit_pct": exits_pct["best"],
        "rule_vs_hold_pct": round(exits_pct["rule_pri"] - exits_pct["close"], 3),
        "rule_vs_best_pct": round(exits_pct["rule_pri"] - exits_pct["best"], 3),
    }


def _simulate_exits(panel_t1: pd.DataFrame, buy_price: float) -> dict | None:
    """panel_t1: T+1 一天 OHLC (DataFrame, 单行)

    返回 9 套退场百分比 + 各类额外信息:
      open / S1 / S2 / avg_up / max95 / low / close / twap / tp2 / half
      twap = (open+close)/2
      half = (open + avg_up)/2  (avg_up = (open+high)/2)
      tp2: 翻红 → 2% 截, 没翻红 → close
      S1: 翻红 → open, 没翻红 → close (平盘卖)
      S2: 10:00 决策规则 — 翻红 → open, 没翻红 → 水下均价 (open+low)/2
      max95: 当 high >= buy * 1.001 → high*0.95 否则 close

    续涨停: T+1 开盘直接涨停 (open vs prev_close >= 9.5%) → 找 T+2
    """
    if panel_t1 is None or panel_t1.empty:
        return None
    row = panel_t1.iloc[0]
    open_p = float(row["开盘"]); high_p = float(row["最高"])
    low_p  = float(row["最低"]); close_p = float(row["收盘"])
    if buy_price <= 0:
        return None

    # 续涨停延后 — 用当日开盘 / 昨收 (= 买入价近似) 比
    prev_close = float(panel_t1["收盘"].shift(1).iloc[0]) if "shift" in dir(panel_t1) else buy_price
    is_limit_up_open = (open_p / buy_price - 1.0) * 100.0 >= 9.5 if buy_price > 0 else False
    if is_limit_up_open:
        # 已经用过 T+1, 后续面板再传 T+2 (caller 处理)
        return None  # caller 改成传 T+2 再调一次
    ret_10oclock = ret_open  # 统一: 不翻红→10:00 价格 (open 代理)

    if open_p > buy_price:
        ret_S1 = ret_open
        ret_tp2 = 2.0 if high_p >= buy_price * 1.02 else ret_close
        ret_twap = (ret_open + ret_close) / 2.0
        ret_half = (ret_open + ret_avg_up) / 2.0
        ret_avg_up_strat = ret_avg_up
        ret_max95 = pct(high_p * 0.95) if high_p > buy_price else ret_close
        ret_trail80 = round(ret_high * 0.8, 3) if ret_high > 0 else ret_close
        ret_trail50 = round(ret_high * 0.5, 3) if ret_high > 0 else ret_close
        ret_gap_target = ret_avg_up if open_p >= buy_price * 1.02 else ret_close
        ret_bull_candle = ret_avg_up if close_p > open_p else ret_open
        ret_gap_cut = ret_close
        ret_S2 = ret_open
        ret_S2_trail80 = ret_trail80
        ret_S2_tp2 = ret_tp2
        ret_S2_avg_up = ret_avg_up_strat
    else:
        ret_S1 = ret_S2 = ret_10oclock
        ret_tp2 = ret_twap = ret_half = ret_10oclock
        ret_avg_up_strat = ret_max95 = ret_10oclock
        ret_trail80 = ret_trail50 = ret_10oclock
        ret_gap_target = ret_bull_candle = ret_gap_cut = ret_10oclock
        ret_S2_trail80 = ret_S2_tp2 = ret_S2_avg_up = ret_10oclock

    return {
        "open":    round(ret_open, 3),
        "S1":      round(ret_S1, 3),
        "S2":      round(ret_S2, 3),
        "S2_trail80": round(ret_S2_trail80, 3),
        "S2_tp2": round(ret_S2_tp2, 3),
        "S2_avg_up": round(ret_S2_avg_up, 3),
        "S3_trail80": round(ret_trail80, 3),
        "trail50": round(ret_trail50, 3),
        "gap_target": round(ret_gap_target, 3),
        "gap_cut_2pct": round(ret_gap_cut, 3),
        "bull_candle": round(ret_bull_candle, 3),
        "avg_up":  round(ret_avg_up_strat, 3),
        "max95":   round(ret_max95, 3),
        "low":     round(ret_low, 3),
        "close":   round(ret_close, 3),
        "twap":    round(ret_twap, 3),
        "tp2":     round(ret_tp2, 3),
        "half":    round(ret_half, 3),
        # 详细元数据 (供 backtest.py 的 exits_pct 7 套扩展复用)
        "exits_pct": {
            "best":     round(ret_high, 3),                # 当日最高价
            "trail_3pct": round(min(ret_high, 3.0), 3) if high_p >= buy_price * 1.03 else ret_close,
            "trail_5pct": round(min(ret_high, 5.0), 3) if high_p >= buy_price * 1.05 else ret_close,
            "trail_8pct": round(min(ret_high, 8.0), 3) if high_p >= buy_price * 1.08 else ret_close,
            "stop_3pct":  round(min(ret_low, -3.0), 3) if low_p <= buy_price * 0.97 else ret_close,
            "close":     round(ret_close, 3),
            "rule_pri":  round(ret_S1, 3),                # = S1: 翻红卖 / 平盘卖
        },
        # 其它元信息
        "recovered":  high_p > buy_price,                 # 当日翻红过 buy
        "trigger":    "S1_recover" if open_p > buy_price else ("tp2_hit" if high_p >= buy_price * 1.02 else "time_exit"),
    }


# ═════════════════════════════════════════════════════════════════
# 6) 多日持仓模拟 (hold_days 日, 命中即平, 退场策略沿用 exits_pct 7 套)
# ═════════════════════════════════════════════════════════════════
def _simulate_hold(df: pd.DataFrame, buy_date: str, sell_date: str,
                   buy_price: float) -> dict | None:
    """T 日买入, T+1 ~ T+hold 期间, 按 7 套退场横向对比。

    df: 该股的日线 panel (含 日期 / 开盘 / 最高 / 最低 / 收盘)
    buy_date / sell_date: YYYYMMDD
    buy_price: 买入均价 (T 日收盘近似)
    """
    if df is None or df.empty or "日期" not in df.columns:
        return None
    sub = df[(df["日期"] >= buy_date) & (df["日期"] <= sell_date)].copy()
    if len(sub) < 2:
        return None
    sub = sub.reset_index(drop=True)
    highs = sub["最高"].astype(float).tolist()
    lows = sub["最低"].astype(float).tolist()
    closes = sub["收盘"].astype(float).tolist()
    opens = sub["开盘"].astype(float).tolist()
    dates = sub["日期"].astype(str).tolist()
    n = len(sub)
    last_i = n - 1

    def _ret(p):
        return (p / buy_price - 1.0) * 100.0 if buy_price > 0 else 0.0

    best_sell = max(highs); best_i = highs.index(best_sell)

    def _trail(thresh, pullback):
        for i in range(1, n):
            if highs[i] >= buy_price * (1 + thresh):
                target = highs[i] * (1 - pullback)
                return max(target, opens[i]), i
        return closes[last_i], last_i

    t3, i3 = _trail(0.03, 0.015)
    t5, i5 = _trail(0.05, 0.02)
    t8, i8 = _trail(0.08, 0.03)

    # stop_3: 单日 low 跌破 0.97, 当日按 low 止损
    s3, i3s = closes[last_i], last_i
    for i in range(1, n):
        if lows[i] <= buy_price * 0.97:
            s3, i3s = lows[i], i
            break

    last_close = closes[last_i]
    last_date = dates[last_i]

    # 主退场由 trigger 决定
    if i3s < last_i:
        main_sell, main_kind = s3, "stop_3pct"
    elif i8 < last_i:
        main_sell, main_kind = t8, "trail_8pct"
    else:
        main_sell, main_kind = last_close, "time_exit"

    exits_pct = {
        "best":      round(_ret(best_sell), 3),
        "trail_3pct": round(_ret(t3), 3),
        "trail_5pct": round(_ret(t5), 3),
        "trail_8pct": round(_ret(t8), 3),
        "stop_3pct": round(_ret(s3), 3),
        "close":     round(_ret(last_close), 3),
        "rule_pri":  round(_ret(main_sell), 3),
    }
    return {
        "buy_date": buy_date, "sell_date": last_date,
        "buy_price": round(buy_price, 3),
        "sell_price": round(main_sell, 3),
        "return_pct": round(_ret(main_sell), 3),
        "trigger": main_kind,
        "hold_days": last_i,
        "exits_pct": exits_pct,
        "best_exit_pct": exits_pct["best"],
        "rule_vs_hold_pct": round(exits_pct["rule_pri"] - exits_pct["close"], 3),
        "rule_vs_best_pct": round(exits_pct["rule_pri"] - exits_pct["best"], 3),
    }


# ═════════════════════════════════════════════════════════════════
# 7) 主入口 — 周期 → 9 套退场胜率统计 (用户的核心需求)
# ═════════════════════════════════════════════════════════════════
def run_for_frontend(period_keys: list[str] | None = None,
                     hold_days: int = 3,
                     top_n: int = 1,
                     sample: int = 1200,
                     require_pass_all: bool = True,
                     breadth_min: int = 0,
                     breadth_min_soft: int = 0,
                     sector_hot_topn: int = 0,
                     sector_inflow_topn: int = 0,
                     require_surge_label: bool = False,
                     enable_actual_10: bool = False,
                     index_late_up: bool = False,
                     sector_late_up: bool = False,
                     tail_vol_ratio_min: float = 0.0,
                     progress_cb=None) -> dict:
    """顶级回测引擎: < 30s 出结果, 9 套退场景胜率 + 7 套持退场景胜率

    period_keys: ["1周","1月","半年","1年"] 任选; None = 默认半年
    hold_days: 持仓天数 (用于次级 7 套退场对比)
    top_n: 每日取几只 (默认 1 只)
    sample: 主板采样数 (默认 1200 只; 0=全量, 网络限流时耗时会很长)
    require_pass_all: True = 严格 8 条规则 (Z3 严格); False = 按 score rank
    breadth_min: 大盘红线 (硬底) — 全 A 红的股票 < 该值 当天不交易 (0=不启用)
    breadth_min_soft: 大盘软线 — 当日红盘介于 [breadth_min_soft, breadth_min) 之间时,
                       只交易 pick 板块当日属于 top {sector_hot_topn} 热门的票 (0=不启用)
    sector_hot_topn: 热门板块 top N (按当日板块 avg change_pct 排名), 配合 soft 用
    sector_inflow_topn: 资金净流入板块 top N (按当日板块 avg amount_ratio 估), 仅交易这些板块的 pick
    require_surge_label: 只选尾盘形态"次日大概率异动"的标的 (daily proxy: 收盘在日线高位+放量)

    2026-07-17 尾盘走势叠加参数:
    enable_actual_10: 用真实 10:00 close 重算水下退场 (慢, 默认关)
    index_late_up: 大盘尾盘强势过滤 (14:30-15:00 上证/创指 红盘)
    sector_late_up: 个股所在申万一级 14:30-15:00 红盘
    tail_vol_ratio_min: 个股尾盘 10min 量比下限 (0=禁用)

    判定逻辑:
      if breadth < breadth_min: skip (硬底, 大熊市空仓)
      elif breadth >= breadth_min_soft: trade (普涨/中性, 任何板块都交易)
      elif sector_hot_topn > 0 and pick.sector ∈ top{sector_hot_topn}_hot[t_date]: trade
      else: skip (结构性弱势日 + 冷门板块 → 跳过)

    返回:
      {
        summary: { trades, win_rate_pct, ... },  # 9 套退场的 S1 主退
        scenarios: { open: {胜率, cum, ...}, S1: {...}, ..., 9 套 },  # ←用户的核心要求
        scenarios_hold: { best, trail_3/5/8, stop_3, close, rule_pri },  # 7 套持退
        windows: [ {window, trades, win_rate, ...}, ... ],
        sector: [ {sector, trades, win_rate, sum_return}, ... ],
        monthly: [ {month, trades, monthly_return_pct, ...} ],
        exit_breakdown: { trigger: {count, pct} },
        equity_curve: [ [date_str, cum_pct], ... ],
        config: { period_keys, hold_days, top_n },
        engine_version: "v4 (vectorized)",
        took_sec: float,
        ts: ISO
      }
    """
    t0 = _time.time()
    period_keys = period_keys or ["半年"]
    if progress_cb: progress_cb("拉股票列表…")
    from .. import data_layer as _dl
    from . import sector_classify as _sc

    all_stocks = _dl.fetch_stock_list() or []
    universe_full = len(all_stocks)
    # 主板 + 按 成交额/活跃度 取前 sample (网络限流, 全量 3000+ 太慢)
    main_stocks = [(c, n) for c, n in all_stocks if _is_main_board(c)]
    if sample > 0 and len(main_stocks) > sample:
        # 按字母序稳定子集 (实际按 code 排不依赖行情 API)
        main_stocks = main_stocks[:sample]
    codes = [c for c, _ in main_stocks]
    names = {c: n for c, n in main_stocks}
    log.info(f"全量 {universe_full} 只, 主板 {len(main_stocks)} 只 (采样 {len(codes)})")

    if progress_cb: progress_cb("拉交易日历…")
    period_days_map = PERIOD_DAYS_MAP
    target_windows = [(k, period_days_map[k]) for k in period_keys if k in period_days_map] or [("半年", 120)]
    max_n = max(d for _, d in target_windows)
    end_str = pd.Timestamp.now().strftime("%Y%m%d")
    start_str = (pd.Timestamp.now() - pd.Timedelta(days=max_n + 250)).strftime("%Y%m%d")
    raw_dates = _dl.fetch_trade_dates(start_str, end_str) or []
    norm_dates = sorted({str(d).replace("-", "") for d in raw_dates if str(d).replace("-", "").isdigit()})
    log.info(f"交易日历: {len(norm_dates)} 天 ({norm_dates[0] if norm_dates else '?'}~{norm_dates[-1] if norm_dates else '?'})")

    if progress_cb: progress_cb(f"拉 {len(codes)} 只日线 (并行 40)…")
    daily_cache = _prefetch_daily(codes, days=max_n + 220, progress_cb=progress_cb)
    if progress_cb: progress_cb(f"日线 {len(daily_cache)} 命中 · 构建 master panel…")
    panel = _build_master_panel(daily_cache, names)
    panel = _add_metrics(panel)
    panel = _vectorized_screen(panel)
    # ── "次日大概率异动" 标签代理 (日线近似) ──
    # 当日收盘在日线中上部位 (close > (high+low)/2 = 尾盘拉升) + 放量 (vol_ratio>1.0)
    if require_surge_label:
        panel["_close_range_pct"] = (panel["收盘"] - panel["最低"]) / (panel["最高"] - panel["最低"] + 0.001)
        panel["_surge_label"] = ((panel["_close_range_pct"] > 0.55)
                                 & (panel["vol_ratio"].fillna(0) > 1.0)).astype(int)
        n_surge = panel["_surge_label"].sum()
        log.info(f"次日大概率异动标签: {n_surge}/{len(panel)} 行 = {n_surge/max(1,len(panel))*100:.1f}%")
    log.info(f"master panel: {len(panel)} 行 (codes={panel['code'].nunique()})")

    # ── 大盘红线 — 全 A 估计红盘数 ──
    # 主板采样只覆盖 sample 只,用 sample 内红盘比例 × 5400 估算
    # 注意: 全 A 含创业/科创/北交所,主板偏蓝筹,大盘下行时主板可能仍扛住 — 估算略偏乐观
    breadth_per_day: dict[str, int] = {}
    if breadth_min > 0 or breadth_min_soft > 0:
        UP_TOTAL_A = 5400
        valid_panel = panel.dropna(subset=["change_pct"])
        g = valid_panel.groupby("日期")
        up_count = g.apply(lambda d: int((d["change_pct"] > 0).sum()))
        total_count = g.size()
        ratio = (up_count / total_count).fillna(0)
        breadth_per_day = (ratio * UP_TOTAL_A).round().astype(int).to_dict()
        low_days_hard = sum(1 for v in breadth_per_day.values() if v < breadth_min) if breadth_min > 0 else 0
        soft_zone_days = sum(1 for v in breadth_per_day.values() if breadth_min <= v < breadth_min_soft) if breadth_min_soft > 0 and breadth_min > 0 else 0
        log.info(f"大盘过滤: {len(breadth_per_day)} 天 · 硬跳过 {low_days_hard} 天 / 软跳过 {soft_zone_days} 天 (估算自 {sample} 主板采样)")
        if progress_cb:
            msg = f"大盘: {len(breadth_per_day)} 天"
            if breadth_min > 0:
                msg += f" · 硬红线 {breadth_min} 跳过 {low_days_hard} 天"
            if breadth_min_soft > 0 and breadth_min > 0:
                msg += f" · 软红线 [{breadth_min},{breadth_min_soft}) 区间 {soft_zone_days} 天待板块热门判定"
            progress_cb(msg)

    # ── 板块映射 (供热门/资金流入共用) ──
    sector_per_code: dict[str, str] = {}
    need_sector = (sector_hot_topn > 0 and breadth_min_soft > 0) or sector_inflow_topn > 0
    if need_sector:
        if progress_cb: progress_cb("板块映射…")
        try:
            bulk = _sc.bulk_get_sector(codes)
            for c in codes:
                sec = (bulk.get(c) or {}).get("taxonomy") or {}
                l1 = sec.get("level1_cluster", "") or _board_fallback_sector(c)
                sector_per_code[c] = l1
            log.info(f"板块映射: {len(sector_per_code)} 只 (其中无板块 {sum(1 for v in sector_per_code.values() if not v)} 只)")
        except Exception as e:
            log.warning(f"板块映射失败, 退化为 board prefix: {e}")
            for c in codes:
                sector_per_code[c] = _board_fallback_sector(c)
        panel["sector"] = panel["code"].map(sector_per_code).fillna("其他")

    # ── 板块热门映射 (用于软红线叠加判定) ──
    hot_sectors_per_day: dict[str, set[str]] = {}
    if sector_hot_topn > 0 and breadth_min_soft > 0:
        if progress_cb: progress_cb(f"板块热门 · 取 {sector_hot_topn} 顶级…")
        sector_avg = panel.dropna(subset=["change_pct"]).groupby(["日期", "sector"])["change_pct"].mean().reset_index()
        for date_str, grp in sector_avg.groupby("日期"):
            top = grp.nlargest(sector_hot_topn, "change_pct")
            hot_sectors_per_day[str(date_str)] = set(top["sector"].astype(str).tolist())
        if progress_cb:
            avg_hot = np.mean([len(v) for v in hot_sectors_per_day.values()]) if hot_sectors_per_day else 0
            progress_cb(f"热门板块就绪 · 平均 {avg_hot:.1f} 板块/日")

    # ── 板块资金净流入 (按 amount_ratio 估) ──
    inflow_sectors_per_day: dict[str, set[str]] = {}
    if sector_inflow_topn > 0:
        if progress_cb: progress_cb(f"资金流入 · 取金额代理人 top {sector_inflow_topn}…")
        inflow_avg = panel.dropna(subset=["amount_ratio"]).groupby(["日期", "sector"])["amount_ratio"].mean().reset_index()
        for date_str, grp in inflow_avg.groupby("日期"):
            top = grp.nlargest(sector_inflow_topn, "amount_ratio")
            inflow_sectors_per_day[str(date_str)] = set(top["sector"].astype(str).tolist())
        if progress_cb:
            avg_inflow = np.mean([len(v) for v in inflow_sectors_per_day.values()]) if inflow_sectors_per_day else 0
            progress_cb(f"资金流入板块就绪 · 平均 {avg_inflow:.1f} 板块/日")

    if progress_cb: progress_cb("构建日期索引 (高速检索)…")
    # ── 建索引: {code: {date_str: row_dict}}, 用普通 dict,O(1) 查 ──
    # 不用 panel.iterrows() (慢), 也不每帧 .loc[] (中); 直接一次性 dict
    cache_by_code_date: dict[str, dict[str, dict]] = {}
    for code, df in daily_cache.items():
        if df is None or df.empty:
            continue
        d = {}
        for _, r in df.iterrows():
            d[str(r["日期"])] = {
                "open": float(r["开盘"]), "high": float(r["最高"]),
                "low": float(r["最低"]), "close": float(r["收盘"]),
                "date_str": str(r["日期"]),
            }
        cache_by_code_date[code] = d
    # panel 也按 (date, code) 索引 — 取 收盘/评分 等
    panel_idx: dict[str, dict[str, dict]] = {}
    for _, row in panel.iterrows():
        d = str(row["日期"])
        panel_idx.setdefault(d, {})[row["code"]] = row.to_dict()
    log.info(f"索引完成: {len(cache_by_code_date)} codes × {len(panel_idx)} dates")

    if progress_cb: progress_cb("向量筛股 → Top N…")

    # ── 按窗口分别取候选 → 模拟 → 统计 ──
    windows_result: list[dict] = []
    all_trades_nine: list[dict] = []      # 9 套退场用 (持仓 1 天)
    all_trades_seven: list[dict] = []     # 7 套退场用 (持仓 N 天)
    equity_curve: list[list] = []         # [[date, cum_pct]]
    skipped = {"no_pick": 0, "no_t1": 0, "no_panel": 0,
               "breadth_low": 0, "breadth_soft_no_hot": 0,
               "index_late_down": 0, "sector_late_down": 0, "tail_vol_low": 0}
    cum_return = 0.0

    # ── 尾盘走势叠加 (R21/R22/R23, 2026-07-17) ────────────────────
    # 用日线 OHLC 代理 14:30-15:00 尾盘强度, 避免拉 5min K线 (慢):
    #   late_pct_proxy = (close - low) / (high - low + 0.001)
    #     0 = 收盘贴近最低 (尾盘弱势), 1 = 收盘贴近最高 (尾盘强势)
    #   late_vol_proxy = late_pct_proxy * log1p(amount_ratio)
    #     尾盘强度 × 当日活跃度 → 个股尾盘放量代理
    # ─────────────────────────────────────────────────────────────
    panel["_late_pct"] = (panel["收盘"] - panel["最低"]) / (panel["最高"] - panel["最低"] + 0.001)
    panel["_late_vol"] = panel["_late_pct"] * np.log1p(panel["amount_ratio"].fillna(1.0))

    # (a) index_late_up_days: 全市场当日 late_pct 均值 > 0.55 → 大盘尾盘强势
    index_late_up_days: set[str] = set()
    if index_late_up:
        if progress_cb: progress_cb("尾盘叠加 · 计算大盘尾盘强度…")
        daily_late = panel.groupby("日期")["_late_pct"].mean()
        index_late_up_days = set(str(d) for d in daily_late[daily_late > 0.55].index)
        log.info(f"index_late_up: {len(index_late_up_days)}/{len(daily_late)} 天大盘尾盘强势 (阈值>0.55)")

    # (b) sector_late_up_per_day: 当日各板块 late_pct 均值 top 5 → 板块尾盘强势
    sector_late_up_per_day: dict[str, set[str]] = {}
    if sector_late_up:
        if progress_cb: progress_cb("尾盘叠加 · 计算板块尾盘强度…")
        sec_late = panel.groupby(["日期", "sector"])["_late_pct"].mean().reset_index()
        for date_str, grp in sec_late.groupby("日期"):
            top = grp.nlargest(5, "_late_pct")
            sector_late_up_per_day[str(date_str)] = set(top["sector"].astype(str).tolist())
        log.info(f"sector_late_up: {len(sector_late_up_per_day)} 天 · 平均 {np.mean([len(v) for v in sector_late_up_per_day.values()]):.1f} 板块/日")

    for wname, wdays in target_windows:
        valid_dates = sorted(panel_idx.keys()) if panel_idx else []
        w_dates = [d for d in norm_dates if d in set(valid_dates)][-wdays - 1:] if valid_dates else []
        if len(w_dates) < 2:
            windows_result.append({"window": wname, "trades": 0, "error": "dates_too_few"})
            continue
        w_trades_nine = []
        for i in range(len(w_dates) - 1):
            t_date = w_dates[i]
            t1_date = w_dates[i + 1]
            today_map = panel_idx.get(t_date)
            if not today_map:
                skipped["no_panel"] += 1
                continue
            # ── 大盘红线: 硬底 (大熊市空仓) ──
            b_today = breadth_per_day.get(t_date, 0) if (breadth_min > 0 or breadth_min_soft > 0) else 9999
            if breadth_min > 0 and b_today < breadth_min:
                skipped["breadth_low"] += 1
                continue
            # ── 软红线: 当日红盘介于 [breadth_min, breadth_min_soft) → 仅交易热门板块 ──
            in_soft_zone = (breadth_min_soft > 0 and breadth_min > 0
                            and breadth_min <= b_today < breadth_min_soft)
            # ── Top N by score (如果 require_pass_all 只在 pass_all 里选) ──
            candidates = []
            for code, row in today_map.items():
                if require_pass_all and not row.get("pass_all"):
                    continue
                # 软红线叠加: 仅当 pick 板块当日热门才纳入候选
                if in_soft_zone and hot_sectors_per_day:
                    sec = sector_per_code.get(code, "")
                    if sec not in hot_sectors_per_day.get(t_date, set()):
                        continue
                # 资金净流入板块过滤: 只交易 amount_ratio top N 板块的 pick
                if sector_inflow_topn > 0 and inflow_sectors_per_day:
                    sec = sector_per_code.get(code, "")
                    if sec not in inflow_sectors_per_day.get(t_date, set()):
                        continue
                # "次日大概率异动"标签过滤: 日线代理 (收盘在高位+放量)
                if require_surge_label and not row.get("_surge_label", False):
                    continue
                # ── 尾盘走势叠加 (R21/R22/R23) ──
                # R21: 大盘尾盘强势 (14:30-15:00 红盘) — 当日不在强势日 → 跳过
                if index_late_up and t_date not in index_late_up_days:
                    skipped["index_late_down"] += 1
                    continue
                # R22: 个股所在板块尾盘强势 — 仅交易当日板块 late_pct top5
                if sector_late_up:
                    sec = sector_per_code.get(code, "")
                    if sec not in sector_late_up_per_day.get(t_date, set()):
                        skipped["sector_late_down"] += 1
                        continue
                # R23: 个股尾盘放量 — late_vol_proxy (尾盘强度×活跃度) 下限
                if tail_vol_ratio_min > 0:
                    late_vol = float(row.get("_late_vol") or 0)
                    if late_vol < tail_vol_ratio_min:
                        skipped["tail_vol_low"] += 1
                        continue
                candidates.append((code, row))
            candidates.sort(key=lambda x: -x[1].get("score", 0))
            chosen = candidates[:top_n]
            if not chosen:
                if in_soft_zone:
                    skipped["breadth_soft_no_hot"] += 1
                else:
                    skipped["no_pick"] += 1
                continue

            # 批量化: 一次算 chosen 所有 trades 的 9 套退场 (~500x 快)
            rows_batch: list[dict] = []
            buys_batch: list[float] = []
            cm1_lookup: list[tuple[code, row, cm1]] = []  # 记录对应关系
            for code, row in chosen:
                buy_price = float(row["收盘"])
                cm1 = cache_by_code_date.get(code, {}).get(t1_date)
                if cm1 is None:
                    skipped["no_t1"] += 1
                    continue
                rows_batch.append(cm1)
                buys_batch.append(buy_price)
                cm1_lookup.append((code, row, cm1))

            if not rows_batch:
                continue
            sim_results = _simulate_batch(rows_batch, buys_batch)

            for (code, row, cm1), r9 in zip(cm1_lookup, sim_results):
                buy_price = float(row["收盘"])
                # 续涨停 → T+2 试一次 (罕见, 个例处理)
                if r9 is None:
                    try:
                        i1 = norm_dates.index(t1_date)
                        t2_date = norm_dates[i1 + 1] if i1 + 1 < len(norm_dates) else None
                    except Exception:
                        t2_date = None
                    if t2_date:
                        cm2 = cache_by_code_date.get(code, {}).get(t2_date)
                        if cm2:
                            r9 = _simulate_from_cache_row(cm2, buy_price)
                            if r9 is not None:
                                r9["hold_extended"] = True
                if r9 is None:
                    continue
                r9.update({
                    "code":         code,
                    "name":         row.get("name", code),
                    "buy_date":     t_date,
                    "buy_price":    round(buy_price, 3),
                    "sell_date":    t1_date,
                    "score":        float(row.get("score", 0)),
                    "change_pct":   round(float(row.get("change_pct", 0)), 3),
                    "date_t":       t_date,
                    "_cm_t1":       cm1,  # actual_10 重算需要
                })
                w_trades_nine.append(r9)
                cum_return += r9["S1"]
                equity_curve.append([str(t_date), round(cum_return, 3)])
        # 7 套
        w_trades_seven = []
        for tr in w_trades_nine:
            cm = cache_by_code_date.get(tr["code"], {})
            t7 = _simulate_hold_from_idx(cm, tr["buy_date"], _add_days(tr["sell_date"], hold_days - 1), tr["buy_price"])
            if t7 is not None:
                t7.update({"code": tr["code"], "name": tr["name"], "pick_date": tr["buy_date"]})
                w_trades_seven.append(t7)

        log.info(f"  {wname}: 9套 {len(w_trades_nine)} 笔, 7套 {len(w_trades_seven)} 笔, skipped={skipped}")
        # 窗口级统计
        ws_nine = _stat_nine_scenarios(w_trades_nine)
        ws_seven = _stat_seven_scenarios(w_trades_seven)
        windows_result.append({
            "window": wname,
            "n_dates": len(w_dates),
            "trades": len(w_trades_nine),
            "recovery_rate": round(sum(1 for t in w_trades_nine if t.get("recovered")) / max(1, len(w_trades_nine)) * 100, 1),
            "skipped_no_pick": skipped["no_pick"],
            "skipped_no_t1": skipped["no_t1"],
            "skipped_breadth_low": skipped["breadth_low"],
            "scenarios_9": ws_nine,                # ← 用户核心要求: 9 套退场胜率
            "scenarios_7": ws_seven,                # 7 套持退对比
            "win_rate_S1": ws_nine.get("S1", {}).get("win_rate_pct", 0),
        })
        all_trades_nine.extend(w_trades_nine)
        all_trades_seven.extend(w_trades_seven)
        if progress_cb: progress_cb(f"{wname} 完成 ({len(w_trades_nine)} 笔)")

    # ── 全期汇总 ──
    if progress_cb: progress_cb("汇总统计…")
    log.info(f"[回测 v4] 汇总开始 · 9套={len(all_trades_nine)} 7套={len(all_trades_seven)} 累计t={_time.time()-t0:.1f}s")
    overall_nine = _stat_nine_scenarios(all_trades_nine)
    log.info(f"[回测 v4] 9套统计完 · t={_time.time()-t0:.1f}s")
    overall_seven = _stat_seven_scenarios(all_trades_seven)
    log.info(f"[回测 v4] 7套统计完 · t={_time.time()-t0:.1f}s")

    # 月度胜负 — 用 9 套 S1 主退场
    monthly = _compute_monthly_from_trades(all_trades_nine, "S1")
    log.info(f"[回测 v4] 月度完 · {len(monthly)}月 · t={_time.time()-t0:.1f}s")

    # 退场原因分布
    exit_breakdown = _exit_breakdown(all_trades_nine)
    log.info(f"[回测 v4] 退出原因完 · t={_time.time()-t0:.1f}s")

    # 板块聚合 — 用 sector_classify
    if progress_cb: progress_cb("板块归类…")
    sector_breakdown = _sector_breakdown(all_trades_nine, _sc)
    log.info(f"[回测 v4] 板块完 · {len(sector_breakdown)} · t={_time.time()-t0:.1f}s")

    # 总评 (9 套 → 用 S1 作为主退场口径)
    summary = _build_summary(all_trades_nine, equity_curve)
    log.info(f"[回测 v4] 总评完 · t={_time.time()-t0:.1f}s")

    # R69: equity_curve 采样 (≤ 500 点), 半年回测原始 ~120 点无需采样, 1年才触发
    if len(equity_curve) > 500:
        step = max(1, len(equity_curve) // 500)
        equity_curve = equity_curve[::step]
        log.info(f"equity_curve 采样 {len(equity_curve)} 点 (step={step})")

    elapsed = round(_time.time() - t0, 2)
    log.info(f"回测 v4 完成: 9套 {len(all_trades_nine)} 笔 · {elapsed}s · S1 胜率 {overall_nine.get('S1',{}).get('win_rate_pct','?')}%")

    # ── 5分钟K线: 水下开盘票的翻红窗口分析 (快, 保留) ──
    if progress_cb: progress_cb("5分钟翻红分析…")
    recovery_stats = _analyze_fivemin_recovery(all_trades_nine, progress_cb=progress_cb)

    # ── actual_10 系列: 用真实 10:00 close 重算水下退场 (慢, 默认关 — 2026-07-17 R1) ──
    if enable_actual_10:
        if progress_cb: progress_cb("actual_10 重算…")
        actual_10_stats = _recompute_actual_10(all_trades_nine)
        log.info(f"actual_10 重算完 · t={_time.time()-t0:.1f}s")
    else:
        actual_10_stats = {"skipped": True, "note": "actual_10 默认关闭 (需 enable_actual_10=true)"}
        log.info(f"actual_10 跳过 (用户未开启) · t={_time.time()-t0:.1f}s")

    out = {
        "summary": summary,
        "windows": windows_result,
        # ✅ 用户要求: 9 套退场胜率 + 推荐 (横表清晰展示)
        "scenarios": overall_nine,
        "scenarios_hold": overall_seven,
        "exit_breakdown": exit_breakdown,
        "monthly": monthly,
        "recovery_5min": recovery_stats,
        "actual_10_stats": actual_10_stats,
        "sector": sector_breakdown,
        "equity_curve": equity_curve,
        # 退场对比表 (前端) — 剔除 _cm_t1 等大字段,保留策略相关 ret + 标识
        "trades": [
            {k: v for k, v in t.items() if not k.startswith("_") and k != "exits_pct" and k != "trigger"}
            | {"S1_recovered": t.get("recovered", False), "S1_trigger": t.get("trigger", "")}
            for t in all_trades_nine[:500]  # 最多 500 笔,超过截断 (防 JSON 爆炸)
        ],
        "trades_count": len(all_trades_nine),
        "config": {
            "period_keys": period_keys,
            "hold_days":   hold_days,
            "top_n":       top_n,
            "require_pass_all": require_pass_all,
            "sample_size": len(codes),
            "universe_size": universe_full,
            "breadth_min": breadth_min,
            "breadth_skipped_days": skipped["breadth_low"],
            "sector_inflow_topn": sector_inflow_topn,
            "require_surge_label": require_surge_label,
            "index_late_up":     index_late_up,
            "sector_late_up":    sector_late_up,
            "tail_vol_ratio_min": tail_vol_ratio_min,
            "index_late_skipped": skipped["index_late_down"],
            "sector_late_skipped": skipped["sector_late_down"],
            "tail_vol_skipped":   skipped["tail_vol_low"],
        },
        "engine_version": "v4 (vectorized · top-tier + late-session)",
        "took_sec": elapsed,
        "ts": pd.Timestamp.now().isoformat(),
    }
    # 落盘 (调试用)
    try:
        import json as _json
        fp = "/Users/kaikai/scripts/tuixue_v3/data/backtest_screener_v4_result.json"
        import os as _os
        _os.makedirs(_os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(_json.dumps(out, ensure_ascii=False, indent=2, default=str))
    except Exception:
        pass
    return out


# ═════════════════════════════════════════════════════════════════
# 7.5) 5分钟K线翻红窗口分析 (新浪5min)
# ═════════════════════════════════════════════════════════════════
# 7.4) 5min K线多源兜底 (Baostock → Sina → Akshare)
# ═════════════════════════════════════════════════════════════════
_FIVEMIN_CACHE_TTL = 86400  # 24h Redis TTL (盘中不缓存,盘后24h足够)


def _fetch_5min_baostock(code: str, start_date: str, end_date: str) -> list[dict] | None:
    """源1: Baostock 5min K线 (前复权, 跟 daily 一致).

    Returns: [{day, open, high, low, close, volume}, ...]
    或 None (失败/无数据)

    R52: import baostock 失败 / FD 异常 → 兜底 None, 不让上层整个 BT 挂掉
    """
    try:
        import baostock as bs
    except (ImportError, OSError) as e:
        # OSError: Bad file descriptor (Python 3.11 + baostock 在多 worker 下偶发)
        log.debug(f"baostock import 失败 {code}: {e}")
        return None
    bs_code = ("sh" if code.startswith(("6", "9", "5")) else "sz") + "." + code
    try:
        lg = bs.login()
        if lg.error_code != "0":
            return None
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,time,open,high,low,close,volume",
            start_date=start_date, end_date=end_date,
            frequency="5", adjustflag="1")  # 1=前复权
        data = []
        while rs.error_code == "0" and rs.next():
            data.append(rs.get_row_data())
        bs.logout()
        if not data:
            return None
        out = []
        for row in data:
            d, t, o, h, l, c, v = row
            # time: 20260715100000000 -> 10:00
            hhmm = t[8:12]
            day_str = f"{d} {hhmm[:2]}:{hhmm[2:4]}:00"
            try:
                out.append({
                    "day":    day_str,
                    "open":   float(o),
                    "high":   float(h),
                    "low":    float(l),
                    "close":  float(c),
                    "volume": float(v) if v else 0,
                    "_src":   "baostock",
                })
            except (ValueError, TypeError):
                continue
        return out or None
    except Exception:
        try: bs.logout()
        except Exception: pass
        return None


def _fetch_5min_sina(code: str, datalen: int = 10000) -> list[dict] | None:
    """源2: 新浪 5min K线 (无复权, 历史约 ~5 个月)

    Returns: [{day, open, high, low, close, volume, _src}, ...]
    """
    import requests as _req
    import json as _json
    mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
    try:
        r = _req.get(
            "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData",
            params={"symbol": f"{mkt}{code}", "scale": "5", "ma": "no", "datalen": str(datalen)},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
        if r.status_code != 200 or not r.text.strip().startswith("["):
            return None
        bars = _json.loads(r.text)
        for b in bars:
            b["_src"] = "sina"
        return bars or None
    except Exception:
        return None


def _fetch_5min_akshare(code: str, start_date: str, end_date: str) -> list[dict] | None:
    """源3: akshare 东财 5min (常被封 IP, 兜底用)"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist_min_em(
            symbol=code, period="5",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust="qfq")
        if df is None or df.empty:
            return None
        out = []
        # 列: 时间, 开盘, 收盘, 最高, 最低, 成交量, ...
        for _, row in df.iterrows():
            t = str(row.get("时间", ""))
            # t 格式: 2026-07-15 10:00
            out.append({
                "day":    t,
                "open":   float(row.get("开盘", 0) or 0),
                "high":   float(row.get("最高", 0) or 0),
                "low":    float(row.get("最低", 0) or 0),
                "close":  float(row.get("收盘", 0) or 0),
                "volume": float(row.get("成交量", 0) or 0),
                "_src":   "akshare",
            })
        return out or None
    except Exception:
        return None


def _fetch_5min_for_code(code: str, start_date: str, end_date: str) -> list[dict]:
    """多重兜底: Sina → Baostock(归一化) → Akshare + Redis 缓存

    start_date/end_date: YYYY-MM-DD
    Returns list of 5min bars, empty list on total failure.

    ⚠️ 源选择关键: 日线 cache 是原始价格 (≈akshare qfq 退化),
       5min 必须用同口径源,否则水下 open=40 但 10:00=240 直接爆 cum.
       Sina 5min 是原始价格 (与 daily cache 同口径), 优先用.
       Baostock 5min 是真前复权 (基数不同), 拉 daily 5min 作归一化因子修正.
       Akshare 5min 兜底 (限频, 慢).
    """
    import json as _json
    cache_key = f"bt5min:v3:{code}:{start_date}:{end_date}"
    # 1) Redis 缓存
    try:
        from .. import cache_store as _cs
        cached = _cs.get(cache_key)
        if cached is not None:
            return _json.loads(cached)
    except Exception:
        pass

    # 2) Sina (主, 原始价格, 跟 daily cache 同口径)
    bars = _fetch_5min_sina(code)
    src = "sina"
    # 3) Sina 不够覆盖 → Baostock (归一化到 daily cache 口径)
    # R52: baostock 在多 worker 下偶发 "Bad file descriptor", 包 try/except 不让上层挂
    if not bars or _sina_coverage_ok(bars, start_date, end_date) is False:
        try:
            bs_bars = _fetch_5min_baostock(code, start_date, end_date)
        except (OSError, ImportError) as e:
            log.debug(f"_fetch_5min_baostock {code} 兜底 None: {e}")
            bs_bars = None
        if bs_bars:
            # 用 baostock 5min 第一根的 close / 同期某天 daily close 估算归一化因子
            bs_bars = _normalize_baostock_5min_to_daily(code, bs_bars, start_date, end_date)
            if bs_bars and (not bars or len(bs_bars) > len(bars)):
                bars = bs_bars
                src = "baostock_norm"
            elif not bars:
                bars = bs_bars
                src = "baostock_norm" if bs_bars else "sina"
    # 4) Akshare 再兜底 (限频, 慎用)
    if not bars:
        bars = _fetch_5min_akshare(code, start_date, end_date)
        src = "akshare"

    # 缓存 (即使空列表也缓存,避免反复打挂掉的源)
    try:
        from .. import cache_store as _cs
        _cs.set(cache_key, _json.dumps(bars or []), ttl=_FIVEMIN_CACHE_TTL)
    except Exception:
        pass

    if bars and not bars[0].get("_src"):
        for b in bars:
            b["_src"] = src
    return bars or []


def _sina_coverage_ok(bars: list[dict], start_date: str, end_date: str) -> bool:
    """Sina 返回的 bars 是否覆盖了 [start_date, end_date] 全段?
    Sina 5min 历史 ~5 个月, 但日期零散; 如果要 2026-01 数据, Sina 100% 没有。
    """
    if not bars:
        return False
    days = {b.get("day", "")[:10] for b in bars if b.get("day")}
    if not days:
        return False
    try:
        s = start_date
        e = end_date
        if min(days) > s:
            return False  # 最早数据都比 start_date 晚
        return True
    except Exception:
        return True


def _normalize_baostock_5min_to_daily(code: str, bars: list[dict],
                                       start_date: str, end_date: str) -> list[dict]:
    """把 baostock 5min (前复权真值) 归一化到 daily cache 的口径 (≈原始价格)
    用 daily cache 第一行的收盘价 ÷ baostock 第一根 5min K 的 open,
       作为整体缩放因子 (前复权 ↔ 原始价格 的转换率)。
    """
    if not bars:
        return bars
    try:
        from ..cache_db import daily as _daily
        df = _daily().get(code, days=30)
        if df is None or df.empty or "日期" not in df.columns:
            return bars
        # 找 cache 里第一个日期 >= start_date 的 row
        from datetime import datetime
        s_dt = datetime.strptime(start_date, "%Y-%m-%d")
        df["_dt"] = df["日期"].apply(lambda x: datetime.strptime(str(x), "%Y%m%d") if x else None)
        sub = df[df["_dt"] >= s_dt].head(1)
        if sub.empty:
            return bars
        daily_close = float(sub.iloc[0]["收盘"])
        # baostock 第一根 (≈ 09:35) 的 open 是当天的近似开盘
        bs_open = float(bars[0].get("open", 0))
        if bs_open <= 0 or daily_close <= 0:
            return bars
        # 缩放因子: 原始 / qfq
        scale = daily_close / bs_open
        if abs(scale - 1.0) < 0.001:
            return bars  # 几乎一致, 不动
        for b in bars:
            for k in ("open", "high", "low", "close"):
                if b.get(k) is not None:
                    b[k] = round(float(b[k]) * scale, 6)
        return bars
    except Exception:
        return bars


# ═════════════════════════════════════════════════════════════════
def _analyze_fivemin_recovery(trades: list[dict], progress_cb=None) -> dict:
    """对水下开盘的交易, 用新浪5min K线分析9:00-10:00翻红概率。

    Sina 5min K 一次返回~1440根K线 (≈60交易日), 按 code 分组一次fetch,
    再按 (code, sell_date) 过滤各笔交易的5min序列。
    """
    if not trades:
        return {"n_underwater": 0, "error": "no_trades"}

    import requests as _req
    import json as _json

    # 收集水下开盘的交易 (open ≤ buy_price)
    underwater = []
    for t in trades:
        open_ret = t.get("open", 0)
        buy_price = t.get("buy_price", 0)
        if open_ret <= 0:  # 不翻红: open ≤ buy_price → open% ≤ 0
            code = t.get("code", "")
            sell_date = str(t.get("sell_date", "") or "")
            if code and len(sell_date) >= 8:
                underwater.append(t)

    if not underwater:
        return {"n_underwater": 0, "note": "all_stocks_open_green"}

    n_total = len(underwater)

    # 按 code 分组, 同 code 只需拉一次 5min (多源兜底)
    by_code: dict[str, list[dict]] = {}
    for t in underwater:
        by_code.setdefault(t["code"], []).append(t)

    recovered_9_10 = 0      # 9:30-10:00 内翻红
    recovered_10_1130 = 0   # 10:00-11:30 翻红
    recovered_13_15 = 0     # 13:00-15:00 翻红
    never_recovered = 0     # 全天未翻红
    fetch_failed = 0
    fetched_ok = 0
    src_counter: dict[str, int] = {}  # 记录每源用了多少次
    _code_idx = 0                    # R97: 进度显示 + cancel 检查位

    for code, trades_for_code in by_code.items():
        _code_idx += 1
        # R97: 每个 code 都调一次 progress_cb, 让 _bt_run_bg._cb 看到 cancel 标记
        if progress_cb:
            progress_cb(f"5分钟翻红 {_code_idx}/{len(by_code)} (分析中…)")
        # 拉一次, 覆盖所有 trade 的日期范围
        sell_dates = sorted({str(t.get("sell_date", ""))[:8] for t in trades_for_code if t.get("sell_date")})
        if not sell_dates:
            fetch_failed += len(trades_for_code)
            continue
        start_date = f"{sell_dates[0][:4]}-{sell_dates[0][4:6]}-{sell_dates[0][6:8]}"
        end_date = f"{sell_dates[-1][:4]}-{sell_dates[-1][4:6]}-{sell_dates[-1][6:8]}"
        bars = _fetch_5min_for_code(code, start_date, end_date)
        if not bars:
            fetch_failed += len(trades_for_code)
            continue
        # 记录来源
        if bars and bars[0].get("_src"):
            src_counter[bars[0]["_src"]] = src_counter.get(bars[0]["_src"], 0) + 1

        for t in trades_for_code:
            sell_date = str(t.get("sell_date", "") or "")
            buy_price = t.get("buy_price", 0)
            if not sell_date or buy_price <= 0:
                never_recovered += 1
                continue
            # sell_date YYYYMMDD → YYYY-MM-DD, 各源格式基本一致
            day_prefix = f"{sell_date[:4]}-{sell_date[4:6]}-{sell_date[6:8]}"
            day_bars = [b for b in bars if b.get("day", "").startswith(day_prefix)]

            recovered = False
            for b in day_bars:
                dt = b.get("day", "")
                high = float(b.get("high", 0) or 0)
                if high >= buy_price:
                    recovered = True
                    fetched_ok += 1
                    time_tag = dt[11:16] if len(dt) >= 16 else "99:99"
                    if time_tag <= "10:00":
                        recovered_9_10 += 1
                    elif time_tag <= "11:30":
                        recovered_10_1130 += 1
                    else:
                        recovered_13_15 += 1
                    break
            if not recovered:
                never_recovered += 1
                fetched_ok += 1

    pct = lambda n: round(n / max(1, n_total) * 100, 1)
    src_str = ",".join(f"{k}={v}" for k, v in src_counter.items()) if src_counter else "none"
    note_parts = [f"多源:{src_str}"]
    if fetch_failed:
        note_parts.append(f"{fetch_failed}笔数据拉取失败")
    return {
        "n_underwater":        n_total,
        "recovered_9_10":      recovered_9_10,
        "recovered_9_10_pct":  pct(recovered_9_10),
        "recovered_10_1130":   recovered_10_1130,
        "recovered_10_1130_pct": pct(recovered_10_1130),
        "recovered_13_15":     recovered_13_15,
        "recovered_13_15_pct": pct(recovered_13_15),
        "never_recovered":     never_recovered,
        "never_recovered_pct": pct(never_recovered),
        "fetch_failed":        fetch_failed,
        "sources":             src_counter,
        "note": "; ".join(note_parts),
    }


def _recompute_actual_10(trades: list[dict]) -> dict:
    """actual_10 系列: 用真实 10:00 close 重算水下退场, 对比"open 代理"差异。

    流程: 收集水下开盘 trades → 按 code 分组 → 多源 5min (Baostock→Sina→Akshare)
          → 找 10:00 bar close → 重跑 _simulate_from_cache_row(actual_10_close=...)
    """
    if not trades:
        return {"error": "no_trades"}

    # 1) 收集水下开盘 trades (open ≤ buy_price, 即 ret_open ≤ 0)
    underwater = []
    for t in trades:
        if t.get("open", 0) <= 0:
            code = t.get("code", "")
            sell_date = str(t.get("sell_date", "") or "")
            cm_t1 = t.get("_cm_t1")
            buy_price = t.get("buy_price", 0)
            if code and len(sell_date) >= 8 and cm_t1 and buy_price > 0:
                underwater.append((t, cm_t1, code, sell_date, buy_price))

    if not underwater:
        return {"n_underwater": 0, "note": "no underwater trades to recompute"}

    n_total = len(underwater)
    n_enriched = 0
    n_failed = 0

    # 2) 按 code 分组 → 同 code 一次拉 (Baostock→Sina→Akshare 兜底)
    by_code: dict[str, list] = {}
    for item in underwater:
        by_code.setdefault(item[2], []).append(item)

    five_min_cache: dict[str, list] = {}
    src_counter: dict[str, int] = {}

    # 2.5) 并行拉多 code 5min (ThreadPoolExecutor, 8 worker 避免被新浪 ban)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_one(code_items):
        code, items = code_items
        sell_dates = sorted({str(t.get("sell_date", ""))[:8] for t, _, _, sd, _ in items if sd})
        if not sell_dates:
            return code, [], len(items)
        start_date = f"{sell_dates[0][:4]}-{sell_dates[0][4:6]}-{sell_dates[0][6:8]}"
        end_date = f"{sell_dates[-1][:4]}-{sell_dates[-1][4:6]}-{sell_dates[-1][6:8]}"
        bars = _fetch_5min_for_code(code, start_date, end_date)
        return code, bars, len(items)

    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = {pool.submit(_fetch_one, (c, items)): c for c, items in by_code.items()}
        for fut in as_completed(futs):
            code, bars, n_items = fut.result()
            if not bars:
                n_failed += n_items
                continue
            five_min_cache[code] = bars
            if bars and bars[0].get("_src"):
                src_counter[bars[0]["_src"]] = src_counter.get(bars[0]["_src"], 0) + 1

    # 3) 对每笔水下交易, 找 10:00 时刻价格, 批量重跑模拟
    # 关键: 10:00 时刻 = 10:00 K线的 OPEN (该K线代表 10:00~10:05, open 就是 10:00 成交价)
    #       之前取的是 close (= 10:05 价格), 跟用户意图差 5 分钟
    # 3.1) 先找所有 10:00 价格 + 准备 batch
    enriched_input: list[tuple] = []   # [(t, cm_t1, actual_10_close), ...]
    for t, cm_t1, code, sell_date, buy_price in underwater:
        bars = five_min_cache.get(code, [])
        day_prefix = f"{sell_date[:4]}-{sell_date[4:6]}-{sell_date[6:8]}"
        ten_bars = [b for b in bars
                    if b.get("day", "").startswith(day_prefix)
                    and b.get("day", "")[11:16] == "10:00"]
        actual_10_close = float(ten_bars[0]["open"]) if ten_bars else None
        if actual_10_close and actual_10_close > 0:
            enriched_input.append((t, cm_t1, actual_10_close, buy_price))
        else:
            n_failed += 1

    # 3.2) 批量 _simulate_batch (向量化 ~500x 快)
    actual10_trades = []
    if enriched_input:
        rows = [cm for _, cm, _, _ in enriched_input]
        buys = [bp for _, _, _, bp in enriched_input]
        a10s = [a for _, _, a, _ in enriched_input]
        sim_results = _simulate_batch(rows, buys, a10s)
        for (t, cm_t1, _, _), sim in zip(enriched_input, sim_results):
            if sim:
                t["S3_actual10_trail80"] = sim["S3_actual10_trail80"]
                t["S_actual10_close"] = sim["S_actual10_close"]
                actual10_trades.append(t)
                n_enriched += 1
            else:
                n_failed += 1
                continue

    # 4) 统计 actual_10 系列 (与原版对比)
    if not actual10_trades:
        return {"n_underwater": n_total, "n_enriched": 0, "n_failed": n_failed,
                "note": "no 5min data available for these trades",
                "src_counter": src_counter}

    actual10_stats = _stat_subset(actual10_trades, ["S3_actual10_trail80", "S_actual10_close"])
    baseline_stats = _stat_subset(actual10_trades, ["S3_trail80", "close"])
    global_baseline = _stat_subset(trades, ["S3_trail80", "close"])

    src_note = ", ".join(f"{k}={v}" for k, v in src_counter.items()) or "none"
    return {
        "n_underwater":     n_total,
        "n_enriched":       n_enriched,
        "n_failed":         n_failed,
        "baseline_same_n":  baseline_stats,
        "actual_10":        actual10_stats,
        "baseline_global":  global_baseline,
        "diff_S3_trail80":  round(actual10_stats.get("S3_actual10_trail80", {}).get("cum_return_pct", 0)
                                  - baseline_stats.get("S3_trail80", {}).get("cum_return_pct", 0), 2),
        "diff_close":       round(actual10_stats.get("S_actual10_close", {}).get("cum_return_pct", 0)
                                  - baseline_stats.get("close", {}).get("cum_return_pct", 0), 2),
        "src_counter":      src_counter,
        "note": f"水下 {n_enriched}/{n_total} 笔用真实 10:00 close 重算 (源: {src_note})",
    }


def _stat_subset(trades: list[dict], keys: list[str]) -> dict[str, dict]:
    """对指定 keys 算 cum/avg/win (复用 _stat_nine_scenarios 的核心逻辑)"""
    if not trades or not keys:
        return {}
    out: dict[str, dict] = {}
    for k in keys:
        rets = [t.get(k, 0) for t in trades if isinstance(t.get(k), (int, float))]
        if not rets:
            continue
        wins = sum(1 for r in rets if r > 0)
        cum = 1.0
        for r in rets:
            cum *= (1 + r / 100)
        cum_pct = (cum - 1) * 100
        out[k] = {
            "trades":          len(rets),
            "wins":            wins,
            "win_rate_pct":    round(wins / len(rets) * 100, 2),
            "avg_pct":         round(float(np.mean(rets)), 3),
            "cum_return_pct":  round(cum_pct, 2),
        }
    return out


# ═════════════════════════════════════════════════════════════════
# 8) 统计 — 9 套退场胜率 (用户核心要求)
# ═════════════════════════════════════════════════════════════════
def _annualized_monthly(cum_pct: float, n_days: int) -> tuple[float, float]:
    """累计% + 实际交易日跨度 → 年化% / 月化%

    2026-07-17 修复: 短窗口 (1 周/2 周) 复利年化爆炸到 988,074,870% 毫无参考价值.
    改用 **简单年化** (r × 250/n_days) — 对短窗口更保守可信,长窗口与复利差距不大.

    cum_pct: 总累计收益 (eg 188.36 表示 +188.36%)
    n_days:  从首笔到末笔的实际交易日数
    """
    if n_days <= 0:
        return 0.0, 0.0
    # 简单年化: 把 N 天的累计收益线性外推到 250 天
    # 公式: 年化 = 累计 × (250 / N). 月化 = 累计 × (21 / N)
    ann = cum_pct * 250.0 / n_days
    mon = cum_pct * 21.0 / n_days
    # 软上限 ±9999% — 避免极端值;前端会用 ↑9999% 标记
    ann = max(-9999.0, min(9999.0, ann))
    mon = max(-9999.0, min(9999.0, mon))
    return round(ann, 2), round(mon, 2)


def _period_days(trades: list[dict]) -> int:
    """从 trades 的 buy_date 取首末日期, 算交易日跨度

    优先用自然日差直接估算 (1.4 倍), 短窗口(< 5 笔) 也走这个口径, 避免除零
    返回的 days 用于 _annualized_monthly → 年化/复利分母
    """
    if not trades:
        return 0
    dates = sorted({str(t.get("date_t") or t.get("buy_date") or t.get("pick_date") or "") for t in trades if t.get("date_t") or t.get("buy_date") or t.get("pick_date")})
    dates = [d for d in dates if d and len(d) >= 8]
    if len(dates) < 2:
        return 0
    try:
        d0 = pd.Timestamp(int(dates[0][:4]), int(dates[0][4:6]), int(dates[0][6:8]))
        d1 = pd.Timestamp(int(dates[-1][:4]), int(dates[-1][4:6]), int(dates[-1][6:8]))
        days = (d1 - d0).days
        # 自然日 → 估算交易日 (1.4 倍, A 股年均 250 / 365 ≈ 0.685)
        # 修正: 国内交易所 2024 年 244 交易日 / 366 自然日 ≈ 0.667, 故用 1/0.685 ≈ 1.46
        return max(1, int(days * 5 / 7))
    except Exception:
        return 0


def _stat_nine_scenarios(trades: list[dict]) -> dict[str, dict]:
    """9 套退场各自胜率 + 均值 + 累计复利 + 年化 + 月化 + 分位 + 期望值 + 盈亏比"""
    if not trades:
        return {}
    keys = ("open", "S1", "S2", "S2_trail80", "S2_tp2", "S2_avg_up",
            "S3_trail80", "trail50", "gap_target", "gap_cut_2pct", "bull_candle",
            "avg_up", "max95", "low", "close", "twap", "tp2", "half")
    by_kind: dict[str, list[float]] = {k: [] for k in keys}
    for t in trades:
        for k in keys:
            v = t.get(k)
            if isinstance(v, (int, float)):
                by_kind[k].append(float(v))
    n_days = _period_days(trades)
    out: dict[str, dict] = {}
    for k, arr in by_kind.items():
        if not arr:
            out[k] = {"n": 0, "win_rate_pct": 0, "avg_pct": 0, "cum_return_pct": 0}
            continue
        a = np.asarray(arr, dtype=float)
        n = len(a)
        wins = int((a > 0).sum())
        win_sum = float(a[a > 0].sum())
        loss_sum = abs(float(a[a < 0].sum()))
        avg = float(a.mean())
        median = float(np.median(a))
        std = float(a.std(ddof=1)) if n > 1 else 0.0
        eq = (1 + a / 100).cumprod()
        cum = float((eq[-1] - 1) * 100) if len(eq) else 0
        ann_pct, mon_pct = _annualized_monthly(cum, n_days)
        # 期望值
        exp = (wins / n) * (win_sum / max(1, wins)) + ((n - wins) / n) * (-loss_sum / max(1, n - wins))
        out[k] = {
            "n": n,
            "wins": wins,
            "win_rate_pct": round(wins / n * 100, 2),
            "avg_pct": round(avg, 3),
            "median_pct": round(median, 3),
            "stddev_pct": round(std, 3),
            "best_pct": round(float(a.max()), 2),
            "worst_pct": round(float(a.min()), 2),
            "cum_return_pct": round(cum, 2),
            "annualized_pct": ann_pct,
            "monthly_pct": mon_pct,
            "period_days": n_days,
            "expectancy_pct": round(exp, 3),
            "profit_factor": round(win_sum / loss_sum, 2) if loss_sum > 0 else None,
            "p25": round(float(np.percentile(a, 25)), 3),
            "p75": round(float(np.percentile(a, 75)), 3),
        }
    # 关键参考: S1 vs close vs best (→ best 用 _exit_breakdown 推到 _maximize 套)
    if "S1" in out and "open" in out:
        out["_rule_vs_open_gap"] = round(out["S1"]["avg_pct"] - out["open"]["avg_pct"], 3)
    if "S1" in out and "close" in out:
        out["_rule_vs_close_gap"] = round(out["S1"]["avg_pct"] - out["close"]["avg_pct"], 3)
    out["_best_strategy"] = max(
        [(k, v["avg_pct"]) for k, v in out.items() if isinstance(v, dict) and "avg_pct" in v and k in keys],
        key=lambda x: x[1], default=("S1", 0)
    )[0]
    return out


def _stat_seven_scenarios(trades: list[dict]) -> dict[str, dict]:
    """7 套持退场景横向对比 (backtest.py 风格) + 年化/月化"""
    if not trades:
        return {}
    keys = ("best", "trail_3pct", "trail_5pct", "trail_8pct", "stop_3pct", "close", "rule_pri")
    by_kind: dict[str, list[float]] = {k: [] for k in keys}
    for t in trades:
        ep = t.get("exits_pct") or {}
        for k in keys:
            v = ep.get(k)
            if isinstance(v, (int, float)):
                by_kind[k].append(float(v))
    n_days = _period_days(trades)
    out: dict[str, dict] = {}
    for k, arr in by_kind.items():
        if not arr:
            continue
        a = np.asarray(arr, dtype=float)
        n = len(a)
        wins = int((a > 0).sum())
        win_sum = float(a[a > 0].sum())
        loss_sum = abs(float(a[a < 0].sum()))
        eq = (1 + a / 100).cumprod()
        cum = float((eq[-1] - 1) * 100) if len(eq) else 0
        ann_pct, mon_pct = _annualized_monthly(cum, n_days)
        out[k] = {
            "n": n,
            "win_rate_pct": round(wins / n * 100, 2),
            "avg_pct": round(float(a.mean()), 3),
            "median_pct": round(float(np.median(a)), 3),
            "cum_return_pct": round(cum, 2),
            "annualized_pct": ann_pct,
            "monthly_pct": mon_pct,
            "period_days": n_days,
            "profit_factor": round(win_sum / loss_sum, 2) if loss_sum > 0 else None,
            "best_pct": round(float(a.max()), 2),
            "worst_pct": round(float(a.min()), 2),
        }
    return out


# ═════════════════════════════════════════════════════════════════
# 9) 月度 / 退出原因 / 板块
# ═════════════════════════════════════════════════════════════════
def _compute_monthly_from_trades(trades: list[dict], main_key: str = "S1") -> list[dict]:
    """按 月 group, 算 月度平均 / 月度累计 / 胜率"""
    if not trades:
        return []
    df = pd.DataFrame([{**t, "_y": str(t.get("date_t", ""))[:6]} for t in trades])
    df["_r"] = df[main_key].astype(float)
    g = df.groupby("_y")
    rows = []
    for ym, gg in g:
        rets = gg["_r"]
        rows.append({
            "month": ym,
            "trades": int(len(gg)),
            "wins": int((rets > 0).sum()),
            "losses": int((rets < 0).sum()),
            "win_rate_pct": round(float((rets > 0).mean() * 100), 2),
            "avg_return_pct": round(float(rets.mean()), 3),
            "monthly_return_pct": round(float(rets.mean()), 3),
            "max_return_pct": round(float(rets.max()), 2),
            "min_return_pct": round(float(rets.min()), 2),
        })
    rows.sort(key=lambda r: r["month"])
    return rows


def _exit_breakdown(trades: list[dict]) -> dict:
    if not trades:
        return {}
    from collections import Counter
    c = Counter(t.get("trigger", "unknown") for t in trades)
    total = sum(c.values())
    return {k: {"count": int(v), "pct": round(v / total * 100, 2)} for k, v in c.most_common()}


def _sector_breakdown(trades: list[dict], sector_classify_mod) -> list[dict]:
    if not trades:
        return []
    # 给每笔 trade 打 sector — 限时 5s, 超时降级到 code prefix (主板 4 大类)
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutTimeout
    def _one(code: str) -> tuple[str, str]:
        try:
            sec = sector_classify_mod.get_sector(code, force_refresh=False) or {}
            l1 = (sec.get("taxonomy") or {}).get("level1_cluster", "") or "—"
            if l1 == "—":
                l1 = _board_fallback_sector(code)
            return code, l1
        except Exception:
            return code, _board_fallback_sector(code)
    t0 = _time.time()
    deadline = 5.0  # 板块总耗时 ≤ 5s, 超时不再等
    pool = {}
    with ThreadPoolExecutor(max_workers=20) as ex:
        for t in trades:
            code = t["code"]
            if "sector" in t:
                continue
            fut = ex.submit(_one, code)
            pool[fut] = t
        for fut in as_completed(pool, timeout=deadline):
            try:
                code, sec = fut.result(timeout=0.5)
            except (FutTimeout, Exception):
                continue
            pool[fut]["sector"] = sec
            if _time.time() - t0 > deadline:
                break
    # 兜底: 超时未返回的 trade 标 board prefix
    for t in trades:
        if not t.get("sector"):
            t["sector"] = _board_fallback_sector(t["code"])
    log.info(f"[sector_breakdown] 耗时 {_time.time()-t0:.1f}s, {len(trades)} 笔")
    df = pd.DataFrame(trades)
    if df.empty:
        return []
    rows = []
    for sec, g in df.groupby("sector"):
        rets = g["S1"].astype(float)
        rows.append({
            "sector": str(sec) if sec else "—",
            "trades": int(len(g)),
            "win_rate_pct": round((rets > 0).sum() / len(rets) * 100, 2),
            "avg_return_pct": round(float(rets.mean()), 3),
            "sum_return_pct": round(float(rets.sum()), 2),
            "best_pct": round(float(rets.max()), 2),
            "worst_pct": round(float(rets.min()), 2),
        })
    rows.sort(key=lambda r: -r["sum_return_pct"])
    return rows


def _build_summary(trades: list[dict], equity_curve: list[list]) -> dict:
    """总体 KPI 概览 (前端顶部卡片用)"""
    if not trades:
        return {"trades": 0, "win_rate_pct": 0, "best_strategy": "S1", "best_avg_pct": 0}
    a = np.array([t["S1"] for t in trades], dtype=float)
    n = len(a)
    wins = int((a > 0).sum())
    win_sum = float(a[a > 0].sum())
    loss_sum = abs(float(a[a < 0].sum()))
    eq = (1 + a / 100).cumprod()
    cum = float((eq[-1] - 1) * 100)
    peak = np.maximum.accumulate(eq)
    dd = float(((eq - peak) / peak * 100).min()) if len(eq) else 0
    std = float(a.std(ddof=1)) if n > 1 else 0
    n_days = _period_days(trades)
    ann_pct, mon_pct = _annualized_monthly(cum, n_days)
    return {
        "trades": n,
        "wins": wins,
        "losses": int((a < 0).sum()),
        "win_rate_pct": round(wins / n * 100, 2),
        "avg_return_pct": round(float(a.mean()), 3),
        "median_return_pct": round(float(np.median(a)), 3),
        "cum_return_pct": round(cum, 2),
        "annualized_pct": ann_pct,
        "monthly_pct": mon_pct,
        "period_days": n_days,
        "max_drawdown_pct": round(dd, 2),
        "stddev_pct": round(std, 3),
        "profit_factor": round(win_sum / loss_sum, 2) if loss_sum > 0 else None,
        "best_strategy": "S1",
        "best_avg_pct": round(float(a.mean()), 3),
        "best_trade_pct": round(float(a.max()), 2),
        "worst_trade_pct": round(float(a.min()), 2),
    }


# ═════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════
def _add_days(date_str: str, n: int) -> str:
    return (pd.Timestamp(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])) + pd.Timedelta(days=n)).strftime("%Y%m%d")
