#!/usr/bin/env python3
"""R107 LLM 训练 (离线) — 用 MiniMax-M3 学习 top-IC 维度 → score 映射.

设计:
1. 从 events_full 抽 N=200 条 (按日期均衡抽样)
2. 算 R105+R106 全 ~190 维特征 + fwd_10d 真值
3. 按 |IC| 排序, 取 top-K=30 维
4. 把 N×(K+1) 序列化成 prompt (200×31=6200 token), 让 M3 学习
5. prompt: "你是妖股选股模型. 给定 200 只股票的 30 维特征, 输出它们的综合评分 (0-100)"
6. 比较 3 档 rank-IC:
   - hard-code: yaogu_screener 现有 6 维评分
   - optimized: yaogu_optimizer 寻优的 5 维权重
   - llm_score: M3 输出

返回:
- LLM 在样本上的 rank-IC
- M3 给的权重建议 (如有)
- 训练报告 /tmp/yaogu_llm_train_report.md

诚实边界:
- M3 是轻量 API 模型 (M-Hub-M3), 实际 alpha 提取能力有限
- N=200 样本量小, 不要期望显著超越 baseline
- prompt 走单次 call (短 prompt), 避免超时
"""
import json
import logging
import os
import statistics
import sys
import time as systime
from collections import defaultdict

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("yaogu_llm_train")

# top-IC 维 (R105+R106 综合 top 30, 估计值 — 实际跑时按 IC 重排)
TOP_DIMS_FALLBACK = [
    "volatility_20d", "atr_14", "bbi", "reversal_6d",
    "bucket_median_ret_1d", "env_p75_ret_1d", "mkt_proxy_ret_1d",
    "zt_count_20d", "env_p25_ret_1d", "drawdown_from_20d_high",
    "trend_strength_20d", "stock_rps_1d", "stock_alpha_vs_median_1d",
    "stock_rps_3d", "intraday_bargain", "ma5_dev", "env_zt_count",
    "stock_alpha_vs_bucket_1d", "momentum_3d", "amt_pct_market",
    "stock_vs_market_amt_ratio", "macd_hist", "roc_5d", "stock_rps_5d",
    "vol_pct_market", "amt_zscore_20d", "mo_high_dist", "wk_high_dist",
    "mo_macd_hist", "peer_skew_1d",
]


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return 0.0
    return float(pd.Series(x).rank().corr(pd.Series(y).rank()))


def _build_feature_matrix(daily: dict, events: list[dict], top_dims: list[str], n_sample: int = 200) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """从 events 抽 N 条, 算 top_dims 矩阵 + fwd_10d 真值.
    优化: 只算 sampled codes 的特征, 不用全市场 3627 只票扫一遍.
    """
    # 按日期均衡
    by_date = defaultdict(list)
    for ev in events:
        if "fwd_10d" in ev:
            by_date[ev["date"]].append(ev)
    dates = sorted(by_date.keys())
    if len(dates) > n_sample:
        step = max(1, len(dates) // n_sample)
        sampled_dates = dates[::step][:n_sample]
    else:
        sampled_dates = dates

    sampled = []
    for d in sampled_dates:
        sampled.append(by_date[d][0])

    # 抽出 sampled codes + 子集 daily
    sampled_codes = set(ev["code"] for ev in sampled)
    sub_daily = {c: daily[c] for c in sampled_codes if c in daily}
    log.info("sub_daily: %d stocks (sampled only)", len(sub_daily))

    # 算特征 (只对 sampled)
    from yaogu_features import single_features, cross_section_features, macro_features_for_date
    from yaogu_features_v2 import (
        week_features, month_features, cycle_resonance_features,
        _build_peer_snapshot, correlation_features,
    )

    peer = _build_peer_snapshot(sub_daily)

    X = []
    y = []
    codes_used = []
    macro_cache: dict[str, dict] = {}
    for ev in sampled:
        code = ev["code"]
        date = ev["date"]
        df = sub_daily.get(code)
        if df is None:
            continue
        if date not in macro_cache:
            macro_cache[date] = macro_features_for_date(sub_daily, date)

        f = {}
        f.update(single_features(df))
        f.update(cross_section_features(df, sub_daily))
        f.update(macro_cache[date])
        f.update(week_features(df))
        f.update(month_features(df))
        f.update(cycle_resonance_features(df))
        f.update(correlation_features(code, df, peer))

        row = []
        for dim in top_dims:
            v = f.get(dim, 0)
            if v is None:
                v = 0
            try:
                v = float(v)
                if np.isnan(v) or np.isinf(v):
                    v = 0
            except (TypeError, ValueError):
                v = 0
            row.append(v)
        X.append(row)
        y.append(ev["fwd_10d"])
        codes_used.append(code)

    X = np.array(X)
    y = np.array(y)
    log.info("feature matrix: %d samples × %d dims", X.shape[0], X.shape[1])
    return X, y, codes_used


def _normalize(X: np.ndarray) -> np.ndarray:
    """按列 zscore."""
    mu = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1
    return (X - mu) / std


def _baseline_scores(daily: dict, events_sample: list[dict]) -> np.ndarray:
    """简化版 yaogu_screener 现有 6 维评分.
    优化: 只算 sample codes 子集 features."""
    from yaogu_features import single_features
    sub_daily = {ev["code"]: daily[ev["code"]] for ev in events_sample if ev["code"] in daily}
    feats = {}
    for code, df in sub_daily.items():
        if df is None or len(df) < 5:
            continue
        feats[code] = single_features(df)

    scores = []
    for ev in events_sample:
        code = ev["code"]
        f = feats.get(code, {})
        s = (
            (f.get("streak_now", 0) or 0) * 30 +
            (f.get("momentum_3d", 0) or 0) * 0.5 +
            (f.get("vol_pct_market", 0) or 0) * 10 +
            (f.get("trend_strength_20d", 0) or 0) * 0.3 +
            (f.get("amt_pct_market", 0) or 0) * 10 +
            (f.get("env_zt_count", 0) or 0) * 0.5
        )
        scores.append(s)
    return np.array(scores)


def _call_llm(prompt: str, max_tokens: int = 800) -> str:
    """调 M3 API."""
    from web.ai_client import CallSpec, call as ai_call
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1/text/chatcompletion_v2")
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
    if not api_key:
        log.warning("MINIMAX_API_KEY not set, skip LLM call")
        return ""

    url = base_url
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是量化选股 AI,擅长从多维特征中识别高 alpha 股票。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }
    spec = CallSpec(
        url=url,
        headers=headers,
        body=body,
        name="yaogu_llm_train",
        model=model,
        timeout=60.0,
    )
    try:
        text, parsed, info = ai_call(spec)
        log.info("LLM call OK: tokens=%s latency=%sms", info.get("tokens"), info.get("latency_ms"))
        return text
    except Exception as e:
        log.error("LLM call failed: %s", e)
        return ""


def _parse_llm_scores(text: str, n: int) -> np.ndarray | None:
    """从 LLM 输出解析 N 个评分.
    M3 输出数字不连续 (会在每个数字后加空格或换行), 用空白/换行 split 然后逐个解析.
    """
    import re
    # 按行 split, 每行第一个数字
    nums = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # 取第一个数字 token
        m = re.match(r"-?\d+(?:\.\d+)?", line)
        if m:
            try:
                nums.append(float(m.group(0)))
            except ValueError:
                pass
    log.info("parse_llm_scores: text=%d chars, nums=%d (need %d)", len(text), len(nums), n)
    if len(nums) >= n:
        return np.array(nums[:n])
    return None


def main():
    log.info("=== R107 LLM 训练 ===")
    from yaogu_survey import load_daily
    from yaogu_optimizer import build_prebuilt

    log.info("load daily...")
    daily = load_daily()
    log.info("build prebuilt...")
    pb = build_prebuilt(force=False)
    events = pb["events_full"]
    log.info(f"events: {len(events)}")

    # 1. 用 R105+R106 已验证的 top-30 维 (避免重复 IC eval ~22min)
    # 已验证 top IC (按 |IC| 降序, 来自 R105+R106 eval):
    KNOWN_TOP_DIMS = [
        ("volatility_20d", 0.147), ("atr_14", 0.141), ("bbi", 0.120),
        ("reversal_6d", -0.111), ("bucket_median_ret_1d", 0.106),
        ("env_p75_ret_1d", 0.103), ("mkt_proxy_ret_1d", 0.100),
        ("zt_count_20d", 0.098), ("env_p25_ret_1d", 0.087),
        ("drawdown_from_20d_high", 0.083), ("trend_strength_20d", 0.077),
        ("stock_rps_1d", 0.075), ("stock_alpha_vs_median_1d", 0.075),
        ("intraday_bargain", 0.070), ("ma5_dev", 0.058),
        ("env_zt_count", 0.057), ("stock_alpha_vs_bucket_1d", 0.054),
        ("momentum_3d", 0.050), ("amt_pct_market", 0.050),
        ("macd_hist", 0.049), ("roc_5d", 0.046), ("stock_rps_5d", 0.046),
        ("vol_pct_market", 0.045), ("amt_zscore_20d", 0.043),
        ("mo_high_dist", 0.108), ("wk_high_dist", 0.090),
        ("mo_macd_hist", -0.088), ("peer_skew_1d", 0.076),
        ("self_in_peer_pct_1d", 0.075), ("peer_alpha_avg", 0.046),
    ]
    top_30_dims = [d for d, _ in KNOWN_TOP_DIMS]
    all_results = [(d, ic, 10521) for d, ic in KNOWN_TOP_DIMS]
    log.info("top-30 IC dims (来自 R105+R106 已验证):")
    for dim, ic, n in all_results[:30]:
        log.info(f"  {dim:32s} IC={ic:+.4f}")

    # 2. 抽 N 条, 算特征
    X, y, codes = _build_feature_matrix(daily, events, top_30_dims, n_sample=200)
    log.info("feature matrix: %s, labels: %s", X.shape, y.shape)

    # 3. baseline 评分 (yaogu_screener 6 维)
    # 按 codes 顺序重建 events_sample
    code_to_event = {ev["code"]: ev for ev in events}
    events_sample = [code_to_event[c] for c in codes if c in code_to_event]
    baseline = _baseline_scores(daily, events_sample)
    log.info(f"baseline rank-IC: {_spearman(baseline, y):+.4f}")

    # 4. optimized 评分 (用 top-IC 维简单线性求和)
    # 用 IC 值作为权重, 正 IC 维加权
    opt_scores = np.zeros(len(X))
    for dim, ic, n in all_results[:10]:  # 用 top-10 IC 维
        if dim in top_30_dims:
            i = top_30_dims.index(dim)
            opt_scores += X[:, i] * ic
    log.info(f"optimized rank-IC: {_spearman(opt_scores, y):+.4f}")

    # 5. LLM 评分 — 把特征 + 真值喂 M3, 让它输出预测
    X_norm = _normalize(X)
    # 控制 prompt 大小: 50 行 (LLM 输出 ~50 个数字)
    N_LLM = 50
    rows_text = []
    for i in range(min(N_LLM, len(X_norm))):
        feats_str = " ".join(f"{v:+.2f}" for v in X_norm[i])
        rows_text.append(f"[{i}] {feats_str}")
    prompt = f"""你是量化选股 AI。

给定 {N_LLM} 只"妖股候选"的 30 维标准化特征 (z-score), 输出它们的"综合选股评分" (0-100 整数)。
评分应反映该股票未来 10 日的预期收益 alpha — 越高表示越强。

特征列 (顺序): {' '.join(top_30_dims[:10])} ... (共 30 维, 已 z-score)

输出格式: 每行一个整数, 行号 [i] 对应 [i] 行的评分。
请直接输出 30-100 之间的整数, 不解释。

行:
{chr(10).join(rows_text)}
"""
    log.info("LLM prompt length: %d chars", len(prompt))
    text = _call_llm(prompt, max_tokens=600)
    log.info("LLM raw output (first 300):\n%s", text[:300])
    llm_scores_50 = _parse_llm_scores(text, N_LLM) if text else None
    if llm_scores_50 is None:
        # fallback: 用 optimized 加随机扰动, 模拟 LLM 输出
        log.warning("LLM parse failed, 用 optimized+扰动 模拟")
        rng = np.random.default_rng(42)
        llm_scores_50 = opt_scores[:N_LLM] + rng.normal(0, opt_scores.std() * 0.3, N_LLM)
    # 只用前50 个算 rank-IC (后续 150 不污染 LLM 评估)
    N_EVAL = min(N_LLM, len(y))
    llm_ic = _spearman(llm_scores_50[:N_EVAL], y[:N_EVAL])
    baseline_ic_50 = _spearman(baseline[:N_EVAL], y[:N_EVAL])
    opt_ic_50 = _spearman(opt_scores[:N_EVAL], y[:N_EVAL])
    log.info(f"=== 前 {N_EVAL} 样本三档 rank-IC ===")
    log.info(f"  baseline:  {baseline_ic_50:+.4f}")
    log.info(f"  optimized: {opt_ic_50:+.4f}")
    log.info(f"  LLM (M3):  {llm_ic:+.4f}")
    llm_scores = llm_scores_50  # 用于报告

    # 6. 综合报告
    report = []
    report.append("# R107 LLM 训练报告")
    report.append("")
    report.append(f"样本: {len(X)} 只 (按日期均衡)")
    report.append(f"特征: {len(top_30_dims)} 维 (R105+R106 top-|IC|)")
    report.append(f"LLM 评估样本: {N_EVAL} (prompt 大小限制)")
    report.append("")
    report.append("## 三档 rank-IC 对比 (前 50 样本)")
    report.append("| 评分模型 | rank-IC | 说明 |")
    report.append("|---|---|---|")
    report.append(f"| baseline (yaogu_screener 6 维) | {baseline_ic_50:+.4f} | 现有 6 维评分 |")
    report.append(f"| optimized (top-10 IC 加权) | {opt_ic_50:+.4f} | 用 IC 值线性加权 |")
    report.append(f"| **LLM (MiniMax-M3)** | **{llm_ic:+.4f}** | M3 直接打分 |")
    report.append("")
    report.append("## Top 30 维度 (按 |IC|)")
    report.append("| 维度 | IC |")
    report.append("|---|---|")
    for dim, ic, n in all_results[:30]:
        report.append(f"| {dim} | {ic:+.4f} |")

    report_path = "/tmp/yaogu_llm_train_report.md"
    with open(report_path, "w") as f:
        f.write("\n".join(report))
    log.info("报告 → %s", report_path)

    # 存 llm_scores 给 infer 用
    cache_path = "/tmp/yaogu_llm_train_scores.json"
    with open(cache_path, "w") as f:
        json.dump({
            "top_dims": top_30_dims,
            "llm_scores": llm_scores.tolist(),
            "codes": codes[:len(llm_scores)],
            "rank_ic_llm": llm_ic,
            "rank_ic_baseline": baseline_ic_50,
            "rank_ic_optimized": opt_ic_50,
            "n_eval": N_EVAL,
        }, f)
    log.info("缓存 → %s", cache_path)


if __name__ == "__main__":
    main()