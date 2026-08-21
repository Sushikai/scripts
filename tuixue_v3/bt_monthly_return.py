"""
尾盘战法 · 科学月度收益回测 [复利模式]
════════════════════════════════════════════════════════════════════
方法：单次连续回测，从 equity_curve 重建复利净值曲线
复利原理：逐笔 trade 的边际收益 delta → capital *= (1+delta/100)
          逐月收益 = (月末净值/月初净值 - 1) × 100
          累计收益 = cumprod(1+r) - 1
策略：尾盘 14:30-14:50 选股 → T+1 卖出，最优参数组合
退场：trail_80 为主推，6 套退场场景对比
输出：月均收益 / 月胜率 / 夏普 / CAGR / 最大回撤 / 逐月明细
"""
from __future__ import annotations

import sys, os, json, time, math
from pathlib import Path
from collections import OrderedDict, defaultdict

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))
sys.path.insert(0, str(BASE))

import numpy as np

import importlib
dl = importlib.import_module("tuixue_v3.data_layer")
bs = importlib.import_module("tuixue_v3.web.backtest_screener")

# ── 猴补丁：禁止板块分类走网络，只用缓存，避免东财 API 超时卡死 ──
_sc = importlib.import_module("tuixue_v3.web.sector_classify")
_original_get_sector = _sc.get_sector

def _cached_only_get_sector(code: str, force_refresh: bool = False):
    """只从缓存取板块，不触发网络请求"""
    try:
        code = code.strip().zfill(6)
        cache = _sc._load_cache()
        stocks = cache.get("stocks", {})
        hit = stocks.get(code)
        if hit:
            board = _sc.detect_board(code)
            sw = hit.get("sw")
            sw_raw = hit.get("sw_raw") or ""
            csrc_raw = hit.get("csrc_raw") or ""
            return _sc._format_sector(code, board, sw, hit.get("source") or "cache",
                                      fresh=False, sw_raw=sw_raw, csrc_raw=csrc_raw)
    except Exception:
        pass
    # 缓存没有就返回空 dict，backtest_screener 会用 board prefix 兜底
    return {}

_sc.get_sector = _cached_only_get_sector
_sc.bulk_get_sector = lambda codes: {c: _cached_only_get_sector(c) for c in codes}
log_patch = lambda: print(f"[patch] 板块分类已切换为缓存-only模式", flush=True)
log_patch()

OUT_DIR = BASE / "output"
OUT_DIR.mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════
# 最优参数（来自多轮 grid search + 逐月回归验证）
# ═══════════════════════════════════════════════════
BEST_PARAMS = {
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

# Baseline（无过滤，原始默认）
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

# 6 套退场主键（与 backtest_screener.SIX_KEYS 一致）
MAIN_KEYS = ("trail_80", "trail_50", "trail_20", "water_avg", "force_10", "force_close")

# ═══════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════

def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def _monthly_compound_from_equity(equity_curve: list[list], monthly_data: list[dict]) -> list[dict]:
    """从 equity_curve 重建复利净值曲线，提取逐月复利收益

    原理：
    1. equity_curve 是 additive (cum_return += exit_val)，每个点 = 累计加法收益%
    2. 相邻点 delta = 单笔 trade 的组合收益贡献（含 position_mult 加权）
    3. 复利重建: capital *= (1 + delta/100)，得到真实净值曲线
    4. 逐月: month_return = (月末净值 / 月初净值 - 1) × 100
    """
    if not equity_curve:
        return []

    # ── Step 1: 从 additive equity_curve 提取每笔 trade 的边际贡献 ──
    deltas: list[tuple[str, float]] = []
    prev_cum = 0.0
    for date_str, cum_pct in equity_curve:
        delta = float(cum_pct) - prev_cum
        deltas.append((str(date_str), delta))
        prev_cum = float(cum_pct)

    # ── Step 2: 逐笔复利，重建净值曲线 ──
    capital = 1.0
    compound_pts: list[tuple[str, float]] = []  # [(date, compound_return_pct)]
    for date_str, delta in deltas:
        capital *= (1.0 + delta / 100.0)
        compound_pts.append((date_str, round((capital - 1.0) * 100.0, 4)))

    # ── Step 3: 按月分组，取每月末复利净值 ──
    eq_by_month: dict[str, list[float]] = defaultdict(list)
    for date_str, comp_pct in compound_pts:
        if len(date_str) >= 6:
            ym = date_str[:6]
            eq_by_month[ym].append(comp_pct)

    # 从 monthly_data 获取笔数/胜率等辅助信息
    monthly_info: dict[str, dict] = {}
    for m in monthly_data:
        ym = str(m.get("month", ""))
        monthly_info[ym] = {
            "trades": int(m.get("trades", 0)),
            "wins": int(m.get("wins", 0)),
            "losses": int(m.get("losses", 0)),
            "win_rate_pct": float(m.get("trail_80_win_rate_pct", 0)),
            "avg_return_pct": round(float(m.get("trail_80_avg", 0)), 3),
        }

    # ── Step 4: 计算逐月复利收益率 ──
    months = sorted(eq_by_month.keys())
    rows: list[dict] = []
    prev_month_end_capital = 1.0  # 初始净值 = 1.0

    for ym in months:
        eq_points = eq_by_month[ym]
        month_end_compound_pct = eq_points[-1]
        month_end_capital = 1.0 + month_end_compound_pct / 100.0

        # 月复利收益率 = (月末净值 / 月初净值 - 1) × 100
        if prev_month_end_capital > 0:
            monthly_ret = (month_end_capital / prev_month_end_capital - 1.0) * 100.0
        else:
            monthly_ret = month_end_compound_pct

        info = monthly_info.get(ym, {})
        rows.append({
            "month": ym,
            "trades": info.get("trades", 0),
            "total_return_pct": round(monthly_ret, 2),
            "avg_return_pct": info.get("avg_return_pct", 0),
            "wins": info.get("wins", 0),
            "losses": info.get("losses", 0),
            "win_rate_pct": info.get("win_rate_pct", 0),
        })

        prev_month_end_capital = month_end_capital

    return rows


def _compute_stats(monthly_rows: list[dict]) -> dict:
    """从逐月数据计算汇总统计"""
    if not monthly_rows:
        return {"error": "no data"}

    monthly_returns = np.array([r["total_return_pct"] for r in monthly_rows])
    n_months = len(monthly_returns)

    # 月均收益（算术平均）
    avg_monthly = float(monthly_returns.mean())

    # 月中位数
    median_monthly = float(np.median(monthly_returns))

    # 月胜率（正收益月占比）
    pos_months = int((monthly_returns > 0).sum())
    monthly_win_rate = round(pos_months / n_months * 100, 2)

    # 最大/最小月收益
    max_month = float(monthly_returns.max())
    min_month = float(monthly_returns.min())

    # 月收益标准差
    std_monthly = float(monthly_returns.std())

    # 下行标准差（只取负月）
    neg_rets = monthly_returns[monthly_returns < 0]
    downside_std = float(neg_rets.std()) if len(neg_rets) > 0 else 0.0

    # 夏普比率（月频，年化）
    # 假设无风险利率 = 0（A 股短线语境）
    if std_monthly > 0:
        sharpe_monthly = avg_monthly / std_monthly
        sharpe_annual = sharpe_monthly * math.sqrt(12)
    else:
        sharpe_monthly = 0
        sharpe_annual = 0

    # Sortino（用下行标准差）
    if downside_std > 0:
        sortino_monthly = avg_monthly / downside_std
    else:
        sortino_monthly = 0

    # 复利累计收益 & 最大回撤（基于净值曲线）
    factors = 1.0 + monthly_returns / 100.0          # 月收益% → 净值因子
    cumprod = np.cumprod(factors)                      # 复利净值序列
    equity_pct = (cumprod - 1.0) * 100.0              # 复利累计收益%
    # 回撤用相对值 (capital - peak) / peak，不会超过 -100%
    peak_capital = np.maximum.accumulate(cumprod)
    dd = (cumprod - peak_capital) / peak_capital      # 相对回撤 (e.g. -0.05 = -5%)
    max_dd = float(dd.min()) * 100.0                   # 转为百分比
    max_dd_month_idx = int(np.argmin(dd))
    max_dd_month = monthly_rows[max_dd_month_idx]["month"] if max_dd_month_idx < len(monthly_rows) else "?"

    # 复利累计收益
    total_cum = float(equity_pct[-1])

    # 复利年化收益率 (CAGR)
    if n_months > 0 and total_cum > -100:
        cagr = ((1.0 + total_cum / 100.0) ** (12.0 / n_months) - 1.0) * 100.0
    else:
        cagr = 0.0

    # 收益分布分位数
    p25 = float(np.percentile(monthly_returns, 25))
    p75 = float(np.percentile(monthly_returns, 75))

    # 卡尔玛比率（复利累计收益 / |最大回撤|）
    calmar = total_cum / abs(max_dd) if max_dd != 0 else float("inf")

    # 盈亏比（正月的平均 / 负月的平均的绝对值）
    pos_avg = float(monthly_returns[monthly_returns > 0].mean()) if pos_months > 0 else 0
    neg_avg = float(abs(monthly_returns[monthly_returns < 0].mean())) if (n_months - pos_months) > 0 else 0
    profit_loss_ratio = pos_avg / neg_avg if neg_avg > 0 else float("inf")

    # 连续盈利/亏损月
    streak_pos = 0
    streak_neg = 0
    max_pos_streak = 0
    max_neg_streak = 0
    for r in monthly_returns:
        if r > 0:
            streak_pos += 1
            streak_neg = 0
            max_pos_streak = max(max_pos_streak, streak_pos)
        elif r < 0:
            streak_neg += 1
            streak_pos = 0
            max_neg_streak = max(max_neg_streak, streak_neg)
        else:
            streak_pos = 0
            streak_neg = 0

    return {
        "period": f"{monthly_rows[0]['month']} ~ {monthly_rows[-1]['month']}",
        "months": n_months,
        "positive_months": pos_months,
        "monthly_win_rate_pct": monthly_win_rate,
        "avg_monthly_return_pct": round(avg_monthly, 2),
        "median_monthly_return_pct": round(median_monthly, 2),
        "max_monthly_return_pct": round(max_month, 2),
        "min_monthly_return_pct": round(min_month, 2),
        "monthly_std_pct": round(std_monthly, 2),
        "downside_std_pct": round(downside_std, 2),
        "sharpe_monthly": round(sharpe_monthly, 3),
        "sharpe_annual": round(sharpe_annual, 3),
        "sortino_monthly": round(sortino_monthly, 3),
        "max_drawdown_pct": round(max_dd, 2),
        "max_drawdown_month": max_dd_month,
        "total_cumulative_return_pct": round(total_cum, 2),
        "cagr_pct": round(cagr, 2),
        "calmar_ratio": round(calmar, 4),
        "profit_loss_ratio": round(profit_loss_ratio, 2),
        "max_consecutive_profit_months": max_pos_streak,
        "max_consecutive_loss_months": max_neg_streak,
        "p25_return_pct": round(p25, 2),
        "p75_return_pct": round(p75, 2),
    }


def run_backtest(label: str, params: dict, sample: int = 1500) -> dict:
    """运行一次完整回测，返回结构化结果"""
    log(f"═══ {label} ═══")
    log(f"参数: {params}")

    t0 = time.time()

    # 预加载数据
    bs._fetch_5min_for_code = lambda code, start, end: []  # 跳过 5min
    all_stocks = dl.fetch_stock_list() or []
    main_stocks = [(c, n) for c, n in all_stocks if bs._is_main_board(c)]
    if sample > 0 and len(main_stocks) > sample:
        main_stocks = main_stocks[:sample]
    codes = [c for c, _ in main_stocks]
    log(f"主板样本: {len(codes)} 只（全量主板 {len(main_stocks)}）")

    log("预加载日线…")
    daily_cache = bs._prefetch_daily(codes, days=600)

    # 运行回测 — 3 年窗口覆盖 2025-2027
    log("运行回测 (3年窗口)…")
    result = bs.run_for_frontend(
        period_keys=["3年"],
        strategy_id="WIN_RATE_V2",
        sample=sample,
        progress_cb=lambda msg: log(f"  {msg}"),
        _skip_recovery=True,
        _daily_cache=daily_cache,
        **params,
    )

    elapsed = time.time() - t0
    log(f"回测完成 · 耗时 {elapsed:.0f}s")

    # 提取数据
    summary = result.get("summary") or {}
    equity_curve = result.get("equity_curve") or []
    monthly_raw = result.get("monthly") or []

    if not monthly_raw:
        log("⚠ 未获取到月度数据，可能回测期无交易")
        return {
            "label": label,
            "params": params,
            "error": "no monthly data",
            "elapsed_sec": round(elapsed, 1),
        }

    # 逐月复利统计
    monthly_rows = _monthly_compound_from_equity(equity_curve, monthly_raw)
    if not monthly_rows:
        return {
            "label": label,
            "params": params,
            "error": "no monthly data",
            "elapsed_sec": round(elapsed, 1),
        }

    monthly_stats = _compute_stats(monthly_rows)

    # 逐笔统计(从 scenarios 获取)
    scenarios = result.get("scenarios") or {}
    trail80_stats = scenarios.get("trail_80", {})
    trade_stats = {
        "total_trades": int(trail80_stats.get("n", 0)),
        "avg_return_per_trade_pct": round(float(trail80_stats.get("avg_pct", 0)), 3),
        "median_return_per_trade_pct": round(float(trail80_stats.get("median_pct", 0)), 3),
        "trade_win_rate_pct": round(float(trail80_stats.get("win_rate_pct", 0)), 2),
        "trade_std_pct": round(float(trail80_stats.get("stddev_pct", 0)), 3),
        "max_single_trade_pct": round(float(trail80_stats.get("best_pct", 0)), 2),
        "min_single_trade_pct": round(float(trail80_stats.get("worst_pct", 0)), 2),
        "profit_factor": trail80_stats.get("profit_factor"),
    }

    return {
        "label": label,
        "params": params,
        "summary": {
            "cum_return_pct": round(float(summary.get("cum_return_pct", 0)), 2),
            "win_rate_pct": round(float(summary.get("win_rate_pct", 0)), 2),
            "profit_factor": round(float(summary.get("profit_factor", 1)), 2),
            "max_drawdown_pct": round(float(summary.get("max_drawdown_pct", 0)), 2),
            "trades": int(summary.get("trades", 0)),
        },
        "monthly_stats": monthly_stats,
        "trade_stats": trade_stats,
        "monthly_detail": monthly_rows,
        "elapsed_sec": round(elapsed, 1),
    }


def print_report(result: dict):
    """打印格式化报告"""
    ms = result.get("monthly_stats", {})
    ts = result.get("trade_stats", {})
    s = result.get("summary", {})
    monthly = result.get("monthly_detail", [])

    label = result.get("label", "?")
    print()
    print("=" * 80)
    print(f"  {label} · 月度收益回测报告 [复利模式]")
    print("=" * 80)

    if "error" in result:
        print(f"  ❌ 错误: {result['error']}")
        return

    # 核心指标
    print()
    print("  ┌─ 核心月度指标 ─────────────────────────────────────┐")
    print(f"  │ 回测周期      {ms.get('period', '?')}")
    print(f"  │ 交易月数      {ms.get('months', 0)}")
    print(f"  │ 月均收益      {ms.get('avg_monthly_return_pct', 0):>+.2f}%")
    print(f"  │ 月收益中位数  {ms.get('median_monthly_return_pct', 0):>+.2f}%")
    print(f"  │ 月胜率        {ms.get('monthly_win_rate_pct', 0)}% ({ms.get('positive_months', 0)}/{ms.get('months', 0)} 月盈利)")
    print(f"  │ 最大月收益    {ms.get('max_monthly_return_pct', 0):>+.2f}%")
    print(f"  │ 最小月收益    {ms.get('min_monthly_return_pct', 0):>+.2f}%")
    print(f"  │ 累计收益(复利){ms.get('total_cumulative_return_pct', 0):>+.2f}%")
    print(f"  │ 年化收益(CAGR){ms.get('cagr_pct', 0):>+.2f}%")
    print(f"  │ 最大回撤      {ms.get('max_drawdown_pct', 0):>+.2f}% ({ms.get('max_drawdown_month', '?')})")
    print("  └─────────────────────────────────────────────────────┘")

    print()
    print("  ┌─ 风险调整指标 ─────────────────────────────────────┐")
    print(f"  │ 月收益标准差  {ms.get('monthly_std_pct', 0):.2f}%")
    print(f"  │ 下行标准差    {ms.get('downside_std_pct', 0):.2f}%")
    print(f"  │ 夏普比率(月)  {ms.get('sharpe_monthly', 0):.3f}")
    print(f"  │ 夏普比率(年)  {ms.get('sharpe_annual', 0):.3f}")
    print(f"  │ Sortino       {ms.get('sortino_monthly', 0):.3f}")
    print(f"  │ Calmar        {ms.get('calmar_ratio', 0):.4f}")
    print(f"  │ 盈亏比(月)    {ms.get('profit_loss_ratio', 0):.2f}")
    print(f"  │ 最长连盈      {ms.get('max_consecutive_profit_months', 0)} 月")
    print(f"  │ 最长连亏      {ms.get('max_consecutive_loss_months', 0)} 月")
    print("  └─────────────────────────────────────────────────────┘")

    print()
    print("  ┌─ 逐笔统计 ──────────────────────────────────────────┐")
    print(f"  │ 总交易笔数    {ts.get('total_trades', 0)}")
    print(f"  │ 笔均收益      {ts.get('avg_return_per_trade_pct', 0):>+.3f}%")
    print(f"  │ 笔收益中位数  {ts.get('median_return_per_trade_pct', 0):>+.3f}%")
    print(f"  │ 胜率(笔)      {ts.get('trade_win_rate_pct', 0)}%")
    print(f"  │ 单笔最大盈利  {ts.get('max_single_trade_pct', 0):>+.2f}%")
    print(f"  │ 单笔最大亏损  {ts.get('min_single_trade_pct', 0):>+.2f}%")
    if ts.get("profit_factor") is not None:
        print(f"  │ 盈亏比(PF)    {ts.get('profit_factor')}")
    print("  └─────────────────────────────────────────────────────┘")

    # 逐月明细表
    if monthly:
        print()
        print("  ┌─ 逐月明细 ─────────────────────────────────────────────┐")
        print(f"  │ {'月份':<8s} {'笔数':>4s} {'月累计':>8s} {'笔均':>8s} {'胜率':>7s} │")
        print("  ├" + "─" * 48 + "┤")
        for r in monthly:
            sign = "+" if r["total_return_pct"] > 0 else ""
            color = "🟢" if r["total_return_pct"] > 0 else "🔴" if r["total_return_pct"] < 0 else "⚪"
            print(f"  │ {color} {r['month']}  {r['trades']:>3d}笔  {sign}{r['total_return_pct']:>7.1f}%  {r['avg_return_pct']:>+7.2f}%  {r['win_rate_pct']:>5.1f}% │")
        print("  └" + "─" * 48 + "┘")


# ═══════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════
if __name__ == "__main__":
    log("尾盘战法 · 科学月度收益回测")
    log(f"最优参数: top_n={BEST_PARAMS['top_n']}, hold_days={BEST_PARAMS['hold_days']}, "
        f"breadth={BEST_PARAMS['breadth_min']}/{BEST_PARAMS['breadth_min_soft']}, "
        f"discount={BEST_PARAMS['late_high_discount']}, regime={BEST_PARAMS['regime_adaptive']}")

    results = {}

    # ── 最优参数回测 ──
    try:
        best_result = run_backtest("最优参数", BEST_PARAMS, sample=1500)
        results["best"] = best_result
        print_report(best_result)
    except Exception as e:
        log(f"最优参数回测失败: {e}")
        import traceback
        traceback.print_exc()

    # ── Baseline 对比回测 ──
    try:
        bl_result = run_backtest("Baseline (无过滤)", BASELINE_PARAMS, sample=1500)
        results["baseline"] = bl_result
        print_report(bl_result)
    except Exception as e:
        log(f"Baseline 回测失败: {e}")

    # ── 对比总结 ──
    best = results.get("best", {})
    bl = results.get("baseline", {})
    if best and bl and "error" not in best and "error" not in bl:
        bm = best.get("monthly_stats", {})
        blm = bl.get("monthly_stats", {})
        print()
        print("=" * 80)
        print("  最优 vs Baseline 对比")
        print("=" * 80)
        print(f"  {'指标':<20s} {'最优参数':>12s} {'Baseline':>12s} {'差值':>12s}")
        print("  " + "-" * 56)
        fields = [
            ("月均收益", "avg_monthly_return_pct", "%", 2),
            ("月收益中位数", "median_monthly_return_pct", "%", 2),
            ("月胜率", "monthly_win_rate_pct", "%", 2),
            ("累计(复利)", "total_cumulative_return_pct", "%", 2),
            ("CAGR", "cagr_pct", "%", 2),
            ("最大回撤", "max_drawdown_pct", "%", 2),
            ("夏普(年)", "sharpe_annual", "", 3),
            ("月标准差", "monthly_std_pct", "%", 2),
        ]
        for name, key, unit, precision in fields:
            bv = bm.get(key, 0)
            blv = blm.get(key, 0)
            diff = bv - blv
            diff_str = f"{diff:+.{precision}f}{unit}" if isinstance(bv, (int, float)) else f"{diff}"
            bv_str = f"{bv:.{precision}f}{unit}" if isinstance(bv, (int, float)) else str(bv)
            blv_str = f"{blv:.{precision}f}{unit}" if isinstance(blv, (int, float)) else str(blv)
            print(f"  {name:<20s} {bv_str:>12s} {blv_str:>12s} {diff_str:>12s}")

    # ── 保存结果 ──
    out_file = OUT_DIR / f"monthly_return_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    log(f"\n结果已保存: {out_file}")

    print()
    log("完成 ✅")
