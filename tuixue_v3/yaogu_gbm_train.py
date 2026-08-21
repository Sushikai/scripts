#!/usr/bin/env python3
"""R115 LGBM 评分 — 用 sklearn GBR 在 150+ 维上训练, 验证非线性是否能超过 R114 线性 IC +0.247."""
import json
import logging
import re
import statistics
import sys
import time as systime
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge, Lasso
from sklearn.model_selection import KFold

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("yaogu_gbm")


def main():
    sys.path.insert(0, str(Path(__file__).parent))
    from yaogu_seat_features import (
        load_all_seats, seat_features, aggregate_top_seat_buys,
        join_lhb_with_seats, combined_score, combined_score_r110,
        combined_score_r111, combined_score_r114,
        build_seat_alpha_rolling,
        build_composite_alpha,
    )
    from yaogu_lhb_features import load_all_lhb_events, add_cross_section

    log.info("=== R115 LGBM/GBR 训练 ===")

    # 1. 加载 168 天全量数据
    seats = seat_features(load_all_seats())
    seat_by_ds = aggregate_top_seat_buys(seats)
    lhb = add_cross_section(load_all_lhb_events())
    joined = join_lhb_with_seats(lhb, seat_by_ds)
    log.info("joined events: %d", len(joined))

    # 2. 准备特征矩阵
    # 数值维度 (排除 fwd_*, __*, lhb_score*, combined_* 等 output)
    SKIP_PREFIXES = (
        "fwd_", "__", "seat_labels",
        "reason_text", "interp_has_famous",  # bool 派生
        "lhb_score", "combined_score",
    )

    feature_names = []
    for k in joined[0].keys():
        if k.startswith(SKIP_PREFIXES):
            continue
        # 检查所有 ev 都有这个 key 且是数值
        vals = [e.get(k) for e in joined if e.get(k) is not None]
        if not vals:
            continue
        try:
            float(vals[0])
        except (TypeError, ValueError):
            continue
        feature_names.append(k)
    log.info("feature count: %d", len(feature_names))

    # 3. 构建 X, y
    rows = []
    ys_1d, ys_2d, ys_5d = [], [], []
    for e in joined:
        row = []
        ok = True
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
        f2 = e.get("fwd_2d")
        f5 = e.get("fwd_5d")
        if f1 is None or pd.isna(f1):
            ok = False
            ys_1d.append(None)
        else:
            ys_1d.append(float(f1))
        if f2 is None or pd.isna(f2):
            ys_2d.append(None)
        else:
            ys_2d.append(float(f2))
        if f5 is None or pd.isna(f5):
            ys_5d.append(None)
        else:
            ys_5d.append(float(f5))
        if ok:
            rows.append(row)

    X = np.array(rows, dtype=np.float32)
    log.info("X shape: %s", X.shape)

    # 4. 5 折交叉验证: 训练 GBR / RF / Ridge / 线性 baseline, 比 rank-IC
    log.info("\n=== 5-fold CV rank-IC (目标 = 上榜后1日收益) ===")
    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    models = {
        "Ridge(α=1)": Ridge(alpha=1.0),
        "Ridge(α=10)": Ridge(alpha=10.0),
        "GBR(n=100, d=3)": GradientBoostingRegressor(n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42),
        "GBR(n=300, d=4)": GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42),
        "RF(n=200, d=8)": RandomForestRegressor(n_estimators=200, max_depth=8, random_state=42, n_jobs=-1),
    }
    valid_mask = np.array([y is not None for y in ys_1d])
    # X 已经是 valid_mask 过滤后的, X_valid 直接是 X
    X_valid = X
    y_valid = np.array([ys_1d[i] for i in range(len(ys_1d)) if ys_1d[i] is not None])
    log.info("valid samples: %d", len(X_valid))

    for name, model in models.items():
        ics = []
        for tr_idx, te_idx in kf.split(X_valid):
            model.fit(X_valid[tr_idx], y_valid[tr_idx])
            preds = model.predict(X_valid[te_idx])
            ic = float(pd.Series(preds).rank().corr(pd.Series(y_valid[te_idx]).rank()))
            ics.append(ic)
        mean_ic = np.mean(ics)
        std_ic = np.std(ics)
        log.info(f"  {name:24s}  rank-IC = {mean_ic:+.4f} ± {std_ic:.4f}")

    # 5. 同验证上榜后 2 日 / 5 日 (用同样 12074 个有 fwd_1d 的样本, 但要看 fwd_2d/fwd_5d 是否齐全)
    for n_fwd in (2, 5):
        log.info(f"\n=== 5-fold CV rank-IC (上榜后{n_fwd}日) ===")
        y_target = (ys_2d if n_fwd == 2 else ys_5d)
        # X 是 12074 行 (fwd_1d 都不为空), 但 fwd_2d/fwd_5d 可能为空
        vmask_in = np.array([y_target[i] is not None for i in range(len(ys_1d)) if ys_1d[i] is not None])
        Xv = X[vmask_in]
        yv = np.array([y_target[i] for i in range(len(y_target)) if y_target[i] is not None and ys_1d[i] is not None])
        log.info("valid samples: %d", len(Xv))
        gbr = GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
        ics = []
        for tr_idx, te_idx in kf.split(Xv):
            gbr.fit(Xv[tr_idx], yv[tr_idx])
            preds = gbr.predict(Xv[te_idx])
            ic = float(pd.Series(preds).rank().corr(pd.Series(yv[te_idx]).rank()))
            ics.append(ic)
        log.info(f"  GBR(n=300, d=4) 上榜后{n_fwd}日 rank-IC = {np.mean(ics):+.4f} ± {np.std(ics):.4f}")

    # 6. 训练全量 GBR, 看 top-K 胜率
    log.info("\n=== 全量 GBR 训练 + top-K 胜率回测 (in-sample) ===")
    gbr = GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
    gbr.fit(X_valid, y_valid)
    preds_in = gbr.predict(X_valid)
    sorted_idx_in = np.argsort(-preds_in)
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        fwds = np.array([e[fwd_key] for e in joined if e.get(fwd_key) is not None])
        if len(fwds) < 50:
            continue
        for k in (10, 20, 50):
            if len(fwds) < k:
                continue
            top_fwds = fwds[sorted_idx_in[:k]]
            wr = float((top_fwds > 0).mean() * 100)
            avg = float(top_fwds.mean())
            log.info(f"  上榜后{n}日 top-{k:3d} (in-sample): 胜率={wr:.1f}%  均收益={avg:+.2f}%")

    # 6b. **Out-of-sample top-K 胜率** (用 5-fold CV 的 test fold 预测, 拼接所有 OOS 预测)
    log.info("\n=== OOS 5-fold CV top-K 胜率 (随机切分, 真实泛化能力) ===")
    oos_preds = np.zeros(len(X_valid))
    for tr_idx, te_idx in kf.split(X_valid):
        m = GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
        m.fit(X_valid[tr_idx], y_valid[tr_idx])
        oos_preds[te_idx] = m.predict(X_valid[te_idx])
    sorted_idx_oos = np.argsort(-oos_preds)
    # 关联回 joined 中 fwd_1d 不为空的 events
    valid_joined = [e for e in joined if e.get("fwd_1d") is not None]
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        fwds_raw = [valid_joined[i].get(fwd_key) for i in range(len(valid_joined))]
        fwds = np.array([v if v is not None else np.nan for v in fwds_raw], dtype=float)
        vmask = ~np.isnan(fwds)
        fwds_v = fwds[vmask]
        idx_v = sorted_idx_oos[vmask]
        if len(fwds_v) < 50:
            continue
        for k in (10, 20, 50):
            if len(fwds_v) < k:
                continue
            top_fwds = fwds_v[idx_v[:k]]
            wr = float((top_fwds > 0).mean() * 100)
            avg = float(top_fwds.mean())
            log.info(f"  上榜后{n}日 top-{k:3d} (OOS): 胜率={wr:.1f}%  均收益={avg:+.2f}%")

    # 6c. **时间序列切分 OOS** (train: 2025-12~2026-04, test: 2026-05~2026-08)
    log.info("\n=== 时间序列切分 OOS (前 5 个月训练, 后 3 个月测试) ===")
    valid_with_date = [(i, e) for i, e in enumerate(joined) if e.get("fwd_1d") is not None]
    valid_with_date.sort(key=lambda x: x[1].get("__date", ""))
    # 找 60% / 40% 分界
    n_total = len(valid_with_date)
    n_train = int(n_total * 0.6)
    # valid_with_date[i][0] 是原 joined 索引, 但 X_valid 是按顺序构建的,
    # 所以 X_valid 的索引 = valid_with_date 中的顺序位置
    train_idx_local = list(range(n_train))
    test_idx_local = list(range(n_train, n_total))
    train_idx_orig = [valid_with_date[i][0] for i in train_idx_local]
    test_idx_orig = [valid_with_date[i][0] for i in test_idx_local]
    log.info(f"train: {n_train}, test: {n_total - n_train}")
    log.info(f"train date range: {valid_with_date[0][1].get('__date')} ~ {valid_with_date[n_train-1][1].get('__date')}")
    log.info(f"test date range:  {valid_with_date[n_train][1].get('__date')} ~ {valid_with_date[-1][1].get('__date')}")

    m_ts = GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
    m_ts.fit(X_valid[train_idx_local], y_valid[train_idx_local])
    preds_ts = m_ts.predict(X_valid[test_idx_local])
    sorted_idx_ts = np.argsort(-preds_ts)
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        fwds_test_raw = [valid_with_date[test_idx_local[i]][1].get(fwd_key) for i in range(len(test_idx_local))]
        fwds_test = np.array([v if v is not None else np.nan for v in fwds_test_raw], dtype=float)
        vmask = ~np.isnan(fwds_test)
        fwds_v = fwds_test[vmask]
        idx_v_local = sorted_idx_ts[vmask]
        if len(fwds_v) < 50:
            continue
        for k in (10, 20, 50):
            if len(fwds_v) < k:
                continue
            top_fwds = fwds_v[idx_v_local[:k]]
            wr = float((top_fwds > 0).mean() * 100)
            avg = float(top_fwds.mean())
            log.info(f"  上榜后{n}日 top-{k:3d} (时间序列 OOS): 胜率={wr:.1f}%  均收益={avg:+.2f}%")

    # 7. 与 R114 线性对比 (同样 in-sample)
    log.info("\n=== R114 线性 vs GBR (in-sample, 同样样本) ===")
    rolling_30_1d = build_seat_alpha_rolling(joined, seat_by_ds, window_days=30, forward_n=1)
    rolling_30_2d = build_seat_alpha_rolling(joined, seat_by_ds, window_days=30, forward_n=2)
    comp_alpha_1d = build_composite_alpha(joined, forward_n=1)
    comp_alpha_2d = build_composite_alpha(joined, forward_n=2)

    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        rolling = rolling_30_1d if n == 1 else (rolling_30_2d if n == 2 else rolling_30_1d)
        comp = comp_alpha_1d if n == 1 else (comp_alpha_2d if n == 2 else comp_alpha_1d)
        scored_r114 = []
        for e in joined:
            if e.get(fwd_key) is None:
                continue
            scored_r114.append((combined_score_r114(e, rolling, comp), e[fwd_key]))
        scored_r114.sort(key=lambda x: -x[0])
        for k in (10, 20, 50):
            if len(scored_r114) < k:
                continue
            top = scored_r114[:k]
            fwds = np.array([x[1] for x in top])
            wr = float((fwds > 0).mean() * 100)
            avg = float(fwds.mean())
            log.info(f"  R114 上榜后{n}日 top-{k:3d}: 胜率={wr:.1f}%  均收益={avg:+.2f}%")

    # 8. 特征重要度
    log.info("\n=== Top 25 特征重要度 (GBR) ===")
    imp = gbr.feature_importances_
    idx = np.argsort(-imp)[:25]
    for i in idx:
        log.info(f"  {feature_names[i]:35s}  imp={imp[i]:.4f}")


if __name__ == "__main__":
    main()
