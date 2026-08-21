#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R404 私募组合 — 集成测试"""
import json, os, sys, urllib.request

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
print("R404 · 私募组合 — 集成测试")
print("=" * 70)

# 1) 保守
print("\n[1] 保守 100w 180d")
r = post("/api/yeren/private_portfolio",
         {"risk_pref": "保守", "capital": 1000000, "horizon_days": 180})
chk("1.1 ok=true", r.get("ok") is True, f"err={r.get('error')}")
data = r.get("data", {})
chk("1.2 n_holdings >= 5",
    data.get("n_holdings", 0) >= 5, f"got {data.get('n_holdings')}")
chk("1.3 weights sum to ~1.0",
    abs(sum(h["weight"] for h in data.get("holdings", [])) - 1.0) < 0.01,
    f"got {sum(h['weight'] for h in data.get('holdings',[])):.3f}")
chk("1.4 has benchmark",
    "benchmark_hs300" in data and "alpha_pct" in data)
chk("1.5 conservative: expected_ret < 15%",
    data.get("expected_horizon_ret_pct", 100) < 15,
    f"got {data.get('expected_horizon_ret_pct')}")

# 2) 平衡
print("\n[2] 平衡 500w 365d")
r2 = post("/api/yeren/private_portfolio",
          {"risk_pref": "平衡", "capital": 5000000, "horizon_days": 365})
chk("2.1 ok=true", r2.get("ok") is True)
chk("2.2 n_holdings >= 6",
    r2.get("data", {}).get("n_holdings", 0) >= 6)

# 3) 激进
print("\n[3] 激进 100w 365d")
r3 = post("/api/yeren/private_portfolio",
          {"risk_pref": "激进", "capital": 1000000, "horizon_days": 365})
chk("3.1 ok=true", r3.get("ok") is True)
chk("3.2 aggressive: expected_ret > 20%",
    r3.get("data", {}).get("expected_horizon_ret_pct", 0) > 20,
    f"got {r3.get('data',{}).get('expected_horizon_ret_pct')}")

# 4) 风险偏好 alpha 对比 (统一 365d 基准)
print("\n[4] alpha 对比 — 激进 > 平衡 > 保守 (365d)")
r_b = post("/api/yeren/private_portfolio",
           {"risk_pref": "平衡", "capital": 1000000, "horizon_days": 365})
r_a = post("/api/yeren/private_portfolio",
           {"risk_pref": "激进", "capital": 1000000, "horizon_days": 365})
r_c = post("/api/yeren/private_portfolio",
           {"risk_pref": "保守", "capital": 1000000, "horizon_days": 365})
d_a = r_a.get("data", {})
d_b = r_b.get("data", {})
d_c = r_c.get("data", {})
chk("4.1 alpha: 激进 > 平衡 > 保守",
    d_a.get("alpha_pct", 0) > d_b.get("alpha_pct", 0) > d_c.get("alpha_pct", 0),
    f"α_保守={d_c.get('alpha_pct')} α_平衡={d_b.get('alpha_pct')} α_激进={d_a.get('alpha_pct')}")

# 5) 非法 risk_pref
print("\n[5] 非法 risk_pref → 400")
r5 = post("/api/yeren/private_portfolio", {"risk_pref": "高", "capital": 1000000})
chk("5.1 rejected",
    not r5.get("ok") and r5.get("status_code") == 400)

# 6) capital < 10000
print("\n[6] capital=1000 → 400")
r6 = post("/api/yeren/private_portfolio", {"risk_pref": "保守", "capital": 1000})
chk("6.1 rejected",
    not r6.get("ok") and r6.get("status_code") == 400)

# 7) horizon 越界
print("\n[7] horizon=10 → 400")
r7 = post("/api/yeren/private_portfolio",
          {"risk_pref": "保守", "capital": 100000, "horizon_days": 10})
chk("7.1 rejected",
    not r7.get("ok") and r7.get("status_code") == 400)

# 8) industry_breakdown sum ≈ 1
print("\n[8] 行业分布")
ib = data.get("industry_breakdown", {})
chk("8.1 industry breakdown sum ≈ 1",
    abs(sum(ib.values()) - 1.0) < 0.01,
    f"got {sum(ib.values()):.3f}")

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R404 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)