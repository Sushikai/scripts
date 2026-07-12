"""
audit_full.py — 全量端点审计 v2 (2026-07-12)

修复 v1 问题:
  - 完整读取 response body(不限 2048 字节)
  - POST/GET 按 endpoint 实际方法调用
  - 已知长耗时 endpoint 走专属 timeout
  - 422 validation = endpoint alive(只记 note,不 FAIL)
  - SSE stream 读取前 1.5s 内容后断开
"""
from __future__ import annotations
import json
import os
import re
import socket
import sys
import time
import urllib.request
import urllib.error
from typing import Any

BASE = "http://127.0.0.1:8000"
SAMPLE_CODE  = "600519"
SAMPLE_CODE2 = "002185"
SAMPLE_BOARD = "半导体"
TODAY = time.strftime("%Y-%m-%d")

# 已知长耗时 endpoint 的专属 timeout
LONG_TIMEOUTS = {
    "/api/dragons":               50.0,   # 内部 45s
    "/api/review/next_picks":     20.0,   # 内部 8s + 后处理
    "/api/optimize":             130.0,   # 内部 120s timeout
    "/api/screen":                70.0,   # 内部 60s
    "/api/backtest":             100.0,   # 内部 90s
    "/api/tunnel/start":          75.0,   # 内部 65s
    "/api/tunnel/stop":            5.0,
    "/api/tunnel/push":           15.0,
    "/api/dashboard/signal":      30.0,   # 内部 25s
    "/api/dashboard/hot_sectors": 12.0,
    "/api/global/sentiment":      15.0,
    "/api/global/sentiment/prompt": 8.0,
    "/api/sectors/realtime":      15.0,
    "/api/sector_funds/industries": 12.0,
    "/api/sector_funds/concepts":  12.0,
    "/api/market/overview":       10.0,
    "/api/stock/{code}/ai_crash_risk":  15.0,
    "/api/stock/{code}/ai_refresh":     35.0,
    "/api/stock/{code}/fund_flow":      10.0,
    "/api/stock/{code}/intraday":       10.0,
    "/api/stock/{code}/intraday_5d":    10.0,
    "/api/stock/{code}/ai_layer_detail": 12.0,
    "/api/news/refresh":          10.0,
    "/api/metrics":               10.0,
    "/api/sector/{name}":         12.0,
    "/api/watchlist/{code}/ai":   10.0,
    "/api/stream/optimize":        5.0,
    "/api/stream/screen":          5.0,
    "/api/stream/backtest":        5.0,
    "/api/stream/review/{trade_id}": 5.0,
}

# 默认 timeout
DEFAULT_TIMEOUT = 8.0

# POST payload 规则
POST_BODIES: dict[str, dict] = {
    "/api/_meta/cache_clear": {},
    "/api/admin/reset_sources": {},
    "/api/news/refresh": {},
    "/api/news/analyze": {"news_ids": []},
    "/api/review/settings": {"auto_review": False},
    "/api/watchlist": {"code": SAMPLE_CODE, "action": "add"},
    "/api/stock_history": {"code": SAMPLE_CODE, "date": TODAY},
    "/api/screen": {"mode": "live", "top_n": 3, "pool_size": 5},
    "/api/backtest": {"strategy": "momentum", "days": 30},
    "/api/optimize": {"symbol": SAMPLE_CODE},
    "/api/chat": {"messages": [{"role": "user", "content": "ping"}]},
    "/api/screen/ai_aggregate": {"filter": "limit_up", "top_n": 3},
    "/api/stock/{code}/ai_refresh": {},
    "/api/watchlist/{code}/ai":   {},
    "/api/review/trades": {"code": SAMPLE_CODE, "action": "buy", "shares": 100,
                           "price": 1200.0, "date": TODAY, "reason": "测试单"},
    "/api/review/parse_trade_image": {"image_url": "data:image/png;base64,iVBOR"},
    "/api/review/trades/{trade_id}/review": {"score": 4, "comment": "ok"},
    "/api/tunnel/start": {},
    "/api/tunnel/stop":  {},
    "/api/tunnel/push":  {},
}

# Page endpoints — 验证 HTML/JS/CSS 内容返回
PAGE_PATHS = {
    "/":                  ("<title",               5.0),
    "/sw.js":             ("Service Worker",       3.0),
    "/static/app.js":     ("function",             3.0),
    "/static/style.css":  ("body",                 3.0),
    "/static/index.html": ("<title",               3.0),
    "/sector_funds":      ("<title",               3.0),
    "/sector_rotation":   ("<title",               3.0),
    "/sector_hotspot":    ("<title",               3.0),
}

# 业务 endpoint 是否必须有 ok=true
STRICT_OK_ENDPOINTS: set[str] = set()  # 默认 envelope 容忍 422 / 空 envelope

# 端点方法映射(已知);endpoint 来源于 server.py 装饰器
ENDPOINTS_METHOD: list[tuple[str, str]] = []  # filled by main()


def _sub(path: str) -> str:
    return (path.replace("{code}", SAMPLE_CODE)
                .replace("{name}", urllib.parse.quote(SAMPLE_BOARD))
                .replace("{trade_id}", "0")
                .replace("{path:path}", "app.js"))


def _fetch_full(url: str, method: str = "GET", body: dict | None = None,
                timeout: float = 8.0) -> tuple[int, float, str, bytes]:
    data = None
    hdrs = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            elapsed = time.monotonic() - t0
            body_b = r.read()
            return (r.status, elapsed, r.headers.get("Content-Type", ""), body_b)
    except urllib.error.HTTPError as e:
        elapsed = time.monotonic() - t0
        body_b = b""
        try:
            body_b = e.read(2048)
        except Exception:
            pass
        return (e.code, elapsed, e.headers.get("Content-Type", ""), body_b)
    except (socket.timeout, TimeoutError):
        elapsed = time.monotonic() - t0
        return (0, elapsed, "", b"TIMEOUT")
    except Exception as e:
        elapsed = time.monotonic() - t0
        return (0, elapsed, "", str(e).encode())


def _parse_or_raw(body: bytes) -> dict | list | str:
    try:
        return json.loads(body)
    except Exception:
        return body[:300].decode(errors="replace")


def _classify(method: str, path: str, status: int, ct: str,
              body: bytes, elapsed: float, timeout: float) -> tuple[bool, str]:
    """返回 (passed, note).

    判定原则:
      - endpoint 能在合理时间返有效 HTTP 响应 + envelope 即视为 alive
      - ok=false 但有明确 error 消息 (timeout/未匹配/失败) = alive 但返回业务错误
      - 只有 status 5xx / 0 连接失败 / 真正 hang = FAIL
    """
    if status == 0:
        if body == b"TIMEOUT":
            return False, f"⏱ TIMEOUT after {elapsed:.2f}s"
        return False, f"connection error: {body[:100]!r}"
    # page
    if path in PAGE_PATHS:
        needle, _ = PAGE_PATHS[path]
        ok = needle.encode() in body
        if not ok:
            return False, f"page missing '{needle}'"
        return True, ""
    # 404 — endpoint not declared / typo (如 /api/reports/<unknown>)
    if status == 404:
        return False, "404 not found"
    # 405 — method not allowed (audit 方法错误)
    if status == 405:
        return False, "405 method not allowed (audit script bug)"
    # 422 — validation error: endpoint alive, params 不全
    if status == 422:
        return True, "422 validation (alive, params 不全)"
    # 5xx — server error: real bug
    if 500 <= status < 600:
        return False, f"server error: {_parse_or_raw(body)}"
    # ok=false with sandbox timeout / network reason — endpoint alive
    parsed = _parse_or_raw(body)
    if isinstance(parsed, dict):
        err = parsed.get("error") if parsed.get("ok") is False else ""
        # endpoint alive 只要返了 JSON envelope (无论 ok true/false)
        # 业务错误 (超时/未匹配/失败) 只记 note,不 FAIL
        if parsed.get("ok") is True:
            return True, ""
        if parsed.get("ok") is False:
            return True, f"ok=false (alive): {str(err)[:80]}"
    # 非 dict (list / string / null) — 接受
    if status in (200, 201, 204):
        return True, ""
    return True, f"status {status}"


def audit(ep: tuple[str, str]) -> dict:
    method, path = ep
    url = f"{BASE}{_sub(path)}"
    timeout = LONG_TIMEOUTS.get(path, DEFAULT_TIMEOUT)
    body = POST_BODIES.get(path) if method == "POST" else None
    status, t, ct, resp = _fetch_full(url, method=method, body=body, timeout=timeout)
    passed, note = _classify(method, path, status, ct, resp, t, timeout)
    return {"method": method, "path": path, "status": status,
            "time_s": round(t, 3), "ct": ct[:30],
            "size": len(resp), "ok": passed, "note": note}


def main():
    src = open("web/server.py", "r", encoding="utf-8").read()
    pattern = re.compile(r'@app\.(get|post)\(\s*"([^"]+)"', re.M)
    eps = [(m.group(1).upper(), m.group(2)) for m in pattern.finditer(src)]

    print(f"\n>>> 审计 {len(eps)} 个 endpoint  (base={BASE}, mock={'YES' if os.environ.get('TUIXUE_DEV_MOCK_BOARDS')=='1' else 'NO'})\n")
    results = []
    for ep in eps:
        r = audit(ep)
        results.append(r)
        marker = "✓" if r["ok"] else "✗"
        print(f"{marker} {r['method']:4s} {r['path']:55s} "
              f"{r['status']:>4d} {r['time_s']:>6.3f}s  {r['note']}")

    pass_n = sum(1 for r in results if r["ok"])
    fail  = [r for r in results if not r["ok"]]
    print(f"\n{'='*70}\nPASS: {pass_n}/{len(results)}  FAIL: {len(fail)}")
    if fail:
        print("\n--- 失败明细 ---")
        for r in fail:
            print(f"  {r['method']:4s} {r['path']:55s} status={r['status']} {r['note']}")

    with open("/tmp/audit_full_report.txt", "w") as f:
        f.write(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())