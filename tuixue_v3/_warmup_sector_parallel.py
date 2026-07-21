"""
退学 v3 · Sector 缓存快速预热 (并行版)
现在 get_sector 的 HTTP 调用在锁外, 可以真正并行.

用法: cd /Users/kaikai/scripts && PYTHONPATH=. python3 tuixue_v3/_warmup_sector_parallel.py
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from concurrent.futures import ThreadPoolExecutor, as_completed

from tuixue_v3.web.sector_classify import get_sector, _load_cache
from tuixue_v3.data_layer import fetch_stock_list

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

log("获取股票列表…")
all_stocks = fetch_stock_list() or []
main_stocks = [(c, n) for c, n in all_stocks if c and len(c) == 6 and c.isdigit() and not c.startswith(("300","301","688","8","9"))]
total = min(len(main_stocks), 5000)
codes = [c for c, _ in main_stocks[:total]]

# 只补缺失的
snap = _load_cache().get("stocks", {})
missing = [c for c in codes if c not in snap or not snap[c].get("sw")]
log(f"共 {len(codes)} 只股票, 已有 {len(codes)-len(missing)} 只行业, 需补 {len(missing)} 只")

if not missing:
    log("全部已缓存, 无需预热")
    sys.exit(0)

t0 = time.time()
done = 0
ok = 0

with ThreadPoolExecutor(max_workers=20) as ex:
    futs = {ex.submit(get_sector, c, False): c for c in missing}
    for f in as_completed(futs, timeout=600):
        try:
            r = f.result(timeout=5)
            if r and r.get("sw"):
                ok += 1
        except Exception:
            pass
        done += 1
        if done % 100 == 0:
            elapsed = time.time() - t0
            log(f"  {done}/{len(missing)} · {ok} 成功 · {elapsed:.0f}s")

elapsed = time.time() - t0
log(f"完成! {done}/{len(missing)} 只, {ok} 有行业, 耗时 {elapsed:.0f}s")
