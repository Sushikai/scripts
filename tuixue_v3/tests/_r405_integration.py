#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R405 跨境行情 — 集成测试"""
import json, os, sys, urllib.request

BASE = os.environ.get("TUIXUE_BASE", "http://127.0.0.1:7799")

results = []
def chk(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")

def get(path, timeout=30):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"_err": str(e)}

print("=" * 70)
print("R405 · 跨境行情 — 集成测试")
print("=" * 70)

# 1) 港股腾讯
print("\n[1] 港股 00700 (腾讯)")
r = get("/api/yeren/cross_border?code=00700&market=HK")
chk("1.1 ok=true", r.get("ok") is True, f"err={r.get('error')}")
data = r.get("data", {})
chk("1.2 market=HK", data.get("market") == "HK")
chk("1.3 currency=HKD", data.get("currency") == "HKD")
chk("1.4 last_price > 0", data.get("last_price", 0) > 0)
chk("1.5 fx_to_cny=0.91", data.get("fx_to_cny") == 0.91)
chk("1.6 cny_price = last * 0.91",
    abs(data.get("cny_price", 0) - data.get("last_price", 0) * 0.91) < 0.01,
    f"got {data.get('cny_price')}")
chk("1.7 has is_trading bool",
    isinstance(data.get("is_trading"), bool))
chk("1.8 change_pct in [-5, 5]",
    -5 <= data.get("change_pct", 100) <= 5,
    f"got {data.get('change_pct')}")

# 2) 美股 AAPL
print("\n[2] 美股 AAPL")
r2 = get("/api/yeren/cross_border?code=AAPL&market=US")
chk("2.1 ok=true", r2.get("ok") is True)
d2 = r2.get("data", {})
chk("2.2 market=US", d2.get("market") == "US")
chk("2.3 currency=USD", d2.get("currency") == "USD")
chk("2.4 fx_to_cny=7.10", d2.get("fx_to_cny") == 7.10)
chk("2.5 cny_price = last * 7.10",
    abs(d2.get("cny_price", 0) - d2.get("last_price", 0) * 7.10) < 0.01)

# 3) 同一股票确定性 (mock seed)
print("\n[3] mock 确定性 (同 code 同价)")
r3 = get("/api/yeren/cross_border?code=00700&market=HK")
chk("3.1 same last_price",
    r3.get("data", {}).get("last_price") == data.get("last_price"))

# 4) 非法 market
print("\n[4] market=JP → 400")
r4 = get("/api/yeren/cross_border?code=00700&market=JP")
chk("4.1 rejected",
    not r4.get("ok") and r4.get("status_code") == 400)

# 5) 港股非法 code
print("\n[5] HK code=123 (3 位) → 400")
r5 = get("/api/yeren/cross_border?code=123&market=HK")
chk("5.1 rejected",
    not r5.get("ok") and r5.get("status_code") == 400)

# 6) 美股非法 code
print("\n[6] US code=123 (数字) → 400")
r6 = get("/api/yeren/cross_border?code=123&market=US")
chk("6.1 rejected",
    not r6.get("ok") and r6.get("status_code") == 400)

# 7) 缓存命中
print("\n[7] 缓存命中")
import time
t0 = time.time()
r7 = get("/api/yeren/cross_border?code=AAPL&market=US")
elapsed = time.time() - t0
chk("7.1 second call < 200ms",
    elapsed < 0.2, f"elapsed={elapsed:.3f}s")

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R405 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)