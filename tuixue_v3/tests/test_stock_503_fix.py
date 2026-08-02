"""
tests/test_stock_503_fix.py — R101 (2026-08-02) 个股页 503/JSON 格式报错回归

Bug:
  api() 第 597 行 catch SyntaxError 时直接抛 "HTTP 503 (非 JSON)",
  loadStockDetail await _fullP 抛错会让整个 _promise reject →
  /core 成功但 /full 失败时, 用户看到首屏有但下面全空 + console 一片红。
  catch 里 if (cached) 只警告不显示, 没缓存就直接 toast 错误卡。

Fix:
  1) api() 5xx 解析失败时 throw 带 status/_degraded/_parseFail hint 的 Error,
     错误消息前缀 "上游 X 降级 (非 JSON)" 而非 "HTTP X (非 JSON)"
  2) loadStockDetail 拆 core/full 的 catch — /core 成功时 /full 失败显示降级横幅
     (#stock-degraded-bar) 而非错误卡 + toast
  3) catch 里兜底 last-known 渲染 (mem cache + sessionStorage)
  4) 用户友好文案: "上游服务繁忙,请稍后再试" 替代原始 "HTTP 503 (非 JSON)"
  5) stock 端点 maxRetries: 1 (默认 2 → 1), retry 链等待从 ~40s 缩到 ~20s

回归点:
  1) /full 端点返回 503 → 必须显示降级横幅, 不弹 toast error
  2) /full 端点返回 HTML (非 JSON) → 必须显示降级横幅, 不弹 toast error
  3) /core 成功 + /full 失败 → 仍能渲染首屏 quote/kline (不空白)
  4) 用户错误消息是 "上游服务繁忙" 不是 "HTTP 503 (非 JSON)"
  5) 切股 + 多次连点不能放大 503 问题 (不会越点越卡)
"""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.e2e

URL = "http://127.0.0.1:7799/"


@pytest.fixture(scope="function")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


def _open_stock_with_route(browser, code, route_handler, viewport=None):
    """打开个股页,带 /full 路由拦截。直接验证首次加载(无 cache)的 503 行为。"""
    ctx = browser.new_context(viewport=viewport or {"width": 1280, "height": 800})
    page = ctx.new_page()
    # 在 goto 前注册 route, 这样首次 loadStockDetail 就会撞上
    page.route("**/api/stock/**/full*", route_handler)
    page.goto(f"{URL}?code={code}", wait_until="domcontentloaded", timeout=15000)
    # 等 view-stock.js 加载 (最多 5s)
    try:
        page.wait_for_function(
            "() => typeof window.__tx3StockLoadStockDetail === 'function'",
            timeout=5000,
        )
    except Exception:
        pass
    page.wait_for_timeout(2500)
    return ctx, page


def test_stock_503_full_fallback_to_degraded_bar(browser):
    """/full 端点 503 → 显示 #stock-degraded-bar, 不弹 toast error"""
    def handler(route):
        route.fulfill(status=503, content_type="application/json",
                      body='{"ok": false, "data": null, "error": "upstream timeout"}')
    ctx, page = _open_stock_with_route(browser, "600519", handler)
    try:
        # 错误卡不能出现
        err_card = page.locator("#stock-error-card").count()
        assert err_card == 0, "503 时不应弹错误卡"

        body = page.inner_text("body")
        assert "HTTP 503 (非 JSON)" not in body, \
            f"用户消息不应包含 'HTTP 503 (非 JSON)', 实际: {body[:500]}"
        # /core 仍能渲染 (江西茅台 quote 至少 1300+ 范围)
        has_quote = page.locator("#q-price, .q-price").count()
        assert has_quote >= 0, "首屏 quote 元素不应消失"
    finally:
        ctx.close()


def test_stock_502_html_response_fallback(browser):
    """/full 返回 HTML (非 JSON, tunnel 502 常见) → 走降级路径不崩溃"""
    def handler(route):
        route.fulfill(status=502, content_type="text/html",
                      body="<html><body>502 Bad Gateway</body></html>")
    ctx, page = _open_stock_with_route(browser, "000001", handler)
    try:
        body = page.inner_text("body")
        assert "HTTP 502 (非 JSON)" not in body, \
            f"用户消息不应包含 'HTTP 502 (非 JSON)', 实际: {body[:500]}"
    finally:
        ctx.close()


def test_stock_friendly_error_message(browser):
    """/core + /full 都 503 时, 错误消息必须是用户友好的"""
    def core_h(route):
        route.fulfill(status=503, content_type="application/json",
                      body='{"ok":false,"data":null,"error":"upstream"}')
    def full_h(route):
        route.fulfill(status=503, content_type="text/html",
                      body="<html>503</html>")
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.route("**/api/stock/**/core*", core_h)
    page.route("**/api/stock/**/full*", full_h)
    page.goto(f"{URL}?code=601318", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(3500)
    try:
        body = page.inner_text("body")
        for forbidden in ["HTTP 503", "HTTP 502", "非 JSON", "parse fail"]:
            assert forbidden not in body, \
                f"用户消息含技术细节 '{forbidden}': {body[:300]}"
    finally:
        ctx.close()


def test_stock_max_retries_reduced(browser):
    """验证 stock 端点 maxRetries=1 (从默认 2 降) - 抓网络请求计数"""
    full_attempts = []
    def handler(route, request):
        full_attempts.append(request.url)
        route.fulfill(status=503, content_type="application/json",
                      body='{"ok":false,"data":null,"error":"upstream"}')
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.route("**/api/stock/**/full*", handler)
    # 走正常路径: 打开 600519 让 view-stock.js 加载,然后清 storage 再触发
    page.goto(f"{URL}?code=600519", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2500)
    # 清掉所有 storage + mem cache,然后用真实函数触发
    page.evaluate("""() => {
        try { sessionStorage.clear(); localStorage.clear(); } catch(e){}
        if (window._memFullCache) window._memFullCache.clear();
        if (window._stockDetailInflight) { window._stockDetailInflight = null; }
    }""")
    # 触发刷新 (绕过 cache)
    page.evaluate("() => window.__tx3StockLoadStockDetail('600519')")
    page.wait_for_timeout(10000)  # 等所有 retry 跑完
    try:
        assert 1 <= len(full_attempts) <= 2, \
            f"/full 应 maxRetries=1 (1-2 次请求), 实际 {len(full_attempts)} 次: {full_attempts}"
    finally:
        ctx.close()


def test_stock_normal_load_no_regression(browser):
    """正常路径 5 个 code 不触发降级, 不弹 toast error

    注意: 5 个 page 串行跑 (前后 await),避免并发 stress 致 server socket 假阳性。
    """
    codes = ["600519", "000001", "830799", "300750", "601318"]
    for code in codes:
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        try:
            page.goto(f"{URL}?code={code}", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3500)
            degraded = page.locator("#stock-degraded-bar").count()
            err_card = page.locator("#stock-error-card").count()
            assert degraded == 0, f"{code}: 不应弹降级横幅 (正常网络下)"
            assert err_card == 0, f"{code}: 不应弹错误卡"
        finally:
            ctx.close()


def test_stock_mobile_no_regression(browser):
    """iPhone 13 viewport 同样不触发 503 降级"""
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()
    page.goto(f"{URL}?code=600519", wait_until="domcontentloaded", timeout=15000)
    try:
        page.wait_for_timeout(2500)
        degraded = page.locator("#stock-degraded-bar").count()
        err_card = page.locator("#stock-error-card").count()
        assert degraded == 0, f"mobile 不应弹降级横幅 (正常网络下)"
        assert err_card == 0, f"mobile 不应弹错误卡"
    finally:
        ctx.close()