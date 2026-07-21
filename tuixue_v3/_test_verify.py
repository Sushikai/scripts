"""
Verify using best params from optimizer (skip optimization, just verify).
"""
import sys, logging, time
logging.basicConfig(level=logging.INFO, force=True, stream=sys.stderr)
sys.path.insert(0, '/Users/kaikai/scripts')

from tuixue_v3 import zt_backtest as zt
from tuixue_v3 import zt_verify as vfy
from tuixue_v3 import zt_config as cfg

params = cfg.OPTIMAL_PARAMS

for label, start, end in [
    ("优化窗口 (In-sample)", cfg.ZT_START, cfg.ZT_OPTIMIZE_WINDOW_END),
    ("留出验证 (Out-of-sample)", cfg.ZT_OPTIMIZE_WINDOW_END, cfg.ZT_END),
]:
    t0 = time.time()
    r = zt.run_zt_backtest(start=start, end=end, **params)
    s = r.get('summary', {}) or {}
    el = time.time() - t0
    print(f"\n=== {label} ===")
    print(f"  笔数={s.get('trades',0)} | 胜率={s.get('win_rate_pct',0):.1f}% | "
          f"平均={s.get('avg_return_pct',0):.2f}% | "
          f"总收益={s.get('total_return_pct',0):.1f}% | "
          f"回撤={s.get('max_drawdown_pct',0):.1f}% | "
          f"日均(简单)={s.get('daily_avg_return_pct',0):.2f}%")

# Check targets on holdout
passed, reasons = vfy._check_targets(r.get('summary',{}), r)
print(f"\n=== 达标检查: {'✅通过' if passed else '❌失败'} ===")
if reasons:
    for rr in reasons:
        print(f"  - {rr}")
