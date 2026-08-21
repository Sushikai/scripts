#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R411 北交所 — 集成测试"""
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
print("R411 · 北交所 — 集成测试")
print("=" * 70)

# 1) 北交所代码 (83xxxx)
print("\n[1] 北交所 830xxx")
r = get("/api/yeren/bj_stock?code=830001")
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
chk("1.2 is_bj=true", data.get("is_bj") is True)
chk("1.3 exchange=北交所",
    data.get("exchange") == "北交所")
chk("1.4 limit_up = prev * 1.30",
    abs(data.get("limit_up", 0) - data.get("prev_close", 0) * 1.30) < 0.5)
chk("1.5 limit_down = prev * 0.70",
    abs(data.get("limit_down", 0) - data.get("prev_close", 0) * 0.70) < 0.5)
chk("1.6 rules.price_limit_pct = 30",
    data.get("rules", {}).get("price_limit_pct") == 30)

# 2) 主板代码 (60xxxx)
print("\n[2] 主板 600519")
r2 = get("/api/yeren/bj_stock?code=600519")
chk("2.1 is_bj=false",
    r2.get("data", {}).get("is_bj") is False)
chk("2.2 exchange=上交所/深交所",
    r2.get("data", {}).get("exchange") == "上交所/深交所")
chk("2.3 rules.price_limit_pct = 10",
    r2.get("data", {}).get("rules", {}).get("price_limit_pct") == 10)

# 3) BJ 43 前缀
print("\n[3] 43xxxx")
r3 = get("/api/yeren/bj_stock?code=430001")
chk("3.1 is_bj=true", r3.get("data", {}).get("is_bj") is True)

# 4) 87/88 前缀
print("\n[4] 87xxxx / 88xxxx")
r4a = get("/api/yeren/bj_stock?code=870001")
r4b = get("/api/yeren/bj_stock?code=880001")
chk("4.1 87 is_bj", r4a.get("data", {}).get("is_bj") is True)
chk("4.2 88 is_bj", r4b.get("data", {}).get("is_bj") is True)

# 5) 非 BJ 前缀
print("\n[5] 000xxx 非北交所")
r5 = get("/api/yeren/bj_stock?code=000001")
chk("5.1 is_bj=false", r5.get("data", {}).get("is_bj") is False)

# 6) 交易规则字段
print("\n[6] 交易规则完整性")
rules = data.get("rules", {})
chk("6.1 has lot_size",
    rules.get("lot_size") == 100)
chk("6.2 has tick_size",
    rules.get("tick_size") == 0.01)
chk("6.3 has trading_hours",
    "9:30" in rules.get("trading_hours", ""))
chk("6.4 has min_capital_wan",
    rules.get("min_capital_wan") == 50)

# 7) 非法 code
print("\n[7] 非法 code → 400")
r7 = get("/api/yeren/bj_stock?code=abc")
chk("7.1 rejected",
    not r7.get("ok") and r7.get("status_code") == 400)

# 8) 缓存
print("\n[8] 缓存命中")
import time
t0 = time.time()
r8 = get("/api/yeren/bj_stock?code=830001")
elapsed = time.time() - t0
chk("8.1 cached < 200ms",
    elapsed < 0.2, f"elapsed={elapsed:.3f}s")

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R411 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)