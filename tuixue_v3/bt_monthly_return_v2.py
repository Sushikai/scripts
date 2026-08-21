"""
尾盘战法 · 真实交易流程月度收益回测 v2
════════════════════════════════════════════════════════════════════
交易流程（用户真实操作）:
  T0:  收盘后选股（看票）
  T+1: 以涨停价竞价 → 实际以 T+1 开盘价买入
       若 T+1 一字涨停（open >= T0_close * 1.095）→ 买不进，跳过
  T+2: 择时卖出（trail_80 策略: 翻红吃高点 80%, 没翻红水下均价）
       若 T+2 涨停（close >= T1_close * 1.095）→ 不卖，延到 T+3
  T+3: 卖出（同 trail_80 逻辑, 或 close 兜底）

收益计算: 复利模式
  每日: capital *= (1 + sum(trade_return * position_mult) / 100)
  position_mult = 1 / top_n (无杠杆等权)
"""
import sys, os, time, json, importlib, logging
from pathlib import Path
from collections import defaultdict
import numpy as np

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))

# ── 猴补丁: 板块分类只走缓存, 不走网络 (避免东财 API 超时卡死) ──
import tuixue_v3.data_layer as _dl
import tuixue_v3.web.sector_classify as _sc
_original_get_sector = _sc.get_sector
def _cached_only_get_sector(code, *a, **kw):
    try:
        cache = getattr(_sc, "_cache", None)
        if cache is None:
            try:
                cache = _sc._load_cache()
            except Exception:
                cache = {}
        if cache and code in cache:
            return cache[code]
    except Exception:
        pass
    # 缓存没有就用 board prefix 兜底
    if code.startswith("60"): return "主板"
    if code.startswith("00"): return "主板"
    return ""
_sc.get_sector = _cached_only_get_sector

bs = importlib.import_module("tuixue_v3.web.backtest_screener")

# 静音 data_layer 的 WARNING (网络源失败日志太多, 屏蔽掉)
logging.getLogger().setLevel(logging.ERROR)
for name in ("tuixue_v3", "tuixue_v3.data_layer", "data_layer"):
    logging.getLogger(name).setLevel(logging.ERROR)

# ── 猴补丁: 捕获 run_for_frontend 内部的完整 trades (不被 500 截断) ──
_captured_trades: list[dict] = []
_original_stat_six = bs._stat_six_scenarios
def _capturing_stat_six(trades, *a, **kw):
    # overall 调用 (all_trades_six) 比 window 级大, 只保留最大的那批
    if len(trades) > len(_captured_trades):
        _captured_trades.clear()
        _captured_trades.extend(trades)
    return _original_stat_six(trades, *a, **kw)
bs._stat_six_scenarios = _capturing_stat_six

OUT_DIR = BASE / "output"
OUT_DIR.mkdir(exist_ok=True)

# ═══════════════════════════════════════
# 参数
# ═══════════════════════════════════════
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

def log(msg):
    t = time.strftime("%H:%M:%S")
    print(f"[{t}] {msg}", flush=True)


# ═══════════════════════════════════════
# 真实交易模拟
# ═══════════════════════════════════════

def _limit_up_price(prev_close: float) -> float:
    """主板涨停价 = round(prev_close * 1.1, 2)"""
    return round(prev_close * 1.1, 2)

def _is_limit_up(open_price: float, prev_close: float) -> bool:
    """T+1 一字涨停: 开盘价 >= 涨停价 (买不进)"""
    return open_price >= _limit_up_price(prev_close) - 0.001  # 容差

def _is_close_limit_up(close_price: float, prev_close: float) -> bool:
    """T+2 收盘涨停: 收盘价 >= 涨停价"""
    return close_price >= _limit_up_price(prev_close) - 0.001

def _simulate_sell(sell_open, sell_high, sell_low, sell_close, buy_price: float) -> dict:
    """择时卖出 — trail_80 策略

    翻红(open > buy) → high × 0.8
    盘中翻水(high >= buy 但 open <= buy) → high × 0.8 × 0.7
    全天没救 → (open + low) / 2 水下均价
    """
    _p = lambda p: (p / buy_price - 1.0) * 100.0
    ret_close = _p(sell_close)
    ret_water = _p((sell_open + sell_low) / 2.0)

    green = sell_open > buy_price
    late = (not green) and (sell_high >= buy_price)

    if green:
        ret_trail80 = round(_p(sell_high) * 0.8, 3)
    elif late:
        ret_trail80 = round(_p(sell_high) * 0.8 * 0.7, 3)
    else:
        ret_trail80 = ret_water

    return {
        "trail_80": ret_trail80,
        "force_close": round(ret_close, 3),
        "water_avg": round(ret_water, 3),
    }


def run_realistic_backtest(params: dict, sample: int = 1500) -> dict:
    """真实交易流程回测"""
    t0 = time.time()
    top_n = params.get("top_n", 4)
    pos_mult = 1.0 / max(1, top_n)

    # ── 1. 加载数据 ──
    log("加载数据…")
    all_stocks = _dl.fetch_stock_list() or []
    main_stocks = [(c, n) for c, n in all_stocks if bs._is_main_board(c)]
    if sample > 0 and len(main_stocks) > sample:
        main_stocks = main_stocks[:sample]
    codes = [c for c, _ in main_stocks]
    names = {c: n for c, n in main_stocks}
    log(f"主板 {len(main_stocks)} 只 (采样 {len(codes)})")

    # 交易日历 — 从日线数据中提取 (更准确)
    daily_cache = bs._prefetch_daily(codes, days=600)
    log(f"日线缓存: {len(daily_cache)} 只")

    # 构建 cache_by_code_date: {code: {date_str: {open,high,low,close}}}
    cache_by_code_date: dict[str, dict[str, dict]] = {}
    all_dates_set: set[str] = set()
    for code, df in daily_cache.items():
        if df is None or df.empty:
            continue
        d = {}
        for _, r in df.iterrows():
            ds = str(r["日期"])
            d[ds] = {
                "open": float(r["开盘"]), "high": float(r["最高"]),
                "low": float(r["最低"]), "close": float(r["收盘"]),
            }
            all_dates_set.add(ds)
        cache_by_code_date[code] = d
    # 用日线数据中的日期做交易日历 (比 fetch_trade_dates 更准)
    norm_dates = sorted(all_dates_set)
    log(f"索引: {len(cache_by_code_date)} codes, {len(norm_dates)} 交易日 ({norm_dates[0]}~{norm_dates[-1]})")

    # ── 2. 调用 run_for_frontend 做选股 (复用现有筛选逻辑) ──
    _captured_trades.clear()
    log("运行选股引擎 (run_for_frontend)…")
    result = bs.run_for_frontend(
        period_keys=["1年"],
        top_n=top_n,
        hold_days=params.get("hold_days", 2),
        breadth_min=params.get("breadth_min", 0),
        breadth_min_soft=params.get("breadth_min_soft", 0),
        sector_hot_topn=params.get("sector_hot_topn", 0),
        sector_inflow_topn=params.get("sector_inflow_topn", 0),
        late_high_discount=params.get("late_high_discount", 1.0),
        require_vwap_strict=params.get("require_vwap_strict", False),
        regime_adaptive=params.get("regime_adaptive", False),
        sample=sample,
        _daily_cache=daily_cache,
    )

    # 用猴补丁捕获的完整 trades (不受 500 截断限制)
    old_trades = list(_captured_trades)

    log(f"选股引擎返回 {len(old_trades)} 笔原始交易")

    if not old_trades:
        return {"error": "no trades from screening", "elapsed": time.time() - t0}

    # ── 3. 重新模拟: 真实买卖流程 ──
    date_index = {d: i for i, d in enumerate(norm_dates)}

    new_trades = []
    stats_skip = defaultdict(int)

    for ot in old_trades:
        t0_date = str(ot.get("buy_date") or ot.get("date_t", ""))
        code = ot.get("code", "")
        score = float(ot.get("score", 0))
        name = ot.get("name", code)

        if t0_date not in date_index or code not in cache_by_code_date:
            stats_skip["no_t0_or_cache"] += 1
            continue

        idx = date_index[t0_date]
        # T+1 (买入日)
        if idx + 1 >= len(norm_dates):
            stats_skip["no_t1"] += 1
            continue
        t1_date = norm_dates[idx + 1]
        # T+2 (卖出日)
        if idx + 2 >= len(norm_dates):
            stats_skip["no_t2"] += 1
            continue
        t2_date = norm_dates[idx + 2]

        code_cache = cache_by_code_date[code]
        t0_data = code_cache.get(t0_date)
        t1_data = code_cache.get(t1_date)
        t2_data = code_cache.get(t2_date)

        if not t0_data or not t1_data or not t2_data:
            stats_skip["missing_ohlc"] += 1
            continue

        t0_close = t0_data["close"]
        t1_open = t1_data["open"]
        t1_close = t1_data["close"]

        # ── 检查 T+1 一字涨停 (买不进) ──
        if _is_limit_up(t1_open, t0_close):
            stats_skip["t1_limit_up_skip"] += 1
            continue

        buy_price = t1_open
        buy_date = t1_date

        # ── 检查 T+2 是否涨停 ──
        t2_close = t2_data["close"]
        if _is_close_limit_up(t2_close, t1_close):
            # 延到 T+3 卖出
            if idx + 3 >= len(norm_dates):
                stats_skip["no_t3"] += 1
                continue
            t3_date = norm_dates[idx + 3]
            t3_data = code_cache.get(t3_date)
            if not t3_data:
                stats_skip["missing_t3"] += 1
                continue
            sell_date = t3_date
            sell_data = t3_data
            deferred = True
        else:
            sell_date = t2_date
            sell_data = t2_data
            deferred = False

        # ── 择时卖出 ──
        sell_result = _simulate_sell(
            sell_data["open"], sell_data["high"],
            sell_data["low"], sell_data["close"],
            buy_price
        )

        ret_trail80 = sell_result["trail_80"]
        ret_close = sell_result["force_close"]

        new_trades.append({
            "code": code,
            "name": name,
            "score": score,
            "t0_date": t0_date,          # 选股日
            "buy_date": buy_date,         # T+1 买入日
            "buy_price": round(buy_price, 3),
            "sell_date": sell_date,        # T+2 或 T+3 卖出日
            "deferred": deferred,          # 是否因涨停延后
            "trail_80": ret_trail80,       # 择时卖出收益率
            "force_close": ret_close,      # 收盘卖出收益率
            "water_avg": sell_result["water_avg"],
            "t1_open_pct": round((t1_open / t0_close - 1) * 100, 2),  # T+1 开盘涨幅
        })

    log(f"重新模拟完成: {len(new_trades)} 笔有效交易, 跳过 {dict(stats_skip)}")

    if not new_trades:
        return {"error": "no valid trades after resimulation", "elapsed": time.time() - t0}

    # ── 4. 复利计算 (按卖出日分组) ──
    # 每日: capital *= (1 + sum(ret * pos_mult) / 100)
    trades_by_sell_date = defaultdict(list)
    for t in new_trades:
        trades_by_sell_date[t["sell_date"]].append(t)

    # 按卖出日排序, 逐日复利
    equity_curve = []  # [(date, capital_pct)]
    capital = 1.0
    for sell_date in sorted(trades_by_sell_date.keys()):
        day_trades = trades_by_sell_date[sell_date]
        day_return = sum(t["trail_80"] * pos_mult for t in day_trades)
        capital *= (1 + day_return / 100.0)
        equity_curve.append((sell_date, round((capital - 1) * 100, 3)))

    # ── 5. 逐月统计 ──
    # 月收益 = 月末 capital / 月初 capital - 1
    monthly_capital = {}  # {YYYYMM: (start_cap, end_cap)}
    prev_capital = 1.0
    current_month = None
    month_start_cap = 1.0

    for sell_date, cum_pct in equity_curve:
        ym = sell_date[:6]
        cap_now = 1 + cum_pct / 100.0
        if current_month is None:
            current_month = ym
            month_start_cap = 1.0  # 第一月从 1.0 开始
        elif ym != current_month:
            # 上月结束
            monthly_capital[current_month] = (month_start_cap, prev_capital)
            current_month = ym
            month_start_cap = prev_capital
        prev_capital = cap_now

    # 最后一个月
    if current_month:
        monthly_capital[current_month] = (month_start_cap, prev_capital)

    # 逐月收益
    monthly_rows = []
    for ym in sorted(monthly_capital.keys()):
        start_cap, end_cap = monthly_capital[ym]
        month_ret = (end_cap / start_cap - 1) * 100

        # 该月交易统计
        month_trades = [t for t in new_trades if t["sell_date"][:6] == ym]
        rets = np.array([t["trail_80"] for t in month_trades])
        deferred_count = sum(1 for t in month_trades if t["deferred"])

        monthly_rows.append({
            "month": ym,
            "trades": len(month_trades),
            "month_return_pct": round(month_ret, 3),
            "avg_per_trade_pct": round(float(rets.mean()), 3) if len(rets) > 0 else 0,
            "win_rate_pct": round(float((rets > 0).mean() * 100), 2) if len(rets) > 0 else 0,
            "wins": int((rets > 0).sum()),
            "losses": int((rets < 0).sum()),
            "max_single_pct": round(float(rets.max()), 2) if len(rets) > 0 else 0,
            "min_single_pct": round(float(rets.min()), 2) if len(rets) > 0 else 0,
            "deferred_count": deferred_count,
            "end_capital": round(end_cap, 6),
        })

    # ── 6. 统计指标 ──
    monthly_returns = np.array([r["month_return_pct"] for r in monthly_rows])

    # 复利净值曲线
    factors = 1.0 + monthly_returns / 100.0
    cumprod = np.cumprod(factors)
    equity_pct = (cumprod - 1.0) * 100.0

    # 最大回撤 (相对回撤)
    peak = np.maximum.accumulate(equity_pct)
    # 避免除零
    peak_safe = np.where(np.abs(peak) < 0.01, 0.01, peak)
    dd_pct = (equity_pct - peak) / np.abs(peak_safe) * 100.0
    max_dd = float(dd_pct.min())
    max_dd_idx = int(np.argmin(dd_pct))
    max_dd_month = monthly_rows[max_dd_idx]["month"] if max_dd_idx < len(monthly_rows) else "?"

    total_cum = float(equity_pct[-1])

    # CAGR
    n_months = len(monthly_rows)
    n_years = n_months / 12.0
    cagr = ((cumprod[-1]) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0

    # 夏普 (月频 → 年化)
    if len(monthly_returns) > 1 and monthly_returns.std() > 0:
        sharpe = float(monthly_returns.mean() / monthly_returns.std() * np.sqrt(12))
    else:
        sharpe = 0.0

    # Sortino
    downside = monthly_returns[monthly_returns < 0]
    if len(downside) > 0 and downside.std() > 0:
        sortino = float(monthly_returns.mean() / downside.std() * np.sqrt(12))
    else:
        sortino = float("inf")

    # 连续盈亏
    max_streak_win = 0
    max_streak_loss = 0
    cur_win = 0
    cur_loss = 0
    for r in monthly_returns:
        if r > 0:
            cur_win += 1
            cur_loss = 0
            max_streak_win = max(max_streak_win, cur_win)
        else:
            cur_loss += 1
            cur_win = 0
            max_streak_loss = max(max_streak_loss, cur_loss)

    # 逐笔统计
    all_rets = np.array([t["trail_80"] for t in new_trades])
    all_close_rets = np.array([t["force_close"] for t in new_trades])

    # 盈亏比
    gains = all_rets[all_rets > 0]
    losses = all_rets[all_rets < 0]
    profit_factor = float(gains.sum() / abs(losses.sum())) if len(losses) > 0 and losses.sum() != 0 else float("inf")

    # T+1 买入分析
    t1_open_pcts = np.array([t["t1_open_pct"] for t in new_trades])

    stats = {
        "monthly_rows": monthly_rows,
        "monthly_stats": {
            "n_months": n_months,
            "avg_monthly_return_pct": round(float(monthly_returns.mean()), 3),
            "median_monthly_return_pct": round(float(np.median(monthly_returns)), 3),
            "monthly_win_rate_pct": round(float((monthly_returns > 0).mean() * 100), 2),
            "total_cumulative_return_pct": round(total_cum, 2),
            "cagr_pct": round(cagr, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "max_drawdown_month": max_dd_month,
            "sharpe_annual": round(sharpe, 3),
            "sortino_annual": round(sortino, 3) if sortino != float("inf") else None,
            "monthly_std_pct": round(float(monthly_returns.std()), 3),
            "max_streak_win": max_streak_win,
            "max_streak_loss": max_streak_loss,
            "calmar_ratio": round(cagr / abs(max_dd), 4) if max_dd != 0 else None,
        },
        "trade_stats": {
            "total_trades": len(new_trades),
            "avg_return_per_trade_pct": round(float(all_rets.mean()), 3),
            "median_return_per_trade_pct": round(float(np.median(all_rets)), 3),
            "trade_win_rate_pct": round(float((all_rets > 0).mean() * 100), 2),
            "trade_std_pct": round(float(all_rets.std()), 3),
            "max_single_trade_pct": round(float(all_rets.max()), 2),
            "min_single_trade_pct": round(float(all_rets.min()), 2),
            "profit_factor": round(profit_factor, 3),
            "deferred_count": sum(1 for t in new_trades if t["deferred"]),
            "deferred_pct": round(sum(1 for t in new_trades if t["deferred"]) / len(new_trades) * 100, 2),
        },
        "buy_stats": {
            "avg_t1_open_pct": round(float(t1_open_pcts.mean()), 3),
            "median_t1_open_pct": round(float(np.median(t1_open_pcts)), 3),
            "t1_gap_up_rate_pct": round(float((t1_open_pcts > 0).mean() * 100), 2),
            "t1_gap_down_rate_pct": round(float((t1_open_pcts < 0).mean() * 100), 2),
            "avg_t1_open_pct_when_gap_up": round(float(t1_open_pcts[t1_open_pcts > 0].mean()), 3) if (t1_open_pcts > 0).any() else 0,
            "avg_t1_open_pct_when_gap_down": round(float(t1_open_pcts[t1_open_pcts < 0].mean()), 3) if (t1_open_pcts < 0).any() else 0,
        },
        "skip_stats": dict(stats_skip),
        "equity_curve": equity_curve,
        "close_vs_trail": {
            "trail_80_total": round(float(all_rets.sum()), 2),
            "force_close_total": round(float(all_close_rets.sum()), 2),
            "trail_80_avg": round(float(all_rets.mean()), 3),
            "force_close_avg": round(float(all_close_rets.mean()), 3),
            "trail_better_count": int((all_rets > all_close_rets).sum()),
            "close_better_count": int((all_close_rets > all_rets).sum()),
        },
        "params": params,
        "elapsed_sec": round(time.time() - t0, 1),
    }

    return stats


# ═══════════════════════════════════════
# 报告打印
# ═══════════════════════════════════════

def print_report(result: dict):
    ms = result["monthly_stats"]
    ts = result["trade_stats"]
    bs_stats = result["buy_stats"]
    skips = result["skip_stats"]
    cvt = result["close_vs_trail"]

    print()
    print("  ╔═══════════════════════════════════════════════════════╗")
    print("  ║     尾盘战法 · 真实交易流程复利回测 v2                ║")
    print("  ║     T0选股 → T+1涨停价竞价买入 → T+2择时卖出        ║")
    print("  ║     T+2涨停 → T+3卖出                                ║")
    print("  ╚═══════════════════════════════════════════════════════╝")
    print()
    print("  ┌─ 核心指标 (复利) ─────────────────────────────────────┐")
    print(f"  │ 回测月数      {ms['n_months']} 个月")
    print(f"  │ 月均收益      {ms['avg_monthly_return_pct']:>+.3f}%")
    print(f"  │ 月收益中位数  {ms['median_monthly_return_pct']:>+.3f}%")
    print(f"  │ 月胜率        {ms['monthly_win_rate_pct']}%")
    print(f"  │ 累计收益      {ms['total_cumulative_return_pct']:>+.2f}%")
    print(f"  │ CAGR(年化)    {ms['cagr_pct']:>+.2f}%")
    print(f"  │ 最大回撤      {ms['max_drawdown_pct']:>+.2f}% ({ms['max_drawdown_month']})")
    print(f"  │ 夏普(年)      {ms['sharpe_annual']}")
    if ms.get("sortino_annual") is not None:
        print(f"  │ Sortino(年)   {ms['sortino_annual']}")
    print(f"  │ 月标准差      {ms['monthly_std_pct']:.3f}%")
    if ms.get("calmar_ratio") is not None:
        print(f"  │ Calmar        {ms['calmar_ratio']}")
    print(f"  │ 最长连盈      {ms['max_streak_win']} 个月")
    print(f"  │ 最长连亏      {ms['max_streak_loss']} 个月")
    print("  └───────────────────────────────────────────────────────┘")

    print()
    print("  ┌─ 交易统计 ────────────────────────────────────────────┐")
    print(f"  │ 总交易笔数    {ts['total_trades']}")
    print(f"  │ 笔均收益      {ts['avg_return_per_trade_pct']:>+.3f}%")
    print(f"  │ 笔收益中位数  {ts['median_return_per_trade_pct']:>+.3f}%")
    print(f"  │ 胜率(笔)      {ts['trade_win_rate_pct']}%")
    print(f"  │ 单笔最大盈利  {ts['max_single_trade_pct']:>+.2f}%")
    print(f"  │ 单笔最大亏损  {ts['min_single_trade_pct']:>+.2f}%")
    print(f"  │ 盈亏比(PF)    {ts['profit_factor']}")
    print(f"  │ 涨停延后笔数  {ts['deferred_count']} ({ts['deferred_pct']}%)")
    print("  └───────────────────────────────────────────────────────┘")

    print()
    print("  ┌─ T+1 买入分析 (开盘价 vs T0收盘) ────────────────────┐")
    print(f"  │ 平均开盘涨幅  {bs_stats['avg_t1_open_pct']:>+.3f}%")
    print(f"  │ 开盘涨幅中位  {bs_stats['median_t1_open_pct']:>+.3f}%")
    print(f"  │ 高开比例      {bs_stats['t1_gap_up_rate_pct']}%")
    print(f"  │ 低开比例      {bs_stats['t1_gap_down_rate_pct']}%")
    print(f"  │ 高开时均值    {bs_stats['avg_t1_open_pct_when_gap_up']:>+.3f}%")
    print(f"  │ 低开时均值    {bs_stats['avg_t1_open_pct_when_gap_down']:>+.3f}%")
    print("  └───────────────────────────────────────────────────────┘")

    print()
    print("  ┌─ trail_80 择时 vs 收盘卖出 对比 ─────────────────────┐")
    print(f"  │ trail_80 笔均 {cvt['trail_80_avg']:>+.3f}%  |  收盘笔均 {cvt['force_close_avg']:>+.3f}%")
    print(f"  │ trail_80 更优 {cvt['trail_better_count']} 笔  |  收盘更优 {cvt['close_better_count']} 笔")
    print("  └───────────────────────────────────────────────────────┘")

    if skips:
        print()
        print("  ┌─ 跳过统计 ────────────────────────────────────────────┐")
        for k, v in sorted(skips.items(), key=lambda x: -x[1]):
            print(f"  │ {k:<30s} {v:>6d}")
        print("  └───────────────────────────────────────────────────────┘")

    monthly = result["monthly_rows"]
    if monthly:
        print()
        print("  ┌─ 逐月明细 ──────────────────────────────────────────────────────────────────┐")
        print(f"  │ {'月份':<8s} {'笔数':>4s} {'月收益':>8s} {'笔均':>8s} {'胜率':>7s} {'最大':>7s} {'最小':>7s} {'延后':>4s} {'月末净值':>10s} │")
        print("  ├" + "─" * 80 + "┤")
        for r in monthly:
            color = "+" if r["month_return_pct"] > 0 else "-" if r["month_return_pct"] < 0 else " "
            print(f"  │ {color} {r['month']}  {r['trades']:>3d}笔  {r['month_return_pct']:>+7.2f}%  {r['avg_per_trade_pct']:>+7.2f}%  {r['win_rate_pct']:>5.1f}%  {r['max_single_pct']:>+6.1f}% {r['min_single_pct']:>+6.1f}% {r['deferred_count']:>3d}  {r['end_capital']:>10.4f} │")
        print("  └" + "─" * 80 + "┘")

    print(f"\n  耗时: {result['elapsed_sec']:.0f}s")


# ═══════════════════════════════════════
# 主流程
# ═══════════════════════════════════════

if __name__ == "__main__":
    log("=== 真实交易流程月度回测 v2 ===")
    log(f"最优参数: top_n={BEST_PARAMS['top_n']}, hold_days={BEST_PARAMS['hold_days']}")
    log("交易流程: T0选股 → T+1涨停价竞价买入 → T+2择时卖出 → 涨停延T+3")

    result = run_realistic_backtest(BEST_PARAMS, sample=800)

    if "error" in result:
        log(f"错误: {result['error']}")
        sys.exit(1)

    print_report(result)

    # 保存 JSON
    ts_str = time.strftime("%Y%m%d_%H%M%S")
    out_path = OUT_DIR / f"bt_v2_realistic_{ts_str}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    log(f"结果已保存: {out_path}")
