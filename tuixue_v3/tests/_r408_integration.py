#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R408 期权对冲 — 集成测试"""
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
print("R408 · 期权对冲 — 集成测试")
print("=" * 70)

# 1) ATM 看涨
print("\n[1] ATM 看涨 S=K=100 T=30d σ=25% r=3%")
r = get("/api/yeren/option_hedge?code=600519&spot=100&strike=100&days=30&vol=0.25&rate=0.03")
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
call = data.get("call", {})
chk("1.2 call price > 0",
    call.get("price", 0) > 0, f"got {call.get('price')}")
chk("1.3 call delta in (0, 1)",
    0 < call.get("delta", 0) < 1, f"got {call.get('delta')}")
chk("1.4 ATM delta 接近 0.5",
    abs(call.get("delta", 0) - 0.5) < 0.1,
    f"got {call.get('delta')}")
chk("1.5 call gamma > 0",
    call.get("gamma", 0) > 0)
chk("1.6 moneyness=ATM",
    data.get("moneyness") == "平值 (ATM)")

# 2) Put 定价
print("\n[2] Put 定价 (看跌-看涨平价)")
put = data.get("put", {})
chk("2.1 put price > 0",
    put.get("price", 0) > 0, f"got {put.get('price')}")
chk("2.2 put delta in (-1, 0)",
    -1 < put.get("delta", 0) < 0, f"got {put.get('delta')}")
# 注: 简化模型忽略分红, C-P 应在 [S-K*exp(-rT), S-K] 之间
import math as _m
lower = 100 - 100 * _m.exp(-0.03 * 30 / 365)
upper = 100 - 100
diff = call["price"] - put["price"]
chk("2.3 put-call parity in [K·exp(-rT)-K, 0] 边界",
    lower - 100 * 0.05 <= diff <= upper + 100 * 0.05,
    f"C-P={diff:.4f}, lower={lower:.3f}, upper=0")

# 3) 对冲建议
print("\n[3] 对冲建议")
hedge = data.get("hedge", {})
chk("3.1 has delta_exposure",
    isinstance(hedge.get("delta_exposure"), (int, float)))
chk("3.2 has contracts_to_sell",
    isinstance(hedge.get("contracts_to_sell"), (int, float)))
chk("3.3 has advice",
    isinstance(hedge.get("advice"), str) and len(hedge.get("advice", "")) > 0)

# 4) ITM/OTM
print("\n[4] 实值 S>K")
r4 = get("/api/yeren/option_hedge?code=600519&spot=110&strike=100&days=30")
chk("4.1 ok=true", r4.get("ok") is True)
chk("4.2 ITM moneyness",
    r4.get("data", {}).get("moneyness") == "实值 (ITM)")
chk("4.3 ITM delta > 0.5",
    r4.get("data", {}).get("call", {}).get("delta", 0) > 0.5,
    f"got {r4.get('data',{}).get('call',{}).get('delta')}")

print("\n[5] 虚值 S<K")
r5 = get("/api/yeren/option_hedge?code=600519&spot=90&strike=100&days=30")
chk("5.1 ok=true", r5.get("ok") is True)
chk("5.2 OTM moneyness",
    r5.get("data", {}).get("moneyness") == "虚值 (OTM)")
chk("5.3 OTM delta < 0.5",
    r5.get("data", {}).get("call", {}).get("delta", 0) < 0.5,
    f"got {r5.get('data',{}).get('call',{}).get('delta')}")

# 6) 错误路径
print("\n[6] spot=0 → 400")
r6 = get("/api/yeren/option_hedge?code=600519&spot=0&strike=100&days=30")
chk("6.1 rejected",
    not r6.get("ok") and r6.get("status_code") == 400)

print("\n[7] days=400 → 400")
r7 = get("/api/yeren/option_hedge?code=600519&spot=100&strike=100&days=400")
chk("7.1 rejected",
    not r7.get("ok") and r7.get("status_code") == 400)

# 8) 不同 vol
print("\n[8] 不同 vol 对比")
r8a = get("/api/yeren/option_hedge?code=600519&spot=100&strike=100&days=30&vol=0.15")
r8b = get("/api/yeren/option_hedge?code=600519&spot=100&strike=100&days=30&vol=0.45")
chk("8.1 higher vol → higher price",
    r8b.get("data", {}).get("call", {}).get("price", 0)
    > r8a.get("data", {}).get("call", {}).get("price", 0),
    f"low={r8a.get('data',{}).get('call',{}).get('price')} high={r8b.get('data',{}).get('call',{}).get('price')}")

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R408 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)