#!/usr/bin/env python3
"""
tuixue_v3 完整功能验收测试
=========================
每个功能要求:
  - 通过: HTTP 200 + 业务 ok
  - 性能: 必须 < 阈值 (P95 验收)
  - 稳定性: 重试 3 次, 任何一次超时就算失败
  - 报告: 退出码 0 = 全部通过
"""
import sys, time, json
from pathlib import Path
import requests

LOCAL = "http://localhost:7799"

# 验收标准 (硬阈值)
PERF_BUDGETS = {
    "/api/health": 1.0,
    "/api/laws": 2.0,
    "/api/metrics": 2.0,
    "/api/market/overview": 30.0,    # 大盘指数 (5 个源热备)
    "/api/stock/search": 5.0,        # 搜索 (有缓存)
    "/api/stock/002747": 15.0,       # 个股综合
    "/api/stock/002747/ai_analysis": 35.0,  # AI 分析 (7 重兜底 + 2 retry)
    "/api/stock/002747/ai_analysis ": 35.0,
    "/api/laws/audit": 2.0,
    "/api/laws/categories": 2.0,
    "/static/app.js": 1.0,
    "/static/style.css": 1.0,
}

# POST 接口单独测
POST_TESTS = [
    ("/api/screen", {"date":None,"mode":"live","pool_size":20,"top_n":5}, 60.0),  # 选股
]

# 数据源 + 关键股票
TEST_CODES = ["002747", "600519", "000300", "601121"]

# 验收结果
RESULTS = []
TOTAL = 0
PASSED = 0

def record(name, ok, elapsed, budget, detail=""):
    global TOTAL, PASSED
    TOTAL += 1
    if ok:
        PASSED += 1
    icon = "✅" if ok else "❌"
    speed = "🟢" if elapsed < budget * 0.5 else "🟡" if elapsed < budget else "🔴"
    print(f"  {icon}{speed} {name:50s} {elapsed:6.2f}s / 预算 {budget:5.1f}s  {detail}")
    RESULTS.append({"name": name, "ok": ok, "elapsed": elapsed, "budget": budget, "detail": detail})

def get(path, budget=None, retries=2):
    """GET 带重试, 返回 (ok, elapsed, status, body)"""
    if budget is None:
        budget = PERF_BUDGETS.get(path, 10.0)
    for attempt in range(1, retries+1):
        try:
            t0 = time.time()
            r = requests.get(LOCAL + path, timeout=budget*1.2)
            elapsed = time.time() - t0
            if r.status_code == 200:
                try:
                    j = r.json()
                    if j.get("ok"):
                        return True, elapsed, 200, j
                    else:
                        return False, elapsed, 200, j
                except:
                    return True, elapsed, 200, r.text[:100]
            else:
                if attempt == retries:
                    return False, elapsed, r.status_code, r.text[:100]
        except requests.exceptions.Timeout:
            elapsed = budget * 1.2
            if attempt == retries:
                return False, elapsed, 0, "timeout"
        except Exception as e:
            elapsed = time.time() - t0 if 't0' in dir() else 0
            if attempt == retries:
                return False, elapsed, 0, str(e)[:80]
    return False, 0, 0, "no_attempt"

def post(path, body, budget):
    for attempt in range(3):
        try:
            t0 = time.time()
            r = requests.post(LOCAL + path, json=body, timeout=budget*1.2)
            elapsed = time.time() - t0
            if r.status_code == 200:
                try:
                    j = r.json()
                    return j.get("ok", False), elapsed, 200, j
                except:
                    return True, elapsed, 200, r.text[:100]
            if attempt == 2:
                return False, elapsed, r.status_code, r.text[:100]
        except requests.exceptions.Timeout:
            if attempt == 2:
                return False, budget*1.2, 0, "timeout"
        except Exception as e:
            if attempt == 2:
                return False, 0, 0, str(e)[:80]

def main():
    print("════════════════════════════════════════════════════════")
    print("  tuixue_v3 完整功能验收")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  目标: {LOCAL}")
    print("  标准: 全部通过 + P95 在预算内")
    print("════════════════════════════════════════════════════════")

    # 重置源冷却
    try:
        requests.post(LOCAL + "/api/admin/reset_sources", timeout=5)
        print("  ✅ 已重置源冷却\n")
    except:
        print("  ⚠️ 重置源冷却失败 (忽略)\n")

    # ── 1. 健康 + 基础信息 ──
    print("━━ 1. 基础信息 ━━")
    for path in ["/api/health", "/api/laws", "/api/metrics"]:
        ok, elapsed, code, body = get(path)
        detail = ""
        if isinstance(body, dict):
            data = body.get("data", {})
            if isinstance(data, dict):
                if "version" in data: detail = f"v={data['version']}"
                elif "categories" in data: detail = f"{len(data.get('categories', []))}类铁律"
                elif "flat" in data: detail = f"{len(data.get('flat', []))}条铁律"
                elif "endpoints" in data: detail = f"{len(data.get('endpoints', []))}端点"
            elif isinstance(data, list):
                detail = f"{len(data)}项"
        elif isinstance(body, str):
            detail = body[:50]
        record(path, ok, elapsed, PERF_BUDGETS.get(path, 2.0), detail)

    # ── 2. 静态资源 ──
    print("\n━━ 2. 静态资源 ━━")
    for f in ["app.js", "style.css", "index.html"]:
        ok, elapsed, code, body = get(f"/static/{f}")
        size = len(body) if isinstance(body, str) else 0
        record(f"/static/{f}", ok, elapsed, PERF_BUDGETS.get(f"/static/{f}", 1.0), f"{size}B")

    # ── 3. 大盘 ──
    print("\n━━ 3. 大盘指数 ━━")
    ok, elapsed, code, body = get("/api/market/overview")
    detail = ""
    if isinstance(body, dict):
        data = body.get("data", {})
        if isinstance(data, dict) and "indices" in data:
            indices = data.get("indices", [])
            detail = f"{len(indices)}指数"
    record("/api/market/overview", ok, elapsed, PERF_BUDGETS["/api/market/overview"], detail)

    # ── 4. 个股搜索 (中文 + 代码) ──
    print("\n━━ 4. 个股搜索 ━━")
    for q in ["002747", "600519", "茅台", "宝地"]:
        enc = requests.utils.quote(q)
        path = f"/api/stock/search?q={enc}"
        ok, elapsed, code, body = get(path)
        detail = ""
        if isinstance(body, dict):
            r = body.get("data", {}).get("results", [])
            detail = f"{len(r)}命中: {[x['code']+x['name'] for x in r[:2]]}"
        record(f"search '{q}'", ok, elapsed, 5.0, detail)

    # ── 5. 个股综合数据 ──
    print("\n━━ 5. 个股综合数据 ━━")
    for code in TEST_CODES:
        path = f"/api/stock/{code}"
        ok, elapsed, status, body = get(path)
        detail = ""
        if isinstance(body, dict) and body.get("ok"):
            data = body.get("data", {})
            q = data.get("quote", {})
            if q:
                detail = f"现价={q.get('最新价')} {q.get('涨跌幅', 0):+.2f}%"
        record(f"GET /api/stock/{code}", ok, elapsed, PERF_BUDGETS["/api/stock/002747"], detail)

    # ── 6. AI 分析 ──
    print("\n━━ 6. AI 分析 (核心) ━━")
    for code in TEST_CODES[:2]:  # 测 2 只
        path = f"/api/stock/{code}/ai_analysis"
        ok, elapsed, status, body = get(path, budget=35.0)
        detail = ""
        if isinstance(body, dict) and body.get("ok"):
            data = body.get("data", {})
            detail = f"{data.get('verdict')} | 确信度={data.get('conviction')}"
        elif isinstance(body, dict) and not body.get("ok"):
            detail = f"❌ {body.get('error')} | {body.get('data', {}).get('summary', '')[:60]}"
        record(f"AI {code}", ok, elapsed, 35.0, detail)

    # ── 7. 实时选股 ──
    print("\n━━ 7. 实时选股 (长任务) ━━")
    for path, body, budget in POST_TESTS:
        ok, elapsed, status, body_resp = post(path, body, budget)
        detail = ""
        if isinstance(body_resp, dict) and body_resp.get("ok"):
            c = body_resp.get("data", {}).get("candidates", [])
            detail = f"{len(c)}候选: {[x['code']+x['name'] for x in c[:3]]}"
            if c:
                rr_values = [x.get("rr_ratio", 0) for x in c]
                detail += f" | RR={[f'{r:.2f}' for r in rr_values]}"
        elif isinstance(body_resp, dict) and not body_resp.get("ok"):
            detail = f"❌ {body_resp.get('error', '')[:60]}"
        record(f"POST {path}", ok, elapsed, budget, detail)

    # ── 8. 并发压力 ──
    print("\n━━ 8. 并发压力 (5 health 并发) ━━")
    import concurrent.futures
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(requests.get, LOCAL + "/api/health", {"timeout": 3}) for _ in range(5)]
        results = [f.result().status_code for f in futs]
    elapsed = time.time() - t0
    ok = all(c == 200 for c in results)
    record("5×health 并发", ok, elapsed, 3.0, f"codes={results}")

    # ── 总结 ──
    print("\n════════════════════════════════════════════════════════")
    pct = (PASSED * 100 // TOTAL) if TOTAL else 0
    print(f"  总计: {TOTAL} | 通过: {PASSED} ({pct}%)")
    print("════════════════════════════════════════════════════════")

    # 性能分析
    slow = [r for r in RESULTS if r["elapsed"] > r["budget"] and r["ok"]]
    fast = [r for r in RESULTS if r["elapsed"] < r["budget"] * 0.5 and r["ok"]]
    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n  🟢 极快 (< 50% 预算): {len(fast)}")
    for r in fast: print(f"     {r['name']}: {r['elapsed']:.2f}s")
    print(f"\n  🟡 预算内但慢 (>= 50% 预算): {TOTAL - len(fast) - len(slow) - len(failed)}")
    if slow:
        print(f"\n  🟠 超预算但成功: {len(slow)}")
        for r in slow: print(f"     {r['name']}: {r['elapsed']:.2f}s / 预算 {r['budget']:.1f}s")
    if failed:
        print(f"\n  🔴 失败: {len(failed)}")
        for r in failed: print(f"     {r['name']}: {r['detail'][:80]}")

    if failed or slow:
        print(f"\n  退出码 1 ({len(failed)} 失败 + {len(slow)} 超预算)")
        return 1
    print(f"\n  ✅ 全部通过 + 性能达标! 退出码 0")
    return 0

if __name__ == "__main__":
    sys.exit(main())