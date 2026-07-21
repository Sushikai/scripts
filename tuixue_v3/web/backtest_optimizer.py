"""
退学 v3 · 尾盘战法自动寻参优化器 (v2 — 纯进程版)
══════════════════════════════════════════════════
设计: 1000 轮随机搜索, 在固定交易模式(实时鉴股→尾盘买入→T+1 卖出)内找最优参数.

第一性原理:
  A 股 T+1 制度下, 日内信号对隔日收益预测 ≈ 随机 (8 轮因子设计已证明).
  优化焦点不在选股因子, 在: 仓位管理 / 分散度 / 市场状态过滤.

搜索参数:
  sample, top_n, hold_days, breadth_min, breadth_min_soft,
  sector_hot_topn, sector_inflow_topn, late_high_discount, require_vwap_strict

评分公式:
  score = (1 + cum_return/100) * (1 + profit_factor) / max(0.5, abs(drawdown) + 2)
  权重: 累计收益 > 盈亏比 > 回撤控制

v2 变更: 去掉 multiprocessing.fork (macOS 不稳定), 主线程直跑,
  靠 Sector 预热 + 日线预下载 + _skip_recovery 让每轮 < 5 min.
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# ── 搜索空间 ──────────────────────────────────────────
PARAM_GRID = {
    "sample":             [500],  # 固定 500 加速 (~2 min/iter), 最优组合再用大 sample 验证
    "top_n":              [3, 4],
    "hold_days":          [2, 3],
    "breadth_min":        [1500, 2000],
    "breadth_min_soft":   [3000, 3500],
    "sector_hot_topn":    [3, 5],
    "sector_inflow_topn": [0, 3],
    "late_high_discount": [1.0, 0.7],
    "require_vwap_strict": [False],
    "regime_adaptive":    [False, True],
}

# 默认值 (当前生产配置)
DEFAULT_PARAMS = {
    "sample": 1000,
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

# 启发式规则 — 避免无意义组合
RULE_BLACKLIST: list[Callable[[dict], bool]] = [
    # 软线需要硬底同时开启
    lambda p: p["breadth_min_soft"] > 0 and p["breadth_min"] == 0,
    # 热门Top 需要软线开启
    lambda p: p["sector_hot_topn"] > 0 and p["breadth_min_soft"] == 0,
]


def _score_result(result: dict) -> float:
    """多指标评分: 累计收益 × 盈亏比 / 回撤"""
    s = (result.get("summary") or {}) if isinstance(result.get("summary"), dict) else {}
    cum = float(s.get("cum_return_pct") or 0)
    pf = float(s.get("profit_factor") or 1.0)
    dd = abs(float(s.get("max_drawdown_pct") or 0))
    trades = int(s.get("trades") or 0)

    if trades < 3:
        return -999.0

    score = (1 + cum / 100) * (1 + pf) / max(0.5, dd + 2)

    if trades < 10:
        score *= 0.5

    return round(score, 4)


def _random_params() -> dict:
    """从搜索空间随机采样一组参数 (避开黑名单组合)"""
    for _ in range(1000):
        p = {k: random.choice(v) for k, v in PARAM_GRID.items()}
        if not any(rule(p) for rule in RULE_BLACKLIST):
            return p
    return dict(DEFAULT_PARAMS)


def _params_key(params: dict) -> str:
    """参数 → JSON key (用于去重/缓存)"""
    return json.dumps({k: params.get(k) for k in PARAM_GRID}, sort_keys=True)


def _format_params(params: dict) -> str:
    """参数 → 人类可读字符串"""
    parts = []
    for k in ["sample", "top_n", "hold_days", "breadth_min",
              "breadth_min_soft", "sector_hot_topn", "sector_inflow_topn",
              "late_high_discount", "require_vwap_strict", "regime_adaptive"]:
        v = params.get(k, DEFAULT_PARAMS.get(k))
        if k == "require_vwap_strict":
            parts.append("vwap" if v else "")
        elif k == "late_high_discount":
            parts.append(f"lhd{v}" if v != 1.0 else "")
        elif k == "breadth_min":
            parts.append(f"h{v}" if v else "")
        elif k == "breadth_min_soft":
            parts.append(f"s{v}" if v else "")
        elif k == "sector_hot_topn":
            parts.append(f"top{v}" if v else "")
        elif k == "sector_inflow_topn":
            parts.append(f"inf{v}" if v else "")
        elif k == "regime_adaptive":
            parts.append("reg" if v else "")
        else:
            parts.append(str(v))
    return "_".join(p for p in parts if p)


@dataclass
class OptimizerState:
    """优化器状态"""
    strategy_id: str = "WIN_RATE_V2"
    period_keys: list[str] = field(default_factory=lambda: ["半年"])
    max_iterations: int = 1000
    iteration: int = 0
    best_score: float = -999.0
    best_params: dict = field(default_factory=dict)
    best_result: dict | None = None
    history: list[dict] = field(default_factory=list)
    seen_keys: set[str] = field(default_factory=set)
    started_at: float = 0.0
    done: bool = False
    # ── Baseline (DEFAULT_PARAMS 跑一次, 作为每轮 delta 的对照) ──
    baseline_score: float = 0.0
    baseline_cum: float = 0.0
    baseline_wr: float = 0.0
    baseline_pf: float = 0.0
    baseline_dd: float = 0.0
    baseline_avg: float = 0.0
    baseline_trades: int = 0
    baseline_params: dict = field(default_factory=dict)
    baseline_elapsed: float = 0.0
    baseline_summary: dict = field(default_factory=dict)


def _warmup_sectors(all_codes: list[str], progress_cb=None) -> int:
    """并行预热板块缓存, 避免迭代中 akshare 超时"""
    from .sector_classify import get_sector, _load_cache
    from concurrent.futures import ThreadPoolExecutor, as_completed

    snap = _load_cache().get("stocks", {})
    missing = [c for c in all_codes if c not in snap or not snap[c].get("sw")]
    if not missing:
        if progress_cb:
            progress_cb(f"[预热] 板块全部已缓存 ({len(all_codes)})")
        return len(all_codes)

    if progress_cb:
        progress_cb(f"[预热] 补 {len(missing)} 只行业 (共 {len(all_codes)} 只)…")

    t0 = _time.time()
    done = ok = 0
    with ThreadPoolExecutor(max_workers=20) as ex:
        futs = {ex.submit(get_sector, c, False): c for c in missing}
        for f in as_completed(futs, timeout=600):
            try:
                r = f.result(timeout=5)
                if r and r.get("sw"):
                    ok += 1
            except Exception:
                pass
            done += 1
            if done % 200 == 0 and progress_cb:
                progress_cb(f"[预热] 板块 {done}/{len(missing)} · {ok} 有行业")

    elapsed = _time.time() - t0
    if progress_cb:
        progress_cb(f"[预热] 板块就绪 ({done}/{len(missing)}, {ok} 有行业, {elapsed:.0f}s)")
    return ok


def run_optimization(
    strategy_id: str = "WIN_RATE_V2",
    period_keys: list[str] | None = None,
    max_iterations: int = 1000,
    progress_cb: Callable[[str], None] | None = None,
    result_cb: Callable[[dict], None] | None = None,
    initial_params: dict | None = None,
) -> OptimizerState:
    """主入口: 跑 max_iterations 轮参数搜索

    v2: 直跑 (无 multiprocessing fork), 预缓存日线 + Sector.
    每轮 2-5 min (vwap_strict 首次稍慢).
    """
    state = OptimizerState(
        strategy_id=strategy_id,
        period_keys=period_keys or ["半年"],
        max_iterations=max_iterations,
        started_at=_time.time(),
    )

    if initial_params:
        state.best_params = dict(initial_params)
        state.seen_keys.add(_params_key(initial_params))
    else:
        state.best_params = dict(DEFAULT_PARAMS)
        state.seen_keys.add(_params_key(DEFAULT_PARAMS))

    # ── Phase 0: 加速 5-min 数据 (monkey-patch 跳过 SQLite 热点) ──
    # 优化不需要 morning high 精确值, simulation 会 2-tier 兜底.
    # 不经 5-min 可省 SQLite 锁竞争, 每轮快 5-10x.
    import tuixue_v3.web.backtest_screener as _bs
    _bs._fetch_5min_for_code = lambda code, start, end: []
    if progress_cb:
        progress_cb("[预热] 跳过 5min 数据 (monkey-patch, 快模式)")

    # ── Phase 1: 数据预热 ──
    from .backtest_screener import _prefetch_daily, _is_main_board
    from .. import data_layer as _dl

    if progress_cb:
        progress_cb("[预热] 获取股票列表…")
    all_stocks = _dl.fetch_stock_list() or []
    main_stocks = [(c, n) for c, n in all_stocks if _is_main_board(c)]
    max_sample = max(PARAM_GRID["sample"])
    if max_sample > 0 and len(main_stocks) > max_sample:
        warmup_codes = [c for c, _ in main_stocks[:max_sample]]
    else:
        warmup_codes = [c for c, _ in main_stocks]
    all_codes = [c for c, _ in main_stocks]

    if progress_cb:
        progress_cb(f"[预热] 预下载 {len(warmup_codes)} 只日线…")
    daily_cache = _prefetch_daily(warmup_codes, days=400, progress_cb=progress_cb)
    if progress_cb:
        progress_cb(f"[预热] 日线就绪 ({len(daily_cache)} 只)")

    if progress_cb:
        progress_cb("[预热] 板块缓存预热…")
    _warmup_sectors(warmup_codes, progress_cb=progress_cb)

    if progress_cb:
        progress_cb(f"[预热] 就绪, 开始 {max_iterations} 轮搜索")

    # ── Phase 1.5: 跑 Baseline (DEFAULT_PARAMS, sample 对齐到搜索空间) ──
    # 用 PARAM_GRID 里的 sample 值 (500) 让 baseline 和 rounds 公平可比.
    baseline_params = {k: DEFAULT_PARAMS.get(k) for k in PARAM_GRID}
    if PARAM_GRID.get("sample"):
        baseline_params["sample"] = max(PARAM_GRID["sample"])  # 通常 500
    if progress_cb:
        progress_cb(f"[0/{max_iterations}] [BASELINE] {_format_params(baseline_params)}…")

    t0 = _time.time()
    baseline_ok = False
    try:
        from .backtest_screener import run_for_frontend as _rff_bs
        baseline_result = _rff_bs(
            period_keys=state.period_keys,
            strategy_id=strategy_id,
            progress_cb=None,
            _skip_recovery=True,
            _daily_cache=daily_cache,
            **baseline_params,
        )
        baseline_ok = True
    except Exception as e:
        log.warning(f"[opt] baseline exception: {e}")
        baseline_result = None

    baseline_elapsed = _time.time() - t0
    bs = (baseline_result.get("summary") or {}) if baseline_result else {}
    state.baseline_score = _score_result(baseline_result) if baseline_ok else 0.0
    state.baseline_cum = float(bs.get("cum_return_pct") or 0)
    state.baseline_wr = float(bs.get("win_rate_pct") or 0)
    state.baseline_pf = float(bs.get("profit_factor") or 1.0)
    state.baseline_dd = abs(float(bs.get("max_drawdown_pct") or 0))
    state.baseline_avg = float(bs.get("avg_return_pct") or 0)
    state.baseline_trades = int(bs.get("trades") or 0)
    state.baseline_params = dict(baseline_params)
    state.baseline_elapsed = baseline_elapsed
    state.baseline_summary = dict(bs)
    state.seen_keys.add(_params_key(baseline_params))
    if progress_cb:
        progress_cb(
            f"[0/{max_iterations}] [BASELINE] ✓ 累计{state.baseline_cum:.1f}% "
            f"胜率{state.baseline_wr:.1f}% 回撤{state.baseline_dd:.1f}% "
            f"盈亏比{state.baseline_pf:.2f} score={state.baseline_score:.2f} "
            f"交易{state.baseline_trades}笔 耗时{baseline_elapsed:.0f}s"
        )

    # ── Phase 2: 搜索循环 ──
    for i in range(max_iterations):
        state.iteration = i + 1

        params = _random_params()
        pkey = _params_key(params)
        while pkey in state.seen_keys and len(state.seen_keys) < 50000:
            params = _random_params()
            pkey = _params_key(params)
        state.seen_keys.add(pkey)

        pkey_short = _format_params(params)
        if progress_cb:
            progress_cb(f"[{i+1}/{max_iterations}] {pkey_short}…")

        t0 = _time.time()
        iter_ok = False
        result = None

        try:
            from .backtest_screener import run_for_frontend as _rff
            result = _rff(
                period_keys=state.period_keys,
                strategy_id=strategy_id,
                progress_cb=None,
                _skip_recovery=True,
                _daily_cache=daily_cache,
                **params,
            )
            iter_ok = True
        except Exception as e:
            elapsed = _time.time() - t0
            if progress_cb:
                progress_cb(f"[{i+1}/{max_iterations}] ✗ 异常: {str(e)[:80]}")
            log.warning(f"[opt] iter {i+1} exception: {e}")

        elapsed = _time.time() - t0

        if not iter_ok or result is None:
            continue

        score = _score_result(result)
        summary = result.get("summary") or {}
        cum = float(summary.get("cum_return_pct") or 0)
        wr = float(summary.get("win_rate_pct") or 0)
        avg_ret = float(summary.get("avg_return_pct") or 0)
        dd = abs(float(summary.get("max_drawdown_pct") or 0))
        pf = float(summary.get("profit_factor") or 1.0)
        trades = int(summary.get("trades") or 0)
        entry = {
            "iteration": i + 1,
            "params": dict(params),
            "score": score,
            "cum_return": cum,
            "win_rate": wr,
            "avg_return": avg_ret,
            "max_drawdown": dd,
            "profit_factor": pf,
            "trades": trades,
            "elapsed": round(elapsed, 1),
            # ── vs Baseline delta (本轮相对 DEFAULT_PARAMS 的优化量) ──
            "delta_cum":     round(cum - state.baseline_cum, 2),
            "delta_wr":      round(wr - state.baseline_wr, 2),
            "delta_pf":      round(pf - state.baseline_pf, 2),
            "delta_dd":      round(dd - state.baseline_dd, 2),  # 负 = 回撤减小
            "delta_avg":     round(avg_ret - state.baseline_avg, 2),
            "delta_score":   round(score - state.baseline_score, 4),
        }
        state.history.append(entry)

        if score > state.best_score:
            state.best_score = score
            state.best_params = dict(params)
            state.best_result = result
            if progress_cb:
                dc = cum - state.baseline_cum
                dwr = wr - state.baseline_wr
                dpf = pf - state.baseline_pf
                dsc = score - state.baseline_score
                progress_cb(
                    f"[{i+1}/{max_iterations}] ★ 新最佳: 累计{cum:.1f}% "
                    f"胜率{wr:.1f}% 回撤{dd:.1f}% 盈亏比{pf:.2f} "
                    f"score={score:.2f} 耗时{elapsed:.0f}s "
                    f"Δcum={dc:+.1f}% Δwr={dwr:+.1f}% Δpf={dpf:+.2f} Δsc={dsc:+.2f}"
                )

        if result_cb:
            result_cb(result)

        if (i + 1) % 50 == 0 and progress_cb:
            elapsed_total = _time.time() - state.started_at
            success_rate = len(state.history) / max(1, i + 1) * 100
            # 计算本批 50 轮的 delta 统计 (mean / max / min)
            recent = state.history[-50:]
            dsc_list = [h["delta_score"] for h in recent if h.get("delta_score") is not None]
            dcum_list = [h["delta_cum"] for h in recent if h.get("delta_cum") is not None]
            if dsc_list:
                avg_dsc = sum(dsc_list) / len(dsc_list)
                max_dsc = max(dsc_list)
                n_better = sum(1 for x in dsc_list if x > 0)
                avg_dcum = sum(dcum_list) / len(dcum_list)
                progress_cb(
                    f"[{i+1}/{max_iterations}] checkpoint · "
                    f"最佳 score={state.best_score:.2f} (Δ{state.best_score-state.baseline_score:+.2f}) · "
                    f"成功 {len(state.history)}/{i+1} ({success_rate:.0f}%) · "
                    f"近 50 轮 Δscore 平均 {avg_dsc:+.2f} 最佳 {max_dsc:+.2f} "
                    f"超 baseline {n_better}/50 ({n_better*2}%) · "
                    f"近 50 轮 Δcum {avg_dcum:+.1f}% · "
                    f"已用 {elapsed_total:.0f}s · "
                    f"预计剩余 {(elapsed_total/(i+1)*(max_iterations-(i+1)))/60:.0f}min"
                )

    state.done = True
    return state


def summarize(state: OptimizerState, top_n: int = 10) -> str:
    """生成优化报告"""
    s = state.best_result.get("summary") or {}
    elapsed_total = _time.time() - state.started_at
    success_rate = len(state.history) / max(1, state.iteration) * 100

    lines = [
        "═" * 70,
        f"尾盘战法自动寻参 · {state.iteration} 轮 · {len(state.history)} 成功 ({success_rate:.0f}%)",
        f"策略: {state.strategy_id}  |  周期: {', '.join(state.period_keys)}",
        f"耗时: {elapsed_total:.0f}s  |  平均 {(elapsed_total/max(1,state.iteration)):.1f}s/轮",
        "",
        "━" * 70,
        "★ Baseline (DEFAULT_PARAMS, sample=500 — 公平对照):",
        f"  累计收益  : {state.baseline_cum:>8.2f}%",
        f"  胜率      : {state.baseline_wr:>8.1f}%",
        f"  平均收益  : {state.baseline_avg:>8.2f}%",
        f"  最大回撤  : {state.baseline_dd:>8.2f}%",
        f"  盈亏比    : {state.baseline_pf:>8.2f}",
        f"  交易笔数  : {state.baseline_trades:>8d}",
        f"  score     : {state.baseline_score:>8.2f}",
        f"  耗时      : {state.baseline_elapsed:>8.0f}s",
        "",
        "★ 最优参数:",
    ]
    for k, v in state.best_params.items():
        default = DEFAULT_PARAMS.get(k)
        marker = " (默认)" if v == default else ""
        lines.append(f"  {k:25s} = {str(v):10s}{marker}")
    lines += ["", f"★ 最优结果 (trail_80 退场口径):"]
    for label, key in [("累计收益", "cum_return_pct"), ("胜率", "win_rate_pct"),
                        ("平均收益", "avg_return_pct"), ("最大回撤", "max_drawdown_pct"),
                        ("盈亏比", "profit_factor"), ("交易笔数", "trades")]:
        v = s.get(key, 0)
        if key == "cum_return_pct":
            lines.append(f"  {label:10s}: {v:.2f}%")
        elif key == "win_rate_pct":
            lines.append(f"  {label:10s}: {v:.1f}%")
        elif key == "trades":
            lines.append(f"  {label:10s}: {v}")
        elif key == "profit_factor":
            lines.append(f"  {label:10s}: {v:.2f}")
        elif key == "max_drawdown_pct":
            lines.append(f"  {label:10s}: {v:.2f}%")
        elif key == "avg_return_pct":
            lines.append(f"  {label:10s}: {v:.2f}%")
    lines += [f"  score    : {state.best_score:.2f}"]

    # ★ 增量 = 最优 - Baseline (核心: "每一轮的优化多少")
    lines += ["", "★ 优化增量 (最优 vs Baseline):"]
    lines.append(f"  Δ累计收益 : {s.get('cum_return_pct', 0) - state.baseline_cum:+8.2f}%")
    lines.append(f"  Δ胜率     : {s.get('win_rate_pct', 0) - state.baseline_wr:+8.1f}%")
    lines.append(f"  Δ平均收益 : {s.get('avg_return_pct', 0) - state.baseline_avg:+8.2f}%")
    lines.append(f"  Δ最大回撤 : {(s.get('max_drawdown_pct') or 0) + state.baseline_dd:+8.2f}%  (负=回撤减小)")
    lines.append(f"  Δ盈亏比   : {s.get('profit_factor', 0) - state.baseline_pf:+8.2f}")
    lines.append(f"  Δ交易笔数 : {s.get('trades', 0) - state.baseline_trades:+8d}")
    lines.append(f"  Δscore    : {state.best_score - state.baseline_score:+8.4f}")

    # ★ 1000 轮 delta 分布统计
    if state.history:
        dsc = [h["delta_score"] for h in state.history if h.get("delta_score") is not None]
        dcum = [h["delta_cum"] for h in state.history if h.get("delta_cum") is not None]
        dwr = [h["delta_wr"] for h in state.history if h.get("delta_wr") is not None]
        n_better = sum(1 for x in dsc if x > 0)
        n_worse = sum(1 for x in dsc if x < 0)
        n_flat = len(dsc) - n_better - n_worse
        if dsc:
            lines += ["", "★ 1000 轮 Δscore 分布:"]
            lines.append(f"  超 baseline: {n_better}/{len(dsc)} ({n_better*100/len(dsc):.1f}%)")
            lines.append(f"  低于       : {n_worse}/{len(dsc)} ({n_worse*100/len(dsc):.1f}%)")
            lines.append(f"  平均 Δscore: {sum(dsc)/len(dsc):+.4f}")
            lines.append(f"  最大 Δscore: {max(dsc):+.4f}")
            lines.append(f"  最小 Δscore: {min(dsc):+.4f}")
            sorted_dsc = sorted(dsc, reverse=True)
            top10_pct = sum(1 for x in dsc if x >= sorted_dsc[max(0, len(sorted_dsc)//10)])
            lines.append(f"  Top 10% 阈值: {sorted_dsc[max(0, len(sorted_dsc)//10)]:+.4f} (超 {top10_pct} 轮)")
        if dcum:
            lines += ["", "★ 1000 轮 Δcum 分布:"]
            lines.append(f"  平均 Δcum  : {sum(dcum)/len(dcum):+.2f}%")
            lines.append(f"  最大 Δcum  : {max(dcum):+.2f}%")
            lines.append(f"  最小 Δcum  : {min(dcum):+.2f}%")
        if dwr:
            lines += ["", "★ 1000 轮 Δwr 分布:"]
            lines.append(f"  平均 Δwr   : {sum(dwr)/len(dwr):+.2f}%")
            lines.append(f"  最大 Δwr   : {max(dwr):+.2f}%")
            lines.append(f"  最小 Δwr   : {min(dwr):+.2f}%")

    # Top N 排名
    if len(state.history) >= top_n:
        sorted_h = sorted(state.history, key=lambda x: -x["score"])[:top_n]
        lines += ["", f"★ Top {top_n} 参数组合 (含 Δ vs Baseline):"]
        for rank, h in enumerate(sorted_h, 1):
            p = h["params"]
            cum = h.get("cum_return", 0) or 0
            wr = h.get("win_rate", 0) or 0
            dd = h.get("max_drawdown", 0) or 0
            pf = h.get("profit_factor", 0) or 0
            tr = h.get("trades", 0) or 0
            dsc = h.get("delta_score", 0) or 0
            dcum = h.get("delta_cum", 0) or 0
            dwr = h.get("delta_wr", 0) or 0
            lines.append(
                f"  #{rank}: score={h['score']:.2f} (Δ{dsc:+.2f}) "
                f"cum={cum:.1f}% (Δ{dcum:+.1f}%) wr={wr:.1f}% (Δ{dwr:+.1f}%) "
                f"DD={dd:.1f}% PF={pf:.2f} tr={tr} "
                f"s={p.get('sample')} tn={p.get('top_n')} "
                f"h={p.get('hold_days')} "
                f"b={p.get('breadth_min')} "
                f"soft={p.get('breadth_min_soft')} "
                f"hot={p.get('sector_hot_topn')} "
                f"inf={p.get('sector_inflow_topn')}"
            )

    lines += ["", "═" * 70]
    return "\n".join(lines)
