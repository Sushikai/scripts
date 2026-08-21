#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R415 财经日历 — 集成测试"""
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
print("R415 · 财经日历 — 集成测试")
print("=" * 70)

# 1) 默认 14 日
print("\n[1] 默认 14 日")
r = get("/api/yeren/fin_calendar?days_ahead=14")
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
events = data.get("events", [])
chk("1.2 has events",
    isinstance(events, list) and len(events) > 0,
    f"got {len(events)}")
chk("1.3 events sorted by ts",
    all(events[i]["ts"] <= events[i + 1]["ts"] for i in range(len(events) - 1)))

# 2) 经济事件
print("\n[2] 经济事件类型")
econ_events = [e for e in events if e["type"] == "经济事件"]
chk("2.1 has 经济事件",
    len(econ_events) > 0)
chk("2.2 经济事件 has importance",
    all(e.get("importance") in ("high", "medium", "low") for e in econ_events))
chk("2.3 has country",
    all(e.get("country") for e in econ_events))

# 3) 财报披露
print("\n[3] 财报披露")
earn = [e for e in events if e["type"] == "财报披露"]
chk("3.1 has 财报披露",
    len(earn) > 0)
chk("3.2 earnings has codes",
    all(isinstance(e.get("codes", []), list) for e in earn))

# 4) 重要性统计
print("\n[4] 统计")
stats = data.get("stats", {})
chk("4.1 n_high > 0",
    stats.get("n_high", 0) > 0)
chk("4.2 n_econ_events == len(econ_events)",
    stats.get("n_econ_events") == len(econ_events))
chk("4.3 n_earnings == len(earn)",
    stats.get("n_earnings") == len(earn))

# 5) 长区间
print("\n[5] days_ahead=30")
r5 = get("/api/yeren/fin_calendar?days_ahead=30")
chk("5.1 ok=true", r5.get("ok") is True)
chk("5.2 更多事件",
    r5.get("data", {}).get("n_events", 0) >= data.get("n_events", 0))

# 6) 短区间
print("\n[6] days_ahead=3")
r6 = get("/api/yeren/fin_calendar?days_ahead=3")
chk("6.1 ok=true", r6.get("ok") is True)
chk("6.2 较少事件",
    r6.get("data", {}).get("n_events", 0) <= data.get("n_events", 0))

# 7) 错误路径
print("\n[7] days_ahead=0 → 400")
r7 = get("/api/yeren/fin_calendar?days_ahead=0")
chk("7.1 rejected",
    not r7.get("ok") and r7.get("status_code") == 400)
r7b = get("/api/yeren/fin_calendar?days_ahead=120")
chk("7.2 120 → 400",
    not r7b.get("ok") and r7b.get("status_code") == 400)

# 8) 缓存
print("\n[8] 缓存命中")
import time
t0 = time.time()
r8 = get("/api/yeren/fin_calendar?days_ahead=14")
elapsed = time.time() - t0
chk("8.1 cached < 200ms",
    elapsed < 0.2, f"elapsed={elapsed:.3f}s")

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R415 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)