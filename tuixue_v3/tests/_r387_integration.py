#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R387 组合再平衡 — 集成测试"""
import json, os, sys, time, urllib.request

BASE = os.environ.get("TUIXUE_BASE", "http://127.0.0.1:7799")

results = []
def chk(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")

def post(path, body, timeout=60):
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
print("R387 · 组合再平衡 — 集成测试")
print("=" * 70)

# 1) 标准 3 持仓, 偏离 ≥10%
print("\n[1] 3 holdings, 偏离 ≥10%")
body = {
    "holdings": [
        {"code": "600519", "target_weight": 0.5, "current_value": 70000},
        {"code": "000001", "target_weight": 0.3, "current_value": 20000},
        {"code": "300750", "target_weight": 0.2, "current_value": 10000},
    ],
    "threshold": 0.1,
}
r = post("/api/yeren/portfolio/rebalance", body)
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
adjusts = data.get("adjusts", [])
stats = data.get("stats", {})
chk("1.2 3 adjusts", len(adjusts) == 3)
chk("1.3 600519 偏离 +0.2 → sell",
    adjusts[0]["action"] == "sell" and adjusts[0]["needs_rebalance"] is True
    and adjusts[0]["amount"] == 20000)
chk("1.4 stats.max_deviation == 0.2",
    abs(stats["max_deviation"] - 0.2) < 0.001)
chk("1.5 stats.n_to_rebalance == 3",
    stats["n_to_rebalance"] == 3)
chk("1.6 stats.total_impact == 40000",
    stats["total_impact"] == 40000)
chk("1.7 stats.turnover_pct == 40",
    stats["turnover_pct"] == 40)

# 2) 偏离 < 阈值 → 不再平衡
print("\n[2] 偏离 < 阈值 → 全部 hold")
body2 = {
    "holdings": [
        {"code": "600519", "target_weight": 0.5, "current_value": 51000},
        {"code": "000001", "target_weight": 0.3, "current_value": 29000},
        {"code": "300750", "target_weight": 0.2, "current_value": 20000},
    ],
    "threshold": 0.15,
}
r2 = post("/api/yeren/portfolio/rebalance", body2)
data2 = r2.get("data", {})
adjusts2 = data2.get("adjusts", [])
stats2 = data2.get("stats", {})
chk("2.1 全部 hold",
    all(a["action"] == "hold" and a["needs_rebalance"] is False for a in adjusts2))
chk("2.2 n_to_rebalance == 0",
    stats2["n_to_rebalance"] == 0)
chk("2.3 total_impact == 0",
    stats2["total_impact"] == 0)

# 3) 空 holdings
print("\n[3] empty → 400")
r3 = post("/api/yeren/portfolio/rebalance", {"holdings": [], "threshold": 0.1})
chk("3.1 empty rejected", not r3.get("ok") and r3.get("status_code") == 400)

# 4) 阈值越界
print("\n[4] 阈值越界 (>1) → 400")
r4 = post("/api/yeren/portfolio/rebalance",
          {"holdings": [{"code": "600519", "target_weight": 0.5, "current_value": 100}],
           "threshold": 1.5})
chk("4.1 invalid threshold rejected",
    not r4.get("ok") and r4.get("status_code") == 400)

# 5) 缓存命中
print("\n[5] 缓存命中")
t0 = time.time()
r5 = post("/api/yeren/portfolio/rebalance", body)
elapsed = time.time() - t0
chk("5.1 second call < 1.5s", elapsed < 1.5, f"elapsed={elapsed:.2f}s")
chk("5.2 same n_to_rebalance", r5.get("data", {}).get("stats", {}).get("n_to_rebalance") == 3)

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R387 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)