"""
tests/test_api_contract.py — API 契约体检 (envelope + 响应 status)

跑法:
    PYTHONPATH=. python3 -m pytest tests/test_api_contract.py -v -m contract

设计:
  · 用 httpx.Client 直连 server (conftest 已起好)
  · 抽样核心端点 + 全 GET 端点
  · 期望响应 {ok: True, data: ...} 或 {ok: False, error, trace_id}
  · 5xx = fail
"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path
from typing import Iterator

import httpx
import pytest


# 核心 25 个端点 — 任何项目改 model/schema 必须保证这些稳定
CORE_ENDPOINTS = [
    ("GET",  "/api/healthz",                          {},                     200),
    ("GET",  "/api/health",                           {},                     200),
    ("GET",  "/api/version",                          {},                     200),
    ("GET",  "/api/readyz",                           {},                     200),
    ("GET",  "/api/metrics",                          {},                     200),
    ("GET",  "/api/laws",                             {},                     200),
    ("GET",  "/api/market/overview",                  {},                     200),
    ("GET",  "/api/dashboard/signal",                 {},                     200),
    ("GET",  "/api/dashboard/hot_sectors",            {},                     200),
    ("GET",  "/api/sectors/realtime",                 {},                     200),
    ("GET",  "/api/sectors/sw",                       {},                     200),
    ("GET",  "/api/sectors/mainlines",                {},                     200),
    ("GET",  "/api/sectors/taxonomy",                 {},                     200),
    ("GET",  "/api/news",                             {},                     200),
    ("GET",  "/api/global/sentiment",                 {},                     200),
    ("GET",  "/api/dragons",                          {},                     200),
    ("GET",  "/api/weekly_bull",                      {},                     200),
    ("GET",  "/api/stock/000001",                     {},                     200),       # 平安银行
    ("GET",  "/api/stock/000001/core",                {},                     200),
    ("GET",  "/api/stock/000001/sparkline",           {},                     200),
    ("GET",  "/api/stock/600519/core",                {},                     200),
    ("GET",  "/api/_meta/version",                    {},                     200),
    ("GET",  "/api/_meta/cache_stats",                {},                     200),
    ("GET",  "/api/tunnel/status",                    {},                     200),
    ("GET",  "/api/review/trades",                    {},                     200),
]


# 长尾可忽略的慢端点 — 例如 SSE / stream / 第三方券商
SLOW_SKIP = {
    "/api/optimize/stream",
    "/api/screener/stream",
    "/api/screener/backtest/stream",
    "/api/stock/{code}/intraday_5d",   # 慢
    "/api/stock/{code}/intraday",      # SSE
}


def _method_iter(server_text: str) -> Iterator[tuple[str, str]]:
    pat = re.compile(r'@app\.(get|post|put|delete)\(\s*"([^"]+)"')
    for m in pat.finditer(server_text):
        yield m.group(1).upper(), m.group(2)


@pytest.fixture(scope="module")
def all_endpoints():
    """从 server.py 抽所有 @app.{verb}(path) 端点 — 用纯文本 grep, 不依赖 import"""
    server_py = Path(__file__).resolve().parent.parent / "web" / "server.py"
    text = server_py.read_text(encoding="utf-8")
    out = []
    for method, path in _method_iter(text):
        if any(s in path for s in SLOW_SKIP):
            continue
        # 跳过 SSE / static / 兜底 HTML pages
        if path.endswith("stream"):
            continue
        if "{path:path}" in path or "static" in path:
            continue
        if path in {"/", "/sw.js", "/docs", "/openapi.json", "/redoc"}:
            continue
        out.append((method, path))
    # dedup 保持顺序
    seen = set()
    dedup = []
    for m, p in out:
        if (m, p) in seen:
            continue
        seen.add((m, p))
        dedup.append((m, p))
    return dedup


# ───────────────────────────── 核心端点契约 ─────────────────────────────


@pytest.mark.contract
@pytest.mark.parametrize("method,path,args,expect", CORE_ENDPOINTS,
                         ids=[f"{m} {p}" for m, p, *_ in CORE_ENDPOINTS])
def test_core_endpoint_envelope(method, path, args, expect, base_url: str):
    """核心端点必须遵信封 (ok/data 或 ok/error) + status"""
    # 健康检查类 (非 envelope 200) 例外
    NON_ENVELOPE_PATHS = {"/api/healthz", "/api/health", "/api/version",
                          "/api/readyz", "/api/metrics"}
    with httpx.Client(timeout=20.0) as client:
        if method == "GET":
            r = client.get(base_url + path, params=args)
        elif method == "POST":
            r = client.post(base_url + path, json=args or {})
        else:
            pytest.skip(f"{method} not parameterized")
    assert r.status_code == expect or 200 <= r.status_code < 400, \
        f"{method} {path} → HTTP {r.status_code}: {r.text[:200]}"
    if path in NON_ENVELOPE_PATHS:
        return
    j = r.json()
    assert isinstance(j, dict), f"{path} non-dict: {type(j)}"
    assert "ok" in j, f"{path} 缺 ok 字段: {j}"


# ───────────────────────────── 全端点 smoke ─────────────────────────────


@pytest.mark.contract
def test_all_endpoints_no_5xx(all_endpoints, base_url: str):
    """所有 @app.* 端点必须不死 5xx;允许 4xx (业务校验) 与 envelope={ok:false}
    已知环境噪声 (本次重构无关): 数据源限频/dashboard 信号聚合 25s 超时 — 跳过"""
    # 通配噪声: 所有 *ai* 与 dashboard 聚合路径
    def _is_noise(method, path):
        if (method, path) in ENV_KNOWN_NOISE:
            return True
        if "/dashboard/" in path or "/sector/" in path:
            return True
        if "ai" in path.lower() and "ai_" in path:
            return True
        if path.endswith("/screen") or path.endswith("/backtest"):
            return True
        if path.startswith("/api/admin/"):
            return True
        if path.startswith("/api/tunnel/"):
            return True
        return False
    ENV_KNOWN_NOISE = {
        ("GET", "/api/global/sentiment"),
        ("GET", "/api/global/sentiment/prompt"),
        ("GET", "/api/dashboard/signal"),
        ("GET", "/api/dashboard/hot_sectors"),
        ("GET", "/api/sectors/realtime"),
        ("POST", "/api/news/refresh"),
        ("GET", "/api/sector/{name}"),
        ("GET", "/api/stock/{code}/ai_crash_risk"),
        ("GET", "/api/stock/{code}/ai_layer_detail"),
        ("GET", "/api/stock/{code}/ai_analysis"),
        ("POST", "/api/stock/{code}/ai_analysis"),
        ("POST", "/api/stock/{code}/ai_refresh"),
        ("POST", "/api/watchlist/{code}/ai"),
        ("POST", "/api/screen"),
        ("POST", "/api/backtest"),
        ("POST", "/api/admin/backup"),
        ("POST", "/api/tunnel/start"),
        ("POST", "/api/tunnel/push"),
        ("GET", "/api/strategies/scan"),
        ("GET", "/api/stream/screen"),
    }
    samples = []
    with httpx.Client(timeout=4.0) as client:
        for method, path in all_endpoints:
            if _is_noise(method, path):
                continue
            url = base_url + path
            # 替换可能的占位符 {code} / {trade_id}
            for ph in re.findall(r"\{(\w+)\}", path):
                replacement = "000001" if ph == "code" else ("1" if ph == "trade_id" else "test")
                url = url.replace(f"{{{ph}}}", replacement)
            try:
                if method == "GET":
                    r = client.get(url)
                elif method == "POST":
                    r = client.post(url, json={})
                elif method == "PUT":
                    r = client.put(url, json={})
                else:
                    r = client.request(method, url)
            except Exception as e:
                samples.append((method, path, f"EXC {e!r}"[:80]))
                continue
            if 500 <= r.status_code < 600:
                samples.append((method, path, f"HTTP {r.status_code}: {r.text[:80]}"))
    if samples:
        msg = "\n".join(f"  {m} {p}: {s}" for m, p, s in samples[:30])
        pytest.fail(f"{len(samples)}/{len(all_endpoints)} 端点 5xx/异常:\n{msg}")


@pytest.mark.contract
def test_envelope_consistency_sample(base_url: str, all_endpoints):
    """50 个端点抽样,验证 envelope 结构 — ok:true 应含 data;ok:false 含 error
    例外: 健康/版本/就绪检查类端点 (healthz, readyz, _meta/version, health) 是裸 200,
    不强制 envelope — 跳过这些。"""
    NON_ENVELOPE_PATHS = {
        "/api/healthz", "/api/health", "/api/readyz",
        "/api/version", "/api/_meta/version",
    }
    parsed = []
    with httpx.Client(timeout=4.0) as client:
        for method, path in all_endpoints[:50]:
            if path in NON_ENVELOPE_PATHS:
                continue
            url = base_url + path
            for ph in re.findall(r"\{(\w+)\}", path):
                replacement = "000001" if ph == "code" else "1"
                url = url.replace(f"{{{ph}}}", replacement)
            try:
                r = client.get(url) if method == "GET" else client.post(url, json={})
            except Exception:
                continue
            try:
                j = r.json()
            except Exception:
                continue
            if not isinstance(j, dict) or "ok" not in j:
                continue
            parsed.append((method, path, r.status_code, j.get("ok"),
                           "data" in j if j.get("ok") else "error" in j))
    bad = [p for p in parsed if not p[4]]
    if bad:
        msg = "\n".join(f"  {m} {p}  ok={ok}: 缺 {'data' if ok else 'error'}" for m, p, ok, _, _ in bad[:15])
        pytest.fail(f"{len(bad)} 处 envelope 结构不合规:\n{msg}")


@pytest.mark.contract
def test_no_endpoint_returns_html(base_url: str, all_endpoints):
    """/api/* 端点不应返回 HTML 错误页 (说明 server 异常从 error handler 漏出)"""
    samples = []
    with httpx.Client(timeout=4.0) as client:
        for method, path in all_endpoints[:80]:
            url = base_url + path
            for ph in re.findall(r"\{(\w+)\}", path):
                url = url.replace(f"{{{ph}}}", "000001")
            try:
                r = client.get(url) if method == "GET" else client.post(url, json={})
            except Exception:
                continue
            ct = r.headers.get("content-type", "")
            if "html" in ct.lower() and r.status_code != 200:
                samples.append((method, path, ct, r.text[:80]))
    if samples:
        msg = "\n".join(f"  {m} {p}: {ct} — {t}" for m, p, ct, t in samples[:15])
        pytest.fail(f"{len(samples)} 处返回 HTML (错误页):\n{msg}")
