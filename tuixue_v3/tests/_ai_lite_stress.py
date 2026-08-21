"""Light 100r stress test (~ 12 min)

Picks 10 most-different questions × 10 codes.
2 worker parallel (after inflight budget).
"""
import os, json, time, requests, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = "http://127.0.0.1:7799"
WORKERS = int(os.environ.get("WORKERS", "2"))
TAG = sys.argv[1] if len(sys.argv) > 1 else "lite"
OUT = f"/tmp/ai_lite_{TAG}.json"

# 10 high-signal questions, variety
QUESTIONS = [
    {"cat": "基础买卖", "q": "现在可以买吗?给出明确结论"},
    {"cat": "板块主线", "q": "板块龙头是谁?这只排名第几?"},
    {"cat": "战法规则", "q": "符合 Y0-Y9 哪几条战法?具体怎么套用?"},
    {"cat": "业绩财务", "q": "业绩反转的核心指标有没有改善?"},
    {"cat": "资金席位", "q": "今天的主力资金净流入/流出多少?"},
    {"cat": "龙虎榜", "q": "上龙虎榜了吗?买入前 5 席位?"},
    {"cat": "K线技术", "q": "MACD/KDJ/RSI 处于什么状态?金叉/死叉?"},
    {"cat": "板块主线", "q": "板块整体估值处于历史百分位多少?"},
    {"cat": "仓位止损", "q": "止损位具体在哪?跌穿后怎么操作?"},
    {"cat": "涨停连板", "q": "涨停是首板还是连板?封单多少?"},
]

CODES = ["002716", "600519", "000858", "300750", "002594",
         "600276", "000333", "300059", "600030", "000725"]

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
print(f"=== Lite stress [{TAG}] {len(jobs)} jobs × {WORKERS} workers ===")
results = []
t0 = time.time()
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = [ex.submit(run_one, *j) for j in jobs]
    for i, fut in enumerate(as_completed(futs), 1):
        r = fut.result()
        results.append(r)
        if i % 25 == 0 or i == len(jobs):
            ok = sum(1 for x in results if x.get('ok'))
            tc = sum(len(x.get('tc',[]) or []) for x in results)
            dt_total = time.time() - t0
            eta = dt_total / i * (len(jobs) - i)
            print(f"  [{i:3d}/{len(jobs)}] ok={ok} tc={tc} elapsed={dt_total:.0f}s eta={eta:.0f}s", flush=True)
            # Incremental save
            json.dump({"tag": TAG, "results": results}, open(OUT+".partial", 'w'), ensure_ascii=False, default=str)

# Final summary
ok_n = sum(1 for x in results if x.get('ok'))
err_n = len(results) - ok_n
tc_n = sum(len(x.get('tc',[]) or []) for x in results)
lat_ok = [x['latency'] for x in results if x.get('ok')]
lat_p50 = sorted(lat_ok)[len(lat_ok)//2] if lat_ok else 0
lat_p95 = sorted(lat_ok)[int(len(lat_ok)*0.95)] if lat_ok else 0
summary = {
    "tag": TAG, "total": len(results), "ok": ok_n, "errors": err_n,
    "ok_pct": round(ok_n / max(1, len(results)) * 100, 1),
    "tool_calls_total": tc_n,
    "avg_latency_s": round(sum(lat_ok)/max(1,len(lat_ok)), 2),
    "p50_latency_s": round(lat_p50, 2),
    "p95_latency_s": round(lat_p95, 2),
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
json.dump({"tag": TAG, "summary": summary, "results": results}, open(OUT, 'w'), ensure_ascii=False, default=str)
print(f"Saved → {OUT}")
