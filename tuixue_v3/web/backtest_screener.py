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
WINDOWS = [("1周", 5), ("2周", 10), ("1月", 21), ("2月", 42), ("半年", 120), ("1年", 250), ("3年", 750)]
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


def _validate_data_quality(daily_cache: dict[str, pd.DataFrame],
                           universe_codes: list[str],
                           name: str = "daily") -> dict:
    """REG 工程化 — 数据质量验证

    检查: 覆盖率, 日期范围, 缺失值, 异常值
    Returns stats dict, 日志记录 + progress_cb 友好
    """
    total = len(universe_codes)
    hit = len(daily_cache)
    cov = hit / max(1, total) * 100
    stats = {"total": total, "hit": hit, "coverage_pct": round(cov, 1)}

    # 日期完整性: 所有有数据的股票的最早/最晚日期
    all_dates: set[str] = set()
    null_count = 0
    for code in universe_codes:
        df = daily_cache.get(code)
        if df is None or df.empty:
            null_count += 1
            continue
        if "日期" in df.columns:
            all_dates.update(str(d) for d in df["日期"] if d is not None)

    stats["null_codes"] = null_count
    stats["null_pct"] = round(null_count / max(1, total) * 100, 1)
    stats["date_span"] = f"{min(all_dates) if all_dates else '?'} ~ {max(all_dates) if all_dates else '?'}"
    stats["unique_dates"] = len(all_dates)

    log.info(f"[REG] {name} 数据质量: 覆盖率 {cov:.0f}% ({hit}/{total}), "
             f"空值 {null_count} ({stats['null_pct']:.0f}%), "
             f"日期跨度 {stats['date_span']} ({stats['unique_dates']} 天)")

    if cov < 50:
        log.warning(f"[REG] {name} 覆盖率仅 {cov:.0f}% — 回测结果可能不完整")
    if null_count > total * 0.3:
        log.warning(f"[REG] {name} 空值 {null_count}/{total} >30% — 数据源可能异常")

    return stats


def _build_data_index(panel: pd.DataFrame,
                      daily_cache: dict[str, pd.DataFrame],
                      codes: list[str],
                      names: dict[str, str],
                      sec_avg_by_date: dict | None = None,
                      progress_cb=None) -> tuple[dict, dict, dict]:
    """Phase 3: 构建数据索引 — panel_idx + cache_by_code_date + 板块均值

    Returns:
      panel_idx:          {date_str: {code: row_dict}}
      cache_by_code_date: {code: {date_str: {open,high,low,close}}}
      sec_avg_by_date:    {date_str: {sector: avg_change_pct}}
    """
    if progress_cb:
        progress_cb("[3/3 索引] 构建数据索引…")

    # cache_by_code_date: 日线快速 O(1) 查 T+1 OHLC
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

    # panel_idx: 按日期索引 {date: {code: row_dict}}
    panel_idx: dict[str, dict[str, dict]] = {}
    for _, row in panel.iterrows():
        d = str(row["日期"])
        panel_idx.setdefault(d, {})[row["code"]] = row.to_dict()

    # 板块均值映射
    if sec_avg_by_date is None:
        sec_avg_by_date = {}
        for d_str, stocks in panel_idx.items():
            sec_chgs: dict[str, list[float]] = {}
            for code, row in stocks.items():
                sec = str(row.get("sector", ""))
                chg = float(row.get("change_pct", 0) or 0)
                if sec and sec not in ("其他", "", "nan"):
                    sec_chgs.setdefault(sec, []).append(chg)
            sec_avg_by_date[d_str] = {s: sum(v) / len(v) for s, v in sec_chgs.items()}

    if progress_cb:
        progress_cb(f"索引完成: {len(cache_by_code_date)} codes × {len(panel_idx)} dates, "
                    f"{sum(len(v) for v in sec_avg_by_date.values())} 板块条目")
    log.info(f"索引完成: {len(cache_by_code_date)} codes × {len(panel_idx)} dates, "
             f"板块均值 {sum(len(v) for v in sec_avg_by_date.values())} 条目")

    return panel_idx, cache_by_code_date, sec_avg_by_date


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
def _compute_vwap_from_5min(bars: list[dict]) -> float | None:
    """从 5min K 算 VWAP = sum(typical_price × volume) / sum(volume)

    typical_price = (high + low + close) / 3
    Returns None if 数据不全 (空 bars 或 volume 全 0)。
    """
    if not bars:
        return None
    total_pv = 0.0
    total_v = 0.0
    for b in bars:
        try:
            h = float(b.get("high", 0) or 0)
            l = float(b.get("low", 0) or 0)
            c = float(b.get("close", 0) or 0)
            v = float(b.get("volume", 0) or 0)
        except (ValueError, TypeError):
            continue
        if v <= 0 or h <= 0 or l <= 0 or c <= 0:
            continue
        typical = (h + l + c) / 3.0
        total_pv += typical * v
        total_v += v
    if total_v <= 0:
        return None
    return total_pv / total_v


def _above_vwap_check(code: str, date_str: str, day_close: float,
                      vwap_cache: dict[tuple[str, str], float | None]) -> bool | None:
    """T 日 close > VWAP 验证 (strict 模式用)

    Returns:
      True  — close > VWAP (水上, 强)
      False — close ≤ VWAP (水下, 弱)
      None  — VWAP 数据缺失 (软通, 不计入 fail)

    vwap_cache: 缓存 [(code, date), vwap|None], 避免重复 fetch
    """
    key = (code, date_str)
    if key not in vwap_cache:
        try:
            sd_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            bars = _fetch_5min_for_code(code, sd_fmt, sd_fmt)
            vwap_cache[key] = _compute_vwap_from_5min(bars)
        except Exception:
            vwap_cache[key] = None
    vwap = vwap_cache[key]
    if vwap is None or vwap <= 0:
        return None
    return day_close > vwap


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
# R56 用户指示: 退场模型彻底重写
#   开盘价卖 (S1) 不切实际 → 删
#   其他 8 套零碎逻辑 (avg_up/twap/tp2/half/gap_* 等) → 删
#   只留 6 套符合现实的退场:
#     trail_80:    翻红 → high × 0.8;        没翻红 → 水下均价 (open+low)/2
#     trail_50:    翻红 → high × 0.5;        没翻红 → 水下均价
#     trail_20:    翻红 → high × 0.2;        没翻红 → 水下均价
#     water_avg:   统一水下均价 (open+low)/2 (不管翻不翻红)
#     force_10:    强制 10:00 前卖出 (≈ T+1 open, 日线代理; 有 actual_10 时用真实 10:00 价)
#     force_close: 强制尾盘卖出 (T+1 close)
SIX_KEYS = ("trail_80", "trail_50", "trail_20", "water_avg", "force_10", "force_close")


def _simulate_from_cache_row(row_t1: dict, buy_price: float, actual_10_close: float | None = None,
                              late_high: float | None = None,
                              late_high_discount: float = 1.0) -> dict | None:
    """从 cache 单行 dict 模拟 6 套退场 — O(1) 时间

    row_t1: {open, high, low, close, date_str}
    actual_10_close: 真实 10:00 K 线 close (若有), 用于替换 force_10 的 open 代理
                     若不传/取不到, force_10 回退到 T+1 open
    late_high: R57 — 9:30-10:00 期间最高价 (若有), 用于 late_recover 档判定
               若不传/取不到, fallback 到 R56 2 档 (只看 open)
    late_high_discount: R57+ — late_recover 收益率折算系数 (默认 1.0 满格)
                        1.0 = 用户原意"按水上价格算收益率" 满格
                        0.7/0.5 = 实际不能在 9:30-10:00 高点全部卖出, 折算更现实

    退场铁律 (R56 + R57 3 档):
      9:30 翻红   (open > buy)        → 拿 high 的 80% / 50% / 20%  (trail_recover, 走 trail_80)
      9:30 不翻红 但 10:00 前 high≥buy → trail_50 中等 (late_recover)
      全天没救                         → 统一水下均价 (water_avg)
      强制基准:   force_10 ≈ open,  force_close = close

    续涨停延后: T+1 开盘涨停 (open ≥ buy×1.095) → return None (交给 T+2)
    """
    open_p = row_t1["open"]; high_p = row_t1["high"]
    low_p = row_t1["low"]; close_p = row_t1["close"]
    if buy_price <= 0 or open_p <= 0:
        return None
    _p = lambda p: (p / buy_price - 1.0) * 100.0
    ret_open = _p(open_p); ret_close = _p(close_p)
    ret_high = _p(high_p); ret_low = _p(low_p)

    # 续涨停延后: T+1 开盘涨停→交给 T+2
    if open_p >= buy_price * 1.095:
        return None

    # ═══════════════════════════════════════════════════════════
    # 统一不翻红退出: 水下均价 (open+low)/2 止损 (用户铁律)
    # ═══════════════════════════════════════════════════════════
    ret_water_avg = _p((open_p + low_p) / 2.0)

    # R58v2 3 档 trigger: 全部用日线 OHLC 判定 (不依赖 5min K, 避免口径问题)
    green = open_p > buy_price           # trail_recover: 开盘翻红
    late = (not green) and (high_p >= buy_price)  # late_recover: 盘中翻过水

    if green:
        ret_trail_80 = round(ret_high * 0.8, 3)
        ret_trail_50 = round(ret_high * 0.5, 3)
        ret_trail_20 = round(ret_high * 0.2, 3)
    elif late:
        # late_recover: 盘中翻水, 可靠性不如开盘翻红, 打折 + discount
        ret_trail_80 = round(ret_high * 0.8 * late_high_discount, 3)
        ret_trail_50 = round(ret_high * 0.5 * late_high_discount, 3)
        ret_trail_20 = round(ret_high * 0.2 * late_high_discount, 3)
    else:
        # ── 全天没救 → 全部走水下均价 ──
        ret_trail_80 = ret_water_avg
        ret_trail_50 = ret_water_avg
        ret_trail_20 = ret_water_avg

    # 强制基准: force_10 ≈ T+1 open (10:00 前卖出, 日线无 10:00 粒度, 用 open 代理)
    #           force_close = T+1 close (尾盘强制)
    ret_force_10 = _p(actual_10_close) if (actual_10_close and actual_10_close > 0) else ret_open
    ret_force_close = ret_close

    return {
        "open":   round(ret_open, 3),
        "high":   round(ret_high, 3),
        "low":    round(ret_low, 3),
        "close":  round(ret_close, 3),
        # ── 6 套退场主键 (用户 R56 重构 + R57 3 档 trigger) ──
        "trail_80":    ret_trail_80,
        "trail_50":    ret_trail_50,
        "trail_20":    ret_trail_20,
        "water_avg":   ret_water_avg,
        "force_10":    round(ret_force_10, 3),
        "force_close": round(ret_force_close, 3),
        # ── 退出原因 (R57 3 档: 开盘翻→trail / 10:00前翻→late / 全天没救→water) ──
        "recovered": high_p > buy_price,
        "trigger": "trail_recover" if green else ("late_recover" if late else "water_avg"),
    }


def _simulate_batch(rows: list[dict], buy_prices: list[float],
                    actual_10_closes: list[float | None] | None = None,
                    late_highs: list[float | None] | None = None,
                    late_high_discount: float = 1.0) -> list[dict | None]:
    """向量化: 一次算 N 笔 trade 的 6 套退场 (~500x 快于 for-loop)

    rows: [{open, high, low, close, date_str}, ...]
    buy_prices: [float, ...]
    actual_10_closes: [float|None, ...] (并行 list, None 表示用 open 代理)
    late_highs: R57 3 档 trigger — 9:30-10:00 期间最高价 [float|None, ...]
                None → fallback 到 R56 2 档 (只用 open 判定)
                有值且 >= buy → late_recover
    late_high_discount: late_recover 收益率折算系数 (默认 1.0 满格, 用户可改 0.5 / 0.7)
                        1.0 = 用户原意"按水上价格算收益率" 满格
                        0.5/0.7 = 实际不能在 9:30-10:00 高点卖出, 折算更现实

    Returns: 与 _simulate_from_cache_row 同 schema 的 list[dict], 失败位 None
    """
    if not rows:
        return []
    n = len(rows)
    actual_10_closes = actual_10_closes or [None] * n
    late_highs = late_highs or [None] * n
    op = np.array([r["open"]  for r in rows], dtype=np.float64)
    hp = np.array([r["high"]  for r in rows], dtype=np.float64)
    lp = np.array([r["low"]   for r in rows], dtype=np.float64)
    cp = np.array([r["close"] for r in rows], dtype=np.float64)
    bp = np.array(buy_prices, dtype=np.float64)
    a10 = np.array([x if (x and x > 0) else 0.0 for x in actual_10_closes], dtype=np.float64)
    has_a10 = a10 > 0
    # R58v2: trigger 分类用日线 OHLC high, 不再依赖 5min K late_highs (口径问题)
    # R57+ 折算: discount 在 ret_trail_80/50/20 分支里乘 late_high_discount
    # R58v2: 不再用 5min K 算 late_high 收益, 全部用日线 OHLC ret_high 同口径
    # 基础 % 收益
    ret_open = (op / bp - 1.0) * 100.0
    ret_close = (cp / bp - 1.0) * 100.0
    ret_high = (hp / bp - 1.0) * 100.0
    ret_low = (lp / bp - 1.0) * 100.0
    # R56 水下均价 = (open+low)/2 (用户铁律: 全天没救→水下均价止损)
    ret_water = ((op + lp) / 2.0 / bp - 1.0) * 100.0
    ret_actual_10 = np.where(has_a10, (a10 / bp - 1.0) * 100.0, ret_open)
    # R57: late_high 翻红收益率 (10:00 前 high 触及 buy) — 已在函数顶部带 discount 计算, 不再重复

    # 续涨停 mask: open >= buy*1.095 → 该笔返回 None
    up_limit_mask = op >= bp * 1.095
    invalid_mask = (bp <= 0) | (op <= 0) | up_limit_mask

    # 翻红 mask: open > buy
    green_mask = op > bp
    # R57 late_recover mask: open ≤ buy 但 T+1 日 high ≥ buy (日线数据判断)
    late_mask = (~green_mask) & (hp >= bp)
    # underwater_mask = ~green_mask & ~late_mask & ~invalid_mask  (全天没救)

    # ── 6 套退场 (R56 重构 + R58v2 修复: 全部用日线 OHLC ret_high, 同一口径) ──
    #   打赏率: trail_80 = high×0.8, trail_50 = high×0.5, trail_20 = high×0.2
    #   late_recover (10:00前拉过) 额外乘 late_high_discount (0.7/0.5 反映可执行性)
    #   water_avg: 统一水下均价, 不管翻红
    ret_trail_80 = np.where(
        green_mask, np.round(ret_high * 0.8, 3),
        np.where(late_mask, np.round(ret_high * 0.8 * late_high_discount, 3), ret_water)
    )
    ret_trail_50 = np.where(
        green_mask, np.round(ret_high * 0.5, 3),
        np.where(late_mask, np.round(ret_high * 0.5 * late_high_discount, 3), ret_water)
    )
    ret_trail_20 = np.where(
        green_mask, np.round(ret_high * 0.2, 3),
        np.where(late_mask, np.round(ret_high * 0.2 * late_high_discount, 3), ret_water)
    )
    # water_avg: 统一水下均价 (不管翻红没翻红, 不管 R57)
    ret_water_avg = ret_water
    # force_10: 强制 10:00 前卖出 (≈ open, 有 actual_10 时用真实 10:00 价)
    ret_force_10 = np.where(has_a10, (a10 / bp - 1.0) * 100.0, ret_open)
    # force_close: 强制尾盘卖出 (T+1 close)
    ret_force_close = ret_close

    out: list[dict | None] = []
    for i in range(n):
        if invalid_mask[i]:
            out.append(None); continue
        recovered = bool(hp[i] > bp[i])
        # R57 3 档 trigger: trail_recover (开盘翻) / late_recover (10:00 前拉过) / water_avg (全天没救)
        if green_mask[i]:
            trig = "trail_recover"
        elif late_mask[i]:
            trig = "late_recover"
        else:
            trig = "water_avg"
        row_out = {
            "open":   round(float(ret_open[i]), 3),
            "high":   round(float(ret_high[i]), 3),
            "low":    round(float(ret_low[i]), 3),
            "close":  round(float(ret_close[i]), 3),
            # ── 6 套退场主键 ──
            "trail_80":    round(float(ret_trail_80[i]), 3),
            "trail_50":    round(float(ret_trail_50[i]), 3),
            "trail_20":    round(float(ret_trail_20[i]), 3),
            "water_avg":   round(float(ret_water_avg[i]), 3),
            "force_10":    round(float(ret_force_10[i]), 3),
            "force_close": round(float(ret_force_close[i]), 3),
            # ── 退出原因 (R57 3 档) ──
            "recovered": recovered,
            "trigger": trig,
        }
        # Round 3: 止损保护 — 任何退场 <= -5% 则截断 (模拟实盘止损单)
        STOP_LOSS = -5.0
        for _sl_key in ("trail_80","trail_50","trail_20","water_avg","force_10","force_close"):
            if row_out.get(_sl_key, 0) < STOP_LOSS:
                row_out[_sl_key] = STOP_LOSS
        out.append(row_out)
    return out


def _simulate_exits(panel_t1: pd.DataFrame, buy_price: float) -> dict | None:
    """panel_t1: T+1 一天 OHLC (DataFrame, 单行)

    R56 重构: 返回 6 套退场 (trail_80/50/20 + water_avg + force_10 + force_close)
      翻红 (open > buy) → trail_80/50/20 = high × 80%/50%/20%
      没翻红             → 全部走 water_avg = (open+low)/2
      force_10  ≈ T+1 open (10:00 前强制)
      force_close = T+1 close (尾盘强制)
    """
    if panel_t1 is None or panel_t1.empty:
        return None
    row = panel_t1.iloc[0]
    open_p = float(row["开盘"]); high_p = float(row["最高"])
    low_p  = float(row["最低"]); close_p = float(row["收盘"])
    if buy_price <= 0:
        return None

    is_limit_up_open = (open_p / buy_price - 1.0) * 100.0 >= 9.5 if buy_price > 0 else False
    if is_limit_up_open:
        return None  # caller 改成传 T+2 再调一次

    def _p(p):
        return (p / buy_price - 1.0) * 100.0
    ret_open = _p(open_p); ret_high = _p(high_p); ret_low = _p(low_p); ret_close = _p(close_p)
    ret_water = _p((open_p + low_p) / 2.0)

    if open_p > buy_price:
        ret_trail_80 = round(ret_high * 0.8, 3)
        ret_trail_50 = round(ret_high * 0.5, 3)
        ret_trail_20 = round(ret_high * 0.2, 3)
    else:
        # R57: 此函数无 late_high 数据, fallback 到 R56 2 档 (water_avg)
        ret_trail_80 = ret_trail_50 = ret_trail_20 = ret_water

    return {
        "open":   round(ret_open, 3),
        "high":   round(ret_high, 3),
        "low":    round(ret_low, 3),
        "close":  round(ret_close, 3),
        "trail_80":    ret_trail_80,
        "trail_50":    ret_trail_50,
        "trail_20":    ret_trail_20,
        "water_avg":   round(ret_water, 3),
        "force_10":    round(ret_open, 3),    # 10:00 强制 ≈ T+1 open
        "force_close": round(ret_close, 3),  # 尾盘强制
        "recovered":   high_p > buy_price,
        "trigger":     "trail_recover" if open_p > buy_price else "water_avg",
    }


# ═════════════════════════════════════════════════════════════════
# 6) 多日持仓模拟 (hold_days 日, 命中即平, 退场策略沿用 exits_pct 7 套)
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
                     strategy_id: str = "baseline",
                     late_high_discount: float = 1.0,
                     require_vwap_strict: bool = False,
                     regime_adaptive: bool = False,
                     progress_cb=None,
                     _daily_cache: dict[str, pd.DataFrame] | None = None,
                     _skip_recovery: bool = False) -> dict:
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

    2026-07-18 R54 策略模板:
    strategy_id: "baseline" (默认) 或 "optimized" (优化策略 = baseline + OPTIMAL_PARAMS)
      退场逻辑完全不变 (9 套), 只换 candidate 入选规则

    判定逻辑:
      if breadth < breadth_min: skip (硬底, 大熊市空仓)
      elif breadth >= breadth_min_soft: trade (普涨/中性, 任何板块都交易)
      elif sector_hot_topn > 0 and pick.sector ∈ top{sector_hot_topn}_hot[t_date]: trade
      else: skip (结构性弱势日 + 冷门板块 → 跳过)

    返回:
      {
        summary: { trades, win_rate_pct, position_per_trade_yuan, amount_after_1_month_yuan, ... },  # R56: trail_80 主退 + 仓位/金额
        scenarios: { trail_80: {...}, trail_50: {...}, ..., 6 套 },  # ←用户核心要求: 6 套退场
        # scenarios_hold: <已删除 — R56 不再输出 7 套持退>
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
    if progress_cb: progress_cb("[1/3 下载] 拉股票列表…")
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

    if progress_cb: progress_cb("[1/3 下载] 拉交易日历…")
    period_days_map = PERIOD_DAYS_MAP
    target_windows = [(k, period_days_map[k]) for k in period_keys if k in period_days_map] or [("半年", 120)]
    max_n = max(d for _, d in target_windows)
    end_str = pd.Timestamp.now().strftime("%Y%m%d")
    start_str = (pd.Timestamp.now() - pd.Timedelta(days=max_n + 250)).strftime("%Y%m%d")
    raw_dates = _dl.fetch_trade_dates(start_str, end_str) or []
    norm_dates = sorted({str(d).replace("-", "") for d in raw_dates if str(d).replace("-", "").isdigit()})
    log.info(f"交易日历: {len(norm_dates)} 天 ({norm_dates[0] if norm_dates else '?'}~{norm_dates[-1] if norm_dates else '?'})")

    if _daily_cache is not None:
        daily_cache = {c: _daily_cache[c] for c in codes if c in _daily_cache}
        if progress_cb: progress_cb(f"[1/3 下载] 复用日线缓存 ({len(daily_cache)}/{len(codes)} 只)")
    else:
        if progress_cb: progress_cb(f"[1/3 下载] 拉 {len(codes)} 只日线 (并行 40)…")
        daily_cache = _prefetch_daily(codes, days=max_n + 220, progress_cb=progress_cb)
    if progress_cb: progress_cb(f"[1/3 下载] 日线 {len(daily_cache)} 命中 · master panel…")
    panel = _build_master_panel(daily_cache, names)

    # ══════════════════════════════════════════════
    # Phase 2: REG — 数据质量验证 (增量 + 回归工程化)
    # ══════════════════════════════════════════════
    if progress_cb: progress_cb("[2/3 REG] 验证数据质量…")
    _reg_stats = _validate_data_quality(daily_cache, codes, f"daily(sample={sample})")
    if _reg_stats["coverage_pct"] < 50:
        log.warning(f"[REG] 数据覆盖率不足 ({_reg_stats['coverage_pct']}%), 回测可能不完整")

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

    # ── 全策略共享: 近 20 日涨停次数 (V2 COLD 因子 + WR1000 筛选用) ──
    g = panel.groupby("code", sort=False)
    panel["_zt_20d_count"] = g["change_pct"].transform(
        lambda s: (s >= 9.5).rolling(20, min_periods=2).sum()
    ).fillna(0).astype(int)

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

    # ══════════════════════════════════════════════
    # Phase 3: 构建数据索引
    # ══════════════════════════════════════════════
    panel_idx, cache_by_code_date, sec_avg_by_date = _build_data_index(
        panel, daily_cache, codes, names, progress_cb=progress_cb
    )

    if progress_cb: progress_cb("[模拟] 向量筛股 → Top N…")

    # ── 按窗口分别取候选 → 模拟 → 统计 ──
    windows_result: list[dict] = []
    all_trades_six: list[dict] = []       # R56: 6 套退场用 (持仓 1 天, 取代原 9 套 + 7 套)
    equity_curve: list[list] = []         # [[date, cum_pct]] — 基于 trail_80 复利
    skipped = {"no_pick": 0, "no_t1": 0, "no_panel": 0,
               "breadth_low": 0, "breadth_soft_no_hot": 0,
               "index_late_down": 0, "sector_late_down": 0, "tail_vol_low": 0,
               "vwap_below": 0, "vwap_strict_uncovered": 0}
    cum_return = 0.0
    # R58: VWAP 严格过滤缓存 — 同一 (code, date) 只 fetch 5min 一次
    vwap_cache: dict[tuple[str, str], float | None] = {}

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
        w_trades_six = []
        for i in range(len(w_dates) - 1):
            t_date = w_dates[i]
            t1_date = w_dates[i + 1]
            today_map = panel_idx.get(t_date)
            if not today_map:
                skipped["no_panel"] += 1
                continue
            # ── 大盘红线: 硬底 (大熊市空仓) ──
            # R101: regime_adaptive → 20 日均广度独立分类市场状态
            if regime_adaptive:
                _sb = sorted(breadth_per_day.items())
                _tr = [v for d, v in _sb if d < t_date][-20:]
                _a20 = sum(_tr) / max(len(_tr), 1) if len(_tr) >= 5 else 1500
                if _a20 >= 2200:
                    eff_hard = max(breadth_min, 500)
                    eff_soft = max(breadth_min_soft, 2000)
                elif _a20 >= 1800:
                    eff_hard = max(breadth_min, 1000)
                    eff_soft = max(breadth_min_soft, 2500)
                elif _a20 >= 1400:
                    eff_hard = max(breadth_min, 1500)
                    eff_soft = max(breadth_min_soft, 3000)
                else:
                    eff_hard = max(breadth_min, 2000)
                    eff_soft = max(breadth_min_soft, 3500)
            else:
                eff_hard = breadth_min
                eff_soft = breadth_min_soft
            b_today = breadth_per_day.get(t_date, 0) if (eff_hard > 0 or eff_soft > 0) else 9999
            if eff_hard > 0 and b_today < eff_hard:
                skipped["breadth_low"] += 1
                continue
            # ── 软红线: 当日红盘介于 [eff_hard, eff_soft) → 仅交易热门板块 ──
            in_soft_zone = (eff_soft > 0 and eff_hard > 0
                            and eff_hard <= b_today < eff_soft)
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
                # ── R58: VWAP 严格过滤 (require_vwap_strict=True) ──
                # T 日 close > VWAP 验证; 历史 5min 数据缺失时软通 (返 None 不计入 fail)
                if require_vwap_strict:
                    day_close = float(row.get("收盘") or 0)
                    if day_close > 0:
                        vwap_pass = _above_vwap_check(code, t_date, day_close, vwap_cache)
                        if vwap_pass is False:  # 显式 close ≤ VWAP 才 fail
                            skipped["vwap_below"] += 1
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

            # 批量化: 一次算 chosen 所有 trades 的 6 套退场 (~500x 快)
            # R57: 同时收集 9:30-10:00 期间最高价 (late_high), 用于 late_recover 档判定
            rows_batch: list[dict] = []
            buys_batch: list[float] = []
            late_highs_batch: list[float | None] = []
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

            # R57: 批量拉 5min K 找 9:30-10:00 期间 high (单 code 一次, 24h cache)
            late_high_by_code_date: dict[tuple[str, str], float] = {}
            if cm1_lookup:
                unique_pairs = {(code, t1_date) for code, _, _ in cm1_lookup}
                for code, sd in unique_pairs:
                    try:
                        sd_fmt = f"{sd[:4]}-{sd[4:6]}-{sd[6:8]}"
                        bars = _fetch_5min_for_code(code, sd_fmt, sd_fmt)
                        if not bars:
                            continue
                        # 9:30-10:00 (含) 期间且是 sell_date 当天的 5min K
                        early = [b for b in bars
                                 if b.get("day", "").startswith(sd_fmt)
                                 and "09:30" <= b.get("day", "")[11:16] <= "10:00"]
                        if not early:
                            continue
                        late_high = max(float(b.get("high", 0) or 0) for b in early)
                        if late_high > 0:
                            late_high_by_code_date[(code, sd)] = late_high
                    except Exception:
                        pass

            for code, row, cm1 in cm1_lookup:
                late_highs_batch.append(late_high_by_code_date.get((code, t1_date)))

            if not rows_batch:
                continue
            sim_results = _simulate_batch(rows_batch, buys_batch, late_highs=late_highs_batch,
                                          late_high_discount=late_high_discount)

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
                            r9 = _simulate_from_cache_row(cm2, buy_price, late_high_discount=late_high_discount)
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
                    "_norm_score":  float(row.get("_norm_score", row.get("score", 0))),
                })

                score = float(row.get("score", 0))
                # 2026-07-20 R-fix: 无杠杆 — 每日 top_n 只均分仓位,每只贡献 1/top_n 收益
                # (之前 mult=1.0 隐含 100% 资金押每只, top_n=4 实质 4x 杠杆, 月化被虚高 ~4×)
                mult = 1.0 / max(1, top_n)
                r9["_position_mult"] = mult
                w_trades_six.append(r9)
                exit_val = r9["trail_80"] * mult
                cum_return += exit_val
                equity_curve.append([str(t_date), round(cum_return, 3)])

        log.info(f"  {wname}: 6套 {len(w_trades_six)} 笔, skipped={skipped}")
        # 窗口级统计
        ws_six = _stat_six_scenarios(w_trades_six)
        windows_result.append({
            "window": wname,
            "n_dates": len(w_dates),
            "trades": len(w_trades_six),
            "recovery_rate": round(sum(1 for t in w_trades_six if t.get("recovered")) / max(1, len(w_trades_six)) * 100, 1),
            "skipped_no_pick": skipped["no_pick"],
            "skipped_no_t1": skipped["no_t1"],
            "skipped_breadth_low": skipped["breadth_low"],
            "scenarios": ws_six,                   # ← 用户核心要求: 6 套退场胜率 (R56 重构)
            "win_rate_trail80": ws_six.get("trail_80", {}).get("win_rate_pct", 0),
        })
        all_trades_six.extend(w_trades_six)
        if progress_cb: progress_cb(f"{wname} 完成 ({len(w_trades_six)} 笔)")

    # ── 全期汇总 ──
    if progress_cb: progress_cb("汇总统计…")
    log.info(f"[回测 v4] 汇总开始 · 6套={len(all_trades_six)} 累计t={_time.time()-t0:.1f}s")
    overall_six = _stat_six_scenarios(all_trades_six)
    log.info(f"[回测 v4] 6套统计完 · t={_time.time()-t0:.1f}s")

    # 月度胜负 — R56: 用 6 套全算, 每套一列 (用户要"每个月收益的显示" + "假设每天只卖一只")
    monthly = _compute_monthly_from_trades(all_trades_six, main_keys=SIX_KEYS)
    log.info(f"[回测 v4] 月度完 · {len(monthly)}月 · t={_time.time()-t0:.1f}s")

    # 退场原因分布 (R56: 改成 trail_recover / water_avg 二选一, 简单清晰)
    exit_breakdown = _exit_breakdown(all_trades_six)
    log.info(f"[回测 v4] 退出原因完 · t={_time.time()-t0:.1f}s")

    # 板块聚合 — 用 sector_classify (R56: 用 trail_80 替代 S1)
    if progress_cb: progress_cb("板块归类…")
    sector_breakdown = _sector_breakdown(all_trades_six, _sc)
    log.info(f"[回测 v4] 板块完 · {len(sector_breakdown)} · t={_time.time()-t0:.1f}s")

    # 总评 (R56: 用 trail_80 作为主退场口径 — 用户指定)
    summary = _build_summary(all_trades_six, equity_curve)
    log.info(f"[回测 v4] 总评完 · t={_time.time()-t0:.1f}s")

    # R69: equity_curve 采样 (≤ 500 点), 半年回测原始 ~120 点无需采样, 1年才触发
    if len(equity_curve) > 500:
        step = max(1, len(equity_curve) // 500)
        equity_curve = equity_curve[::step]
        log.info(f"equity_curve 采样 {len(equity_curve)} 点 (step={step})")

    elapsed = round(_time.time() - t0, 2)
    log.info(f"回测 v4 完成: 6套 {len(all_trades_six)} 笔 · {elapsed}s · trail_80 胜率 {overall_six.get('trail_80',{}).get('win_rate_pct','?')}%")

    # ── 5分钟K线: 水下开盘票的翻红窗口分析 (快, 保留) ──
    recovery_stats = {"skipped": True, "note": "_skip_recovery=True"}
    if not _skip_recovery:
        if progress_cb: progress_cb("5分钟翻红分析…")
        recovery_stats = _analyze_fivemin_recovery(all_trades_six, progress_cb=progress_cb)

    # ── actual_10 系列: 用真实 10:00 close 重算水下退场 (慢, 默认关 — 2026-07-17 R1) ──
    if enable_actual_10:
        if progress_cb: progress_cb("actual_10 重算…")
        actual_10_stats = _recompute_actual_10(all_trades_six)
        log.info(f"actual_10 重算完 · t={_time.time()-t0:.1f}s")
    else:
        actual_10_stats = {"skipped": True, "note": "actual_10 默认关闭 (需 enable_actual_10=true)"}
        log.info(f"actual_10 跳过 (用户未开启) · t={_time.time()-t0:.1f}s")

    out = {
        "summary": summary,
        "windows": windows_result,
        # ✅ 用户要求 (R56 重构): 6 套退场胜率 + 推荐 (横向展示)
        "scenarios": overall_six,
        "exit_breakdown": exit_breakdown,
        "monthly": monthly,
        "recovery_5min": recovery_stats,
        "actual_10_stats": actual_10_stats,
        "sector": sector_breakdown,
        "equity_curve": equity_curve,
        # 退场对比表 (前端) — 剔除 _cm_t1 等大字段,保留 6 套 ret + 标识
        "trades": [
            {k: v for k, v in t.items() if not k.startswith("_") and k != "trigger"}
            | {"recovered": t.get("recovered", False), "trigger": t.get("trigger", ""),
               "position_mult": t.get("_position_mult", 1.0)}
            for t in all_trades_six[:500]  # 最多 500 笔,超过截断 (防 JSON 爆炸)
        ],
        "trades_count": len(all_trades_six),
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
            # R54: 策略模板 id (UI 显示)
            "strategy_id": strategy_id,

            # R57+: late_high 折算系数 (1.0 / 0.7 / 0.5)
            "late_high_discount": late_high_discount,
            # R58: VWAP 严格过滤跳过数 (close ≤ VWAP 被排除的笔数)
            "vwap_below_skipped": skipped.get("vwap_below", 0),
            "vwap_strict_mode":   require_vwap_strict,
        },
        "engine_version": "v4 (vectorized · top-tier + late-session)",
        "took_sec": elapsed,
        "ts": pd.Timestamp.now().isoformat(),
    }
    return out


# 最优参数 (25 轮寻参 R2 冠军, Walk-Forward 验证通过)
OPTIMAL_PARAMS = {
    "top_n": 4,
    "hold_days": 2,
    "breadth_min": 1500,
    "breadth_min_soft": 3500,
    "sector_hot_topn": 3,
    "sector_inflow_topn": 3,
    "late_high_discount": 0.7,
    "require_vwap_strict": False,
    "regime_adaptive": True,
}

# 基线策略参数 (原始默认, 无过滤)
BASELINE_PARAMS = {
    "top_n": 1,
    "hold_days": 3,
    "breadth_min": 0,
    "breadth_min_soft": 0,
    "sector_hot_topn": 0,
    "sector_inflow_topn": 0,
    "late_high_discount": 1.0,
    "require_vwap_strict": False,
    "regime_adaptive": False,
}


def run_dual_strategy(compare_to_baseline: bool = False,
                      baseline_overrides: dict | None = None, **kwargs) -> dict:
    """双策略并跑: 主策略 + baseline 对比 (共享数据管线, 边际成本 < 10s)

    当 compare_to_baseline=True 且主策略非 baseline 时, 自动跑 baseline 对比.
    baseline_overrides 可指定 baseline 的独立参数 (如 tn=1, 无过滤).
    Returns: {"primary": {...}, "baseline": {...} | None}
    """
    primary = run_for_frontend(**kwargs)
    if not compare_to_baseline or kwargs.get("strategy_id", "baseline") == "baseline":
        return {"primary": primary, "baseline": None}
    # 跑 baseline 对比
    base_kwargs = dict(kwargs)
    base_kwargs["strategy_id"] = "baseline"
    base_kwargs["progress_cb"] = None  # 不弹进度, 静默跑
    if baseline_overrides:
        base_kwargs.update(baseline_overrides)
    baseline = run_for_frontend(**base_kwargs)
    return {"primary": primary, "baseline": baseline}


def run_optimized_vs_baseline(period_keys: list[str] | None = None,
                               sample: int = 1000,
                               progress_cb=None,
                               optimized_params: dict | None = None) -> dict:
    """一键跑 优化策略 vs 基线策略 (不同参数, 不同策略 ID)

    优化策略 = baseline + optimized_params (或 OPTIMAL_PARAMS 默认冠军)
    基线策略 = baseline + BASELINE_PARAMS
    Returns: {"optimized": {...}, "baseline": {...}}
    """
    # 2026-07-20: 1000 轮优化器完成后, OPTIMIZER_BEST 写入 cache_store,
    # 这里优先用优化器发现的 best params (字段在白名单内)
    _safe_overrides = {}
    if optimized_params and isinstance(optimized_params, dict):
        for k, v in optimized_params.items():
            if k in OPTIMAL_PARAMS:
                _safe_overrides[k] = v
    merged = {**OPTIMAL_PARAMS, **_safe_overrides}
    if _safe_overrides:
        log.info(f"使用优化器 best params 覆盖: {list(_safe_overrides.keys())}")
    opt = run_for_frontend(
        period_keys=period_keys or ["半年"],
        sample=sample, progress_cb=progress_cb,
        strategy_id="baseline", **merged,
    )
    bl = run_for_frontend(
        period_keys=period_keys or ["半年"],
        sample=sample, progress_cb=None,
        strategy_id="baseline", **BASELINE_PARAMS,
    )
    return {"optimized": opt, "baseline": bl}


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
        cached = _cs.get_store().get(cache_key)
        if cached is not None:
            # R-fix-2026-08-01: cache_store.get() 返 dict (已 JSON decode),老版返 string
            if isinstance(cached, str):
                # 兼容旧版 raw string
                cached = _json.loads(cached)
            return cached
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
        # R-fix-2026-07-18: 上午汇总 (9:30 + 10:00-11:30) — 对比尾盘用户要的"上午翻红概率"
        "recovered_am":        recovered_9_10 + recovered_10_1130,
        "recovered_am_pct":    pct(recovered_9_10 + recovered_10_1130),
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
    """对指定 keys 算 cum/avg/win (复用 _stat_six_scenarios 的核心逻辑)"""
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

    2026-07-19 第一性原理: **复利年化** — 这才是复利策略的正确度量.
    公式: (1 + r_cum)^(250/n) - 1, 其中 r_cum = cum_pct/100.
    短窗口 (1-2 周) 会爆到 10⁶%+, 但这是复利数学的自然结果, 不是 bug.

    cum_pct: 总累计收益 (eg 188.36 表示 +188.36%)
    n_days:  从首笔到末笔的实际交易日数
    """
    if n_days <= 0:
        return 0.0, 0.0
    r = cum_pct / 100.0
    # 复利年化: (1+r)^(250/n) - 1, 再转回 %
    ann = ((1 + r) ** (250.0 / n_days) - 1) * 100.0
    mon = ((1 + r) ** (21.0 / n_days) - 1) * 100.0
    # 软上限 ±99999999% 让 100 万倍以内能显示 (年化 10K%=100x 完全在范围内)
    ann = max(-99999999.0, min(99999999.0, ann))
    mon = max(-99999999.0, min(99999999.0, mon))
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


def _stat_six_scenarios(trades: list[dict]) -> dict[str, dict]:
    """6 套退场各自胜率 + 均值 + 累计复利 + 年化 + 月化 + 分位 + 期望值 + 盈亏比

    R56 重构: 9 套 → 6 套 (trail_80/50/20 + water_avg + force_10 + force_close)
    """
    if not trades:
        return {}
    keys = SIX_KEYS
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
    # R56: 关键参考 — trail_80 (主推) vs force_close (尾盘强平)
    if "trail_80" in out and "force_close" in out:
        out["_trail80_vs_close_gap"] = round(out["trail_80"]["avg_pct"] - out["force_close"]["avg_pct"], 3)
    if "trail_80" in out and "force_10" in out:
        out["_trail80_vs_force10_gap"] = round(out["trail_80"]["avg_pct"] - out["force_10"]["avg_pct"], 3)
    # 推荐策略 = 6 套里 avg_pct 最高的
    out["_best_strategy"] = max(
        [(k, v["avg_pct"]) for k, v in out.items() if isinstance(v, dict) and "avg_pct" in v and k in keys],
        key=lambda x: x[1], default=("trail_80", 0)
    )[0]
    return out


# ═════════════════════════════════════════════════════════════════
# 9) 月度 / 退出原因 / 板块
# ═════════════════════════════════════════════════════════════════
def _compute_monthly_from_trades(trades: list[dict], main_keys: tuple = SIX_KEYS) -> list[dict]:
    """按 月 group, 每套退场分别算 月度平均 / 月度累计 / 胜率 / 笔数

    R56 重构: 改 multi-key (一个 key 一列), 用户可以横向对比
    "如果用 trail_80 / force_close / water_avg 各能赚多少"
    每月结构: { month, trades, wins, losses, trail_80_avg, trail_50_avg, ..., force_close_avg }
    """
    if not trades:
        return []
    df = pd.DataFrame([{**t, "_y": str(t.get("date_t", ""))[:6]} for t in trades])
    # 6 套退场各自一列 (前缀 _ 避免与 trade 自带字段冲突)
    for k in main_keys:
        if k in df.columns:
            df[f"_{k}"] = df[k].astype(float)
    g = df.groupby("_y", sort=True)
    rows = []
    main_key = "trail_80"  # R56: 主指标用 trail_80 (用户指定主推)
    for ym, gg in g:
        main_rets = gg.get(f"_{main_key}", gg.get(main_keys[0], pd.Series(dtype=float)))
        row = {
            "month": ym,
            "trades": int(len(gg)),
            "wins": int((main_rets > 0).sum()) if len(main_rets) else 0,
            "losses": int((main_rets < 0).sum()) if len(main_rets) else 0,
            "win_rate_pct": round(float((main_rets > 0).mean() * 100), 2) if len(main_rets) else 0,
            # 主指标 (trail_80) — 兼容旧字段
            "avg_return_pct": round(float(main_rets.mean()), 3) if len(main_rets) else 0,
            "monthly_return_pct": round(float(main_rets.mean()), 3) if len(main_rets) else 0,
            "max_return_pct": round(float(main_rets.max()), 2) if len(main_rets) else 0,
            "min_return_pct": round(float(main_rets.min()), 2) if len(main_rets) else 0,
        }
        # R56: 6 套退场每月 avg + win_rate 各一列
        for k in main_keys:
            col = f"_{k}"
            if col in gg.columns:
                arr = gg[col]
                row[f"{k}_avg"] = round(float(arr.mean()), 3) if len(arr) else 0
                row[f"{k}_win_rate_pct"] = round(float((arr > 0).mean() * 100), 2) if len(arr) else 0
            else:
                row[f"{k}_avg"] = 0
                row[f"{k}_win_rate_pct"] = 0
        rows.append(row)
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
        # R56: 用 trail_80 替代 S1 作为主退场口径
        rets = g["trail_80"].astype(float) if "trail_80" in g.columns else g.iloc[0].get("trail_80", pd.Series([0]))
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
    """总体 KPI 概览 (前端顶部卡片用)

    R55 重构:
      - 仓位按 V2 score 差异化 (高置信度 → 大仓位)
      - 累计收益用 equity_curve (已含 position_mult 加权)
      - 复利年化: (1+r_cum)^(250/n) - 1
      - 新增 trading_days_actual: 实际交易天数
    """
    DEFAULT_POSITION = 20000.0
    if not trades:
        return {
            "trades": 0, "win_rate_pct": 0,
            "best_strategy": "trail_80", "best_avg_pct": 0,
            "position_per_trade_yuan": DEFAULT_POSITION,
            "amount_after_1_month_yuan": DEFAULT_POSITION,
            "amount_after_1_year_yuan": DEFAULT_POSITION,
            "trading_days_actual": 0,
        }

    # R55: position-weighted returns
    mults = np.array([t.get("_position_mult", 1.0) for t in trades], dtype=float)
    a = np.array([t.get("trail_80", t.get("S1", 0)) for t in trades], dtype=float)
    n = len(a)
    total_mult = float(mults.sum())
    aw = a * mults  # 每笔按仓位加权后的收益

    wins = int((aw > 0).sum())
    losses = int((aw < 0).sum())
    win_sum = float(aw[aw > 0].sum())
    loss_sum = abs(float(aw[aw < 0].sum()))

    # 加权平均收益率 (每 1W 仓位的平均收益)
    avg_weighted = round(float(aw.sum() / total_mult), 3) if total_mult > 0 else 0

    # 累计收益用 equity_curve (已含 position_mult 加权)
    cum = equity_curve[-1][1] if equity_curve and len(equity_curve) > 0 else 0

    n_days = _period_days(trades)
    ann_pct, mon_pct = _annualized_monthly(cum, n_days)

    # 回撤 + 标准差 (用加权收益)
    peak = np.maximum.accumulate(1 + aw / 100 / total_mult * mults) if False else 0
    # 简化: 用 equity_curve 算回撤
    eq_arr = np.array([p[1] for p in equity_curve], dtype=float) if equity_curve else np.array([0])
    if len(eq_arr) > 1:
        eq_vals = 1 + eq_arr / 100
        peak_vals = np.maximum.accumulate(eq_vals)
        dd = float(((eq_vals - peak_vals) / peak_vals * 100).min())
    else:
        dd = 0
    std = round(float(aw.std(ddof=1)), 3) if n > 1 else 0

    # 金额换算: 用平均仓位
    avg_position = DEFAULT_POSITION * total_mult / n if n > 0 else DEFAULT_POSITION
    monthly_pct_user = round(avg_weighted * 21.0, 2)
    yearly_pct_user = round(avg_weighted * 250.0, 2)
    amount_1m = round(avg_position * (1 + monthly_pct_user / 100), 2)
    amount_1y = round(avg_position * (1 + yearly_pct_user / 100), 2)

    # 实际交易天数
    dates = sorted({str(t.get("buy_date") or t.get("date_t", "")) for t in trades
                    if t.get("buy_date") or t.get("date_t")})
    dates = [d for d in dates if d and len(d) >= 8]

    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / n * 100, 2),
        "avg_return_pct": avg_weighted,
        "median_return_pct": round(float(np.median(aw)), 3),
        "cum_return_pct": round(cum, 2),
        "annualized_pct": ann_pct,
        "monthly_pct": mon_pct,
        "period_days": n_days,
        "max_drawdown_pct": round(dd, 2),
        "stddev_pct": std,
        "profit_factor": round(win_sum / loss_sum, 2) if loss_sum > 0 else None,
        "best_strategy": "trail_80",
        "best_avg_pct": avg_weighted,
        "best_trade_pct": round(float(aw.max()), 2),
        "worst_trade_pct": round(float(aw.min()), 2),
        # 仓位 + 金额
        "position_per_trade_yuan": round(avg_position, 2),
        "total_trades": n,
        "avg_daily_return_pct": avg_weighted,
        "monthly_return_pct_user": monthly_pct_user,
        "yearly_return_pct_user": yearly_pct_user,
        "amount_after_1_month_yuan": amount_1m,
        "amount_after_1_year_yuan": amount_1y,
        # R55: 实际交易天数
        "trading_days_actual": len(dates),
    }


# ═════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════
def _add_days(date_str: str, n: int) -> str:
    return (pd.Timestamp(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])) + pd.Timedelta(days=n)).strftime("%Y%m%d")
