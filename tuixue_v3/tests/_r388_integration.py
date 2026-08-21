#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R388 组合风险暴露 — 集成测试"""
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
print("R388 · 组合风险暴露 — 集成测试")
print("=" * 70)

# 1) 3 只持仓, 50/30/20
print("\n[1] 3 holdings 50/30/20")
body = {
    "holdings": [
        {"code": "600519", "current_value": 50000},
        {"code": "000001", "current_value": 30000},
        {"code": "300750", "current_value": 20000},
    ]
}
r = post("/api/yeren/portfolio/risk", body)
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
items = data.get("items", [])
chk("1.2 3 items", len(items) == 3)
chk("1.3 600519 industry 食品饮料",
    items[0]["industry"] == "食品饮料" and items[0]["size"] == "大盘")
chk("1.4 industry_dist 3 个",
    len(data.get("industry_dist", [])) == 3)
chk("1.5 size_dist 2 个 (大盘+中盘)",
    len(data.get("size_dist", [])) == 2)
chk("1.6 style_dist 2 个 (价值+成长)",
    len(data.get("style_dist", [])) == 2)
hhi = data.get("hhi", 0)
chk("1.7 HHI == 0.5^2 + 0.3^2 + 0.2^2 = 0.38",
    abs(hhi - 0.38) < 0.001, f"hhi={hhi}")
chk("1.8 触发 single_stock 告警 (单股 50%)",
    any(a["type"] == "single_stock" for a in data.get("alerts", [])))
chk("1.9 触发 hhi 告警 (HHI>0.25)",
    any(a["type"] == "hhi" for a in data.get("alerts", [])))

# 2) 分散组合 (5 只, 各 20%) → 无告警
print("\n[2] 5 holdings 分散 (20% each) → 无 single_stock 告警")
body2 = {
    "holdings": [
        {"code": "600519", "current_value": 20000},
        {"code": "000001", "current_value": 20000},
        {"code": "300750", "current_value": 20000},
        {"code": "600036", "current_value": 20000},
        {"code": "000858", "current_value": 20000},
    ]
}
r2 = post("/api/yeren/portfolio/risk", body2)
data2 = r2.get("data", {})
hhi2 = data2.get("hhi", 0)
chk("2.1 HHI = 5×0.04 = 0.2",
    abs(hhi2 - 0.2) < 0.001, f"hhi={hhi2}")
chk("2.2 无 single_stock 告警",
    not any(a["type"] == "single_stock" for a in data2.get("alerts", [])))

# 3) 单股 100% 集中 → 多个告警
print("\n[3] 单只 100% 集中")
body3 = {"holdings": [{"code": "600519", "current_value": 100000}]}
r3 = post("/api/yeren/portfolio/risk", body3)
data3 = r3.get("data", {})
chk("3.1 HHI == 1.0", abs(data3.get("hhi", 0) - 1.0) < 0.001)
chk("3.2 stats.n_alerts >= 2",
    data3.get("stats", {}).get("n_alerts", 0) >= 2)

# 4) 空 holdings
print("\n[4] empty → 400")
r4 = post("/api/yeren/portfolio/risk", {"holdings": []})
chk("4.1 empty rejected", not r4.get("ok") and r4.get("status_code") == 400)

# 5) 缓存命中
print("\n[5] 缓存命中")
t0 = time.time()
r5 = post("/api/yeren/portfolio/risk", body)
elapsed = time.time() - t0
chk("5.1 second call < 1.5s", elapsed < 1.5, f"elapsed={elapsed:.2f}s")
chk("5.2 same hhi", abs(r5.get("data", {}).get("hhi", 0) - hhi) < 0.001)

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R388 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)