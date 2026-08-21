#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R412 港股通 — 集成测试"""
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
print("R412 · 港股通 — 集成测试")
print("=" * 70)

# 1) 主板 00700 (腾讯)
print("\n[1] 主板 00700 (腾讯)")
r = get("/api/yeren/gtja_south?code=00700")
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
chk("1.2 board=main",
    data.get("board") == "main")
chk("1.3 board_name=主板",
    "主板" in data.get("board_name", ""))
chk("1.4 last_price_hkd > 0",
    data.get("last_price_hkd", 0) > 0)
chk("1.5 fx_hkd_to_cny=0.91",
    data.get("fx_hkd_to_cny") == 0.91)
chk("1.6 cny_price = hkd × 0.91",
    abs(data.get("cny_price", 0)
        - data.get("last_price_hkd", 0) * 0.91) < 0.01,
    f"got {data.get('cny_price')}")
chk("1.7 has is_gtja bool",
    isinstance(data.get("is_gtja"), bool))

# 2) 南向资金
print("\n[2] 南向资金")
chk("2.1 south_net_yi_hkd 有值",
    isinstance(data.get("south_net_yi_hkd"), (int, float)))
chk("2.2 south_direction 流入/流出",
    data.get("south_direction") in ("净流入", "净流出"),
    f"got {data.get('south_direction')}")
chk("2.3 5 日历史资金",
    isinstance(data.get("south_history_5d"), list)
    and len(data.get("south_history_5d", [])) == 5)

# 3) 创业板 GEM 80000
print("\n[3] 创业板 80001")
r3 = get("/api/yeren/gtja_south?code=80001")
chk("3.1 ok=true", r3.get("ok") is True)
chk("3.2 board=gem",
    r3.get("data", {}).get("board") == "gem")
chk("3.3 board_name=GEM",
    "GEM" in r3.get("data", {}).get("board_name", ""))

# 4) 确定性
print("\n[4] mock 确定性")
r4 = get("/api/yeren/gtja_south?code=00700")
chk("4.1 same last_price_hkd",
    r4.get("data", {}).get("last_price_hkd") == data.get("last_price_hkd"))

# 5) 错误路径
print("\n[5] 非法 code")
r5a = get("/api/yeren/gtja_south?code=1234")  # 4 位
chk("5.1 4 位 → 400",
    not r5a.get("ok") and r5a.get("status_code") == 400)
r5b = get("/api/yeren/gtja_south?code=abc")
chk("5.2 非数字 → 400",
    not r5b.get("ok") and r5b.get("status_code") == 400)
r5c = get("/api/yeren/gtja_south?code=12345a")
chk("5.3 含字母 → 400",
    not r5c.get("ok") and r5c.get("status_code") == 400)

# 6) 缓存
print("\n[6] 缓存命中")
import time
t0 = time.time()
r6 = get("/api/yeren/gtja_south?code=00700")
elapsed = time.time() - t0
chk("6.1 cached < 200ms",
    elapsed < 0.2, f"elapsed={elapsed:.3f}s")

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R412 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)