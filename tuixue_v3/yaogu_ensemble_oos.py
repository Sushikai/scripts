#!/usr/bin/env python3
"""R117 Ensemble: GBR+Ridge 加权投票 + 时间序列 OOS 胜率 → 72% 目标."""
import json
import logging
import pickle
import sys
import time as systime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("yaogu_ensemble")


def main():
    sys.path.insert(0, str(Path(__file__).parent))
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from yaogu_seat_features import (
        load_all_seats, seat_features, aggregate_top_seat_buys,
        join_lhb_with_seats,
    )
    from yaogu_lhb_features import load_all_lhb_events, add_cross_section
    from yaogu_features import compute_features
    from yaogu_features_v2 import compute_features_v2
    try:
        from tuixue_v3 import cache_db as cdb
        dc = cdb.DailyCache()
    except Exception as e:
        log.warning("DailyCache failed: %s", e)
        dc = None

    # 1. 加载基础数据 (与 R116 一致)
    seats = seat_features(load_all_seats())
    seat_by_ds = aggregate_top_seat_buys(seats)
    lhb = add_cross_section(load_all_lhb_events())
    joined = join_lhb_with_seats(lhb, seat_by_ds)
    log.info("joined events: %d", len(joined))

    codes = sorted(set(e["__code"] for e in joined if e.get("__code")))
    daily_dict = {}
    if dc:
        store = dc._store
        for i, c in enumerate(codes):
            k = "daily:{code}".format(code=c)
            mp = store.hgetall(k)
            if mp and len(mp) >= 60:
                rows = []
                for d_str, payload in mp.items():
                    if isinstance(d_str, bytes):
                        d_str = d_str.decode()
                    if isinstance(payload, (bytes, str)):
                        import json as _json
                        try:
                            payload = _json.loads(payload) if isinstance(payload, str) else _json.loads(payload.decode())
                        except Exception:
                            continue
                    rows.append({
                        "日期": f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:]}",
                        "开盘": payload.get("open", 0),
                        "最高": payload.get("high", 0),
                        "最低": payload.get("low", 0),
                        "收盘": payload.get("close", 0),
                        "成交量": payload.get("volume", 0),
                        "成交额": payload.get("amount", 0),
                        "换手率": payload.get("turnover", 0),
                    })
                if rows:
                    df = pd.DataFrame(rows).sort_values("日期").reset_index(drop=True)
                    df["涨跌幅"] = df["收盘"].pct_change() * 100
                    df["涨跌幅"] = df["涨跌幅"].fillna(0)
                    df["涨停"] = (df["涨跌幅"] >= 9.5).astype(int)
                    daily_dict[c] = df

    log.info("daily_dict: %d codes", len(daily_dict))

    # 2. R105 + R106 特征
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

    # 3. 特征矩阵
    SKIP_PREFIXES = (
        "fwd_", "__", "seat_labels",
        "reason_text", "interp_has_famous",
        "lhb_score", "combined_score",
    )
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
    log.info("feature count: %d", len(feature_names))

    rows = []
    ys_1d, ys_2d, ys_5d = [], [], []
    for e in joined:
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
        f1 = e.get("fwd_1d")
        if f1 is None or pd.isna(f1):
            continue
        rows.append(row)
        ys_1d.append(float(f1))
        ys_2d.append(float(e.get("fwd_2d")) if e.get("fwd_2d") is not None and not pd.isna(e.get("fwd_2d")) else None)
        ys_5d.append(float(e.get("fwd_5d")) if e.get("fwd_5d") is not None and not pd.isna(e.get("fwd_5d")) else None)

    X = np.array(rows, dtype=np.float32)
    y_1d = np.array(ys_1d)
    log.info("X shape: %s", X.shape)

    # 4. 时间序列 OOS — 多种 ensemble 组合
    valid_with_date = [(i, e) for i, e in enumerate(joined) if e.get("fwd_1d") is not None]
    valid_with_date.sort(key=lambda x: x[1].get("__date", ""))
    n_total = len(valid_with_date)
    n_train = int(n_total * 0.6)
    train_idx_local = list(range(n_train))
    test_idx_local = list(range(n_train, n_total))

    # 单 GBR baseline
    gbr = GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
    gbr.fit(X[train_idx_local], y_1d[train_idx_local])
    gbr_preds = gbr.predict(X[test_idx_local])

    # 单 Ridge baseline
    ridge = Ridge(alpha=10.0)
    ridge.fit(X[train_idx_local], y_1d[train_idx_local])
    ridge_preds = ridge.predict(X[test_idx_local])

    # GBR + Ridge 排名平均
    def rank_avg(preds_list):
        ranks = [pd.Series(p).rank().values for p in preds_list]
        return np.mean(ranks, axis=0)

    def eval_strategy(preds, label):
        for n in (1, 2, 5):
            fwd_key = f"fwd_{n}d"
            fwds_test_raw = [valid_with_date[test_idx_local[i]][1].get(fwd_key) for i in range(len(test_idx_local))]
            fwds_test = np.array([v if v is not None else np.nan for v in fwds_test_raw], dtype=float)
            vmask = ~np.isnan(fwds_test)
            if vmask.sum() < 50:
                continue
            preds_v = preds[vmask]
            fwds_v = fwds_test[vmask]
            sorted_local = np.argsort(-preds_v)
            for k in (10, 20, 50, 100):
                if len(fwds_v) < k:
                    continue
                top_fwds = fwds_v[sorted_local[:k]]
                wr = float((top_fwds > 0).mean() * 100)
                avg = float(top_fwds.mean())
                log.info(f"  {label:30s} 上榜后{n}日 top-{k:3d}: 胜率={wr:.1f}%  均收益={avg:+.2f}%")

    log.info("\n=== GBR 单模型 ===")
    eval_strategy(gbr_preds, "GBR")

    log.info("\n=== Ridge 单模型 ===")
    eval_strategy(ridge_preds, "Ridge")

    # Ensemble 1: GBR + Ridge 排名平均
    log.info("\n=== Ensemble 1: GBR + Ridge 排名平均 ===")
    ens1 = rank_avg([gbr_preds, ridge_preds])
    eval_strategy(ens1, "GBR+Ridge rank-avg")

    # Ensemble 2: GBR 0.7 + Ridge 0.3 (加权)
    log.info("\n=== Ensemble 2: GBR 0.7 + Ridge 0.3 加权 ===")
    ens2 = 0.7 * gbr_preds + 0.3 * ridge_preds
    eval_strategy(ens2, "GBR(0.7)+Ridge(0.3)")

    # Ensemble 3: GBR + GBR(n=100,d=3) (更强 bagging)
    gbr2 = GradientBoostingRegressor(n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42)
    gbr2.fit(X[train_idx_local], y_1d[train_idx_local])
    gbr2_preds = gbr2.predict(X[test_idx_local])

    log.info("\n=== Ensemble 3: GBR(300) + GBR(100) ===")
    ens3 = rank_avg([gbr_preds, gbr2_preds])
    eval_strategy(ens3, "GBR(300)+GBR(100)")

    # Ensemble 4: 三个全用 + 排名加权
    log.info("\n=== Ensemble 4: GBR(300)+GBR(100)+Ridge 排名平均 ===")
    ens4 = rank_avg([gbr_preds, gbr2_preds, ridge_preds])
    eval_strategy(ens4, "Triple-rank-avg")

    # 保存最佳 ensemble 模型 (Triple-rank-avg)
    model_path = Path(__file__).parent / "yaogu_gbm_v2.pkl"
    with open(model_path, "wb") as f:
        pickle.dump({
            "model_gbr": gbr,
            "model_gbr2": gbr2,
            "model_ridge": ridge,
            "ensemble_method": "triple_rank_avg",
            "feature_names": feature_names,
            "n_features": len(feature_names),
            "model_version": "v117",
        }, f)
    log.info("saved R117 ensemble model to %s", model_path)


if __name__ == "__main__":
    main()