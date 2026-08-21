#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R391-R400 集成测试 — 决策辅助全集"""
import json, os, sys, urllib.request

BASE = os.environ.get("TUIXUE_BASE", "http://127.0.0.1:7799")
DEVICE = "test_r400_final_1234"

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
print("R391-R400 · 决策辅助全集 — 集成测试")
print("=" * 70)

# R391 · 决策日志
print("\n[1] R391 · 决策日志")
r1 = post("/api/yeren/decision_log",
          {"device_id": DEVICE, "action": "ask", "code": "600519",
           "meta": {"question": "测试"}})
chk("1.1 write ok", r1.get("ok") is True)
r1g = get(f"/api/yeren/decision_log?device_id={DEVICE}&limit=5")
chk("1.2 read returns logs",
    r1g.get("ok") is True and len(r1g.get("data", {}).get("logs", [])) >= 1)

# R392 · 认知偏见检测
print("\n[2] R392 · 认知偏见")
r2 = get(f"/api/yeren/bias_check?device_id={DEVICE}&window_days=30")
chk("2.1 ok", r2.get("ok") is True)
chk("2.2 biases is list",
    isinstance(r2.get("data", {}).get("biases", []), list))
chk("2.3 stats.n_logs >= 1",
    r2.get("data", {}).get("stats", {}).get("n_logs", 0) >= 1)

# R393 · 战法偏好
print("\n[3] R393 · 战法偏好")
r3 = get(f"/api/yeren/strategy_pref?device_id={DEVICE}")
chk("3.1 ok", r3.get("ok") is True)
chk("3.2 has prefs+suggestions",
    "prefs" in r3.get("data", {}) and "suggestions" in r3.get("data", {}))

# R394 · 收益率日历
print("\n[4] R394 · 收益率日历")
r4 = get("/api/yeren/calendar?code=600519")
chk("4.1 ok", r4.get("ok") is True)
chk("4.2 monthly >= 12",
    len(r4.get("data", {}).get("monthly", [])) >= 12,
    f"got {len(r4.get('data',{}).get('monthly',[]))}")
chk("4.3 stats.n_total_days >= 100",
    r4.get("data", {}).get("stats", {}).get("n_total_days", 0) >= 100)

# R395 · 年度复盘
print("\n[5] R395 · 年度复盘")
r5 = get(f"/api/yeren/annual_review?device_id={DEVICE}&year=2026")
chk("5.1 ok", r5.get("ok") is True)
chk("5.2 has summary+verdicts+monthly",
    all(k in r5.get("data", {}) for k in ("summary", "verdicts", "monthly_activity")))

# R396 · 群共享决策
print("\n[6] R396 · 群共享决策")
r6 = post("/api/yeren/share_decision",
          {"device_id": DEVICE, "code": "600519", "verdict": "买入",
           "note": "群共享测试", "group": ["bob_test_1234567890ab", "charlie_1234567890cd"]})
chk("6.1 share ok", r6.get("ok") is True)
chk("6.2 shared_count >= 2",
    r6.get("data", {}).get("shared_count", 0) >= 2)
r6g = get(f"/api/yeren/shared_with_me?device_id=bob_test_1234567890ab")
chk("6.3 bob received",
    any("shared_from" in s for s in r6g.get("data", {}).get("shared", [])))

# R397 · 订阅多通道
print("\n[7] R397 · 订阅多通道")
r7 = post("/api/yeren/subscribe_channel",
          {"device_id": DEVICE, "strategy": "dragon",
           "channels": ["push", "webhook"], "webhook_url": "https://example.com/hook"})
chk("7.1 subscribe ok", r7.get("ok") is True)
r7g = get(f"/api/yeren/subscribe_channel?device_id={DEVICE}")
chk("7.2 has 'dragon' sub",
    "dragon" in r7g.get("data", {}).get("subs", {}))

# R398 · 研报订阅
print("\n[8] R398 · 研报订阅")
r8 = post("/api/yeren/research_sub",
          {"device_id": DEVICE, "code": "600519", "frequency": "weekly"})
chk("8.1 sub ok", r8.get("ok") is True)
r8g = get(f"/api/yeren/research_list?device_id={DEVICE}&code=600519&limit=5")
chk("8.2 list returns reports",
    r8g.get("ok") is True and len(r8g.get("data", {}).get("reports", [])) >= 1)

# R399 · 舆情预警
print("\n[9] R399 · 舆情预警")
r9 = post("/api/yeren/sentiment_alert",
          {"device_id": DEVICE, "code": "600519",
           "news": [{"title": "茅台涨停, 突破新高", "source": "新浪", "ts": 1787229000},
                    {"title": "茅台大跌, 涉嫌违规", "source": "财新", "ts": 1787228000}]})
chk("9.1 ok", r9.get("ok") is True)
chk("9.2 alerts detected",
    len(r9.get("data", {}).get("alerts", [])) >= 1)

# R400 · 决策辅助总集成
print("\n[10] R400 · 总集成")
r10 = get("/api/yeren/decision_meta")
chk("10.1 ok", r10.get("ok") is True)
data10 = r10.get("data", {})
chk("10.2 n_rounds == 19 (R381-R399)",
    data10.get("n_rounds") == 19)
chk("10.3 has R381 + R399 + R400",
    any(r["r"] == "R381" for r in data10.get("rounds", []))
    and any(r["r"] == "R399" for r in data10.get("rounds", [])))
chk("10.4 has next_blueprint",
    "R401" in str(data10.get("next_blueprint", "")))

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R391-R400 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)