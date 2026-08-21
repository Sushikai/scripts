"""
Single-month backtest with full trade detail listing.

Usage: python _test_zt_month.py <YYYYMM> [loose]
  e.g.  python _test_zt_month.py 202608           # strict OPTIMAL_PARAMS
        python _test_zt_month.py 202608 loose     # loose (top=3, board=all)
"""
import sys, logging, time, pickle
logging.basicConfig(level=logging.INFO, force=True, stream=sys.stderr)
sys.path.insert(0, '/Users/kaikai/scripts')

from tuixue_v3 import zt_backtest as zt
from tuixue_v3.zt_config import OPTIMAL_PARAMS

month = sys.argv[1] if len(sys.argv) > 1 else "202608"
mode = sys.argv[2] if len(sys.argv) > 2 else "strict"  # strict | loose

# 1. 加载预构建缓存
cache_path = "/tmp/zt_prebuilt_20251201_20260804_all.pkl"
with open(cache_path, "rb") as f:
    pb = pickle.load(f)
print(f"[cache] dates={len(pb[1])} zt_days={len(pb[3])}")

# 2. 切片 dates 到指定月份
all_dates = pb[1]
month_dates = [d for d in all_dates if d.startswith(month)]
print(f"[{month}] dates={len(month_dates)} first={month_dates[0] if month_dates else '-'} last={month_dates[-1] if month_dates else '-'}")

# 3. 切片 zt_cache 也只留当月
zt_cache_m = {d: v for d, v in pb[3].items() if d.startswith(month)}
print(f"[{month}] zt_days={len(zt_cache_m)} total candidates={sum(len(v) for v in zt_cache_m.values())}")

# 4. 切片 market_context
mc_m = {d: v for d, v in pb[4].items() if d.startswith(month)} if pb[4] else {}

# 5. 构造新的预构建
prebuilt_m = (pb[0], month_dates, pb[2], zt_cache_m, mc_m)

# 6. 选参数
if mode == "loose":
    p = {
        'min_streak': 1, 'max_streak': 10,
        'burst_max': 99, 'sealed_before': '14:00',
        'mcap_min_yi': 0, 'mcap_max_yi': 99999,
        'turnover_min_pct': 0, 'turnover_max_pct': 100,
        'limit_order_min_yi': 0,
        'top_n': 3, 'board_filter': 'all',
        'exit_strategy': 'close_t1',
        'market_filter_mode': 'off',
        'breadth_up_min': 0, 'zt_pool_min': 0,
        'vol_ratio_min': 0, 'limit_up_pct_min': 9.5,
        'burst_window_3d': 99, 'yiziban_required': False,
        'gap_open_required': False,
        'pct_chg_5d_max': 999, 'pct_chg_5d_min': 0,
        'sector_limit': 99, 'mcap_strict': False, 'is_st_exclude': False,
        'ma_align_required': 0, 'trend_5d_min': 0, 'vol_trend_min': 0,
        'upper_shadow_max': 999, 'body_at_pct_min': 0, 'ma_converge_max': 999,
        'kdj_k_max': 999, 'rsi_6_max': 999, 'boll_dist_upper_min': -999,
        'macd_dif_min': -999, 'macd_dif_chg_required': 0, 'gap_pct_min': -999,
        'vp_same_dir_min': 0, 'vol_shrink_required': False, 'vol_top_div_max': 99,
        'vol_step_min': 0, 'promote_ratio_min': 0, 'prev_zt_avg_ret_min': -999,
        'strong_zt_ratio_min': 0, 'yang_bao_yin_required': False,
        'weekday_allow': 'all', 'avoid_report_window': False,
    }
else:
    p = dict(OPTIMAL_PARAMS)
    p['exit_strategy'] = 'close_t1'

print(f"[{mode} params] top_n={p.get('top_n')} board={p.get('board_filter')} vol_trend_min={p.get('vol_trend_min')} ma_align={p.get('ma_align_required')}")

year = month[:4]
mo = month[4:6]
end_day = "31" if mo in ("01","03","05","07","08","10","12") else ("30" if mo in ("04","06","09","11") else "28")
start = f"{year}-{mo}-01"
end = f"{year}-{mo}-{end_day}"

t0 = time.time()
r = zt.run_zt_backtest(
    start=start, end=end,
    top_n=p.get('top_n', 3),
    board_filter=p.get('board_filter', 'all'),
    sample=0,
    min_streak=p.get('min_streak', 1),
    max_streak=p.get('max_streak', 5),
    burst_max=p.get('burst_max', 99),
    turnover_min_pct=p.get('turnover_min_pct', 0),
    turnover_max_pct=p.get('turnover_max_pct', 100),
    mcap_min_yi=p.get('mcap_min_yi', 0),
    mcap_max_yi=p.get('mcap_max_yi', 999999),
    limit_order_min_yi=p.get('limit_order_min_yi', 0),
    _prebuilt=prebuilt_m,
    exit_strategy='close_t1',
    market_filter_mode=p.get('market_filter_mode', 'off'),
    breadth_up_min=p.get('breadth_up_min', 0),
    zt_pool_min=p.get('zt_pool_min', 0),
    vol_ratio_min=p.get('vol_ratio_min', 0),
    limit_up_pct_min=p.get('limit_up_pct_min', 9.5),
    burst_window_3d=p.get('burst_window_3d', 99),
    yiziban_required=p.get('yiziban_required', False),
    gap_open_required=p.get('gap_open_required', False),
    pct_chg_5d_max=p.get('pct_chg_5d_max', 999),
    pct_chg_5d_min=p.get('pct_chg_5d_min', 0),
    sector_limit=p.get('sector_limit', 99),
    mcap_strict=p.get('mcap_strict', False),
    is_st_exclude=p.get('is_st_exclude', False),
    ma_align_required=p.get('ma_align_required', 0),
    trend_5d_min=p.get('trend_5d_min', 0),
    vol_trend_min=p.get('vol_trend_min', 0),
    upper_shadow_max=p.get('upper_shadow_max', 999),
    body_at_pct_min=p.get('body_at_pct_min', 0),
    ma_converge_max=p.get('ma_converge_max', 999),
    kdj_k_max=p.get('kdj_k_max', 999),
    rsi_6_max=p.get('rsi_6_max', 999),
    boll_dist_upper_min=p.get('boll_dist_upper_min', -999),
    macd_dif_min=p.get('macd_dif_min', -999),
    macd_dif_chg_required=p.get('macd_dif_chg_required', 0),
    gap_pct_min=p.get('gap_pct_min', -999),
    vp_same_dir_min=p.get('vp_same_dir_min', 0),
    vol_shrink_required=p.get('vol_shrink_required', False),
    vol_top_div_max=p.get('vol_top_div_max', 99),
    vol_step_min=p.get('vol_step_min', 0),
    promote_ratio_min=p.get('promote_ratio_min', 0),
    prev_zt_avg_ret_min=p.get('prev_zt_avg_ret_min', -999),
    strong_zt_ratio_min=p.get('strong_zt_ratio_min', 0),
    yang_bao_yin_required=p.get('yang_bao_yin_required', False),
    weekday_allow=p.get('weekday_allow', 'all'),
    avoid_report_window=p.get('avoid_report_window', False),
)
s = r.get('summary', {})
elapsed = time.time() - t0

print(f"\n{'='*80}")
print(f"{month} 回测结果 (close_t1) [{mode}]")
print(f"{'='*80}")
print(f"  交易笔数: {s.get('trades',0)}")
print(f"  胜率: {s.get('win_rate_pct',0):.2f}%")
print(f"  日均收益: {s.get('daily_avg_return_pct',0):.3f}%")
print(f"  总收益: {s.get('total_return_pct',0):.2f}%")
print(f"  最大回撤: {s.get('max_drawdown_pct',0):.2f}%")
print(f"  日期数: {r.get('trade_dates_total',0)}")
print(f"  候选总数: {r.get('candidates_found',0)}")
print(f"  耗时: {elapsed:.1f}s")

# 7. 输出每笔交易明细
trades = r.get('trades', [])
if trades:
    print(f"\n{'='*95}")
    print(f"交易明细 ({len(trades)} 笔)")
    print(f"  说明: 涨停日 = buy_date - 1; 买入日 = T+1 开盘; 卖出日 = T+1 收盘")
    print(f"{'='*95}")
    print(f"{'#':>3} {'代码':<7} {'名称':<10} {'涨停日':<10} {'买入日':<10} {'卖出日':<10} {'买入价':>7} {'卖出价':>7} {'收益%':>7} {'胜?':>3}")
    print("-" * 95)
    wins = 0
    cum_ret = 0.0
    for i, t in enumerate(trades, 1):
        code = t.get('code', '?')
        name = t.get('name', '?')
        buy_date = str(t.get('buy_date', '?'))[-6:]
        sell_date = str(t.get('sell_date', '?'))[-6:]
        zt_date = str(t.get('pick_date', t.get('zt_date', '?')))[-6:]
        buy_px = t.get('buy_price', 0)
        sell_px = t.get('sell_price', 0)
        ret_pct = t.get('return_pct', t.get('pnl_pct', 0))
        is_win = ret_pct > 0
        if is_win:
            wins += 1
        cum_ret += ret_pct
        flag = '✓' if is_win else '✗'
        print(f"{i:>3} {code:<7} {name:<10} {zt_date:<10} {buy_date:<10} {sell_date:<10} {buy_px:>7.2f} {sell_px:>7.2f} {ret_pct:>+6.2f}% {flag:>3}")
    print("-" * 95)
    print(f"胜: {wins}/{len(trades)} ({100*wins/len(trades):.1f}%)  | 累计净收益: {cum_ret:+.2f}%")

# 8. Scenario 对比
sc = r.get('scenario_compare', {})
if sc:
    print(f"\n{'='*80}")
    print(f"Scenario 退场对比 (按累计收益排序)")
    print(f"{'='*80}")
    print(f"{'退场方式':<14} {'笔数':>5} {'胜率':>7} {'均收益':>8} {'累计':>9} {'盈亏比':>7}")
    print("-" * 60)
    for k, v in sorted(sc.items(), key=lambda x: -x[1].get('cum_return_pct', 0)):
        pf = v.get('profit_factor')
        pf_s = f"{pf:>6.2f}" if pf is not None else "    — "
        print(f"{k:<14} {v['n']:>5} {v['win_rate_pct']:>6.1f}% {v['avg_pct']:>+7.2f}% {v['cum_return_pct']:>+8.1f}% {pf_s:>7}")

sys.stdout.flush()
