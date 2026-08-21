"""
更严格的过拟合检测 — 多窗口 rolling OOS
+ 关键参数消融 (把 vol_trend_min=2 / ma_align_required=1 拿掉看胜率崩不崩)
"""
import sys, logging, time, pickle
logging.basicConfig(level=logging.INFO, force=True, stream=sys.stderr)
sys.path.insert(0, '/Users/kaikai/scripts')

from tuixue_v3 import zt_backtest as zt
from tuixue_v3.zt_config import OPTIMAL_PARAMS

cache_path = "/tmp/zt_prebuilt_20251201_20260804_all.pkl"
with open(cache_path, "rb") as f:
    pb = pickle.load(f)

all_dates = sorted(pb[1])


def make_prebuilt(dates_subset):
    dates_set = set(dates_subset)
    zt_cache_m = {d: v for d, v in pb[3].items() if d in dates_set}
    mc_m = {d: v for d, v in pb[4].items() if d in dates_set} if pb[4] else {}
    return (pb[0], dates_subset, pb[2], zt_cache_m, mc_m)


def run_window(start_date, end_date, params):
    dates_subset = [d for d in all_dates if start_date <= d <= end_date]
    if not dates_subset:
        return None
    pre = make_prebuilt(dates_subset)
    t0 = time.time()
    r = zt.run_zt_backtest(
        start=f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}",
        end=f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}",
        top_n=params.get('top_n', 1), board_filter=params.get('board_filter', 'main'),
        sample=0,
        min_streak=params['min_streak'], max_streak=params['max_streak'],
        burst_max=params['burst_max'],
        turnover_min_pct=params['turnover_min_pct'], turnover_max_pct=params['turnover_max_pct'],
        mcap_min_yi=params['mcap_min_yi'], mcap_max_yi=params['mcap_max_yi'],
        limit_order_min_yi=params.get('limit_order_min_yi', 0),
        _prebuilt=pre,
        exit_strategy='close_t1',
        market_filter_mode=params.get('market_filter_mode', 'off'),
        breadth_up_min=params.get('breadth_up_min', 0),
        zt_pool_min=params.get('zt_pool_min', 0),
        vol_ratio_min=params.get('vol_ratio_min', 0),
        limit_up_pct_min=params.get('limit_up_pct_min', 9.5),
        burst_window_3d=params.get('burst_window_3d', 99),
        yiziban_required=params.get('yiziban_required', False),
        gap_open_required=params.get('gap_open_required', False),
        pct_chg_5d_max=params.get('pct_chg_5d_max', 999),
        pct_chg_5d_min=params.get('pct_chg_5d_min', 0),
        sector_limit=params.get('sector_limit', 99),
        mcap_strict=params.get('mcap_strict', False),
        is_st_exclude=params.get('is_st_exclude', False),
        ma_align_required=params.get('ma_align_required', 0),
        trend_5d_min=params.get('trend_5d_min', 0),
        vol_trend_min=params.get('vol_trend_min', 0),
        upper_shadow_max=params.get('upper_shadow_max', 999),
        body_at_pct_min=params.get('body_at_pct_min', 0),
        ma_converge_max=params.get('ma_converge_max', 999),
        kdj_k_max=params.get('kdj_k_max', 999),
        rsi_6_max=params.get('rsi_6_max', 999),
        boll_dist_upper_min=params.get('boll_dist_upper_min', -999),
        macd_dif_min=params.get('macd_dif_min', -999),
        macd_dif_chg_required=params.get('macd_dif_chg_required', 0),
        gap_pct_min=params.get('gap_pct_min', -999),
        vp_same_dir_min=params.get('vp_same_dir_min', 0),
        vol_shrink_required=params.get('vol_shrink_required', False),
        vol_top_div_max=params.get('vol_top_div_max', 99),
        vol_step_min=params.get('vol_step_min', 0),
        promote_ratio_min=params.get('promote_ratio_min', 0),
        prev_zt_avg_ret_min=params.get('prev_zt_avg_ret_min', -999),
        strong_zt_ratio_min=params.get('strong_zt_ratio_min', 0),
        yang_bao_yin_required=params.get('yang_bao_yin_required', False),
        weekday_allow=params.get('weekday_allow', 'all'),
        avoid_report_window=params.get('avoid_report_window', False),
    )
    s = r.get('summary', {})
    return {
        'start': start_date, 'end': end_date,
        'n_dates': len(dates_subset),
        'n_trades': s.get('trades', 0),
        'wr': s.get('win_rate_pct', 0),
        'ret': s.get('total_return_pct', 0),
        'dd': s.get('max_drawdown_pct', 0),
        'avg': s.get('avg_return_pct', 0),
        'elapsed': time.time() - t0,
    }


print(f"\n{'='*100}")
print(f"1. Rolling OOS — 每月单独跑, 每月都是独立 OOS 测试")
print(f"{'='*100}")

p = dict(OPTIMAL_PARAMS); p['exit_strategy'] = 'close_t1'
months = sorted(set(d[:6] for d in all_dates))
print(f"  {'月份':<10} {'日期数':>6} {'笔数':>5} {'胜率':>7} {'收益%':>8} {'回撤%':>7} {'均收益%':>9}  状态")
print(f"  {'-'*75}")

month_results = []
for m in months:
    m_start = next((d for d in all_dates if d.startswith(m)), None)
    m_end = next((d for d in reversed(all_dates) if d.startswith(m)), None)
    if not m_start or not m_end:
        continue
    w = run_window(m_start, m_end, p)
    if not w:
        continue
    month_results.append(w)
    if w['n_trades'] >= 3:
        if w['wr'] >= 70:
            flag = "✅"
        elif w['wr'] >= 50:
            flag = "⚠ "
        else:
            flag = "❌"
    else:
        flag = "·小样本"
    print(f"  {m:<10} {w['n_dates']:>6} {w['n_trades']:>5} {w['wr']:>6.1f}% {w['ret']:>+7.1f}% {w['dd']:>6.1f}% {w['avg']:>+8.2f}%  {flag}")

# 月份稳定性分析
big_months = [w for w in month_results if w['n_trades'] >= 3]
if big_months:
    wrs = [w['wr'] for w in big_months]
    rets = [w['ret'] for w in big_months]
    pos_months = sum(1 for w in big_months if w['ret'] > 0)
    print(f"\n  大样本月份 (n≥3): {len(big_months)} 个月")
    print(f"    胜率区间: {min(wrs):.1f}% ~ {max(wrs):.1f}% (spread={max(wrs)-min(wrs):.1f}pp)")
    print(f"    收益区间: {min(rets):+.1f}% ~ {max(rets):+.1f}%")
    print(f"    盈利月份: {pos_months}/{len(big_months)}")

# ── 2. 关键参数消融 (拿掉 vol_trend_min / ma_align_required 看胜率是否塌) ──
print(f"\n{'='*100}")
print(f"2. 关键参数消融 — 拿掉 vol_trend_min=2 / ma_align_required=1 看胜率")
print(f"{'='*100}")

ablations = [
    ('OPTIMAL_PARAMS (基线)', p),
    ('去掉 vol_trend_min=2', {**p, 'vol_trend_min': 0}),
    ('去掉 ma_align_required=1', {**p, 'ma_align_required': 0}),
    ('去掉 trend_5d_min=3', {**p, 'trend_5d_min': 0}),
    ('去掉 board=main', {**p, 'board_filter': 'all'}),
    ('全打开 (no filters)', {**p, 'vol_trend_min': 0, 'ma_align_required': 0,
                                'trend_5d_min': 0, 'board_filter': 'all',
                                'mcap_strict': False, 'is_st_exclude': False,
                                'gap_open_required': False, 'pct_chg_5d_max': 999,
                                'turnover_min_pct': 0, 'turnover_max_pct': 100,
                                'mcap_min_yi': 0, 'mcap_max_yi': 99999}),
]

print(f"  {'配置':<28} {'笔数':>5} {'胜率':>7} {'收益%':>8} {'回撤%':>7}  备注")
print(f"  {'-'*75}")
base_wr = None
for label, params in ablations:
    w = run_window(all_dates[0], all_dates[-1], params)
    if not w:
        continue
    if base_wr is None:
        base_wr = w['wr']
    delta = w['wr'] - base_wr
    if delta > -2:
        flag = "· 持平"
    elif delta > -10:
        flag = "⚠ 退化"
    else:
        flag = "❌ 关键参数!"
    print(f"  {label:<28} {w['n_trades']:>5} {w['wr']:>6.1f}% {w['ret']:>+7.1f}% {w['dd']:>6.1f}%  {flag} ({delta:+.1f}pp)")

# ── 3. 改个核心阈值看稳定性 ──
print(f"\n{'='*100}")
print(f"3. vol_trend_min 鲁棒性 — 1.5/2.0/2.5/3.0 各跑一遍")
print(f"{'='*100}")

for vt in [0, 1.0, 1.5, 2.0, 2.5, 3.0]:
    pp = {**p, 'vol_trend_min': vt}
    w = run_window(all_dates[0], all_dates[-1], pp)
    if w:
        print(f"  vol_trend_min={vt:<5} → 笔数={w['n_trades']:>3} 胜率={w['wr']:>6.1f}% 收益={w['ret']:>+7.1f}%")

print(f"\n{'='*100}")
print(f"4. 最终结论")
print(f"{'='*100}")
print(f"  关键观察:")
print(f"  - OOS 月度胜率分布 vs full 75%")
print(f"  - 关键参数 (vol_trend_min=2 / ma_align=1) 拿掉后胜率是否崩塌")
print(f"  - 月份波动是否在 ±15pp 内")

sys.stdout.flush()
