"""
Minimal zt_backtest test — debug which step hangs.
"""
import sys, logging, time
logging.basicConfig(level=logging.INFO, force=True, stream=sys.stderr)
sys.path.insert(0, '/Users/kaikai/scripts')

from tuixue_v3 import data_layer as dl
from tuixue_v3 import cache_db as cdb
from tuixue_v3 import zt_config as cfg

t0 = time.time()

# Step 1: trade dates
dates = dl.fetch_trade_dates('2026-03-01', '2026-03-15')
print(f"Step1 trade_dates: {len(dates)} ({time.time()-t0:.1f}s)", flush=True)

# Step 2: stock list
all_stocks = dl.fetch_stock_list_all()
print(f"Step2 stocks: {len(all_stocks)} ({time.time()-t0:.1f}s)", flush=True)

# Step 3: load cache for all stocks
daily_cache = {}
hits = 0
codes_all = [c for c,_ in all_stocks]
for i, c in enumerate(codes_all):
    df = cdb.daily().get(c, 80)
    if df is not None and not df.empty:
        daily_cache[c] = df
        hits += 1
    if (i+1) % 1000 == 0:
        print(f"Step3 {i+1}/{len(codes_all)} hits={hits} ({time.time()-t0:.1f}s)", flush=True)
print(f"Step3 done: {hits}/{len(codes_all)} ({time.time()-t0:.1f}s)", flush=True)
