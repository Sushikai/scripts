#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R414 加密货币联动 — 集成测试"""
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
print("R414 · 加密货币联动 — 集成测试")
print("=" * 70)

# 1) BTC
print("\n[1] BTC (比特币)")
r = get("/api/yeren/crypto_link?symbol=BTC")
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
chk("1.2 name=比特币",
    data.get("name") == "比特币")
chk("1.3 last_price_usd > 50000",
    data.get("last_price_usd", 0) > 50000,
    f"got {data.get('last_price_usd')}")
chk("1.4 cny_price = usd × 7.10",
    abs(data.get("cny_price", 0)
        - data.get("last_price_usd", 0) * 7.10) < 1)
chk("1.5 change_24h_pct in [-10, 10]",
    -10 <= data.get("change_24h_pct", 100) <= 10)
chk("1.6 change_7d_pct in [-15, 15]",
    -15 <= data.get("change_7d_pct", 100) <= 15)

# 2) 联动相关性
print("\n[2] A 股联动")
corrs = data.get("cn_stocks_correlation", [])
chk("2.1 has 2 cn_stocks",
    len(corrs) == 2, f"got {len(corrs)}")
chk("2.2 corr in [0.3, 1.0]",
    all(0.3 <= c.get("correlation", 0) <= 1.0 for c in corrs))
chk("2.3 has avg_correlation",
    isinstance(data.get("avg_correlation"), (int, float)))

# 3) 资金流向
print("\n[3] 资金流向")
chk("3.1 has net_inflow",
    isinstance(data.get("net_inflow_yi_usd"), (int, float)))
chk("3.2 flow_direction",
    data.get("flow_direction") in ("净流入", "净流出"))

# 4) ETH
print("\n[4] ETH (以太坊)")
r4 = get("/api/yeren/crypto_link?symbol=ETH")
chk("4.1 ok=true", r4.get("ok") is True)
chk("4.2 name=以太坊",
    r4.get("data", {}).get("name") == "以太坊")
chk("4.3 last_price_usd in [2550, 8550]",
    2550 <= r4.get("data", {}).get("last_price_usd", 0) <= 8550)

# 5) DOGE
print("\n[5] DOGE (狗狗币)")
r5 = get("/api/yeren/crypto_link?symbol=DOGE")
chk("5.1 ok=true", r5.get("ok") is True)
chk("5.2 last_price_usd < 1",
    r5.get("data", {}).get("last_price_usd", 100) < 1)

# 6) 非法 symbol
print("\n[6] symbol=SHIB → 400")
r6 = get("/api/yeren/crypto_link?symbol=SHIB")
chk("6.1 rejected",
    not r6.get("ok") and r6.get("status_code") == 400)

# 7) 确定性
print("\n[7] mock 确定性")
r7 = get("/api/yeren/crypto_link?symbol=BTC")
chk("7.1 same last_price_usd",
    r7.get("data", {}).get("last_price_usd") == data.get("last_price_usd"))

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R414 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)