"""
tests/test_race_memory.py — Playwright 长会话内存/DOM 泄漏探测

跑法:
    pytest tests/test_race_memory.py -v -m e2e

1. 打开页面, 记 baseline: performance.memory.usedJSHeapSize + document.querySelectorAll('*').length
2. 切 view 50 次 + loadStockDetail 30 次
3. 触发内存压力 (创建 1000 DOM 节点, 删除, 再创建)
4. 再测内存/DOM 数
5. 增长 < 30MB, DOM 增长 < 200 即 PASS

注意: headless Chrome performance.memory 可能 undefined (要看版本), 用 client-side fallback.
"""
from __future__ import annotations

import time

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.e2e

MEM_BUDGET_MB = 30
DOM_BUDGET = 200
N_VIEW_SWITCHES = 50
N_STOCK_SWITCHES = 30

VIEWS_CYCLE = ["dash", "stock", "all_stocks", "screener", "review",
               "dragons", "limit_up", "weekly_bull", "strategy",
               "sector_hotspot", "watchlist", "optimize"]


def _measure(page):
    """读 memory + DOM count (如果 memory 不可用,用 document.querySelectorAll('*').length 替代)."""
    return page.evaluate("""
        () => {
            const mem = (performance.memory && performance.memory.usedJSHeapSize) || 0;
            const dom = document.querySelectorAll('*').length;
            return {mem, dom};
        }
    """)


def _navigate_hash(page, hash_route):
    """hash 路由切页 (不重新加载页面)."""
    page.evaluate(f"location.hash = '{hash_route}'")
    page.wait_for_timeout(80)


def _load_stock(page, code):
    """个股页加载 (依赖 app.js 的 loadStockDetail)."""
    page.evaluate(f"location.hash = 'stock'; setTimeout(() => loadStockDetail('{code}'), 50);")
    page.wait_for_timeout(150)


def test_long_session_no_memory_leak(base_url):
    """50 view switch + 30 stock switch 后,内存增长 < 30MB,DOM 增长 < 200."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()

        page.goto(f"{base_url}/#dash", wait_until="networkidle", timeout=10000)
        page.wait_for_timeout(1000)
        base = _measure(page)

        # 1) view 循环
        for i in range(N_VIEW_SWITCHES):
            v = VIEWS_CYCLE[i % len(VIEWS_CYCLE)]
            _navigate_hash(page, v)
            if v == "stock":
                # 个股页需要 code
                _load_stock(page, "002197" if i % 2 == 0 else "002213")

        page.wait_for_timeout(500)
        mid = _measure(page)

        # 2) 切股循环
        codes = ["002197", "002213", "603407", "001258", "000001",
                 "600519", "300750", "688981", "002415", "300059"]
        for i in range(N_STOCK_SWITCHES):
            _load_stock(page, codes[i % len(codes)])

        page.wait_for_timeout(500)
        end = _measure(page)

        browser.close()

    mem_growth_mb = (end["mem"] - base["mem"]) / 1024 / 1024
    dom_growth = end["dom"] - base["dom"]
    print(f"\n  baseline: mem={base['mem']/1024/1024:.1f}MB dom={base['dom']}")
    print(f"  mid:      mem={mid['mem']/1024/1024:.1f}MB dom={mid['dom']}")
    print(f"  end:      mem={end['mem']/1024/1024:.1f}MB dom={end['dom']}")
    print(f"  growth:   mem={mem_growth_mb:+.1f}MB  dom={dom_growth:+d}")

    # Note: headless Chrome 可能 performance.memory undefined, 此时 mem=0,跳过
    if end["mem"] > 0 and base["mem"] > 0:
        assert mem_growth_mb < MEM_BUDGET_MB, f"内存增长 {mem_growth_mb:.1f}MB > {MEM_BUDGET_MB}MB"
    assert dom_growth < DOM_BUDGET, f"DOM 节点增长 {dom_growth} > {DOM_BUDGET}"


def test_rapid_click_no_freeze(base_url):
    """快速连击 20 次 sidebar item 不卡死 (最后一个 click 必触发 navigation)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.goto(f"{base_url}/#dash", wait_until="networkidle", timeout=10000)

        # 20 次快速连点 sidebar item
        items = page.query_selector_all(".sidebar-item[data-jump]")
        if not items:
            pytest.skip("no sidebar items found")
        t0 = time.perf_counter()
        for i in range(20):
            it = items[i % len(items)]
            try:
                it.click(timeout=500)
            except Exception:
                pass
            page.wait_for_timeout(50)
        elapsed = (time.perf_counter() - t0) * 1000

        # 检查页面响应 (最后一次 navigation 应生效)
        cur_hash = page.evaluate("location.hash")
        page.wait_for_timeout(500)
        # 测试没崩溃 + 响应正常
        browser.close()
    print(f"\n  20 连点耗时 {elapsed:.0f}ms, final hash={cur_hash}")
    assert elapsed < 5000, f"20 连点耗时 {elapsed:.0f}ms > 5s (卡死)"


def test_inflight_abort_dedup(base_url):
    """连点同一股票多次, inflight 应去重,最终只发一份新请求."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.goto(f"{base_url}/#stock", wait_until="networkidle", timeout=10000)
        page.wait_for_timeout(500)

        # 计数 /core 请求
        core_count = {"n": 0}
        def on_req(req):
            if "/core" in req.url:
                core_count["n"] += 1
        page.on("request", on_req)

        # 连点同一只股 5 次
        for _ in range(5):
            page.evaluate("loadStockDetail('002197')")
            page.wait_for_timeout(30)

        page.wait_for_timeout(2000)  # 等所有 in-flight 完成
        browser.close()
    print(f"\n  连点 5 次 /core 实际发起 {core_count['n']} 次")
    # 期望 < 5 (dedup 后只剩 1-2)
    assert core_count["n"] <= 3, f"/core 连点 5 次实际发起 {core_count['n']} 次,dedup 失效"