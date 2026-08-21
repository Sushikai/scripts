#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R385 决策历史面板 — 集成测试

测试 /api/yeren/decision_history endpoint 完整链路:
  1) 空 device → ok, 空 records
  2) device_id 校验 (非合规 device_id)
  3) seed 3 条历史 (买入/回避/观望 + 1 user 噪音) → endpoint 拉 kline → T+1/T+3/T+5 + 命中率
  4) 缓存命中 (5min TTL)
"""
import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("TUIXUE_BASE", "http://127.0.0.1:7799")
DEVICE = f"test_hist_r385_{int(time.time())}"

results = []
def chk(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")

def http_get(path, timeout=60):
    url = f"{BASE}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"_err": str(e)}

print("=" * 70)
print(f"R385 · 决策历史面板 — 集成测试  (device={DEVICE})")
print("=" * 70)

# 1) 空 device
print("\n[1] 空 device_id → 空 records")
r = http_get("/api/yeren/decision_history?device_id=&limit=10")
chk("1.1 empty device returns ok", r.get("ok") is True)
chk("1.2 records empty",
    r.get("data", {}).get("records") == [],
    f"got {len(r.get('data',{}).get('records',[]))}")

# 2) 非法 device_id (长度/格式不合规)
print("\n[2] 非法 device_id → 空 records (regex fail)")
r = http_get("/api/yeren/decision_history?device_id=ab&limit=10")
chk("2.1 invalid device short → ok + empty",
    r.get("ok") is True and r.get("data", {}).get("records") == [])

# 3) seed + 拉 kline
print("\n[3] seed 3 条决策 + 1 user 噪音 → 3 records + stats")
import datetime as dt
base = dt.datetime(2026, 7, 15, 10, 30).timestamp()
recs = [
    {"device_id": DEVICE, "ts": int(base), "role": "assistant",
     "content": "分析 600519 茅台 — 当前价 1420 元, 资金净流入, 板块强势, 建议【买入】", "code": "600519"},
    {"device_id": DEVICE, "ts": int(base + 86400), "role": "assistant",
     "content": "000001 平安银行 — 业绩承压, 建议【回避】", "code": "000001"},
    {"device_id": DEVICE, "ts": int(base + 86400*2), "role": "assistant",
     "content": "300750 宁德时代 — 横盘整理, 建议【观望】", "code": "300750"},
    {"device_id": DEVICE, "ts": int(base + 86400*3), "role": "user",
     "content": "其他股票 600000 也帮我看看"},  # user, ignored
]
path = f"data/yeren_history/{DEVICE}.jsonl"
os.makedirs("data/yeren_history", exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    for rec in recs:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

r = http_get(f"/api/yeren/decision_history?device_id={DEVICE}&limit=10", timeout=120)
print(f"  raw: {json.dumps(r, ensure_ascii=False)[:300]}...")
data = r.get("data", {})
records = data.get("records", [])
stats = data.get("stats", {})

chk("3.1 records >= 1", len(records) >= 1, f"got {len(records)}")
chk("3.2 stats has hit_rate_t1",
    "hit_rate_t1" in stats,
    f"keys={list(stats.keys())}")
chk("3.3 stats has n_buy/n_avoid",
    stats.get("n_buy", 0) >= 1 and stats.get("n_avoid", 0) >= 1,
    f"buy={stats.get('n_buy')} avoid={stats.get('n_avoid')}")

# 4) 校验每条 record 结构
if records:
    rec0 = records[0]
    needed = {"code", "verdict", "base_date", "base_close", "t1_pct", "t3_pct", "t5_pct", "hit_t1"}
    chk("4.1 record schema complete",
        needed.issubset(rec0.keys()),
        f"missing={needed - set(rec0.keys())}")
    chk("4.2 t1_pct numeric",
        isinstance(rec0.get("t1_pct"), (int, float)),
        f"type={type(rec0.get('t1_pct')).__name__}")
    chk("4.3 verdict 关键词 in [买入,回避,观望]",
        rec0.get("verdict") in ("买入", "回避", "观望"),
        f"verdict={rec0.get('verdict')}")

# 5) 缓存命中
print("\n[4] 缓存命中 (5min TTL)")
t0 = time.time()
r2 = http_get(f"/api/yeren/decision_history?device_id={DEVICE}&limit=10", timeout=30)
elapsed = time.time() - t0
chk("5.1 second call still returns records",
    len(r2.get("data", {}).get("records", [])) == len(records))
chk("5.2 second call < 1.5s (cache hit)",
    elapsed < 1.5,
    f"elapsed={elapsed:.2f}s")

# 6) limit 生效
print("\n[5] limit 边界")
r3 = http_get(f"/api/yeren/decision_history?device_id={DEVICE}&limit=1", timeout=30)
chk("6.1 limit=1 截断",
    len(r3.get("data", {}).get("records", [])) <= 1)

# 7) cleanup
try:
    os.remove(path)
except Exception:
    pass

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R385 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)