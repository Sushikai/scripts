"""
退学 v3 · Sector 缓存预热
先跑这个填充板块缓存, 再跑 1000 轮优化
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tuixue_v3.web.sector_classify import get_sector, bulk_get_sector
from tuixue_v3.data_layer import fetch_stock_list

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

log("获取股票列表…")
all_stocks = fetch_stock_list() or []
# 主板 (同优化器过滤)
main_stocks = [(c, n) for c, n in all_stocks if c and len(c) == 6 and c.isdigit() and not c.startswith(("300","301","688","8","9"))]
total = min(len(main_stocks), 2000)  # 预填充 2000 只就够了 (最大 sample)
codes = [c for c, _ in main_stocks[:total]]

log(f"预热板块缓存: {total} 只…")
t0 = time.time()
done = 0
for c in codes:
    try:
        r = get_sector(c, force_refresh=False)
        # get_sector 自动写缓存
    except Exception as e:
        pass
    done += 1
    if done % 200 == 0:
        elapsed = time.time() - t0
        log(f"  {done}/{total}, {elapsed:.0f}s")

elapsed = time.time() - t0
log(f"完成! {total} 只, 耗时 {elapsed:.0f}s ({elapsed/max(1,total):.2f}s/只)")
