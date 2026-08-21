#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R416 全球宏观 — 集成测试"""
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
print("R416 · 全球宏观 — 集成测试")
print("=" * 70)

# 1) 美国
print("\n[1] US (美国)")
r = get("/api/yeren/macro_global?country=US")
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
snap = data.get("snapshot", {})
chk("1.2 country=US",
    data.get("country") == "US")
chk("1.3 gdp > 0",
    snap.get("gdp_yoy_pct", 0) > 0)
chk("1.4 cpi > 0",
    isinstance(snap.get("cpi_yoy_pct"), (int, float)))
chk("1.5 pmi > 50 (扩张)",
    snap.get("pmi", 0) > 50)
chk("1.6 currency=USD",
    snap.get("currency") == "USD")
chk("1.7 has phase",
    data.get("phase") in ("扩张期", "滞胀风险", "收缩期", "温和复苏"))
chk("1.8 has policy",
    "偏鹰" in data.get("policy", "")
    or "偏鸽" in data.get("policy", "")
    or "中性" in data.get("policy", ""))
chk("1.9 cn_link 非空",
    len(data.get("cn_link", "")) > 0)

# 2) 中国
print("\n[2] CN (中国)")
r2 = get("/api/yeren/macro_global?country=CN")
chk("2.1 currency=CNY",
    r2.get("data", {}).get("snapshot", {}).get("currency") == "CNY")
chk("2.2 central_bank=PBOC",
    r2.get("data", {}).get("snapshot", {}).get("central_bank") == "PBOC")
chk("2.3 fx_to_cny=1.0",
    r2.get("data", {}).get("snapshot", {}).get("fx_to_cny") == 1.0)

# 3) EU 欧元区
print("\n[3] EU (欧元区)")
r3 = get("/api/yeren/macro_global?country=EU")
chk("3.1 ECB",
    r3.get("data", {}).get("snapshot", {}).get("central_bank") == "ECB")
chk("3.2 phase=收缩期 (PMI<50)",
    r3.get("data", {}).get("phase") == "收缩期",
    f"pmi=49.7 got {r3.get('data',{}).get('phase')}")

# 4) JP 日本
print("\n[4] JP (日本)")
r4 = get("/api/yeren/macro_global?country=JP")
chk("4.1 BOJ",
    r4.get("data", {}).get("snapshot", {}).get("central_bank") == "BOJ")
chk("4.2 rate_pct 接近 0",
    r4.get("data", {}).get("snapshot", {}).get("rate_pct", 100) < 1)

# 5) 4 国联动
print("\n[5] 4 国对比")
for c in ["US", "CN", "EU", "JP"]:
    rr = get(f"/api/yeren/macro_global?country={c}")
    s = rr.get("data", {}).get("snapshot", {})
    chk(f"5.{c} ok",
        rr.get("ok") is True
        and s.get("gdp_yoy_pct") is not None
        and s.get("cpi_yoy_pct") is not None
        and s.get("pmi") is not None,
        f"gdp={s.get('gdp_yoy_pct')}, cpi={s.get('cpi_yoy_pct')}, pmi={s.get('pmi')}")

# 6) 非法 country
print("\n[6] country=UK → 400")
r6 = get("/api/yeren/macro_global?country=UK")
chk("6.1 rejected",
    not r6.get("ok") and r6.get("status_code") == 400)

# 7) 缓存
print("\n[7] 缓存")
import time
t0 = time.time()
r7 = get("/api/yeren/macro_global?country=US")
elapsed = time.time() - t0
chk("7.1 cached < 200ms",
    elapsed < 0.2, f"elapsed={elapsed:.3f}s")

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R416 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)