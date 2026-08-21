#!/usr/bin/env python3
"""R119 过拟合诊断 — 多角度验证 R116 GBR 是否真的稳定."""
import json
import logging
import sys
import time as systime
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("overfit")


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
    from tuixue_v3 import cache_db as cdb

    # 1. 加载数据 (与 R116 一致)
    seats = seat_features(load_all_seats())
    seat_by_ds = aggregate_top_seat_buys(seats)
    lhb = add_cross_section(load_all_lhb_events())
    joined = join_lhb_with_seats(lhb, seat_by_ds)
    log.info("joined events: %d", len(joined))

    dc = cdb.DailyCache()
    codes = sorted(set(e["__code"] for e in joined if e.get("__code")))
    daily_dict = {}
    store = dc._store
    for c in codes:
        k = "daily:{code}".format(code=c)
        mp = store.hgetall(k)
        if mp and len(mp) >= 60:
            rows = []
            for d_str, payload in mp.items():
                if isinstance(d_str, bytes):
                    d_str = d_str.decode()
                if isinstance(payload, (bytes, str)):
                    try:
                        payload = json.loads(payload) if isinstance(payload, str) else json.loads(payload.decode())
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

    rows = []
    ys_1d = []
    valid_idx_in_joined = []
    for idx, e in enumerate(joined):
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
        valid_idx_in_joined.append(idx)
    X = np.array(rows, dtype=np.float32)
    y_1d = np.array(ys_1d)
    log.info("X shape: %s", X.shape)

    # valid_with_date: 索引 = X 的行号, e = joined 中对应的 event
    valid_with_date = [(i, joined[valid_idx_in_joined[i]]) for i in range(len(valid_idx_in_joined))]
    valid_with_date.sort(key=lambda x: x[1].get("__date", ""))

    # 重新映射 sorted 顺序的 X
    sorted_X_idx = [v[0] for v in valid_with_date]
    X_sorted = X[sorted_X_idx]
    y_sorted = y_1d[sorted_X_idx]

    dates = [valid_with_date[i][1].get("__date") for i in range(len(valid_with_date))]

    # ═══════════════════════════════════════
    # 诊断 1: 多种 train/test 切分比例
    # ═══════════════════════════════════════
    log.info("\n" + "=" * 60)
    log.info("诊断 1: 多种 train/test 切分比例")
    log.info("=" * 60)

    for ratio in (0.5, 0.6, 0.7, 0.8, 0.9):
        n_train = int(len(X_sorted) * ratio)
        gbr = GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
        gbr.fit(X_sorted[:n_train], y_sorted[:n_train])
        train_preds = gbr.predict(X_sorted[:n_train])
        test_preds = gbr.predict(X_sorted[n_train:])
        train_ic = float(pd.Series(train_preds).rank().corr(pd.Series(y_sorted[:n_train]).rank()))
        test_ic = float(pd.Series(test_preds).rank().corr(pd.Series(y_sorted[n_train:]).rank()))
        # 计算 top-10 胜率 (test set)
        fwds_test = np.array([valid_with_date[i][1].get("fwd_1d") for i in range(n_train, len(valid_with_date))])
        vmask = ~np.isnan(fwds_test)
        sorted_idx = np.argsort(-test_preds[vmask])
        top10_wr = float((fwds_test[vmask][sorted_idx[:10]] > 0).mean() * 100) if vmask.sum() >= 10 else 0
        log.info(f"  ratio={ratio:.0%} train_ic={train_ic:+.4f} test_ic={test_ic:+.4f}  "
                 f"IC下降={train_ic - test_ic:+.4f}  top-10胜率={top10_wr:.0f}%")

    # ═══════════════════════════════════════
    # 诊断 2: 滚动窗口 Walk-Forward CV (按月)
    # ═══════════════════════════════════════
    log.info("\n" + "=" * 60)
    log.info("诊断 2: 滚动窗口 Walk-Forward CV (按月)")
    log.info("=" * 60)

    # 按月分组
    months = defaultdict(list)
    for i, d in enumerate(dates):
        if d:
            ym = d[:7]
            months[ym].append(i)

    month_keys = sorted(months.keys())
    log.info("months: %s", month_keys)

    # 训练: 截止到 month[t-1] 的所有数据, 测试 month[t]
    monthly_ics = []
    for t in range(2, len(month_keys)):
        train_months = month_keys[:t]
        test_month = month_keys[t]
        train_idx = []
        for m in train_months:
            train_idx.extend(months[m])
        test_idx = months[test_month]

        if len(train_idx) < 100 or len(test_idx) < 30:
            continue

        gbr = GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
        gbr.fit(X_sorted[train_idx], y_sorted[train_idx])
        test_preds = gbr.predict(X_sorted[test_idx])
        test_ic = float(pd.Series(test_preds).rank().corr(pd.Series(y_sorted[test_idx]).rank()))
        monthly_ics.append({"train_months": train_months, "test_month": test_month,
                            "n_train": len(train_idx), "n_test": len(test_idx),
                            "ic": test_ic})
        log.info(f"  train={train_months[0]}~{train_months[-1]} test={test_month} "
                 f"n_train={len(train_idx)} n_test={len(test_idx)} ic={test_ic:+.4f}")

    if monthly_ics:
        ics = [m["ic"] for m in monthly_ics]
        log.info(f"\n  滚动 IC: mean={np.mean(ics):+.4f}  std={np.std(ics):.4f}  "
                 f"min={min(ics):+.4f}  max={max(ics):+.4f}")
        log.info(f"  IC 波动系数 (std/|mean|): {np.std(ics) / abs(np.mean(ics)):.2f}")
        log.info(f"  → 波动系数 < 0.5 = 稳定, 0.5-1.0 = 中等, >1.0 = 不稳定")

    # ═══════════════════════════════════════
    # 诊断 3: 残差分析 (train vs test)
    # ═══════════════════════════════════════
    log.info("\n" + "=" * 60)
    log.info("诊断 3: 残差分析 (train vs test)")
    log.info("=" * 60)

    n_train = int(len(X_sorted) * 0.6)
    gbr = GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
    gbr.fit(X_sorted[:n_train], y_sorted[:n_train])
    train_resid = y_sorted[:n_train] - gbr.predict(X_sorted[:n_train])
    test_resid = y_sorted[n_train:] - gbr.predict(X_sorted[n_train:])
    train_rmse = float(np.sqrt(np.mean(train_resid ** 2)))
    test_rmse = float(np.sqrt(np.mean(test_resid ** 2)))
    ratio = test_rmse / train_rmse if train_rmse > 0 else 1.0
    log.info(f"  train RMSE: {train_rmse:.4f}")
    log.info(f"  test  RMSE: {test_rmse:.4f}")
    log.info(f"  RMSE 比: {ratio:.2f}")
    log.info(f"  → 比值 1.0-1.2 = 健康, 1.2-1.5 = 轻微过拟合, >1.5 = 严重过拟合")

    # ═══════════════════════════════════════
    # 诊断 4: 特征重要度稳定性 (5-fold)
    # ═══════════════════════════════════════
    log.info("\n" + "=" * 60)
    log.info("诊断 4: 特征重要度稳定性 (5-fold)")
    log.info("=" * 60)

    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    imp_per_fold = []
    for fold_idx, (tr_idx, te_idx) in enumerate(kf.split(X_sorted)):
        gbr = GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
        gbr.fit(X_sorted[tr_idx], y_sorted[tr_idx])
        imp_per_fold.append(gbr.feature_importances_)

    imp_arr = np.array(imp_per_fold)
    top10_per_fold = []
    for i in range(5):
        top10 = set(np.argsort(-imp_arr[i])[:10])
        top10_per_fold.append(top10)
        log.info(f"  fold {i+1} top-10: {[feature_names[j] for j in top10]}")

    # 计算 fold 间 top-10 重叠 (Jaccard)
    overlaps = []
    for i in range(5):
        for j in range(i + 1, 5):
            inter = len(top10_per_fold[i] & top10_per_fold[j])
            union = len(top10_per_fold[i] | top10_per_fold[j])
            overlaps.append(inter / union if union > 0 else 0)
    log.info(f"\n  top-10 Jaccard 相似度 (5 folds): {np.mean(overlaps):.2f}")
    log.info(f"  → > 0.6 = 特征稳定, 0.4-0.6 = 中等, < 0.4 = 不稳定")

    # ═══════════════════════════════════════
    # 诊断 5: 调参敏感性
    # ═══════════════════════════════════════
    log.info("\n" + "=" * 60)
    log.info("诊断 5: 调参敏感性 (n_estimators × max_depth)")
    log.info("=" * 60)

    configs = [
        (100, 3, 0.1),
        (200, 3, 0.05),
        (300, 4, 0.05),
        (500, 4, 0.05),
        (300, 5, 0.05),
        (500, 6, 0.03),
    ]
    for n_est, d, lr in configs:
        train_ics, test_ics = [], []
        for tr_idx, te_idx in kf.split(X_sorted):
            gbr = GradientBoostingRegressor(n_estimators=n_est, max_depth=d, learning_rate=lr, random_state=42)
            gbr.fit(X_sorted[tr_idx], y_sorted[tr_idx])
            train_ics.append(float(pd.Series(gbr.predict(X_sorted[tr_idx])).rank().corr(pd.Series(y_sorted[tr_idx]).rank())))
            test_ics.append(float(pd.Series(gbr.predict(X_sorted[te_idx])).rank().corr(pd.Series(y_sorted[te_idx]).rank())))
        log.info(f"  n={n_est:3d} d={d} lr={lr:.2f}  "
                 f"train_IC={np.mean(train_ics):+.4f}  test_IC={np.mean(test_ics):+.4f}  "
                 f"gap={np.mean(train_ics) - np.mean(test_ics):+.4f}")

    # ═══════════════════════════════════════
    # 诊断 6: Learning curve (训练样本 vs IC)
    # ═══════════════════════════════════════
    log.info("\n" + "=" * 60)
    log.info("诊断 6: Learning curve (训练样本量 vs test IC)")
    log.info("=" * 60)

    n_total = len(X_sorted)
    for ratio in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        n_use = int(n_total * ratio)
        # 用前 n_use 训练, 后 30% 测试
        n_test_start = int(n_total * 0.7)
        ics = []
        for tr_idx, te_idx in KFold(n_splits=3, shuffle=True, random_state=42).split(X_sorted[:n_use]):
            gbr = GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
            gbr.fit(X_sorted[:n_use][tr_idx], y_sorted[:n_use][tr_idx])
            test_preds = gbr.predict(X_sorted[n_test_start:])
            ics.append(float(pd.Series(test_preds).rank().corr(pd.Series(y_sorted[n_test_start:]).rank())))
        log.info(f"  n_train={n_use:5d} ({ratio:.0%})  test_IC={np.mean(ics):+.4f} ± {np.std(ics):.4f}")

    log.info("\n" + "=" * 60)
    log.info("诊断总结")
    log.info("=" * 60)
    log.info("如果所有指标都显示稳定 → R116 GBR 是健康的")
    log.info("如果指标显示不稳 → 需重新训练 (更小模型/更强正则/更少特征)")


if __name__ == "__main__":
    main()