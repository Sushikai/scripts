#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R420 智能投顾 v1 — 集成测试"""
import json, os, sys, urllib.request

BASE = os.environ.get("TUIXUE_BASE", "http://127.0.0.1:7799")
DEVICE = "test_r420_robo_1234abcdefgh"

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
print("R420 · 智能投顾 v1 — 集成测试")
print("=" * 70)

# 1) 基础
print("\n[1] 600519 默认")
r = get("/api/yeren/robo_advisor?device_id=test_r420_robo_1234abcdefgh&code=600519",
        timeout=60)
chk("1.1 ok=true", r.get("ok") is True, f"err={r.get('error')}")
data = r.get("data", {})
chk("1.2 has user_segment",
    data.get("user_segment") in ("新手", "活跃", "资深"))
chk("1.3 has suggestion_style",
    isinstance(data.get("suggestion_style"), str)
    and len(data.get("suggestion_style", "")) > 0)
chk("1.4 has metrics",
    all(k in data.get("metrics", {}) for k in ("last_close", "ma20", "ret_20_pct",
                                                "ret_60_pct", "volatility")))

# 2) 信号 + 评分
print("\n[2] 信号 + 评分")
chk("2.1 signal in [强烈买入, 买入, 持有, 减仓, 卖出]",
    data.get("signal") in ("强烈买入", "买入", "持有", "减仓", "卖出"),
    f"got {data.get('signal')}")
chk("2.2 signal_score in [0, 1]",
    0 <= data.get("signal_score", -1) <= 1)

# 3) 风险偏好 + 行动
print("\n[3] 风险偏好 + 行动")
chk("3.1 risk_pref in [保守, 平衡, 激进]",
    data.get("risk_pref") in ("保守", "平衡", "激进"),
    f"got {data.get('risk_pref')}")
chk("3.2 actions list >= 1",
    isinstance(data.get("actions"), list)
    and len(data.get("actions", [])) >= 1)

# 4) 引用模块
print("\n[4] 引用模块")
modules = data.get("modules_used", [])
chk("4.1 has 5+ modules",
    len(modules) >= 5)
chk("4.2 has R401",
    any("R401" in m for m in modules))
chk("4.3 has R403",
    any("R403" in m for m in modules))

# 5) 免责声明
print("\n[5] 免责声明")
chk("5.1 has disclaimer",
    isinstance(data.get("disclaimer"), str)
    and len(data.get("disclaimer", "")) > 10)

# 6) 非法 device_id
print("\n[6] 非法 device_id → 400")
r6 = get("/api/yeren/robo_advisor?device_id=ab&code=600519")
chk("6.1 rejected",
    not r6.get("ok") and r6.get("status_code") == 400)

# 7) 非法 code
print("\n[7] 非法 code → 400")
r7 = get("/api/yeren/robo_advisor?device_id=test_r420_robo_1234abcdefgh&code=abc")
chk("7.1 rejected",
    not r7.get("ok") and r7.get("status_code") == 400)

# 8) 缓存
print("\n[8] 缓存命中")
import time
t0 = time.time()
r8 = get("/api/yeren/robo_advisor?device_id=test_r420_robo_1234abcdefgh&code=600519",
         timeout=30)
elapsed = time.time() - t0
chk("8.1 cached < 500ms",
    elapsed < 0.5, f"elapsed={elapsed:.3f}s")
chk("8.2 same signal",
    r8.get("data", {}).get("signal") == data.get("signal"))

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R420 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)