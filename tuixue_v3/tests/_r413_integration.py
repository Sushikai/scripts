#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R413 美股 ADR — 集成测试"""
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
print("R413 · 美股 ADR — 集成测试")
print("=" * 70)

# 1) 阿里巴巴 BABA
print("\n[1] BABA (阿里巴巴)")
r = get("/api/yeren/adr_quote?code=BABA")
chk("1.1 ok=true", r.get("ok") is True)
data = r.get("data", {})
chk("1.2 is_chinese_adr=true",
    data.get("is_chinese_adr") is True)
chk("1.3 name=阿里巴巴",
    data.get("name") == "阿里巴巴")
chk("1.4 last_price_usd > 0",
    data.get("last_price_usd", 0) > 0)
chk("1.5 fx_usd_cny=7.10",
    data.get("fx_usd_cny") == 7.10)
chk("1.6 cny_price_est = usd × 7.10",
    abs(data.get("cny_price_est", 0)
        - data.get("last_price_usd", 0) * 7.10) < 0.1)
chk("1.7 cn_hk_code=09988",
    data.get("cn_hk_code") == "09988")
chk("1.8 ratio=8",
    data.get("ratio") == 8)

# 2) 价差
print("\n[2] 价差分析")
chk("2.1 has spread_pct_est",
    isinstance(data.get("spread_pct_est"), (int, float)))
chk("2.2 spread_status 文案",
    data.get("spread_status") in ("溢价", "折价", "平价"))

# 3) 京东 JD
print("\n[3] JD (京东)")
r3 = get("/api/yeren/adr_quote?code=JD")
chk("3.1 is_chinese_adr=true",
    r3.get("data", {}).get("is_chinese_adr") is True)
chk("3.2 cn_hk_code=09618",
    r3.get("data", {}).get("cn_hk_code") == "09618")

# 4) 非中概股
print("\n[4] AAPL (苹果非中概)")
r4 = get("/api/yeren/adr_quote?code=AAPL")
chk("4.1 is_chinese_adr=false",
    r4.get("data", {}).get("is_chinese_adr") is False)
chk("4.2 cn_hk_code=None",
    r4.get("data", {}).get("cn_hk_code") is None)
chk("4.3 spread_status=非中概股 ADR",
    r4.get("data", {}).get("spread_status") == "非中概股 ADR")

# 5) 时区
print("\n[5] 时区判断")
chk("5.1 is_trading bool",
    isinstance(data.get("is_trading"), bool))

# 6) 确定性
print("\n[6] mock 确定性")
r6 = get("/api/yeren/adr_quote?code=BABA")
chk("6.1 same last_price_usd",
    r6.get("data", {}).get("last_price_usd") == data.get("last_price_usd"))

# 7) 错误路径
print("\n[7] 非法 code")
r7a = get("/api/yeren/adr_quote?code=123")  # 数字
chk("7.1 数字 → 400",
    not r7a.get("ok") and r7a.get("status_code") == 400)
r7b = get("/api/yeren/adr_quote?code=TOOLONG6")  # 6 位
chk("7.2 6 位 → 400",
    not r7b.get("ok") and r7b.get("status_code") == 400)

print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("=" * 70)
print(f"  R413 总计 {passed}/{total} 通过")
print("=" * 70)
sys.exit(0 if passed == total else 1)