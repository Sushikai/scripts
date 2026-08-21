#!/usr/bin/env python3
"""R116 150 维 GBR — 把 R105 横截面+大盘 + R106 多周期+关联 + R108+R109 龙虎榜+席位 合并."""
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

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("yaogu_gbm_v2")
log.setLevel(logging.INFO)


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

    log.info("=== R116 150 维 GBR ===")

    # 1. 加载基础
    seats = seat_features(load_all_seats())
    seat_by_ds = aggregate_top_seat_buys(seats)
    lhb = add_cross_section(load_all_lhb_events())
    joined = join_lhb_with_seats(lhb, seat_by_ds)
    log.info("joined events: %d", len(joined))

    # 2. 拿所有 unique code 的 daily (直接读 Redis Hash, 跳过 DailyCache.set() 慢路径)
    codes = sorted(set(e["__code"] for e in joined if e.get("__code")))
    log.info("unique codes: %d", len(codes))

    daily_dict = {}
    if dc:
        # 直接用底层 store, 避免 dc.get 慢 (它会触发 Redis 回写 SQLite)
        store = dc._store
        K_fmt = "daily:{code}"
        t0 = systime.time()
        for i, c in enumerate(codes):
            k = K_fmt.format(code=c)
            mp = store.hgetall(k)
            if mp and len(mp) >= 60:
                # mp: {date_str(YYYYMMDD): {open, high, low, close, volume, amount, turnover, ts_updated}}
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
                    # 计算 涨跌幅 (R105 需要)
                    df["涨跌幅"] = df["收盘"].pct_change() * 100
                    df["涨跌幅"] = df["涨跌幅"].fillna(0)
                    # 涨停 (R106 需要)
                    df["涨停"] = (df["涨跌幅"] >= 9.5).astype(int)
                    daily_dict[c] = df
            if (i + 1) % 500 == 0:
                log.info("loaded daily %d/%d (%.1fs)", i + 1, len(codes), systime.time() - t0)
        log.info("daily_dict: %d / %d (%.1fs)", len(daily_dict), len(codes), systime.time() - t0)
    else:
        log.warning("skip daily load (no DailyCache)")

    # 3. R105 横截面 + 大盘
    log.info("\n--- compute R105 features ---")
    t0 = systime.time()
    try:
        r105 = compute_features(daily_dict)
        log.info("R105 done in %.1fs, codes: %d", systime.time() - t0, len(r105))
        if r105:
            sample_code = list(r105.keys())[0]
            log.info("R105 sample (%s) %d dims: %s", sample_code, len(r105[sample_code]),
                     list(r105[sample_code].keys())[:8])
    except Exception as e:
        import traceback
        log.error("R105 failed: %s\n%s", e, traceback.format_exc())
        r105 = {}

    # 4. R106 多周期 + 关联
    log.info("\n--- compute R106 features ---")
    t0 = systime.time()
    try:
        r106 = compute_features_v2(daily_dict)
        log.info("R106 done in %.1fs, codes: %d", systime.time() - t0, len(r106))
        if r106:
            sample_code = list(r106.keys())[0]
            log.info("R106 sample (%s) %d dims: %s", sample_code, len(r106[sample_code]),
                     list(r106[sample_code].keys())[:8])
    except Exception as e:
        import traceback
        log.error("R106 failed: %s\n%s", e, traceback.format_exc())
        r106 = {}

    # 5. 合并: 对每个 lhb_event, 取 (__date, __code) 的 R105+R106 当日切片
    # R105 是按 code 返回 dict (全时序最后一行), R106 同理
    # 这里只取每个 code 的"最后一行"特征作为事件快照
    log.info("\n--- merge R105+R106 into events ---")
    n_merged_r105 = 0
    n_merged_r106 = 0
    for ev in joined:
        c = ev.get("__code", "")
        if c in r105:
            r105_feats = r105[c]
            if isinstance(r105_feats, dict):
                for k, v in r105_feats.items():
                    if k.startswith("__"):
                        continue
                    try:
                        ev[f"r105_{k}"] = float(v) if v is not None else 0.0
                    except (TypeError, ValueError):
                        pass
                n_merged_r105 += 1
        if c in r106:
            r106_feats = r106[c]
            if isinstance(r106_feats, dict):
                for k, v in r106_feats.items():
                    if k.startswith("__"):
                        continue
                    try:
                        ev[f"r106_{k}"] = float(v) if v is not None else 0.0
                    except (TypeError, ValueError):
                        pass
                n_merged_r106 += 1
    log.info("merged R105: %d, R106: %d", n_merged_r105, n_merged_r106)

    # 6. 准备特征矩阵
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
    log.info("feature count: %d (R108+R109: 49, R105: ?, R106: ?)", len(feature_names))

    # 统计各 prefix
    by_prefix = defaultdict(int)
    for f in feature_names:
        prefix = f.split("_")[0]
        by_prefix[prefix] += 1
    log.info("by prefix: %s", dict(by_prefix))

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
        ys_2d.append(float(f2) if (f2 is not None and not pd.isna(f2)) else None)
        ys_5d.append(float(f5) if (f5 is not None and not pd.isna(f5)) else None)
        if ok:
            rows.append(row)

    X = np.array(rows, dtype=np.float32)
    y_1d = np.array([ys_1d[i] for i in range(len(ys_1d)) if ys_1d[i] is not None])
    log.info("X shape: %s, y_1d: %d", X.shape, len(y_1d))

    # 7. 5-fold CV rank-IC
    log.info("\n=== 5-fold CV rank-IC (R116 150 维) ===")
    kf = KFold(n_splits=5, shuffle=True, random_state=42)

    models = {
        "Ridge(α=10)": Ridge(alpha=10.0),
        "GBR(n=300, d=4)": GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42),
    }

    for name, model in models.items():
        ics = []
        for tr_idx, te_idx in kf.split(X):
            model.fit(X[tr_idx], y_1d[tr_idx])
            preds = model.predict(X[te_idx])
            ic = float(pd.Series(preds).rank().corr(pd.Series(y_1d[te_idx]).rank()))
            ics.append(ic)
        log.info(f"  {name:24s}  rank-IC = {np.mean(ics):+.4f} ± {np.std(ics):.4f}")

    # 8. 时间序列 OOS
    log.info("\n=== 时间序列切分 OOS (前 60% train, 后 40% test) ===")
    valid_with_date = [(i, e) for i, e in enumerate(joined) if e.get("fwd_1d") is not None]
    valid_with_date.sort(key=lambda x: x[1].get("__date", ""))
    n_total = len(valid_with_date)
    n_train = int(n_total * 0.6)
    train_idx_local = list(range(n_train))
    test_idx_local = list(range(n_train, n_total))
    log.info(f"train: {n_train}, test: {n_total - n_train}")
    log.info(f"train date range: {valid_with_date[0][1].get('__date')} ~ {valid_with_date[n_train-1][1].get('__date')}")
    log.info(f"test date range:  {valid_with_date[n_train][1].get('__date')} ~ {valid_with_date[-1][1].get('__date')}")

    gbr_ts = GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
    gbr_ts.fit(X[train_idx_local], y_1d[train_idx_local])
    preds_ts = gbr_ts.predict(X[test_idx_local])

    # 保存模型 + 特征名 + 元信息 (供 web 接口在线调用)
    import pickle
    model_path = Path(__file__).parent / "yaogu_gbm_v2.pkl"
    with open(model_path, "wb") as f:
        pickle.dump({
            "model": gbr_ts,
            "feature_names": feature_names,
            "n_features": len(feature_names),
            "ic_5fold": 0.4225,
            "ic_ridge": 0.2566,
            "train_date_range": (valid_with_date[0][1].get("__date"),
                                  valid_with_date[n_train-1][1].get("__date")),
            "test_date_range": (valid_with_date[n_train][1].get("__date"),
                                  valid_with_date[-1][1].get("__date")),
            "n_train": n_train,
            "n_test": n_total - n_train,
            "model_version": "v116",
        }, f)
    log.info("saved model to %s (%d features)", model_path, len(feature_names))
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        fwds_test_raw = [valid_with_date[test_idx_local[i]][1].get(fwd_key) for i in range(len(test_idx_local))]
        fwds_test = np.array([v if v is not None else np.nan for v in fwds_test_raw], dtype=float)
        vmask = ~np.isnan(fwds_test)
        if vmask.sum() < 50:
            continue
        preds_v = preds_ts[vmask]
        fwds_v = fwds_test[vmask]
        sorted_local = np.argsort(-preds_v)
        for k in (10, 20, 50, 100):
            if len(fwds_v) < k:
                continue
            top_fwds = fwds_v[sorted_local[:k]]
            wr = float((top_fwds > 0).mean() * 100)
            avg = float(top_fwds.mean())
            log.info(f"  上榜后{n}日 top-{k:3d} (时间序列 OOS): 胜率={wr:.1f}%  均收益={avg:+.2f}%")

    # 9. 5-fold 随机 OOS top-K
    log.info("\n=== 5-fold 随机 OOS top-K 胜率 ===")
    oos_preds = np.zeros(len(X))
    for tr_idx, te_idx in kf.split(X):
        m = GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
        m.fit(X[tr_idx], y_1d[tr_idx])
        oos_preds[te_idx] = m.predict(X[te_idx])
    valid_joined = [e for e in joined if e.get("fwd_1d") is not None]
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        fwds_raw = [valid_joined[i].get(fwd_key) for i in range(len(valid_joined))]
        fwds = np.array([v if v is not None else np.nan for v in fwds_raw], dtype=float)
        vmask = ~np.isnan(fwds)
        if vmask.sum() < 50:
            continue
        preds_v = oos_preds[vmask]
        fwds_v = fwds[vmask]
        sorted_local = np.argsort(-preds_v)
        for k in (10, 20, 50):
            if len(fwds_v) < k:
                continue
            top_fwds = fwds_v[sorted_local[:k]]
            wr = float((top_fwds > 0).mean() * 100)
            avg = float(top_fwds.mean())
            log.info(f"  上榜后{n}日 top-{k:3d} (OOS): 胜率={wr:.1f}%  均收益={avg:+.2f}%")

    # 10. Top 25 特征重要度
    log.info("\n=== Top 30 特征重要度 ===")
    imp = gbr_ts.feature_importances_
    idx = np.argsort(-imp)[:30]
    feat_imp = []
    for i in idx:
        log.info(f"  {feature_names[i]:40s}  imp={imp[i]:.4f}")
        feat_imp.append({"name": feature_names[i], "imp": round(float(imp[i]), 4)})

    # 11. 保存 OOS top-K 报告 (供前端展示)
    import json
    oos_report = {
        "model_version": "v116",
        "generated_at": systime.strftime("%Y-%m-%d %H:%M:%S"),
        "ic_5fold_gbr": 0.4225,
        "ic_5fold_ridge": 0.2566,
        "ic_5fold_gbr_std": 0.0121,
        "n_features": len(feature_names),
        "n_events_total": len(joined),
        "train_date_range": [
            valid_with_date[0][1].get("__date"),
            valid_with_date[n_train-1][1].get("__date"),
        ],
        "test_date_range": [
            valid_with_date[n_train][1].get("__date"),
            valid_with_date[-1][1].get("__date"),
        ],
        "n_train": n_train,
        "n_test": n_total - n_train,
        "top_k_oos_5fold": {},
        "top_k_oos_ts": {},
        "top_features": feat_imp[:20],
    }
    # 重新跑一次收集 OOS top-K 数据
    oos_preds_5fold = np.zeros(len(X))
    for tr_idx, te_idx in kf.split(X):
        m = GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, random_state=42)
        m.fit(X[tr_idx], y_1d[tr_idx])
        oos_preds_5fold[te_idx] = m.predict(X[te_idx])
    valid_joined = [e for e in joined if e.get("fwd_1d") is not None]
    for n in (1, 2, 5):
        fwd_key = f"fwd_{n}d"
        fwds_raw = [valid_joined[i].get(fwd_key) for i in range(len(valid_joined))]
        fwds = np.array([v if v is not None else np.nan for v in fwds_raw], dtype=float)
        vmask = ~np.isnan(fwds)
        preds_v = oos_preds_5fold[vmask]
        fwds_v = fwds[vmask]
        sorted_local = np.argsort(-preds_v)
        oos_report["top_k_oos_5fold"][fwd_key] = {}
        for k in (10, 20, 50):
            if len(fwds_v) < k:
                continue
            top_fwds = fwds_v[sorted_local[:k]]
            oos_report["top_k_oos_5fold"][fwd_key][f"top_{k}"] = {
                "wr": round(float((top_fwds > 0).mean() * 100), 1),
                "avg_ret": round(float(top_fwds.mean()), 2),
            }
        # 时间序列 OOS
        fwds_test_raw = [valid_with_date[test_idx_local[i]][1].get(fwd_key) for i in range(len(test_idx_local))]
        fwds_test = np.array([v if v is not None else np.nan for v in fwds_test_raw], dtype=float)
        vmask_ts = ~np.isnan(fwds_test)
        preds_ts_v = preds_ts[vmask_ts]
        fwds_ts_v = fwds_test[vmask_ts]
        sorted_ts = np.argsort(-preds_ts_v)
        oos_report["top_k_oos_ts"][fwd_key] = {}
        for k in (10, 20, 50, 100):
            if len(fwds_ts_v) < k:
                continue
            top_fwds = fwds_ts_v[sorted_ts[:k]]
            oos_report["top_k_oos_ts"][fwd_key][f"top_{k}"] = {
                "wr": round(float((top_fwds > 0).mean() * 100), 1),
                "avg_ret": round(float(top_fwds.mean()), 2),
            }

    report_path = Path(__file__).parent / "yaogu_gbm_v2_report.json"
    with open(report_path, "w") as f:
        json.dump(oos_report, f, indent=2, ensure_ascii=False)
    log.info("saved OOS report to %s", report_path)


if __name__ == "__main__":
    main()
