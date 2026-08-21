#!/usr/bin/env python3
"""R120 GBR + 可执行性 + 真实胜率回测 — 集成 R116 GBR 模型 + 涨跌停/滑点/跳空/一字板过滤."""
import json
import logging
import pickle
import sys
import time as systime
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("gbm_bt")

# ═══════════════════════════════════════
# 可执行性常量
# ═══════════════════════════════════════
COST_BPS = 0.66  # 双边: 滑点 0.2% + 买卖费 0.06% + 印花税 0.1%
SLIPPAGE_PCT = 0.3  # 额外滑点 0.3% (开盘撮合冲击)
PRICE_LIMIT_PCT = 9.8  # 主板涨停限制 (ST 5%, 创业板/科创板 19.8%)
PRICE_LIMIT_GEM = 19.8  # 创业板/科创板/北交所


def is_price_limit_up(code: str, pct: float) -> bool:
    """判断是否涨停. 创业板/科创板 20%, 主板 10%."""
    # 简化: 默认主板 ±10%, 北证/创/科 ±20%
    if code.startswith(("30", "688")):  # 创业板/科创板
        return pct >= PRICE_LIMIT_GEM - 0.5
    return pct >= PRICE_LIMIT_PCT - 0.5


def is_one_word_open(open_p: float, high_p: float, low_p: float) -> bool:
    """一字板识别: open == high (顶到天)"""
    return abs(open_p - high_p) <= high_p * 0.003 and open_p > 0


def is_limit_down(open_p: float, low_p: float, pct: float) -> bool:
    """跌停识别: 开盘跌停则当日卖不出"""
    if code := "000000":
        pass
    return pct <= -9.5


# ═══════════════════════════════════════
# 单笔可执行性模拟
# ═══════════════════════════════════════
def sim_trade_executable(
    code: str,
    signal_date: str,
    daily: dict[str, pd.DataFrame],
    entry_streak: int,
    hold_days: int,
    stop_loss_pct: float,
    skip_open_gap_low: float = -2.0,  # 开盘价 < 昨收 * (1 + skip_open_gap_low%) 不买
    skip_open_gap_high: float = 8.0,   # 开盘价 > 昨收 * (1 + skip_open_gap_high%) 不追
    skip_open_chase_pct: float = 5.0,  # T+1 开盘涨幅 > 5% 不买
) -> dict | None:
    """真实可执行的单笔回测.

    流程:
    1. 找到 signal_date (上榜日)
    2. T+1 开盘价买入
       - 一字板 (开盘 == 最高) → 空仓 0 收益 (买不到)
       - 开盘跌停 → 空仓
       - 开盘 < 昨收 * (1 - 2%) → 弱开盘, 不买
       - 开盘 > 昨收 * 1.05 → 追高风险, 不买
    3. 持有 hold_days 天或触发止损
       - 跌停日不可卖 (除非已经触发止损)
       - 收盘止损 / 硬止损 / 持有期满
    4. 计算收益 (扣双边成本 + 滑点)
    """
    df = daily.get(code)
    if df is None:
        return None

    # 找到 signal_date 的索引
    sig_idx = None
    for i, row in df.iterrows():
        if str(row["日期"]).replace("-", "")[:8] == signal_date:
            sig_idx = i
            break
    if sig_idx is None:
        return None

    # T+1 索引 (signal_date 的下一个交易日)
    t1_idx = sig_idx + 1
    if t1_idx >= len(df):
        return None

    t1_row = df.iloc[t1_idx]
    t0_row = df.iloc[sig_idx]
    open_p = float(t1_row["开盘"])
    high_p = float(t1_row["最高"])
    low_p = float(t1_row["最低"])
    close_p = float(t1_row["收盘"])
    pct_t1 = float(t1_row["涨跌幅"])
    close_t0 = float(t0_row["收盘"])

    # ═══ 可执行性检查 ═══
    skip_reason = None

    # 1. 一字板买不到
    if is_one_word_open(open_p, high_p, low_p):
        return {"code": code, "date": str(t1_row["日期"]), "buy": 0, "sell": 0,
                "ret": 0.0, "trigger": "skip_one_word", "skip": True, "streak": entry_streak, "hold": 0}

    # 2. 开盘跌停 (跌幅 -10%)
    if pct_t1 <= -9.5:
        return {"code": code, "date": str(t1_row["日期"]), "buy": 0, "sell": 0,
                "ret": 0.0, "trigger": "skip_limit_down", "skip": True, "streak": entry_streak, "hold": 0}

    # 3. 开盘弱 (低开 -2% 以下) → 不买
    if close_t0 > 0 and open_p < close_t0 * (1 + skip_open_gap_low / 100):
        return {"code": code, "date": str(t1_row["日期"]), "buy": 0, "sell": 0,
                "ret": 0.0, "trigger": "skip_weak_open", "skip": True, "streak": entry_streak, "hold": 0}

    # 4. 追高风险 (开盘高开 > 5%) → 不买
    if close_t0 > 0 and open_p > close_t0 * (1 + skip_open_chase_pct / 100):
        return {"code": code, "date": str(t1_row["日期"]), "buy": 0, "sell": 0,
                "ret": 0.0, "trigger": "skip_chase", "skip": True, "streak": entry_streak, "hold": 0}

    # ═══ 买入 (T+1 开盘价 + 滑点) ═══
    buy_price = open_p * (1 + SLIPPAGE_PCT / 100)

    # ═══ 持有 + 退出规则 ═══
    sell_price = None
    exit_date = None
    trigger = None
    hold = 0

    for j in range(t1_idx, min(t1_idx + hold_days + 1, len(df))):
        row = df.iloc[j]
        close, high, low = float(row["收盘"]), float(row["最高"]), float(row["最低"])
        pct_day = float(row["涨跌幅"])

        # 硬止损: 最低价触及
        if low <= buy_price * (1 + stop_loss_pct / 100):
            sell_price = buy_price * (1 + stop_loss_pct / 100)
            exit_date = str(row["日期"])
            trigger = "stop_loss"
            hold = j - t1_idx
            break

        # 当日是否跌停 (不可卖)
        is_down_limit = pct_day <= -9.5

        # 持有期满或收盘退出 (跌停日不可卖, 等次日)
        if j == t1_idx + hold_days:
            if not is_down_limit:
                sell_price = close
                exit_date = str(row["日期"])
                trigger = "hold_full"
                hold = j - t1_idx
                break
            # 跌停日不卖, 等次日 (但 hold_days 已到, 强行按次日开盘价卖)
            if j + 1 < len(df):
                next_row = df.iloc[j + 1]
                sell_price = float(next_row["开盘"]) * (1 - SLIPPAGE_PCT / 100)
                exit_date = str(next_row["日期"])
                trigger = "hold_full_next_open"
                hold = (j + 1) - t1_idx
                break

    if sell_price is None:
        # 持有期结束仍无触发, 用最后一个交易日收盘价
        last_row = df.iloc[min(t1_idx + hold_days, len(df) - 1)]
        sell_price = float(last_row["收盘"]) * (1 - SLIPPAGE_PCT / 100)
        exit_date = str(last_row["日期"])
        trigger = "hold_end"
        hold = min(hold_days, len(df) - 1 - t1_idx)

    ret = (sell_price / buy_price - 1) * 100 - COST_BPS
    return {"code": code, "date": str(t1_row["日期"]), "buy": round(buy_price, 2),
            "sell": round(sell_price, 2), "ret": round(ret, 2),
            "trigger": trigger, "streak": entry_streak, "hold": hold, "skip": False}


# ═══════════════════════════════════════
# 加载 GBR 模型 + 离线评分
# ═══════════════════════════════════════
def score_lhb_events_with_gbr(events: list[dict], daily: dict[str, pd.DataFrame],
                               seats: dict, seat_by_ds: dict) -> list[dict]:
    """对每个 lhb_event 用 R116 GBR 模型打分, 返回 [{ev, score}, ...]."""
    sys.path.insert(0, str(Path(__file__).parent))
    from yaogu_seat_features import join_lhb_with_seats, aggregate_top_seat_buys
    from yaogu_lhb_features import load_all_lhb_events, add_cross_section
    from yaogu_features import compute_features
    from yaogu_features_v2 import compute_features_v2

    # 1. 加载/构建 joined
    lhb = add_cross_section(load_all_lhb_events())
    joined = join_lhb_with_seats(lhb, seat_by_ds)
    log.info("joined events: %d", len(joined))

    # 2. 加载 daily
    codes = sorted(set(e["__code"] for e in joined if e.get("__code")))
    daily_dict = {}
    for c in codes:
        if c in daily:
            daily_dict[c] = daily[c]
    log.info("daily_dict: %d codes", len(daily_dict))

    # 3. R105+R106 特征
    r105 = compute_features(daily_dict)
    r106 = compute_features_v2(daily_dict)
    for ev in joined:
        c = ev.get("__code", "")
        if c in r105 and isinstance(r105[c], dict):
            for k, v in r105[c].items():
                if k.startswith("__"):
                    continue
                try:
                    ev[f"r105_{k}"] = float(v) if v is not None else 0.0
                except (TypeError, ValueError):
                    pass
        if c in r106 and isinstance(r106[c], dict):
            for k, v in r106[c].items():
                if k.startswith("__"):
                    continue
                try:
                    ev[f"r106_{k}"] = float(v) if v is not None else 0.0
                except (TypeError, ValueError):
                    pass

    # 4. 加载 GBR 模型
    model_path = Path(__file__).parent / "yaogu_gbm_v2.pkl"
    with open(model_path, "rb") as f:
        bundle = pickle.load(f)

    # 兼容 R116 (单 model) 和 R117 (ensemble)
    if "model_gbr" in bundle:
        model = bundle["model_gbr"]
        feature_names = bundle["feature_names"]
        log.info("loaded R117 ensemble model")
    else:
        model = bundle["model"]
        feature_names = bundle["feature_names"]
        log.info("loaded R116 single GBR")

    # 5. 评分
    rows = []
    valid_evs = []
    for ev in joined:
        row = []
        ok = True
        for k in feature_names:
            v = ev.get(k)
            try:
                v = float(v) if v is not None else 0.0
                if np.isnan(v) or np.isinf(v):
                    v = 0.0
            except (TypeError, ValueError):
                v = 0.0
                ok = False
            row.append(v)
        if ok:
            rows.append(row)
            valid_evs.append(ev)

    X = np.array(rows, dtype=np.float32)
    scores = model.predict(X)
    log.info("scored %d events", len(scores))

    # 关联 ev → score
    scored = []
    for ev, sc in zip(valid_evs, scores):
        scored.append({
            "code": ev.get("__code", ""),
            "date": str(ev.get("__date", "")).replace("-", ""),
            "score": float(sc),
            "fwd_1d": ev.get("fwd_1d"),
            "fwd_2d": ev.get("fwd_2d"),
            "fwd_5d": ev.get("fwd_5d"),
        })
    return scored


# ═══════════════════════════════════════
# 真实可执行性回测 (GBM top-K + 涨跌停/滑点)
# ═══════════════════════════════════════
def run_gbm_executable_bt(
    scored_events: list[dict],
    daily: dict[str, pd.DataFrame],
    top_k_per_day: int = 5,
    hold_days: int = 2,
    stop_loss_pct: float = -8.0,
    start: str = "20251201",
    end: str = "20260811",
) -> dict:
    """对每日选 top-K GBR 评分事件 → 真实可执行性回测."""
    # 按日期分组
    by_date = defaultdict(list)
    for s in scored_events:
        if start <= s["date"] <= end:
            by_date[s["date"]].append(s)

    all_trades = []
    skipped_count = defaultdict(int)

    for date, day_events in sorted(by_date.items()):
        # 按 GBR 评分排序
        day_events.sort(key=lambda x: -x["score"])
        # 取 top-K
        picks = day_events[:top_k_per_day]
        for p in picks:
            trade = sim_trade_executable(
                code=p["code"], signal_date=p["date"], daily=daily,
                entry_streak=2, hold_days=hold_days,
                stop_loss_pct=stop_loss_pct,
            )
            if trade is None:
                continue
            if trade.get("skip"):
                skipped_count[trade["trigger"]] += 1
                continue
            trade["gbm_score"] = round(p["score"], 4)
            all_trades.append(trade)

    if not all_trades:
        return {"trades": 0, "skipped": dict(skipped_count)}

    # 计算胜率
    wins = sum(1 for t in all_trades if t["ret"] > 0)
    total = len(all_trades)
    avg_ret = float(np.mean([t["ret"] for t in all_trades]))
    median_ret = float(np.median([t["ret"] for t in all_trades]))
    total_ret = float(np.sum([t["ret"] for t in all_trades]))

    return {
        "trades": total,
        "wins": wins,
        "win_rate": round(wins / total * 100, 1),
        "avg_ret": round(avg_ret, 2),
        "median_ret": round(median_ret, 2),
        "total_ret": round(total_ret, 2),
        "skipped": dict(skipped_count),
        "skip_total": sum(skipped_count.values()),
        "hold_days": hold_days,
        "stop_loss_pct": stop_loss_pct,
        "top_k_per_day": top_k_per_day,
        "n_days": len(by_date),
    }


def main():
    sys.path.insert(0, str(Path(__file__).parent))
    from yaogu_seat_features import load_all_seats, seat_features, aggregate_top_seat_buys
    from yaogu_survey import load_daily

    log.info("=== R120 GBR + 可执行性回测 ===")

    # 1. 加载基础
    seats = seat_features(load_all_seats())
    seat_by_ds = aggregate_top_seat_buys(seats)
    daily = load_daily()
    log.info("loaded: %d seats, %d daily codes", len(seats), len(daily))

    # 2. 加载 daily 中加 涨跌幅 和 涨停 列 (R105/R106 需要)
    for code, df in daily.items():
        if "涨跌幅" not in df.columns:
            df["涨跌幅"] = df["收盘"].pct_change() * 100
            df["涨跌幅"] = df["涨跌幅"].fillna(0)
        if "涨停" not in df.columns:
            df["涨停"] = (df["涨跌幅"] >= 9.5).astype(int)

    # 3. GBR 评分
    scored = score_lhb_events_with_gbr(None, daily, seats, seat_by_ds)

    # 4. 多组合超参搜索 (10000 轮简化: 5×5×5 = 125 个组合)
    log.info("\n=== 超参搜索 (top-K × hold_days × stop_loss) ===")

    configs = []
    for top_k in (3, 5, 7, 10, 15):
        for hold in (1, 2, 3, 5, 7):
            for sl in (-5, -7, -10, -12, -15):
                configs.append((top_k, hold, sl))

    results = []
    for top_k, hold, sl in configs:
        r = run_gbm_executable_bt(
            scored_events=scored, daily=daily,
            top_k_per_day=top_k, hold_days=hold, stop_loss_pct=sl,
        )
        results.append({"top_k": top_k, "hold": hold, "sl": sl, **r})

    # 5. 按胜率排序, 输出 top-20
    results.sort(key=lambda x: (x.get("win_rate", 0), x.get("avg_ret", -100)), reverse=True)
    log.info("\n=== Top 20 配置 (按胜率) ===")
    log.info(f"{'top_k':>5} {'hold':>5} {'sl':>5} {'trades':>6} {'wins':>5} {'wr%':>6} "
             f"{'avg':>7} {'median':>7} {'skipped':>8}")
    for r in results[:20]:
        log.info(f"{r['top_k']:5d} {r['hold']:5d} {r['sl']:5d} "
                 f"{r['trades']:6d} {r['wins']:5d} {r['win_rate']:6.1f} "
                 f"{r['avg_ret']:+7.2f} {r['median_ret']:+7.2f} "
                 f"{r['skip_total']:8d}")

    # 6. 按总收益排序
    results.sort(key=lambda x: x.get("total_ret", -100), reverse=True)
    log.info("\n=== Top 10 配置 (按总收益) ===")
    log.info(f"{'top_k':>5} {'hold':>5} {'sl':>5} {'trades':>6} {'wr%':>6} {'avg':>7} {'total':>9}")
    for r in results[:10]:
        log.info(f"{r['top_k']:5d} {r['hold']:5d} {r['sl']:5d} "
                 f"{r['trades']:6d} {r['win_rate']:6.1f} "
                 f"{r['avg_ret']:+7.2f} {r['total_ret']:+9.2f}")

    # 7. 找胜率 ≥ 70% 且 avg > 0 的所有配置
    log.info("\n=== 胜率 ≥ 65% 且 avg > 0 的配置 (实盘候选) ===")
    good = [r for r in results if r.get("win_rate", 0) >= 65 and r.get("avg_ret", -100) > 0]
    good.sort(key=lambda x: -x["avg_ret"])
    log.info(f"找到 {len(good)} 个候选配置:")
    for r in good[:15]:
        log.info(f"  top_k={r['top_k']:3d} hold={r['hold']} sl={r['sl']:+3d}% "
                 f"wr={r['win_rate']:.1f}% avg={r['avg_ret']:+.2f}% total={r['total_ret']:+.1f}% "
                 f"trades={r['trades']}")

    # 8. 保存最优配置
    best = results[0]  # 胜率最高
    out = {
        "model_version": "v120",
        "generated_at": systime.strftime("%Y-%m-%d %H:%M:%S"),
        "best_config": best,
        "top_5_configs": results[:5],
        "all_configs_count": len(results),
        "n_good_configs": len(good),
    }
    out_path = Path(__file__).parent / "yaogu_gbm_executable_report.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    log.info(f"\n保存最优配置到 {out_path}")
    log.info(f"最优: top_k={best['top_k']} hold={best['hold']} sl={best['sl']}% "
             f"wr={best['win_rate']:.1f}% avg={best['avg_ret']:+.2f}%")


if __name__ == "__main__":
    main()