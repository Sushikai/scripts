#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R402 智能体编排 — 集成测试"""
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
print("R402 · 智能体编排 — 集成测试")
print("=" * 70)

# 1) 简单线性 DAG (3 agents)
print("\n[1] 线性 DAG: a1 → a2 → a3")
body = {
    "device_id": "test_r402_1234567890ab",
    "agents": [
        {"id": "a1", "task": "fetch_quote 600519", "depends_on": []},
        {"id": "a2", "task": "compute_signal 基于行情", "depends_on": ["a1"]},
        {"id": "a3", "task": "summarize 写报告", "depends_on": ["a2"]},
    ],
}
r = post("/api/yeren/agent_orchestrate", body)
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
chk("1.2 n_layers=3",
    data.get("n_layers") == 3, f"got {data.get('n_layers')}")
chk("1.3 topo_order is list of 3",
    len(data.get("topo_order", [])) == 3)
chk("1.4 first layer is serial (1 agent)",
    len(data.get("plan", [[]])[0].get("agents", [])) == 1)
chk("1.5 estimate_seconds > 0",
    data.get("estimate_seconds", 0) > 0)

# 2) 并行 DAG (a1 → [a2, a3] → a4)
print("\n[2] 并行 DAG: a1 → (a2, a3) → a4")
body2 = {
    "agents": [
        {"id": "a1", "task": "fetch_quote 600519", "depends_on": []},
        {"id": "a2", "task": "compute_signal 行情", "depends_on": ["a1"]},
        {"id": "a3", "task": "sentiment 舆情", "depends_on": ["a1"]},
        {"id": "a4", "task": "summarize 综合", "depends_on": ["a2", "a3"]},
    ],
}
r2 = post("/api/yeren/agent_orchestrate", body2)
chk("2.1 ok=true", r2.get("ok") is True)
data2 = r2.get("data", {})
chk("2.2 n_layers=3",
    data2.get("n_layers") == 3, f"got {data2.get('n_layers')}")
chk("2.3 layer 2 is parallel",
    data2.get("plan", [{}, {}, {}])[1].get("mode") == "parallel"
    and len(data2.get("plan", [{}, {}, {}])[1].get("agents", [])) == 2,
    f"got mode={data2.get('plan',[{},{},{}])[1].get('mode')}, "
    f"n={len(data2.get('plan',[{},{},{}])[1].get('agents', []))}")

# 3) 工具推荐
print("\n[3] 工具关键词匹配")
r3 = post("/api/yeren/agent_orchestrate", body2)
plan3 = r3.get("data", {}).get("plan", [])
chk("3.1 a1 has fetch_quote rec",
    any("行情拉取" in a.get("rec_tools", [])
        for layer in plan3 for a in layer.get("agents", [])
        if a.get("id") == "a1"))
chk("3.2 a2 has compute_signal rec",
    any("信号计算" in a.get("rec_tools", [])
        for layer in plan3 for a in layer.get("agents", [])
        if a.get("id") == "a2"))

# 4) 环形依赖 → 400
print("\n[4] 环形 DAG → 400")
body4 = {
    "agents": [
        {"id": "a1", "task": "t", "depends_on": ["a2"]},
        {"id": "a2", "task": "t", "depends_on": ["a1"]},
    ],
}
r4 = post("/api/yeren/agent_orchestrate", body4)
chk("4.1 cycle rejected",
    not r4.get("ok") and r4.get("status_code") == 400,
    f"err={r4.get('error')}")

# 5) ID 重复 → 400
print("\n[5] 重复 ID → 400")
r5 = post("/api/yeren/agent_orchestrate",
          {"agents": [{"id": "a1", "task": "t", "depends_on": []},
                      {"id": "a1", "task": "t2", "depends_on": []}]})
chk("5.1 dup id rejected",
    not r5.get("ok") and r5.get("status_code") == 400)

# 6) 空 agents → 400
print("\n[6] 空 agents → 400")
r6 = post("/api/yeren/agent_orchestrate", {"agents": []})
chk("6.1 empty rejected",
    not r6.get("ok") and r6.get("status_code") == 400)

# 7) 依赖不存在的 ID → 400
print("\n[7] 依赖不存在 → 400")
r7 = post("/api/yeren/agent_orchestrate",
          {"agents": [{"id": "a1", "task": "t", "depends_on": ["ghost"]}]})
chk("7.1 ghost dep rejected",
    not r7.get("ok") and r7.get("status_code") == 400)

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R402 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)