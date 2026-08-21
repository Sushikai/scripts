#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R409 大宗交易 — 集成测试"""
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
print("R409 · 大宗交易 — 集成测试")
print("=" * 70)

# 1) 默认 30 日
print("\n[1] 600519 默认 30 日")
r = get("/api/yeren/block_trade?code=600519&lookback_days=30")
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
chk("1.2 has 3-15 trades",
    3 <= data.get("n_trades", 0) <= 15,
    f"got {data.get('n_trades')}")
trades = data.get("trades", [])
chk("1.3 trades sorted desc by ts",
    all(trades[i]["ts"] >= trades[i + 1]["ts"] for i in range(len(trades) - 1)))
chk("1.4 trade has buyer",
    all(t.get("buyer") and isinstance(t["buyer"], str) for t in trades))
chk("1.5 trade has amount_yi > 0",
    all(t.get("amount_yi", 0) > 0 for t in trades))
chk("1.6 discount_pct in [-7, 3]",
    all(-7 <= t.get("discount_pct", 100) <= 3 for t in trades))

# 2) 聚合统计
print("\n[2] 聚合统计")
stats = data.get("stats", {})
chk("2.1 total_amount_yi > 0",
    stats.get("total_amount_yi", 0) > 0)
chk("2.2 has avg_discount_pct",
    isinstance(stats.get("avg_discount_pct"), (int, float)))
chk("2.3 n_discount + n_premium == n_trades",
    stats.get("n_discount", 0) + stats.get("n_premium", 0) == data.get("n_trades"))
chk("2.4 has top_buyers list",
    isinstance(stats.get("top_buyers"), list)
    and len(stats.get("top_buyers", [])) <= 5)
chk("2.5 has win_rate_post_pct in [0,100]",
    0 <= stats.get("win_rate_post_pct", 0) <= 100)

# 3) 确定性
print("\n[3] mock 确定性 (同 code)")
r3 = get("/api/yeren/block_trade?code=600519&lookback_days=30")
chk("3.1 same n_trades",
    r3.get("data", {}).get("n_trades") == data.get("n_trades"))
chk("3.2 same total_amount_yi",
    r3.get("data", {}).get("stats", {}).get("total_amount_yi")
    == stats.get("total_amount_yi"))

# 4) lookback 越界
print("\n[4] lookback=3 → 400")
r4 = get("/api/yeren/block_trade?code=600519&lookback_days=3")
chk("4.1 rejected",
    not r4.get("ok") and r4.get("status_code") == 400)

# 5) 非法 code
print("\n[5] 非法 code → 400")
r5 = get("/api/yeren/block_trade?code=abc")
chk("5.1 rejected",
    not r5.get("ok") and r5.get("status_code") == 400)

# 6) 后续表现
print("\n[6] 后续 30 日表现")
chk("6.1 post_30d_ret_pct in [-15, 25]",
    all(-15 <= t.get("post_30d_ret_pct", 100) <= 25 for t in trades))

# 7) 长 lookback
print("\n[7] lookback=120")
r7 = get("/api/yeren/block_trade?code=000001&lookback_days=120")
chk("7.1 ok=true", r7.get("ok") is True)
chk("7.2 n_trades >= 3",
    r7.get("data", {}).get("n_trades", 0) >= 3)

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R409 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)