"""
Validate best params on holdout period (May-Jun 2026).
"""
import sys, logging, time
logging.basicConfig(level=logging.INFO, force=True, stream=sys.stderr)
sys.path.insert(0, '/Users/kaikai/scripts')

from tuixue_v3 import zt_backtest as zt

best_params = dict(
    min_streak=2, max_streak=3, burst_max=0, sealed_before="10:00",
    mcap_min_yi=10.0, mcap_max_yi=500.0,
    turnover_min_pct=2.0, turnover_max_pct=50.0,
    limit_order_min_yi=0.3,
    top_n=5,
    trail_activate_pct=1.0, trail_pullback_pct=0.5, stop_loss_pct=-5.0,
)

# Holdout: May-Jun 2026
for period_label, start, end in [
    ("优化窗口 (验证)", "2025-12-01", "2026-04-30"),
    ("留出验证", "2026-05-01", "2026-06-30"),
]:
    t0 = time.time()
    r = zt.run_zt_backtest(start=start, end=end, **best_params)
    s = r.get('summary', {}) or {}
    elapsed = time.time() - t0
    print(f"\n=== {period_label} ({start}→{end}) ===")
    print(f"  笔数={s.get('trades',0)} | 胜率={s.get('win_rate_pct',0):.1f}% | "
          f"平均={s.get('avg_return_pct',0):.2f}% | "
          f"总收益={s.get('total_return_pct',0):.1f}% | "
          f"回撤={s.get('max_drawdown_pct',0):.1f}% | "
          f"日均={s.get('daily_avg_return_pct',0):.2f}%")
    print(f"  耗时: {elapsed:.0f}s")
