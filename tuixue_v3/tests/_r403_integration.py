#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R403 量化交易信号 — 集成测试"""
import json, os, sys, time, urllib.request

BASE = os.environ.get("TUIXUE_BASE", "http://127.0.0.1:7799")

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
print("R403 · 量化交易信号 — 集成测试")
print("=" * 70)

# 1) 默认 lookback=20
print("\n[1] 600519 默认 lookback=20")
r = get("/api/yeren/quant_signal?code=600519", timeout=60)
chk("1.1 ok=true", r.get("ok") is True, f"err={r.get('error')}")
data = r.get("data", {})
chk("1.2 has 3 signals",
    all(k in data.get("signals", {}) for k in ("momentum", "mean_reversion", "breakout")),
    f"got {list(data.get('signals',{}).keys())}")
chk("1.3 composite in [-1, 1]",
    -1.0 <= data.get("composite", 0) <= 1.0,
    f"got {data.get('composite')}")
chk("1.4 verdict in [买入, 卖出, 持有]",
    data.get("verdict") in ("买入", "卖出", "持有"),
    f"got {data.get('verdict')}")
chk("1.5 last_close > 0",
    data.get("last_close", 0) > 0)
chk("1.6 n_bars >= 20",
    data.get("n_bars", 0) >= 20, f"got {data.get('n_bars')}")

# 2) 自定义 lookback=60
print("\n[2] 000001 lookback=60")
r2 = get("/api/yeren/quant_signal?code=000001&lookback=60", timeout=60)
chk("2.1 ok=true", r2.get("ok") is True)
chk("2.2 n_bars >= 60",
    r2.get("data", {}).get("n_bars", 0) >= 60,
    f"got {r2.get('data',{}).get('n_bars')}")

# 3) lookback 越界
print("\n[3] lookback=200 → 400")
r3 = get("/api/yeren/quant_signal?code=600519&lookback=200")
chk("3.1 rejected",
    not r3.get("ok") and r3.get("status_code") == 400)

# 4) 非法 code
print("\n[4] 非法 code → 400")
r4 = get("/api/yeren/quant_signal?code=abc")
chk("4.1 rejected",
    not r4.get("ok") and r4.get("status_code") == 400)

# 5) 缓存命中
print("\n[5] 缓存命中 < 200ms")
t0 = time.time()
r5 = get("/api/yeren/quant_signal?code=600519", timeout=30)
elapsed = time.time() - t0
chk("5.1 second call < 500ms",
    elapsed < 0.5, f"elapsed={elapsed:.3f}s")
chk("5.2 same composite",
    r5.get("data", {}).get("composite") == data.get("composite"))

# 6) 多次 lookback 同一股票
print("\n[6] 不同 lookback 对比")
r6a = get("/api/yeren/quant_signal?code=600519&lookback=10", timeout=30)
r6b = get("/api/yeren/quant_signal?code=600519&lookback=30", timeout=30)
chk("6.1 both ok",
    r6a.get("ok") is True and r6b.get("ok") is True)
chk("6.2 different n_bars",
    r6a.get("data", {}).get("n_bars") != r6b.get("data", {}).get("n_bars"),
    f"a={r6a.get('data',{}).get('n_bars')}, b={r6b.get('data',{}).get('n_bars')}")

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R403 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)