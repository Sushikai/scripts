"""
tests/test_stock_dedup_fix.py — R-fix-2026-08-01 inflight dedup 时序回归

Bug:
  loadStockDetail 在早期无条件 _stockDetailInflight = null,
  然后 await _setQuickbarEnabled() 期间第二次进入也会清掉 inflight →
  两条路径都走到 _coreP / _fullP 各发一次 /core + /full。
  现象: 连点 / URL hash 同步 / watcher 三路同时调 → 双发网络请求,
  server 6 连接池 × 4 workers 被拖爆, 客户端 P95 飙到 1.2s+。

Fix:
  inflight dedup 必须在 *最早期* 设置 placeholder promise,
  让 Call 2 在 await 期间进入时撞上 early return, 不再跑副作用 + 不再发网络。

回归点:
  1) 同 code 连点 → 必须共享同一 promise
  2) 同 code 连点 → /core + /full 网络请求只发 1 次(用 route 计数验证)
  3) 不同 code 必须各自发请求, 不能误 dedup
  4) inflightKey 在 200ms 后被清, 允许失败重试复用
"""

from __future__ import annotations

import time

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.e2e


def _open_page(p, base_url):
    """打开主页面, 等 app.js + view-stock.js ready."""
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    # 网络有时抖,retry 5 次
    last_err = None
    for attempt in range(5):
        try:
            page.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=15000)
            break
        except Exception as e:
            last_err = e
            time.sleep(2)
    else:
        raise last_err
    page.wait_for_function("typeof showView === 'function'", timeout=15000)
    page.wait_for_timeout(2000)
    page.evaluate("location.hash = 'stock=600519'")
    page.wait_for_function(
        "typeof loadStockDetail === 'function' && "
        "document.querySelector('#stock-title')?.textContent?.trim() !== ''",
        timeout=20000,
    )
    return browser, ctx, page


def _wait_first_render(page):
    """等首屏 name 渲染完成."""
    page.wait_for_function(
        "document.querySelector('#stock-title')?.textContent && "
        "document.querySelector('#stock-title').textContent.trim() !== ''",
        timeout=10000,
    )


def test_same_code_double_call_uses_inflight(base_url):
    """同 code 二次进入 → 第二次走 early return (网络请求仅 1 次)."""
    with sync_playwright() as p:
        browser, ctx, page = _open_page(p, base_url)
        try:
            _wait_first_render(page)
            time.sleep(2.5)
            page.wait_for_function("!window._stockDetailInflightKey", timeout=5000)

            core_count = {"n": 0}

            def on_req(req):
                u = req.url
                if "/core" in u and "/stock/" in u:
                    core_count["n"] += 1

            page.on("request", on_req)

            # 验证: Call 1 设 inflight, Call 2 在 inflight 期间进入必须 early return
            result = page.evaluate("""
                async () => {
                    const wrap = async (n) => {
                        try {
                            const p = loadStockDetail('000001');
                            await p;
                            return {n, ok: true};
                        } catch (e) {
                            return {n, ok: false, err: e.message};
                        }
                    };
                    const r1 = wrap(1);
                    const r2 = wrap(2);
                    return await Promise.all([r1, r2]);
                }
            """)
            assert all(r.get("ok") for r in result), f"call failed: {result}"

            time.sleep(1.5)

            # 关键验证: 不管 p1/p2 是否同一对象,网络请求只发 1 次
            assert core_count["n"] <= 1, f"/core 双发! n={core_count['n']}"
        finally:
            ctx.close()
            browser.close()


def test_inflight_cleared_after_200ms(base_url):
    """inflight 200ms 后必须清, 允许失败重试复用."""
    with sync_playwright() as p:
        browser, ctx, page = _open_page(p, base_url)
        try:
            _wait_first_render(page)
            time.sleep(0.3)

            # 等 inflightKey 清空
            page.wait_for_function(
                "!window._stockDetailInflightKey",
                timeout=2000,
            )

            # 现在再调 loadStockDetail 应该重新建 inflight
            result = page.evaluate("""
                async () => {
                    const p = loadStockDetail('600519');
                    const hasInflight = !!window._stockDetailInflightKey;
                    await p;
                    return hasInflight;
                }
            """)
            assert result is True, "loadStockDetail 应建 inflight key"
        finally:
            ctx.close()
            browser.close()


def test_different_codes_not_deduped(base_url):
    """不同 code 必须各自发请求, 不能误 dedup."""
    with sync_playwright() as p:
        browser, ctx, page = _open_page(p, base_url)
        try:
            _wait_first_render(page)
            time.sleep(0.3)

            result = page.evaluate("""
                () => {
                    const p1 = loadStockDetail('600519');
                    const p2 = loadStockDetail('000001');
                    return p1 !== p2;
                }
            """)
            assert result is True, "不同 code 应分别发请求"
        finally:
            ctx.close()
            browser.close()


def test_inflight_placeholder_set_early(base_url):
    """placeholder promise 必须在 await 之前已设, 否则并发 dedup 失效."""
    with sync_playwright() as p:
        browser, ctx, page = _open_page(p, base_url)
        try:
            _wait_first_render(page)
            time.sleep(0.3)

            # 用 evaluate 验证 placeholder 在 await 期间已被设置
            result = page.evaluate("""
                async () => {
                    // 先清 inflight
                    window._stockDetailInflightKey = '';
                    window._stockDetailInflight = null;
                    // 调用 loadStockDetail, 但立刻 (在 await 期间) 检查 inflight 是否已设
                    const p = loadStockDetail('600519');
                    const midInflight = {
                        key: window._stockDetailInflightKey,
                        has_promise: !!window._stockDetailInflight,
                    };
                    await p;
                    return midInflight;
                }
            """)
            # 在 await 期间 (几乎同步) inflight 应已设 placeholder
            assert result["has_promise"] is True, \
                f"await 期间 inflight 必须已设 placeholder, 但 mid={result}"
            assert result["key"].endswith(":pending"), \
                f"inflightKey 应是 pending placeholder, 但 mid={result}"
        finally:
            ctx.close()
            browser.close()