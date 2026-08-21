#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R418 ESG 评级 — 集成测试"""
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
print("R418 · ESG 评级 — 集成测试")
print("=" * 70)

# 1) 默认
print("\n[1] 600519 默认")
r = get("/api/yeren/esg_rating?code=600519")
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
scores = data.get("scores", {})
chk("1.2 has 5 维度 scores",
    all(k in scores for k in ("environment_E", "social_S", "governance_G",
                              "product_P", "innovation_I")))
chk("1.3 scores in [0, 100]",
    all(0 <= s <= 100 for s in scores.values()))
chk("1.4 overall in [0, 100]",
    0 <= data.get("overall", 0) <= 100, f"got {data.get('overall')}")
chk("1.5 grade in [AAA, AA, A, BBB, BB, B]",
    data.get("grade") in ("AAA", "AA", "A", "BBB", "BB", "B"))
chk("1.6 verdict 非空",
    isinstance(data.get("verdict"), str) and len(data.get("verdict", "")) > 0)

# 2) 加权验证
print("\n[2] overall 加权计算")
expected = round(
    scores["environment_E"] * 0.25 + scores["social_S"] * 0.20
    + scores["governance_G"] * 0.25 + scores["product_P"] * 0.15
    + scores["innovation_I"] * 0.15, 1)
chk("2.1 overall == 加权和",
    abs(data.get("overall", 0) - expected) < 0.2,
    f"got {data.get('overall')} vs {expected}")

# 3) 同业对比
print("\n[3] 同业对比")
peers = data.get("peers", [])
chk("3.1 has 5 peers",
    len(peers) == 5)
chk("3.2 peer.overall in [40, 100]",
    all(40 <= p.get("overall", 0) <= 100 for p in peers))
chk("3.3 peer_avg_overall",
    isinstance(data.get("peer_avg_overall"), (int, float)))
chk("3.4 industry_rank format N/6",
    "/" in data.get("industry_rank", ""))

# 4) 改进建议
print("\n[4] 改进建议")
sug = data.get("suggestions", [])
chk("4.1 has 3 suggestions",
    len(sug) == 3)
chk("4.2 has 最弱维度",
    any("最弱维度" in s for s in sug))

# 5) 等级一致性
print("\n[5] grade 与 overall 一致")
overall = data.get("overall", 0)
if overall >= 85:
    expected_grade = "AAA"
elif overall >= 75:
    expected_grade = "AA"
elif overall >= 65:
    expected_grade = "A"
elif overall >= 55:
    expected_grade = "BBB"
elif overall >= 45:
    expected_grade = "BB"
else:
    expected_grade = "B"
chk("5.1 grade 匹配 overall",
    data.get("grade") == expected_grade,
    f"got {data.get('grade')} vs expected {expected_grade}")

# 6) 确定性
print("\n[6] mock 确定性")
r6 = get("/api/yeren/esg_rating?code=600519")
chk("6.1 same overall",
    r6.get("data", {}).get("overall") == data.get("overall"))
chk("6.2 same grade",
    r6.get("data", {}).get("grade") == data.get("grade"))

# 7) 非法 code
print("\n[7] 非法 code → 400")
r7 = get("/api/yeren/esg_rating?code=abc")
chk("7.1 rejected",
    not r7.get("ok") and r7.get("status_code") == 400)

# 8) 缓存
print("\n[8] 缓存")
import time
t0 = time.time()
r8 = get("/api/yeren/esg_rating?code=600519")
elapsed = time.time() - t0
chk("8.1 cached < 200ms",
    elapsed < 0.2, f"elapsed={elapsed:.3f}s")

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R418 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)