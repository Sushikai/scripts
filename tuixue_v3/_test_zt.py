"""
Full-window zt_backtest test.
"""
import sys, logging, time
logging.basicConfig(level=logging.INFO, force=True, stream=sys.stderr)
sys.path.insert(0, '/Users/kaikai/scripts')

from tuixue_v3 import zt_backtest as zt

t0 = time.time()
r = zt.run_zt_backtest(
    start='2025-12-01', end='2026-04-30',
    top_n=3, board_filter='all', sample=0,
    min_streak=1, max_streak=5, burst_max=99,
    turnover_min_pct=0, turnover_max_pct=100,
    mcap_min_yi=0, mcap_max_yi=999999,
    limit_order_min_yi=0,
)
s = r.get('summary', {})
elapsed = time.time() - t0

result = (
    f"Trades: {s.get('trades',0)} | "
    f"胜率: {s.get('win_rate_pct',0)}% | "
    f"日均: {s.get('daily_avg_return_pct',0)}% | "
    f"总收益: {s.get('total_return_pct',0)}% | "
    f"最大回撤: {s.get('max_drawdown_pct',0)}%\n"
    f"Dates: {r.get('trade_dates_total',0)} | "
    f"Candidates: {r.get('candidates_found',0)} | "
    f"NoPool: {r.get('no_pool_days',0)}\n"
    f"Elapsed: {elapsed:.0f}s\n"
)
print(result)
# Print scenario compare
sc = r.get('scenario_compare', {})
if sc:
    print("Scenario compare (top exits):")
    for k, v in sorted(sc.items(), key=lambda x: -x[1].get('cum_return_pct', 0)):
        print(f"  {k:12s} n={v['n']:>4d} win={v['win_rate_pct']:>5.1f}% avg={v['avg_pct']:>6.2f}% cum={v['cum_return_pct']:>8.1f}% pf={v.get('profit_factor','—')}")
sys.stdout.flush()
