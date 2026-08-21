#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R386 模拟组合 — 集成测试"""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("TUIXUE_BASE", "http://127.0.0.1:7799")

results = []
def chk(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")

def post(path, body, timeout=120):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"_err": str(e)}

print("=" * 70)
print("R386 · 模拟组合 — 集成测试")
print("=" * 70)

# 1) 2 只股票, 决策日期 2026-06-15
print("\n[1] 2 holdings (买入/回避) → nav + stats")
body = {
    "initial": 100000,
    "holdings": [
        {"code": "600519", "weight": 0.5, "decision_date": "20260615", "verdict": "买入"},
        {"code": "000001", "weight": 0.5, "decision_date": "20260615", "verdict": "回避"},
    ],
}
r = post("/api/yeren/portfolio/sim", body, timeout=120)
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data") or {}
nav = data.get("nav", [])
stats = data.get("stats", {})
chk("1.2 nav len >= 5", len(nav) >= 5, f"got {len(nav)}")
chk("1.3 nav[0].value == initial",
    abs(nav[0]["value"] - 100000) < 1 if nav else False,
    f"first={nav[0]['value'] if nav else 'none'}")
chk("1.4 stats.total_ret_pct present",
    "total_ret_pct" in stats)
chk("1.5 stats.max_drawdown_pct >= 0",
    stats.get("max_drawdown_pct", -1) >= 0,
    f"dd={stats.get('max_drawdown_pct')}")
chk("1.6 stats.sharpe finite",
    isinstance(stats.get("sharpe"), (int, float)) and stats["sharpe"] != 0)
chk("1.7 stats.codes 包含 600519,000001",
    "600519" in stats.get("codes", []) and "000001" in stats.get("codes", []))

# 2) 边界: 空 holdings
print("\n[2] empty holdings → 400")
r2 = post("/api/yeren/portfolio/sim", {"holdings": [], "initial": 100000})
chk("2.1 empty holdings rejected",
    not r2.get("ok") and r2.get("status_code") == 400,
    f"err={r2.get('error')}")

# 3) 边界: 无效 code (5 位)
print("\n[3] invalid code (5 位) → 自动跳过")
body3 = {
    "initial": 100000,
    "holdings": [
        {"code": "12345", "weight": 0.5, "decision_date": "20260615", "verdict": "买入"},
        {"code": "600519", "weight": 0.5, "decision_date": "20260615", "verdict": "买入"},
    ],
}
r3 = post("/api/yeren/portfolio/sim", body3, timeout=120)
chk("3.1 invalid code skipped, valid used",
    r3.get("ok") is True,
    f"err={r3.get('error')}")
chk("3.2 codes 只含 600519",
    r3.get("data", {}).get("stats", {}).get("codes") == ["600519"])

# 4) 3 holdings
print("\n[4] 3 holdings (茅台/平安/宁德)")
body4 = {
    "initial": 300000,
    "holdings": [
        {"code": "600519", "weight": 0.4, "decision_date": "20260615", "verdict": "买入"},
        {"code": "000001", "weight": 0.3, "decision_date": "20260615", "verdict": "回避"},
        {"code": "300750", "weight": 0.3, "decision_date": "20260615", "verdict": "观望"},
    ],
}
r4 = post("/api/yeren/portfolio/sim", body4, timeout=120)
chk("4.1 ok=true", r4.get("ok") is True)
chk("4.2 codes 3 只",
    len(r4.get("data", {}).get("stats", {}).get("codes", [])) == 3,
    f"codes={r4.get('data',{}).get('stats',{}).get('codes')}")
chk("4.3 nav[0] == initial",
    abs(r4["data"]["nav"][0]["value"] - 300000) < 1)

# 5) 缓存命中
print("\n[5] 缓存命中")
import time
t0 = time.time()
r5 = post("/api/yeren/portfolio/sim", body, timeout=30)
elapsed = time.time() - t0
chk("5.1 second call < 1.5s",
    elapsed < 1.5,
    f"elapsed={elapsed:.2f}s")
chk("5.2 second call same nav len",
    len(r5.get("data", {}).get("nav", [])) == len(nav))

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R386 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)