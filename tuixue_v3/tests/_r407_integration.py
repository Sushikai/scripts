#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R407 量化因子库 — 集成测试"""
import json, os, sys, urllib.request

BASE = os.environ.get("TUIXUE_BASE", "http://127.0.0.1:7799")

results = []
def chk(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")

def get(path, timeout=60):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"_err": str(e)}

print("=" * 70)
print("R407 · 量化因子库 — 集成测试")
print("=" * 70)

# 1) 默认 60 日
print("\n[1] 600519 默认 60 日")
r = get("/api/yeren/factor_lib?code=600519", timeout=60)
chk("1.1 ok=true", r.get("ok") is True, f"err={r.get('error')}")
data = r.get("data", {})
factors = data.get("factors", [])
chk("1.2 has 12 factors", len(factors) == 12, f"got {len(factors)}")
chk("1.3 has 5 categories",
    len({f["category"] for f in factors}) == 5)
chk("1.4 趋势: BIAS_MA5",
    any(f["name"] == "BIAS_MA5" and f["category"] == "趋势" for f in factors))
chk("1.5 动量: MOM_20",
    any(f["name"] == "MOM_20" and f["category"] == "动量" for f in factors))
chk("1.6 波动: VOL_STD",
    any(f["name"] == "VOL_STD" and f["category"] == "波动" for f in factors))
chk("1.7 量能: VOL_RATIO",
    any(f["name"] == "VOL_RATIO" and f["category"] == "量能" for f in factors))
chk("1.8 估值: RANGE_POS",
    any(f["name"] == "RANGE_POS" and f["category"] == "估值" for f in factors))

# 2) 排名
print("\n[2] 因子排名")
ranked = data.get("ranked", [])
chk("2.1 ranked len == 12", len(ranked) == 12)
chk("2.2 rank 1-12 连续",
    [r["rank"] for r in ranked] == list(range(1, 13)))
chk("2.3 |value| 降序",
    all(abs(ranked[i]["value"]) >= abs(ranked[i + 1]["value"])
        for i in range(11)))

# 3) top_3
print("\n[3] top_3 显著因子")
top3 = data.get("top_3", [])
chk("3.1 has 3 top", len(top3) == 3)
chk("3.2 包含 name+value",
    all("name" in t and "value" in t for t in top3))

# 4) significant 标记
print("\n[4] significant 标记")
chk("4.1 n_significant >= 0",
    data.get("n_significant", -1) >= 0)
chk("4.2 significant 因子数 == |value|>0.05 的因子数",
    data.get("n_significant") == sum(1 for f in factors if abs(f["value"]) > 0.05))

# 5) lookback 越界
print("\n[5] lookback=10 → 400")
r5 = get("/api/yeren/factor_lib?code=600519&lookback=10")
chk("5.1 rejected",
    not r5.get("ok") and r5.get("status_code") == 400)

# 6) 不同 lookback
print("\n[6] 000001 lookback=120")
r6 = get("/api/yeren/factor_lib?code=000001&lookback=120", timeout=60)
chk("6.1 ok=true", r6.get("ok") is True)
chk("6.2 120 n_bars",
    r6.get("data", {}).get("n_bars", 0) >= 100,
    f"got {r6.get('data',{}).get('n_bars')}")

# 7) 缓存
print("\n[7] 缓存命中")
import time
t0 = time.time()
r7 = get("/api/yeren/factor_lib?code=600519", timeout=30)
elapsed = time.time() - t0
chk("7.1 cached < 500ms",
    elapsed < 0.5, f"elapsed={elapsed:.3f}s")

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R407 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)