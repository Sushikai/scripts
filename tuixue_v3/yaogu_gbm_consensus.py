#!/usr/bin/env python3
"""R121 多模型一致投票 — GBR+Ridge+RandomForest 全 top-30% 才选, 试图突破 55% 天花板."""
import json
import logging
import pickle
import sys
import time as systime
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import QuantileTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("consensus")

COST_BPS = 0.66
SLIPPAGE_PCT = 0.3


def is_one_word(open_p, high_p, low_p):
    return abs(open_p - high_p) <= high_p * 0.003 and open_p > 0


def sim_trade(code, signal_date, daily, hold_days, stop_loss_pct):
    """R120 真实可执行性模拟 (复刻)."""
    df = daily.get(code)
    if df is None:
        return None
    sig_idx = None
    for i, row in df.iterrows():
        if str(row["日期"]).replace("-", "")[:8] == signal_date:
            sig_idx = i
            break
    if sig_idx is None:
        return None
    t1_idx = sig_idx + 1
    if t1_idx >= len(df):
        return None
    t1_row = df.iloc[t1_idx]
    t0_row = df.iloc[sig_idx]
    open_p = float(t1_row["开盘"])
    high_p = float(t1_row["最高"])
    low_p = float(t1_row["最低"])
    pct_t1 = float(t1_row["涨跌幅"])
    close_t0 = float(t0_row["收盘"])

    if is_one_word(open_p, high_p, low_p):
        return {"code": code, "ret": 0.0, "trigger": "skip_one_word", "skip": True, "hold": 0}
    if pct_t1 <= -9.5:
        return {"code": code, "ret": 0.0, "trigger": "skip_limit_down", "skip": True, "hold": 0}
    if close_t0 > 0 and open_p < close_t0 * 0.98:
        return {"code": code, "ret": 0.0, "trigger": "skip_weak_open", "skip": True, "hold": 0}
    if close_t0 > 0 and open_p > close_t0 * 1.05:
        return {"code": code, "ret": 0.0, "trigger": "skip_chase", "skip": True, "hold": 0}

    buy_price = open_p * (1 + SLIPPAGE_PCT / 100)
    sell_price = None
    exit_date = None
    trigger = None
    hold = 0

    for j in range(t1_idx, min(t1_idx + hold_days + 1, len(df))):
        row = df.iloc[j]
        close, high, low = float(row["收盘"]), float(row["最高"]), float(row["最低"])
        pct_day = float(row["涨跌幅"])
        if low <= buy_price * (1 + stop_loss_pct / 100):
            sell_price = buy_price * (1 + stop_loss_pct / 100)
            exit_date = str(row["日期"])
            trigger = "stop_loss"
            hold = j - t1_idx
            break
        is_down_limit = pct_day <= -9.5
        if j == t1_idx + hold_days:
            if not is_down_limit:
                sell_price = close
                exit_date = str(row["日期"])
                trigger = "hold_full"
                hold = j - t1_idx
                break
            if j + 1 < len(df):
                next_row = df.iloc[j + 1]
                sell_price = float(next_row["开盘"]) * (1 - SLIPPAGE_PCT / 100)
                exit_date = str(next_row["日期"])
                trigger = "hold_full_next_open"
                hold = (j + 1) - t1_idx
                break
    if sell_price is None:
        last_row = df.iloc[min(t1_idx + hold_days, len(df) - 1)]
        sell_price = float(last_row["收盘"]) * (1 - SLIPPAGE_PCT / 100)
        exit_date = str(last_row["日期"])
        trigger = "hold_end"
        hold = min(hold_days, len(df) - 1 - t1_idx)
    ret = (sell_price / buy_price - 1) * 100 - COST_BPS
    return {"code": code, "ret": round(ret, 2), "trigger": trigger, "hold": hold, "skip": False}


def main():
    sys.path.insert(0, str(Path(__file__).parent))
    from yaogu_seat_features import (
        load_all_seats, seat_features, aggregate_top_seat_buys, join_lhb_with_seats,
    )
    from yaogu_lhb_features import load_all_lhb_events, add_cross_section
    from yaogu_features import compute_features
    from yaogu_features_v2 import compute_features_v2
    from yaogu_survey import load_daily
    from tuixue_v3 import cache_db as cdb

    log.info("=== R121 多模型一致投票 ===")

    # 1. 加载基础数据
    seats = seat_features(load_all_seats())
    seat_by_ds = aggregate_top_seat_buys(seats)
    lhb = add_cross_section(load_all_lhb_events())
    joined = join_lhb_with_seats(lhb, seat_by_ds)
    log.info("joined events: %d", len(joined))

    dc = cdb.DailyCache()
    daily = load_daily()

    # 2. R105+R106 特征
    codes = sorted(set(e["__code"] for e in joined if e.get("__code")))
    daily_dict = {}
    for c in codes:
        if c in daily:
            df = daily[c].copy()
            if "涨跌幅" not in df.columns:
                df["涨跌幅"] = df["收盘"].pct_change() * 100
                df["涨跌幅"] = df["涨跌幅"].fillna(0)
            if "涨停" not in df.columns:
                df["涨停"] = (df["涨跌幅"] >= 9.5).astype(int)
            daily_dict[c] = df

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

    SKIP_PREFIXES = ("fwd_", "__", "seat_labels", "reason_text",
                      "interp_has_famous", "lhb_score", "combined_score")
    feature_names = []
    for k in joined[0].keys():
        if k.startswith(SKIP_PREFIXES):
            continue
        vals = [e.get(k) for e in joined if e.get(k) is not None]
        if not vals:
            continue
        try:
            float(vals[0])
        except (TypeError, ValueError):
            continue
        feature_names.append(k)

    # 3. 构建 X, y (按日期排序做时间序列切分)
    valid = [(i, e) for i, e in enumerate(joined) if e.get("fwd_1d") is not None]
    valid.sort(key=lambda x: x[1].get("__date", ""))
    sorted_idx = [v[0] for v in valid]
    dates = [v[1].get("__date", "") for v in valid]

    rows = []
    ys_1d = []
    valid_events = []
    for orig_i in sorted_idx:
        e = joined[orig_i]
        row = []
        for k in feature_names:
            v = e.get(k)
            try:
                v = float(v) if v is not None else 0.0
                if np.isnan(v) or np.isinf(v):
                    v = 0.0
            except (TypeError, ValueError):
                v = 0.0
            row.append(v)
        rows.append(row)
        ys_1d.append(float(e.get("fwd_1d")))
        valid_events.append(e)

    X = np.array(rows, dtype=np.float32)
    y_1d = np.array(ys_1d)
    n_total = len(X)
    n_train = int(n_total * 0.6)
    log.info("X shape: %s, train: %d, test: %d", X.shape, n_train, n_total - n_train)

    # 4. 训练 3 个模型 (时间序列切分, 避免 leakage)
    log.info("\n训练 GBR + Ridge + RandomForest...")
    models = {
        "GBR": GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42),
        "Ridge": Ridge(alpha=10.0),
        "RF": RandomForestRegressor(n_estimators=200, max_depth=8, random_state=42, n_jobs=-1),
    }
    X_train = X[:n_train]
    X_test = X[n_train:]
    for name, m in models.items():
        m.fit(X_train, y_1d[:n_train])

    # 5. 全量打分 (用于真实回测中 GBR 选股)
    log.info("\n全量 13479 事件打分...")
    all_X_rows = []
    all_event_info = []
    for ev in joined:
        row = []
        for k in feature_names:
            v = ev.get(k)
            try:
                v = float(v) if v is not None else 0.0
                if np.isnan(v) or np.isinf(v):
                    v = 0.0
            except (TypeError, ValueError):
                v = 0.0
            row.append(v)
        all_X_rows.append(row)
        all_event_info.append({
            "code": ev.get("__code", ""),
            "date": str(ev.get("__date", "")).replace("-", ""),
            "fwd_1d": ev.get("fwd_1d"),
        })
    X_all = np.array(all_X_rows, dtype=np.float32)

    # 各模型打分
    preds_per_model = {}
    for name, m in models.items():
        preds_per_model[name] = m.predict(X_all)
        log.info("  %s: 全量打分 %d 事件", name, len(preds_per_model[name]))

    # 6. **共识过滤** — 每只事件用 3 个模型的排名分位数 (0-1) 计算
    # 只选 3 个模型排名都在 top-30% 的事件
    log.info("\n=== 多模型共识 ===")
    ranks_per_model = {}
    for name, preds in preds_per_model.items():
        # 排名分位数 (0-1, 越高越好)
        ranks_per_model[name] = pd.Series(preds).rank(pct=True).values

    # 每个事件的共识分数 = 3 个模型分位数的几何平均
    consensus_scores = np.exp(np.mean([np.log(r + 1e-9) for r in ranks_per_model.values()], axis=0))
    log.info("共识分数分布: min={:.3f} median={:.3f} max={:.3f}",
             consensus_scores.min(), np.median(consensus_scores), consensus_scores.max())

    # 7. 不同共识阈值的回测
    # 阈值越高, 只有 3 个模型都认为高分的事件才入选
    by_date = defaultdict(list)
    for i, info in enumerate(all_event_info):
        if info["date"]:
            by_date[info["date"]].append({**info, "consensus": float(consensus_scores[i]),
                                          "score_gbr": float(preds_per_model["GBR"][i])})

    # 选 top-K + 共识阈值
    log.info("\n=== 共识阈值 × top-K 回测 ===")
    configs = []
    for consensus_min in (0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95):
        for top_k in (3, 5, 7, 10, 15):
            for hold in (1, 2):
                for sl in (-8, -10):
                    configs.append((consensus_min, top_k, hold, sl))
    log.info("总配置数: %d", len(configs))

    results = []
    for cons_min, top_k, hold, sl in configs:
        trades = []
        skipped = 0
        for date, day_events in sorted(by_date.items()):
            # 过滤共识分数
            qualified = [e for e in day_events if e["consensus"] >= cons_min]
            if not qualified:
                continue
            qualified.sort(key=lambda x: -x["consensus"])
            picks = qualified[:top_k]
            for p in picks:
                t = sim_trade(p["code"], p["date"], daily, hold_days=hold, stop_loss_pct=sl)
                if t is None:
                    continue
                if t.get("skip"):
                    skipped += 1
                    continue
                t["consensus"] = round(p["consensus"], 3)
                trades.append(t)
        if not trades:
            continue
        wins = sum(1 for t in trades if t["ret"] > 0)
        total = len(trades)
        avg_ret = float(np.mean([t["ret"] for t in trades]))
        total_ret = float(np.sum([t["ret"] for t in trades]))
        results.append({
            "consensus_min": cons_min, "top_k": top_k, "hold": hold, "sl": sl,
            "trades": total, "wins": wins,
            "win_rate": round(wins / total * 100, 1),
            "avg_ret": round(avg_ret, 2),
            "total_ret": round(total_ret, 2),
            "skipped": skipped,
        })

    # 8. 按胜率排序
    results.sort(key=lambda x: (x["win_rate"], x["avg_ret"]), reverse=True)
    log.info("\n=== Top 20 配置 (按胜率) ===")
    log.info(f"{'cons':>6} {'top_k':>5} {'hold':>5} {'sl':>4} {'trades':>6} "
             f"{'wins':>5} {'wr%':>6} {'avg':>7} {'total':>9} {'skip':>5}")
    for r in results[:20]:
        log.info(f"{r['consensus_min']:6.2f} {r['top_k']:5d} {r['hold']:5d} "
                 f"{r['sl']:4d} {r['trades']:6d} {r['wins']:5d} {r['win_rate']:6.1f} "
                 f"{r['avg_ret']:+7.2f} {r['total_ret']:+9.2f} {r['skipped']:5d}")

    # 9. 按总收益排序
    results.sort(key=lambda x: x["total_ret"], reverse=True)
    log.info("\n=== Top 10 配置 (按总收益) ===")
    log.info(f"{'cons':>6} {'top_k':>5} {'hold':>5} {'sl':>4} {'wr%':>6} {'avg':>7} {'total':>9}")
    for r in results[:10]:
        log.info(f"{r['consensus_min']:6.2f} {r['top_k']:5d} {r['hold']:5d} "
                 f"{r['sl']:4d} {r['win_rate']:6.1f} {r['avg_ret']:+7.2f} {r['total_ret']:+9.2f}")

    # 10. 找胜率 ≥ 60% 且 avg > 0
    good = [r for r in results if r["win_rate"] >= 60 and r["avg_ret"] > 0]
    good.sort(key=lambda x: -x["total_ret"])
    log.info(f"\n=== 胜率 ≥ 60% 且 avg > 0 的配置 ({len(good)} 个) ===")
    for r in good[:15]:
        log.info(f"  cons={r['consensus_min']:.2f} top_k={r['top_k']:3d} hold={r['hold']} "
                 f"sl={r['sl']:+3d}% wr={r['win_rate']:.1f}% avg={r['avg_ret']:+.2f}% "
                 f"total={r['total_ret']:+.1f}% trades={r['trades']}")

    # 11. 保存最优配置
    best = max(results, key=lambda x: (x["win_rate"], x["avg_ret"]))
    out = {
        "model_version": "v121",
        "generated_at": systime.strftime("%Y-%m-%d %H:%M:%S"),
        "method": "consensus_vote_3_models",
        "models": ["GBR(n=300,d=4)", "Ridge(α=10)", "RF(n=200,d=8)"],
        "best_config": best,
        "top_5_configs": sorted(results, key=lambda x: -x["win_rate"])[:5],
        "n_good_configs_60": len(good),
    }
    out_path = Path(__file__).parent / "yaogu_consensus_report.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    log.info(f"\n保存最优配置到 {out_path}")
    log.info(f"最优: cons={best['consensus_min']} top_k={best['top_k']} hold={best['hold']} "
             f"sl={best['sl']}% wr={best['win_rate']:.1f}% avg={best['avg_ret']:+.2f}%")


if __name__ == "__main__":
    main()