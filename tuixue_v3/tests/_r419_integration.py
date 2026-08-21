#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R419 量化回测平台 — 集成测试"""
import json, os, sys, urllib.request

BASE = os.environ.get("TUIXUE_BASE", "http://127.0.0.1:7799")

results = []
def chk(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")

def post(path, body, timeout=120):
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
print("R419 · 量化回测平台 — 集成测试")
print("=" * 70)

# 1) 默认公式
print("\n[1] 简单公式 momentum > 0")
r = post("/api/yeren/quant_backtest",
         {"formula": "momentum > 0", "lookback": 60, "hold_days": 5},
         timeout=120)
chk("1.1 ok=true", r.get("ok") is True, f"err={r.get('error')}")
data = r.get("data", {})
chk("1.2 n_codes_tested == 5",
    data.get("n_codes_tested") == 5)
chk("1.3 has stats",
    "stats" in data and "avg_ret_pct" in data["stats"])
chk("1.4 has trades list",
    isinstance(data.get("trades"), list))

# 2) AND 复合
print("\n[2] 复合 AND 公式")
r2 = post("/api/yeren/quant_backtest",
          {"formula": "momentum > 0 and vol < 0.05", "lookback": 60, "hold_days": 5},
          timeout=120)
chk("2.1 ok=true", r2.get("ok") is True)
chk("2.2 n_signals <= 5",
    r2.get("data", {}).get("n_signals", 100) <= 5)

# 3) 公式永真
print("\n[3] 公式永真 (momentum > -100)")
r3 = post("/api/yeren/quant_backtest",
          {"formula": "momentum > -100", "lookback": 60, "hold_days": 5},
          timeout=120)
chk("3.1 ok=true", r3.get("ok") is True)
chk("3.2 n_signals == 5 (全部触发)",
    r3.get("data", {}).get("n_signals") == 5,
    f"got {r3.get('data',{}).get('n_signals')}")
chk("3.3 n_trades == 5",
    r3.get("data", {}).get("n_trades") == 5)

# 4) 公式永假
print("\n[4] 公式永假 (momentum > 100)")
r4 = post("/api/yeren/quant_backtest",
          {"formula": "momentum > 100", "lookback": 60, "hold_days": 5},
          timeout=120)
chk("4.1 ok=true", r4.get("ok") is True)
chk("4.2 n_signals == 0",
    r4.get("data", {}).get("n_signals") == 0)
chk("4.3 stats.avg_ret_pct == 0",
    r4.get("data", {}).get("stats", {}).get("avg_ret_pct") == 0)

# 5) 非法公式
print("\n[5] 非法变量 → 400")
r5 = post("/api/yeren/quant_backtest",
          {"formula": "evil_func() > 0", "lookback": 60, "hold_days": 5},
          timeout=120)
chk("5.1 rejected (含函数调用)",
    not r5.get("ok") and r5.get("status_code") == 400)

# 6) lookback 越界
print("\n[6] lookback=10 → 400")
r6 = post("/api/yeren/quant_backtest",
          {"formula": "momentum > 0", "lookback": 10, "hold_days": 5})
chk("6.1 rejected",
    not r6.get("ok") and r6.get("status_code") == 400)

# 7) hold_days 越界
print("\n[7] hold_days=50 → 400")
r7 = post("/api/yeren/quant_backtest",
          {"formula": "momentum > 0", "lookback": 60, "hold_days": 50})
chk("7.1 rejected",
    not r7.get("ok") and r7.get("status_code") == 400)

# 8) 空 formula
print("\n[8] formula='' → 400")
r8 = post("/api/yeren/quant_backtest",
          {"formula": "", "lookback": 60, "hold_days": 5})
chk("8.1 rejected",
    not r8.get("ok") and r8.get("status_code") == 400)

# 9) 业绩归因
print("\n[9] 业绩归因字段")
r9 = post("/api/yeren/quant_backtest",
          {"formula": "momentum > -100", "lookback": 60, "hold_days": 5},
          timeout=120)
stats = r9.get("data", {}).get("stats", {})
chk("9.1 has best/worst",
    "best_trade_pct" in stats and "worst_trade_pct" in stats)
chk("9.2 has sharpe",
    isinstance(stats.get("sharpe"), (int, float)))
chk("9.3 win_rate in [0,100]",
    0 <= stats.get("win_rate_pct", -1) <= 100)

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R419 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)