#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R401 用户分层 — 集成测试"""
import json, os, sys, urllib.request

BASE = os.environ.get("TUIXUE_BASE", "http://127.0.0.1:7799")
DEVICE = "test_r401_segment_1234abcdef"

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
print("R401 · 用户分层 — 集成测试")
print("=" * 70)

# 1) 无 device_id
print("\n[1] 无 device_id → anonymous")
r1 = get("/api/yeren/user_segment")
chk("1.1 ok=true", r1.get("ok") is True)
chk("1.2 segment=anonymous",
    r1.get("data", {}).get("segment") == "anonymous",
    f"got {r1.get('data',{}).get('segment')}")

# 2) 非法 device_id
print("\n[2] 非法 device_id → anonymous")
r2 = get("/api/yeren/user_segment?device_id=ab")
chk("2.1 ok=true", r2.get("ok") is True)
chk("2.2 segment=anonymous",
    r2.get("data", {}).get("segment") == "anonymous")

# 3) 有效 device_id (无日志)
print("\n[3] 有效 device_id 但无日志 → newbie")
r3 = get(f"/api/yeren/user_segment?device_id={DEVICE}")
chk("3.1 ok=true", r3.get("ok") is True)
chk("3.2 segment in [newbie, active, expert, dormant]",
    r3.get("data", {}).get("segment") in ("newbie", "active", "expert", "dormant"),
    f"got {r3.get('data',{}).get('segment')}")
chk("3.3 stats.n_logs_total is int",
    isinstance(r3.get("data", {}).get("stats", {}).get("n_logs_total"), int))
chk("3.4 has recommendations list",
    isinstance(r3.get("data", {}).get("recommendations"), list))

# 4) 缓存命中 (二次调用 < 500ms)
print("\n[4] 缓存命中")
import time
t0 = time.time()
r4 = get(f"/api/yeren/user_segment?device_id={DEVICE}")
elapsed = time.time() - t0
chk("4.1 second call < 500ms",
    elapsed < 0.5, f"elapsed={elapsed:.3f}s")
chk("4.2 same segment",
    r4.get("data", {}).get("segment") == r3.get("data", {}).get("segment"))

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R401 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)