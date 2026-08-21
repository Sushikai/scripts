#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R390 组合回测 — 集成测试"""
import json, os, sys, time, urllib.request

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
print("R390 · 组合回测 — 集成测试")
print("=" * 70)

# 1) 2 holdings 60日
print("\n[1] 2 holdings 60 日回测")
body = {
    "holdings": [
        {"code": "600519", "weight": 0.5},
        {"code": "000001", "weight": 0.5},
    ],
    "start_date": "20260601",
    "end_date": "20260801",
    "rebalance_days": 30,
    "initial": 100000,
}
r = post("/api/yeren/portfolio/backtest", body, timeout=120)
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
nav = data.get("nav", [])
stats = data.get("stats", {})
chk("1.2 nav len >= 10", len(nav) >= 10, f"got {len(nav)}")
chk("1.3 nav[0].value == initial",
    abs(nav[0]["value"] - 100000) < 1)
chk("1.4 stats has total_ret_pct",
    "total_ret_pct" in stats)
chk("1.5 stats has annualized_pct",
    "annualized_pct" in stats)
chk("1.6 stats has max_drawdown_pct",
    "max_drawdown_pct" in stats)
chk("1.7 stats has sharpe",
    isinstance(stats.get("sharpe"), (int, float)))
chk("1.8 stats.n_days >= 30",
    stats.get("n_days", 0) >= 30, f"got {stats.get('n_days')}")

# 2) 短区间 (5 日)
print("\n[2] 短区间 10 日")
body2 = {
    "holdings": [{"code": "600519", "weight": 1.0}],
    "start_date": "20260720",
    "end_date": "20260801",
    "rebalance_days": 7,
}
r2 = post("/api/yeren/portfolio/backtest", body2, timeout=120)
chk("2.1 short range ok",
    r2.get("ok") is True or "短" in str(r2.get("error", "")).lower() or "区间" in str(r2.get("error", "")),
    f"err={r2.get('error')}")

# 3) rebalance_days 越界
print("\n[3] rebalance_days 越界 → 400")
r3 = post("/api/yeren/portfolio/backtest",
          {"holdings": [{"code": "600519", "weight": 1}],
           "start_date": "20260601", "end_date": "20260801", "rebalance_days": 200})
chk("3.1 invalid rebalance_days rejected",
    not r3.get("ok") and r3.get("status_code") == 400)

# 4) date 格式错
print("\n[4] date 格式错 → 400")
r4 = post("/api/yeren/portfolio/backtest",
          {"holdings": [{"code": "600519", "weight": 1}],
           "start_date": "2026/06/01", "end_date": "20260801", "rebalance_days": 30})
chk("4.1 bad date rejected",
    not r4.get("ok") and r4.get("status_code") == 400)

# 5) 空 holdings
print("\n[5] empty → 400")
r5 = post("/api/yeren/portfolio/backtest",
          {"holdings": [], "start_date": "20260601", "end_date": "20260801", "rebalance_days": 30})
chk("5.1 empty rejected", not r5.get("ok") and r5.get("status_code") == 400)

# 6) 缓存命中
print("\n[6] 缓存命中")
t0 = time.time()
r6 = post("/api/yeren/portfolio/backtest", body, timeout=30)
elapsed = time.time() - t0
chk("6.1 second call < 1.5s", elapsed < 1.5, f"elapsed={elapsed:.2f}s")
chk("6.2 same n_days",
    r6.get("data", {}).get("stats", {}).get("n_days") == stats.get("n_days"))

# 7) 3 holdings
print("\n[7] 3 holdings")
body7 = {
    "holdings": [
        {"code": "600519", "weight": 0.4},
        {"code": "000001", "weight": 0.3},
        {"code": "300750", "weight": 0.3},
    ],
    "start_date": "20260601",
    "end_date": "20260801",
    "rebalance_days": 20,
}
r7 = post("/api/yeren/portfolio/backtest", body7, timeout=120)
chk("7.1 3 holdings ok",
    r7.get("ok") is True)
chk("7.2 codes 3",
    len(r7.get("data", {}).get("stats", {}).get("codes", [])) == 3)

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R390 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)