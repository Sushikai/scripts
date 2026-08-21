#!/usr/bin/env python3
"""R106 多周期共振 + 关联网络 ~70 维.

目的: 在 R105 单股 40 维 + 横截面 70 维 + 环境 30 维 的基础上,
引入多周期 K 线对齐 (周/月 = 日线重采样, 5/30/60min 留接口后续接 API)
和同分位/同涨幅段关联网络.

设计:
- week_features(df): 周线动量 (5 日重采样) 10 维
- month_features(df): 月线动量 (20 日重采样) 5 维
- cycle_resonance(daily): 多周期 MACD/RSI/MA 同向 bool 5 维
- relative_features(df, daily): 同 RPS 段 联动 / 关联排名 20 维
- compute_features_v2(daily): 整合 → {code: {dim: value}}
- eval_single_dim_ic(daily, events): rank-IC 评估

注意:
- 多周期 K 线不依赖外部 API, 全用 daily 重采样 (week=5d, month=20d)
- "关联网络" 简化为 "同 RPS 段 / 同涨跌幅段" 近似 (无板块数据时)
- R107 LLM 训练时只取 IC top-50 维
"""
import logging
import statistics
import sys
import time as systime
from collections import defaultdict

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("yaogu_v2")


# ═══════════════════════════════════════════
# A. 多周期动量 (15 维) — 日线重采样周/月
# ═══════════════════════════════════════════

def _resample(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """把日线按 n 日重采样. 每 n 日取一根, 以最后一日收盘/期内最后一日 close 为该周期 close.
    返回 DataFrame 索引 = 周期号 (从 0 开始), 列: 日期/开盘/收盘/最高/最低/成交量/成交额.
    """
    if df is None or len(df) < n:
        return df
    n_rows = len(df)
    # 每个周期的索引 = 周期末尾 i = n-1, 2n-1, ..., n_rows-1 (向下取整)
    cycle_ends = np.arange(n - 1, n_rows, n)
    out = pd.DataFrame()
    out["日期"] = df["日期"].iloc[cycle_ends].values
    out["开盘"] = df["开盘"].iloc[cycle_ends - n + 1].values  # 周期首日开盘
    out["收盘"] = df["收盘"].iloc[cycle_ends].values  # 周期末日收盘
    # 高/低/成交量/成交额: 周期内 max/min/sum
    highs = []
    lows = []
    vols = []
    amts = []
    for i, end in enumerate(cycle_ends):
        start = end - n + 1
        highs.append(df["最高"].iloc[start:end + 1].max())
        lows.append(df["最低"].iloc[start:end + 1].min())
        vols.append(df["成交量"].iloc[start:end + 1].sum())
        amts.append(df["成交额"].iloc[start:end + 1].sum())
    out["最高"] = highs
    out["最低"] = lows
    out["成交量"] = vols
    out["成交额"] = amts
    out.index = list(range(len(out)))
    return out


def week_features(df: pd.DataFrame) -> dict:
    """10 维: 周线动量 (5 日重采样)."""
    f = {}
    if df is None or len(df) < 25:
        return {k: 0 for k in [
            "wk_ret_1w", "wk_ret_2w", "wk_ret_4w", "wk_ma5_dev",
            "wk_above_ma4w_pct", "wk_vol_ratio", "wk_amt_zscore",
            "wk_high_dist", "wk_rsi_4w", "wk_macd_hist"]}
    wk = _resample(df, 5)
    if len(wk) < 5:
        return {k: 0 for k in [
            "wk_ret_1w", "wk_ret_2w", "wk_ret_4w", "wk_ma5_dev",
            "wk_above_ma4w_pct", "wk_vol_ratio", "wk_amt_zscore",
            "wk_high_dist", "wk_rsi_4w", "wk_macd_hist"]}

    c = wk["收盘"]
    o = wk["开盘"]
    h = wk["最高"]
    l = wk["最低"]
    v = wk["成交量"]
    amt = wk["成交额"]

    # 1 周 / 2 周 / 4 周 收益
    if len(c) >= 2 and c.iloc[-2] > 0:
        f["wk_ret_1w"] = float((c.iloc[-1] - c.iloc[-2]) / c.iloc[-2] * 100)
    else:
        f["wk_ret_1w"] = 0
    if len(c) >= 3 and c.iloc[-3] > 0:
        f["wk_ret_2w"] = float((c.iloc[-1] - c.iloc[-3]) / c.iloc[-3] * 100)
    else:
        f["wk_ret_2w"] = 0
    if len(c) >= 5 and c.iloc[-5] > 0:
        f["wk_ret_4w"] = float((c.iloc[-1] - c.iloc[-5]) / c.iloc[-5] * 100)
    else:
        f["wk_ret_4w"] = 0

    # 周 MA5 偏离
    ma5 = c.rolling(5, min_periods=1).mean().iloc[-1]
    f["wk_ma5_dev"] = float((c.iloc[-1] - ma5) / max(ma5, 1e-6) * 100)

    # 4 周中收盘 > 周 MA 的比例
    ma4w = c.rolling(4, min_periods=1).mean()
    above_pct = (c > ma4w).tail(4).mean() * 100
    f["wk_above_ma4w_pct"] = float(above_pct) if pd.notna(above_pct) else 50.0

    # 量比
    vol_ma = v.tail(8).mean()
    f["wk_vol_ratio"] = float(v.iloc[-1] / max(vol_ma, 1e-6))

    # 成交额 zscore
    amt_mean = amt.tail(8).mean()
    amt_std = amt.tail(8).std()
    f["wk_amt_zscore"] = float((amt.iloc[-1] - amt_mean) / max(amt_std, 1e-6))

    # 距离周线高点
    high_4w = h.tail(4).max()
    f["wk_high_dist"] = float((high_4w - c.iloc[-1]) / max(c.iloc[-1], 1e-6) * 100)

    # 周线 RSI(4 周 = 20 日)
    if len(c) >= 5:
        delta = c.diff()
        gain = delta.where(delta > 0, 0).tail(4).mean()
        loss = (-delta.where(delta < 0, 0)).tail(4).mean()
        if loss > 0:
            rs = gain / loss
            f["wk_rsi_4w"] = float(100 - 100 / (1 + rs))
        else:
            f["wk_rsi_4w"] = 100.0
    else:
        f["wk_rsi_4w"] = 50.0

    # 周线 MACD hist
    if len(c) >= 5:
        ema3 = c.ewm(span=3, adjust=False).mean()
        ema9 = c.ewm(span=9, adjust=False).mean()
        dif = ema3 - ema9
        dea = dif.ewm(span=3, adjust=False).mean()
        f["wk_macd_hist"] = float((dif.iloc[-1] - dea.iloc[-1]) * 2)
    else:
        f["wk_macd_hist"] = 0

    return f


def month_features(df: pd.DataFrame) -> dict:
    """5 维: 月线动量 (20 日重采样)."""
    f = {}
    if df is None or len(df) < 60:
        return {k: 0 for k in [
            "mo_ret_1m", "mo_ret_3m", "mo_ma3_dev",
            "mo_high_dist", "mo_macd_hist"]}
    mo = _resample(df, 20)
    if len(mo) < 4:
        return {k: 0 for k in [
            "mo_ret_1m", "mo_ret_3m", "mo_ma3_dev",
            "mo_high_dist", "mo_macd_hist"]}

    c = mo["收盘"]
    h = mo["最高"]

    if len(c) >= 2 and c.iloc[-2] > 0:
        f["mo_ret_1m"] = float((c.iloc[-1] - c.iloc[-2]) / c.iloc[-2] * 100)
    else:
        f["mo_ret_1m"] = 0
    if len(c) >= 4 and c.iloc[-4] > 0:
        f["mo_ret_3m"] = float((c.iloc[-1] - c.iloc[-4]) / c.iloc[-4] * 100)
    else:
        f["mo_ret_3m"] = 0

    ma3 = c.rolling(3, min_periods=1).mean().iloc[-1]
    f["mo_ma3_dev"] = float((c.iloc[-1] - ma3) / max(ma3, 1e-6) * 100)

    high_3m = h.tail(3).max()
    f["mo_high_dist"] = float((high_3m - c.iloc[-1]) / max(c.iloc[-1], 1e-6) * 100)

    if len(c) >= 4:
        ema3 = c.ewm(span=3, adjust=False).mean()
        ema9 = c.ewm(span=9, adjust=False).mean()
        dif = ema3 - ema9
        dea = dif.ewm(span=3, adjust=False).mean()
        f["mo_macd_hist"] = float((dif.iloc[-1] - dea.iloc[-1]) * 2)
    else:
        f["mo_macd_hist"] = 0

    return f


# ═══════════════════════════════════════════
# B. 多周期共振 (5 维) — 跨周期 bool 对齐
# ═══════════════════════════════════════════

def cycle_resonance_features(df: pd.DataFrame) -> dict:
    """5 维: 日/周/月 三周期 MACD/RSI/MA 同向 bool."""
    f = {}
    if df is None or len(df) < 60:
        return {k: 0 for k in [
            "resonance_macd", "resonance_ma", "resonance_mom",
            "divergence_wkd", "resonance_above_ma"]}

    c = df["收盘"]
    # 日线 MACD
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    dif_d = ema12.iloc[-1] - ema26.iloc[-1]
    dea_d = (ema12 - ema26).ewm(span=9, adjust=False).mean().iloc[-1]
    macd_d = (dif_d - dea_d) * 2

    # 周线 MACD
    wk = _resample(df, 5)
    cw = wk["收盘"]
    if len(cw) >= 5:
        ew3 = cw.ewm(span=3, adjust=False).mean()
        ew9 = cw.ewm(span=9, adjust=False).mean()
        dif_w = ew3.iloc[-1] - ew9.iloc[-1]
        dea_w = (ew3 - ew9).ewm(span=3, adjust=False).mean().iloc[-1]
        macd_w = (dif_w - dea_w) * 2
    else:
        macd_w = 0

    # 月线 MACD
    mo = _resample(df, 20)
    cm = mo["收盘"]
    if len(cm) >= 4:
        em3 = cm.ewm(span=3, adjust=False).mean()
        em9 = cm.ewm(span=9, adjust=False).mean()
        dif_m = em3.iloc[-1] - em9.iloc[-1]
        dea_m = (em3 - em9).ewm(span=3, adjust=False).mean().iloc[-1]
        macd_m = (dif_m - dea_m) * 2
    else:
        macd_m = 0

    # 共振: 三周期 MACD 同号
    f["resonance_macd"] = int((macd_d > 0) == (macd_w > 0) == (macd_m > 0))

    # 共振: 三周期 收盘 > MA
    ma_d = c.rolling(20, min_periods=1).mean().iloc[-1]
    ma_w = cw.rolling(4, min_periods=1).mean().iloc[-1] if len(cw) >= 4 else 0
    ma_m = cm.rolling(3, min_periods=1).mean().iloc[-1] if len(cm) >= 3 else 0
    f["resonance_ma"] = int((c.iloc[-1] > ma_d) == (cw.iloc[-1] > ma_w) == (cm.iloc[-1] > ma_m))

    # 共振: 三周期动量同向 (1w ret + 1m ret 同号)
    mom_d = c.iloc[-1] - c.iloc[-5] if len(c) >= 5 else 0
    mom_w = cw.iloc[-1] - cw.iloc[-2] if len(cw) >= 2 else 0
    mom_m = cm.iloc[-1] - cm.iloc[-2] if len(cm) >= 2 else 0
    f["resonance_mom"] = int((mom_d > 0) == (mom_w > 0) == (mom_m > 0))

    # 背离: 周线↑日线↓
    f["divergence_wkd"] = int(macd_w > 0 and macd_d < 0)

    # 多周期共振 (均线上方)
    f["resonance_above_ma"] = int((c.iloc[-1] > ma_d) and (cw.iloc[-1] > ma_w) and (cm.iloc[-1] > ma_m))

    return f


# ═══════════════════════════════════════════
# C. 关联网络 (20 维) — 横截面聚合一次, 每只股复用
# ═══════════════════════════════════════════

def _build_peer_snapshot(daily: dict[str, pd.DataFrame]) -> dict:
    """对所有股票一次性算横截面快照, 返回 {code: peer_metrics}."""
    t0 = systime.time()
    snapshot = {}
    for code, df in daily.items():
        if df is None or len(df) < 25:
            snapshot[code] = None
            continue
        c = df["收盘"]
        ret_1d = 0.0
        ret_5d = 0.0
        if len(c) >= 2 and c.iloc[-2] > 0:
            ret_1d = (c.iloc[-1] - c.iloc[-2]) / c.iloc[-2] * 100
        if len(c) >= 6 and c.iloc[-6] > 0:
            ret_5d = (c.iloc[-1] - c.iloc[-6]) / c.iloc[-6] * 100
        v = df["成交量"]
        vol_ma = v.tail(20).mean()
        vol_ratio = v.iloc[-1] / max(vol_ma, 1e-6) if vol_ma > 0 else 1.0
        # 连板
        last_n = 0
        for x in df["涨停"].iloc[::-1].values:
            if x:
                last_n += 1
            else:
                break
        # 站上 MA20
        ma20 = c.rolling(20, min_periods=1).mean().iloc[-1]
        above_ma20 = 1 if c.iloc[-1] > ma20 else 0
        # 20 日新高
        high_20 = df["最高"].tail(20).max()
        near_high = 1 if c.iloc[-1] >= high_20 * 0.99 else 0
        # 涨停
        zt = 1 if df["涨停"].iloc[-1] else 0
        snapshot[code] = {
            "ret_1d": ret_1d,
            "ret_5d": ret_5d,
            "vol_ratio": vol_ratio,
            "streak": last_n,
            "above_ma20": above_ma20,
            "near_high_20": near_high,
            "zt": zt,
        }
    log.info("peer_snapshot: %d stocks (%.1fs)", len(snapshot), systime.time() - t0)
    return snapshot


def correlation_features(code: str, df: pd.DataFrame, peer_snapshot: dict) -> dict:
    """20 维: 简化版关联网络 (peer_snapshot 预先算好)."""
    f = {}
    if df is None or len(df) < 5 or not peer_snapshot:
        return {k: 0 for k in [
            "corr_peer_ret_1d_avg", "corr_peer_ret_5d_avg",
            "corr_peer_zt_rate", "corr_peer_zt_count",
            "corr_peer_vol_ratio_avg",
            "self_in_peer_pct_1d", "self_in_peer_pct_5d",
            "peer_streak_avg", "peer_streak_max",
            "peer_alpha_avg", "peer_alpha_vs_self",
            "peer_ret_median_5d", "peer_above_ma20_pct",
            "peer_top5_avg", "peer_top5_dev",
            "corr_self_vs_peer_avg", "corr_self_vs_peer_5d",
            "peer_skew_1d", "peer_kurt_1d", "peer_high_20d_pct"]}

    self_snap = peer_snapshot.get(code) or {}
    ret_1d_self = self_snap.get("ret_1d", 0)
    ret_5d_self = self_snap.get("ret_5d", 0)

    peer_ret_1d = []
    peer_ret_5d = []
    peer_zt = 0
    peer_streak = []
    peer_above = 0
    peer_vol_ratio_list = []
    peer_n = 0
    peer_high = 0

    for other_code, snap in peer_snapshot.items():
        if other_code == code or snap is None:
            continue
        peer_n += 1
        peer_ret_1d.append(snap["ret_1d"])
        peer_ret_5d.append(snap["ret_5d"])
        peer_zt += snap["zt"]
        peer_streak.append(snap["streak"])
        peer_above += snap["above_ma20"]
        peer_vol_ratio_list.append(snap["vol_ratio"])
        peer_high += snap["near_high_20"]

    if peer_ret_1d:
        arr_1d = np.array(peer_ret_1d)
        f["corr_peer_ret_1d_avg"] = float(arr_1d.mean())
        f["peer_skew_1d"] = float(pd.Series(arr_1d).skew())
        f["peer_kurt_1d"] = float(pd.Series(arr_1d).kurt())
        top5 = np.sort(arr_1d)[-5:]
        f["peer_top5_avg"] = float(top5.mean())
        f["peer_top5_dev"] = float(top5[-1] - ret_1d_self)
    else:
        f["corr_peer_ret_1d_avg"] = 0
        f["peer_skew_1d"] = 0
        f["peer_kurt_1d"] = 0
        f["peer_top5_avg"] = 0
        f["peer_top5_dev"] = 0

    if peer_ret_5d:
        arr_5d = np.array(peer_ret_5d)
        f["corr_peer_ret_5d_avg"] = float(arr_5d.mean())
        f["peer_ret_median_5d"] = float(np.median(arr_5d))
    else:
        f["corr_peer_ret_5d_avg"] = 0
        f["peer_ret_median_5d"] = 0

    f["corr_peer_zt_count"] = peer_zt
    f["corr_peer_zt_rate"] = peer_zt / max(peer_n, 1)
    f["peer_above_ma20_pct"] = peer_above / max(peer_n, 1) * 100
    f["peer_high_20d_pct"] = peer_high / max(peer_n, 1) * 100

    if peer_streak:
        f["peer_streak_avg"] = float(np.mean(peer_streak))
        f["peer_streak_max"] = float(np.max(peer_streak))
    else:
        f["peer_streak_avg"] = 0
        f["peer_streak_max"] = 0

    if peer_vol_ratio_list:
        f["corr_peer_vol_ratio_avg"] = float(np.mean(peer_vol_ratio_list))
    else:
        f["corr_peer_vol_ratio_avg"] = 0

    # 自排名 (1d / 5d)
    if peer_ret_1d:
        all_r = peer_ret_1d + [ret_1d_self]
        f["self_in_peer_pct_1d"] = float(sum(1 for r in all_r if r <= ret_1d_self) / len(all_r) * 100)
    else:
        f["self_in_peer_pct_1d"] = 50

    if peer_ret_5d:
        all_r = peer_ret_5d + [ret_5d_self]
        f["self_in_peer_pct_5d"] = float(sum(1 for r in all_r if r <= ret_5d_self) / len(all_r) * 100)
    else:
        f["self_in_peer_pct_5d"] = 50

    f["peer_alpha_avg"] = float(ret_5d_self - np.mean(peer_ret_5d)) if peer_ret_5d else 0
    f["peer_alpha_vs_self"] = float(np.mean(peer_ret_1d) - ret_1d_self) if peer_ret_1d else 0
    f["corr_self_vs_peer_avg"] = float(ret_1d_self - np.mean(peer_ret_1d)) if peer_ret_1d else 0
    f["corr_self_vs_peer_5d"] = float(ret_5d_self - np.mean(peer_ret_5d)) if peer_ret_5d else 0

    return f


# ═══════════════════════════════════════════
# 整合入口
# ═══════════════════════════════════════════

def compute_features_v2(daily: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """R106: 多周期共振 15 维 + 关联网络 20 维 = 35 维 per stock.
    返回 {code: {dim: value}}."""
    t0 = systime.time()
    peer = _build_peer_snapshot(daily)
    out = {}
    for code, df in daily.items():
        f = {}
        f.update(week_features(df))
        f.update(month_features(df))
        f.update(cycle_resonance_features(df))
        f.update(correlation_features(code, df, peer))
        out[code] = f
    n_dim = len(out[list(out.keys())[0]]) if out else 0
    log.info("compute_features_v2: %d stocks × %d dims (%.1fs)", len(out), n_dim, systime.time() - t0)
    return out


# ═══════════════════════════════════════════
# 单维 rank-IC 评估 (R106 验证)
# ═══════════════════════════════════════════

def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return 0.0
    rx = pd.Series(x).rank()
    ry = pd.Series(y).rank()
    return float(rx.corr(ry))


def eval_single_dim_ic(daily: dict[str, pd.DataFrame],
                        events_full: list[dict],
                        forward_n: int = 10) -> list[tuple[str, float, int]]:
    """对 R106 每个维度算 rank-IC."""
    fwd_map = {}
    for ev in events_full:
        fr = ev.get("fwd_10d") if forward_n == 10 else ev.get(f"fwd_{forward_n}d")
        if fr is not None:
            fwd_map[(ev["code"], ev["date"])] = fr

    feats = compute_features_v2(daily)

    pairs_per_dim: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for ev in events_full:
        code = ev["code"]
        date = ev["date"]
        if (code, date) not in fwd_map:
            continue
        f = feats.get(code, {})
        if not f:
            continue
        fwd = fwd_map[(code, date)]
        for dim, val in f.items():
            if val is None:
                continue
            try:
                v = float(val)
                if np.isnan(v) or np.isinf(v):
                    continue
                pairs_per_dim[dim].append((v, fwd))
            except (TypeError, ValueError):
                continue

    results = []
    for dim, pairs in pairs_per_dim.items():
        if len(pairs) < 30:
            continue
        xs = np.array([p[0] for p in pairs])
        ys = np.array([p[1] for p in pairs])
        ic = _spearman(xs, ys)
        results.append((dim, ic, len(pairs)))
    results.sort(key=lambda x: -abs(x[1]))

    log.info("=== R106 Top 30 单维 rank-IC (forward 10d) ===")
    for dim, ic, n in results[:30]:
        marker = "★" if ic > 0.05 else ("✗" if ic < -0.05 else " ")
        log.info(f"  {marker} {dim:32s} IC={ic:+.4f}  n={n}")
    log.info("=== Bottom 20 ===")
    for dim, ic, n in results[-20:]:
        marker = "★" if ic > 0.05 else ("✗" if ic < -0.05 else " ")
        log.info(f"  {marker} {dim:32s} IC={ic:+.4f}  n={n}")
    return results


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

def _cli():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--eval", action="store_true")
    p.add_argument("--forward", type=int, default=10)
    args = p.parse_args()

    from yaogu_survey import load_daily
    from yaogu_optimizer import build_prebuilt
    log.info("load daily...")
    daily = load_daily()
    log.info(f"daily: {len(daily)} stocks")
    log.info("build prebuilt (events + fwd_ret)...")
    pb = build_prebuilt(force=False)
    events = pb["events_full"]
    log.info(f"events_full: {len(events)} 条")
    log.info(f"eval R106 35 dims × {len(events)} events")
    eval_single_dim_ic(daily, events, forward_n=args.forward)


if __name__ == "__main__":
    _cli()