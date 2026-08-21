#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R389 黑天鹅压测 — 集成测试"""
import json, os, sys, time, urllib.request

BASE = os.environ.get("TUIXUE_BASE", "http://127.0.0.1:7799")

results = []
def chk(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")

def post(path, body, timeout=30):
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
print("R389 · 黑天鹅压测 — 集成测试")
print("=" * 70)

# 1) covid_2020 场景
print("\n[1] covid_2020 + 2 holdings")
body = {
    "holdings": [
        {"code": "600519", "current_value": 50000},
        {"code": "300750", "current_value": 30000},
    ],
    "scenario": "covid_2020",
}
r = post("/api/yeren/portfolio/stress", body)
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
chk("1.2 scenario_name 包含 2020",
    "2020" in data.get("scenario_name", ""))
items = data.get("items", [])
chk("1.3 2 items", len(items) == 2)
chk("1.4 600519 (大盘/价值) shock ≈ (-7 + -8)/2 = -7.5%",
    abs(items[0]["shock_pct"] - (-0.075)) < 0.001,
    f"got {items[0]['shock_pct']}")
chk("1.5 300750 (中盘/成长) shock ≈ (-9 + -10)/2 = -9.5%",
    abs(items[1]["shock_pct"] - (-0.095)) < 0.001,
    f"got {items[1]['shock_pct']}")
stats = data.get("stats", {})
chk("1.6 stats.loss_pct > 0 (整体亏损 8.25%)",
    stats["loss_pct"] > 0, f"got {stats['loss_pct']}")
chk("1.7 worst_code == 300750",
    stats["worst_code"] == "300750")
nav = data.get("nav", [])
chk("1.8 nav 11 天", len(nav) == 11)
chk("1.9 nav[5] (谷底) < total_before",
    nav[5]["value"] < stats["total_before"])

# 2) sep_2024 上涨场景
print("\n[2] sep_2024 上涨场景 → 负损失 (盈利)")
body2 = {
    "holdings": [{"code": "600519", "current_value": 100000}],
    "scenario": "sep_2024",
}
r2 = post("/api/yeren/portfolio/stress", body2)
data2 = r2.get("data", {})
chk("2.1 sep_2024 loss_pct < 0 (盈利)",
    data2.get("stats", {}).get("loss_pct", 0) < 0,
    f"got {data2.get('stats', {}).get('loss_pct')}")

# 3) global_2008 极端
print("\n[3] global_2008 极端 → 严重亏损")
body3 = {
    "holdings": [{"code": "600519", "current_value": 100000}],
    "scenario": "global_2008",
}
r3 = post("/api/yeren/portfolio/stress", body3)
data3 = r3.get("data", {})
chk("3.1 global_2008 loss_pct > 10% (亏损 11%)",
    data3.get("stats", {}).get("loss_pct", 0) > 10,
    f"got {data3.get('stats', {}).get('loss_pct')}")

# 4) 非法 scenario
print("\n[4] 非法 scenario → 400")
r4 = post("/api/yeren/portfolio/stress",
          {"holdings": [{"code": "600519", "current_value": 100}], "scenario": "fake"})
chk("4.1 invalid scenario rejected",
    not r4.get("ok") and r4.get("status_code") == 400)

# 5) 空 holdings
print("\n[5] empty → 400")
r5 = post("/api/yeren/portfolio/stress", {"holdings": [], "scenario": "covid_2020"})
chk("5.1 empty rejected", not r5.get("ok") and r5.get("status_code") == 400)

# 6) 缓存命中
print("\n[6] 缓存命中")
t0 = time.time()
r6 = post("/api/yeren/portfolio/stress", body)
elapsed = time.time() - t0
chk("6.1 second call < 1.5s", elapsed < 1.5, f"elapsed={elapsed:.2f}s")

# 7) scenarios 列表
print("\n[7] scenarios 列表 (≥3)")
r7 = post("/api/yeren/portfolio/stress", body)
chk("7.1 scenarios list >= 3",
    len(r7.get("data", {}).get("scenarios", [])) >= 3,
    f"got {r7.get('data',{}).get('scenarios')}")

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R389 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)