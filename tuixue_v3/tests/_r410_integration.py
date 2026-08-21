#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R410 ETF 套利 — 集成测试"""
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
print("R410 · ETF 套利 — 集成测试")
print("=" * 70)

# 1) 默认
print("\n[1] 510300 (沪深 300ETF)")
r = get("/api/yeren/etf_arb?code=510300")
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
chk("1.2 iopv > 0",
    data.get("iopv", 0) > 0, f"got {data.get('iopv')}")
chk("1.3 market_price > 0",
    data.get("market_price", 0) > 0)
chk("1.4 premium_pct 在 ±1%",
    abs(data.get("premium_pct", 100)) < 1,
    f"got {data.get('premium_pct')}%")
chk("1.5 arb_threshold_pct = 0.15",
    data.get("arb_threshold_pct") == 0.15)
chk("1.6 has direction",
    data.get("direction") in ("正向套利 (申购卖出)", "反向套利 (买入赎回)", "无套利空间"))
chk("1.7 has steps list",
    isinstance(data.get("steps"), list) and len(data.get("steps", [])) > 0)

# 2) 折溢价关系
print("\n[2] premium 计算正确")
m = data.get("market_price", 0)
i = data.get("iopv", 0)
expected_prem = round((m - i) / i * 100, 4)
chk("2.1 premium_pct 公式",
    abs(data.get("premium_pct", 0) - expected_prem) < 0.001,
    f"got {data.get('premium_pct')} vs {expected_prem}")

# 3) arb_opportunity 与 |premium| 一致
print("\n[3] arb_opportunity 阈值判定")
prem_abs = abs(data.get("premium_pct", 0))
chk("3.1 arb_opportunity == |premium| >= 0.15",
    data.get("arb_opportunity", False) == (prem_abs >= 0.15),
    f"prem={prem_abs} opp={data.get('arb_opportunity')}")

# 4) 确定性
print("\n[4] mock 确定性")
r4 = get("/api/yeren/etf_arb?code=510300")
chk("4.1 same iopv",
    r4.get("data", {}).get("iopv") == data.get("iopv"))
chk("4.2 same market_price",
    r4.get("data", {}).get("market_price") == data.get("market_price"))

# 5) 不同 ETF
print("\n[5] 不同 ETF")
r5 = get("/api/yeren/etf_arb?code=510500")
chk("5.1 ok=true", r5.get("ok") is True)
# 不一定相同
chk("5.2 iopv > 0",
    r5.get("data", {}).get("iopv", 0) > 0)

# 6) 非法 code
print("\n[6] 非法 code → 400")
r6 = get("/api/yeren/etf_arb?code=abc")
chk("6.1 rejected",
    not r6.get("ok") and r6.get("status_code") == 400)

# 7) 短 TTL
print("\n[7] 缓存 (30s 短 TTL)")
import time
t0 = time.time()
r7 = get("/api/yeren/etf_arb?code=510300")
elapsed = time.time() - t0
chk("7.1 cached < 200ms",
    elapsed < 0.2, f"elapsed={elapsed:.3f}s")

# 8) direction 文案
print("\n[8] direction 与 premium 符号一致")
prem = data.get("premium_pct", 0)
d = data.get("direction", "")
if prem > 0.15:
    chk("8.1 溢价 → 正向", "正向" in d)
elif prem < -0.15:
    chk("8.1 折价 → 反向", "反向" in d)
else:
    chk("8.1 无套利", "无套利" in d)

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R410 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)