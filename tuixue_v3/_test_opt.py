"""
Full optimizer — 100 iterations.
"""
import sys, logging
logging.basicConfig(level=logging.INFO, force=True, stream=sys.stderr)
sys.path.insert(0, '/Users/kaikai/scripts')

from tuixue_v3 import zt_optimizer as opt

r = opt.run_optimize(
    start='2025-12-01', end='2026-04-30',
    iterations=100, population=20,
)
print("\n=== Best Params ==")
for k, v in r['best_params'].items():
    print(f"  {k}: {v}")
s = r['best_result'].get('summary', {}) or {}
print(f"\nScore: {r['best_score']:.1f}")
print(f"Trades: {s.get('trades',0)} | 胜率: {s.get('win_rate_pct',0)}% | "
      f"总收益: {s.get('total_return_pct',0)}% | 回撤: {s.get('max_drawdown_pct',0)}%")
print(f"Elapsed: {r['elapsed_sec']:.0f}s")
