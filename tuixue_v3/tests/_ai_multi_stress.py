"""Multi-tool stress test (~ 6 min)

Question set focused on queries that should trigger 2-5 tool calls in parallel.
User said: "AI回答同时可以调多种工具 比如我说给我推荐同时满足各种战法的股票".
"""
import os, json, time, requests, sys
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "http://127.0.0.1:7799"
WORKERS = int(os.environ.get("WORKERS", "2"))
TAG = sys.argv[1] if len(sys.argv) > 1 else "multi"
OUT = f"/tmp/ai_multi_{TAG}.json"

# Multi-tool questions (no code) — should trigger 3-5 tool calls
QUESTIONS = [
    {"cat": "多工具组合", "q": "给我推荐同时满足 Y0+Y1+Y4 战法 + 资金净流入 + 龙头涨停 的股票, 至少 5 只"},
    {"cat": "多工具组合", "q": "今天最值得买的 3 只龙头是哪些?分别命中哪些战法?"},
    {"cat": "多工具组合", "q": "近一周业绩反转 + 主力流入 + 涨停封板的股票,推荐 5 只"},
    {"cat": "多工具组合", "q": "当前主线板块有哪些?每个板块的龙头和战法命中情况?"},
    {"cat": "多工具组合", "q": "技术面+资金面+战法三维共振的股票,推荐 5 只"},
    {"cat": "多工具组合", "q": "周线擒牛战法 + 主力建仓 + 涨停接力的股票,推荐 5 只"},
]

# Some codes too
CODES = ["002716", "600519"]  # smaller set for multi-tool

def run_one(idx, code, q):
    t = time.time()
    try:
        r = requests.post(f"{BASE}/api/yeren/ai/chat?_nocache=1",
            json={"code": code, "message": q, "history": []}, timeout=60)
        dt = time.time() - t
        j = r.json()
        ok = j.get("ok", False)
        d = j.get("data", {}) or {}
        return {"idx": idx, "code": code, "q": q, "ok": ok,
                "latency": round(dt, 2),
                "reply_len": len(d.get("reply", "")),
                "tc": [tc.get("call","?") for tc in d.get("tool_calls", [])],
                "rules_hit": d.get("rules_hit", []),
                "error": j.get("error", "")[:120] if not ok else ""}
    except Exception as e:
        return {"idx": idx, "code": code, "q": q, "ok": False,
                "latency": round(time.time() - t, 2), "error": str(e)[:120]}

jobs = [(i, c, q["q"]) for i, q in enumerate(QUESTIONS) for c in CODES]
print(f"=== Multi-tool stress [{TAG}] {len(jobs)} jobs × {WORKERS} workers ===")
results = []
t0 = time.time()
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = [ex.submit(run_one, *j) for j in jobs]
    for i, fut in enumerate(as_completed(futs), 1):
        r = fut.result()
        results.append(r)
        if i % 12 == 0 or i == len(jobs):
            ok = sum(1 for x in results if x.get('ok'))
            tc = sum(len(x.get('tc',[]) or []) for x in results)
            dt_total = time.time() - t0
            eta = dt_total / i * (len(jobs) - i)
            print(f"  [{i:3d}/{len(jobs)}] ok={ok} tc={tc} elapsed={dt_total:.0f}s eta={eta:.0f}s", flush=True)
            json.dump({"tag": TAG, "results": results}, open(OUT+".partial", 'w'), ensure_ascii=False, default=str)

ok_n = sum(1 for x in results if x.get('ok'))
tc_n = sum(len(x.get('tc',[]) or []) for x in results)
lat_ok = [x['latency'] for x in results if x.get('ok')]

# Per-question stats
from collections import defaultdict
by_q = defaultdict(lambda: {"ok":0, "tc":0, "n":0, "tool_set": []})
for r in results:
    if r.get("error"):
        continue
    q = r["q"]
    by_q[q]["n"] += 1
    if r.get("ok"):
        by_q[q]["ok"] += 1
    by_q[q]["tc"] += len(r.get("tc") or [])
    for tc in r.get("tc", []) or []:
        by_q[q]["tool_set"].append(tc.split(",")[0])

print("\n=== Per-question ===")
for q, s in sorted(by_q.items(), key=lambda x: -x[1]["tc"]/max(x[1]["n"],1)):
    avg = s["tc"]/max(s["n"],1)
    from collections import Counter
    top_tools = Counter(s["tool_set"]).most_common(5)
    print(f"  tc_avg={avg:.2f} ok={s['ok']}/{s['n']} {q[:55]}")
    print(f"    top tools: {top_tools}")

summary = {
    "tag": TAG, "total": len(results), "ok": ok_n, "errors": len(results)-ok_n,
    "ok_pct": round(ok_n / max(1, len(results)) * 100, 1),
    "tool_calls_total": tc_n,
    "avg_latency_s": round(sum(lat_ok)/max(1,len(lat_ok)), 2),
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
json.dump({"tag": TAG, "summary": summary, "results": results}, open(OUT, 'w'), ensure_ascii=False, default=str)
print(f"Saved → {OUT}")
