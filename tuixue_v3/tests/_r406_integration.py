#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R406 实时盘口 — 集成测试"""
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
print("R406 · 实时盘口 — 集成测试")
print("=" * 70)

# 1) 五档盘口
print("\n[1] 600519 五档盘口")
r = get("/api/yeren/orderbook?code=600519")
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
chk("1.2 has 5 bids", len(data.get("bids", [])) == 5)
chk("1.3 has 5 asks", len(data.get("asks", [])) == 5)
chk("1.4 bids descending", all(
    data["bids"][i]["price"] > data["bids"][i + 1]["price"]
    for i in range(4)))
chk("1.5 asks ascending", all(
    data["asks"][i]["price"] < data["asks"][i + 1]["price"]
    for i in range(4)))
chk("1.6 bid1 > ask1 (有价差)",
    data["bids"][0]["price"] < data["asks"][0]["price"],
    f"bid1={data['bids'][0]['price']} ask1={data['asks'][0]['price']}")

# 2) 委比
print("\n[2] 委比计算")
bid_v = data.get("bid_vol_total", 0)
ask_v = data.get("ask_vol_total", 0)
chk("2.1 committee_ratio_pct in [-100, 100]",
    -100 <= data.get("committee_ratio_pct", 0) <= 100,
    f"got {data.get('committee_ratio_pct')}")
expected_cr = round((bid_v - ask_v) / (bid_v + ask_v) * 100, 2) if (bid_v + ask_v) else 0
chk("2.2 committee_ratio 正确",
    abs(data.get("committee_ratio_pct", 0) - expected_cr) < 0.01)

# 3) 量比
print("\n[3] 量比")
chk("3.1 vol_ratio in [0.5, 3.0]",
    0.5 <= data.get("vol_ratio", 0) <= 3.0,
    f"got {data.get('vol_ratio')}")

# 4) 逐笔成交
print("\n[4] 逐笔成交")
trades = data.get("trades", [])
chk("4.1 has 5 trades", len(trades) == 5)
chk("4.2 trade price near last_price",
    all(abs(t["price"] - data["last_price"]) < data["spread"] * 2
        for t in trades))
chk("4.3 direction in 买/卖",
    all(t["direction"] in ("买", "卖") for t in trades))

# 5) depth_imbalance 文案
print("\n[5] 深度失衡文案")
chk("5.1 imbalance in [买盘优势, 卖盘优势, 均衡]",
    data.get("depth_imbalance") in ("买盘优势", "卖盘优势", "均衡"),
    f"got {data.get('depth_imbalance')}")

# 6) 确定性
print("\n[6] 同 code 同价 (mock seed)")
r6 = get("/api/yeren/orderbook?code=600519")
chk("6.1 same last_price",
    r6.get("data", {}).get("last_price") == data.get("last_price"))

# 7) 非法 code
print("\n[7] 非法 code → 400")
r7 = get("/api/yeren/orderbook?code=abc")
chk("7.1 rejected",
    not r7.get("ok") and r7.get("status_code") == 400)

# 8) 短 TTL
print("\n[8] 缓存 (10s 短 TTL)")
import time
t0 = time.time()
r8 = get("/api/yeren/orderbook?code=000001")
elapsed = time.time() - t0
chk("8.1 cached call < 200ms",
    elapsed < 0.2, f"elapsed={elapsed:.3f}s")

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R406 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)