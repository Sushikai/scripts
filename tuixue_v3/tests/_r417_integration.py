#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R417 产业链图谱 — 集成测试"""
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


def get_encoded(path, params=None, timeout=30):
    try:
        from urllib.parse import urlencode
        if params:
            path = f"{path}?{urlencode(params)}"
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"_err": str(e)}

print("=" * 70)
print("R417 · 产业链图谱 — 集成测试")
print("=" * 70)

# 1) 新能源
print("\n[1] 新能源产业链")
r = get_encoded("/api/yeren/industry_chain", {"industry": "新能源"})
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
chk("1.2 has 3 stages",
    all(k in data for k in ("upstream", "midstream", "downstream")))
chk("1.3 upstream has codes",
    all(n.get("code") and n.get("rel") for n in data.get("upstream", [])))
chk("1.4 midstream has 300750 (宁德)",
    any(n.get("code") == "300750" for n in data.get("midstream", [])))
chk("1.5 rel in [0, 1]",
    all(0 <= n.get("rel", -1) <= 1
        for stage in ("upstream", "midstream", "downstream")
        for n in data.get(stage, [])))

# 2) 半导体
print("\n[2] 半导体产业链")
r2 = get_encoded("/api/yeren/industry_chain", {"industry": "半导体"})
chk("2.1 ok=true", r2.get("ok") is True)
chk("2.2 has 688981 (中芯)",
    any(n.get("code") == "688981" for n in r2.get("data", {}).get("upstream", [])))

# 3) 医药
print("\n[3] 医药产业链")
r3 = get_encoded("/api/yeren/industry_chain", {"industry": "医药"})
chk("3.1 ok=true", r3.get("ok") is True)
chk("3.2 has 600276 (恒瑞)",
    any(n.get("code") == "600276"
        for stage in ("upstream", "midstream", "downstream")
        for n in r3.get("data", {}).get(stage, [])))

# 4) 统计
print("\n[4] 统计")
stats = data.get("stats", {})
chk("4.1 avg_relationship in [0,1]",
    0 <= stats.get("avg_relationship", -1) <= 1)
chk("4.2 n_upstream+n_mid+n_down == 各列表实际长度之和",
    stats.get("n_upstream", 0) == len(data.get("upstream", []))
    and stats.get("n_midstream", 0) == len(data.get("midstream", []))
    and stats.get("n_downstream", 0) == len(data.get("downstream", [])))
chk("4.3 has core_node",
    stats.get("core_node", {}).get("code"))

# 5) 核心节点
print("\n[5] 核心节点")
core = stats.get("core_node", {})
chk("5.1 core_node rel == max",
    core.get("rel") == max(
        n.get("rel", 0)
        for stage in ("upstream", "midstream", "downstream")
        for n in data.get(stage, [])),
    f"core={core.get('rel')} vs actual max")

# 6) 非法 industry
print("\n[6] industry=银行 → 400")
r6 = get_encoded("/api/yeren/industry_chain", {"industry": "银行"})
chk("6.1 rejected",
    not r6.get("ok") and r6.get("status_code") == 400)

# 7) 缓存
print("\n[7] 缓存")
import time
t0 = time.time()
r7 = get_encoded("/api/yeren/industry_chain", {"industry": "新能源"})
elapsed = time.time() - t0
chk("7.1 cached < 200ms",
    elapsed < 0.2, f"elapsed={elapsed:.3f}s")

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R417 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)