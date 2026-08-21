"""R313 query 关键词扩展 验证测试
10 题覆盖新加的关键词 (封成比/封单/炸板/反包/接力/BOLL/OBV/季报/同比/超大大单)
"""
import os, json, time, requests, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "http://127.0.0.1:7799"
WORKERS = int(os.environ.get("WORKERS", "2"))
TAG = sys.argv[1] if len(sys.argv) > 1 else "r313"
OUT = f"/tmp/ai_r313_{TAG}.json"

# 10 题覆盖 R313 新增关键词
QUESTIONS = [
    {"new": "封成比", "q": "002716 封成比多少?封单几个亿?"},
    {"new": "炸板", "q": "002716 最近有没有炸板?炸板几次?"},
    {"new": "BOLL", "q": "002716 BOLL 布林线位置?上下轨压力支撑?"},
    {"new": "OBV", "q": "002716 OBV 能量潮趋势?是否量价齐升?"},
    {"new": "同比", "q": "002716 同比增速多少?扣非利润同比是否提升?"},
    {"new": "季报", "q": "002716 最近一季报业绩如何?超预期吗?"},
    {"new": "反包", "q": "002716 今日是否反包?k线形态?"},
    {"new": "接力", "q": "002716 涨停接力可能性?梯队里排第几?"},
    {"new": "特大单", "q": "002716 特大单净流入多少?超大大单增减?"},
    {"new": "压力位", "q": "002716 压力位在哪?支撑位在哪?"},
]

CODE = "002716"

def run_one(idx, q):
    t = time.time()
    try:
        r = requests.post(f"{BASE}/api/yeren/ai/chat?_nocache=1",
            json={"code": CODE, "message": q, "history": []}, timeout=50)
        dt = time.time() - t
        j = r.json()
        ok = j.get("ok", False)
        d = j.get("data", {}) or {}
        return {"idx": idx, "q": q, "ok": ok,
                "latency": round(dt, 2),
                "reply_len": len(d.get("reply", "")),
                "tc": [tc.get("call","?") for tc in d.get("tool_calls", [])],
                "rules_hit": d.get("rules_hit", []),
                "error": j.get("error", "")[:120] if not ok else ""}
    except Exception as e:
        return {"idx": idx, "q": q, "ok": False,
                "latency": round(time.time() - t, 2), "error": str(e)[:120]}

print(f"=== R313 keyword test [{TAG}] {len(QUESTIONS)} jobs × {WORKERS} workers ===")
results = []
t0 = time.time()
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = [ex.submit(run_one, i, q["q"]) for i, q in enumerate(QUESTIONS)]
    for i, fut in enumerate(as_completed(futs), 1):
        r = fut.result()
        results.append(r)
        if i % 5 == 0 or i == len(QUESTIONS):
            ok = sum(1 for x in results if x.get('ok'))
            tc = sum(len(x.get('tc',[]) or []) for x in results)
            dt_total = time.time() - t0
            eta = dt_total / i * (len(QUESTIONS) - i)
            print(f"  [{i:3d}/{len(QUESTIONS)}] ok={ok} tc={tc} elapsed={dt_total:.0f}s eta={eta:.0f}s", flush=True)
            json.dump({"tag": TAG, "results": results}, open(OUT+".partial", 'w'), ensure_ascii=False, default=str)

print("\n=== per-new-keyword ===")
new_stats = {}
for r in results:
    if r.get('error'): continue
    new_label = [q["new"] for q in QUESTIONS if q["q"] == r["q"]][0]
    if new_label not in new_stats:
        new_stats[new_label] = {"ok":0, "tc":0, "n":0}
    new_stats[new_label]["n"] += 1
    if r.get('ok'): new_stats[new_label]["ok"] += 1
    new_stats[new_label]["tc"] += len(r.get('tc') or [])

for new_label, s in new_stats.items():
    avg = s["tc"]/max(s["n"],1)
    print(f'  tc={s["tc"]} ok={s["ok"]}/{s["n"]}  [{new_label}]')

ok_n = sum(1 for x in results if x.get('ok'))
tc_n = sum(len(x.get('tc',[]) or []) for x in results)
summary = {"tag": TAG, "total": len(results), "ok": ok_n, "ok_pct": round(ok_n/len(results)*100, 1), "tool_calls": tc_n}
print(json.dumps(summary, ensure_ascii=False, indent=2))
json.dump({"tag": TAG, "summary": summary, "results": results}, open(OUT, 'w'), ensure_ascii=False, default=str)
print(f"Saved → {OUT}")
