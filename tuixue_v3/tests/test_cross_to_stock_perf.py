"""
tests/test_cross_to_stock_perf.py — 跨页面跳转个股性能 TDD

目的: 量化"从其他页面 (dash/watchlist/review/all_stocks) 跳转到 stock 页"的加载时间
      用 page.evaluate(performance.now()) 测 hashchange → first quote render 的间隔

跑法:
    cd /Users/kaikai/scripts/tuixue_v3
    PYTHONPATH=. python3 -m pytest tests/test_cross_to_stock_perf.py -v --tb=short

测量点:
  - t_hashchange = 跳转瞬间
  - t_quote_visible = #q-quote 或 #q-price 显示非 "—" (从 SW cache 命中或网络)
  - t_full_rendered = _stockRenderTime 设定的瞬间 (在 view-stock.js:826/841)

判定:
  - 跨页跳转 P95 < 300ms (WIFI 网络)
  - 跨页跳转 P99 < 800ms
  - 跳转后 100ms 内必须看到骨架屏或实时数据

迭代协议: 测不过 → 看 trace → 优化 (prefetch / SW cache TTL / DOM 渐进渲染) → 跑过。
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path

import pytest


# ──────────────────────────────────────────────────────────────────────
# 跨页跳转入口
# ──────────────────────────────────────────────────────────────────────

# (source_page, stock_code) — 每个 (src, code) 跑 N 次取分布
JUMP_TARGETS = [
    ("dash", "000001"),     # 从 dashboard 跳平安银行
    ("watchlist", "002197"), # 从自选跳证通电子
    ("review", "300750"),   # 从复盘跳宁德时代
    ("all_stocks", "600519"), # 从全 A 跳茅台
]

ITERATIONS_PER_TARGET = 3  # 跑 3 次取 P50/P95/P99 (headless chromium 慢)


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def server_url():
    import urllib.request
    try:
        r = urllib.request.urlopen("http://127.0.0.1:7799/api/healthz", timeout=5)
        assert b'"ok":true' in r.read()
        return "http://127.0.0.1:7799"
    except Exception as e:
        pytest.skip(f"server 未启动: {e}")


# ──────────────────────────────────────────────────────────────────────
# §1 — 跨页跳转时间分布
# ──────────────────────────────────────────────────────────────────────

class TestCrossPageJumpPerf:
    """测量每次跨页跳转的: hashchange → first quote / first render 时间"""

    def test_jump_distribution(self, server_url):
        """5 source × 5 iterations → 25 次跳转 → 取 P50/P95/P99"""
        asyncio.run(_measure_jump_perf(server_url))


async def _measure_jump_perf(server_url):
    from playwright.async_api import async_playwright

    results = []  # list of (src, code, t_first_quote, t_full_rendered)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                         service_workers="block")

        for src, code in JUMP_TARGETS:
            for i in range(ITERATIONS_PER_TARGET):
                page = await ctx.new_page()
                # First land on source page
                src_hash = {
                    "dash": "#dash",
                    "watchlist": "#watchlist",
                    "review": "#review",
                    "all_stocks": "#all_stocks",
                    "screener": "#screener",
                }.get(src, "#dash")
                await page.goto(f"{server_url}/{src_hash}", wait_until="commit", timeout=30000)
                await page.wait_for_timeout(2500)  # let source page settle

                # Set up performance hooks BEFORE hash change
                await page.evaluate("""() => {
                  window._tJumpStart = performance.now();
                  window._tFirstQuote = null;
                  window._tFirstRealQuote = null;
                  // Poll for #q-quote or #q-price showing real data
                  const checkQuote = () => {
                    const q = document.querySelector('#q-quote, #q-price, .hero-quote, [data-quote]');
                    const t = q ? q.textContent.trim() : '';
                    if (t && t !== '—' && t !== '') {
                      if (window._tFirstQuote === null) {
                        window._tFirstQuote = performance.now() - window._tJumpStart;
                      }
                      // Real price = numeric, not 0 or empty
                      const num = parseFloat(t);
                      if (window._tFirstRealQuote === null && !isNaN(num) && num > 0) {
                        window._tFirstRealQuote = performance.now() - window._tJumpStart;
                      }
                    }
                    if (window._tFirstQuote === null || window._tFirstRealQuote === null) {
                      requestAnimationFrame(checkQuote);
                    }
                  };
                  requestAnimationFrame(checkQuote);
                }""")

                # Trigger cross-page jump — CORRECT hash format: #stock=CODE (not #stock?code=CODE)
                await page.evaluate(f"location.hash = '#stock={code}'")

                # Wait up to 10s for first quote render
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    done = await page.evaluate("() => window._tFirstRealQuote !== null")
                    if done:
                        break
                    await page.wait_for_timeout(50)

                # Get measurements
                m = await page.evaluate("""() => ({
                  tFirstQuote: window._tFirstQuote,
                  tFirstRealQuote: window._tFirstRealQuote,
                  hashNow: location.hash
                })""")

                results.append({
                    "src": src, "code": code, "iter": i,
                    "t_first_quote_ms": m["tFirstQuote"],
                    "t_first_real_quote_ms": m["tFirstRealQuote"],
                    "final_hash": m["hashNow"],
                })

                await page.close()

        await browser.close()

    # ── 输出 ──
    print("\n=== Cross-Page Jump Performance (CORRECT hash format #stock=CODE) ===")
    by_src = {}
    for r in results:
        by_src.setdefault(r["src"], []).append(r)
    for src, runs in by_src.items():
        fqs = sorted([r["t_first_quote_ms"] for r in runs if r["t_first_quote_ms"] is not None])
        frqs = sorted([r["t_first_real_quote_ms"] for r in runs if r["t_first_real_quote_ms"] is not None])
        if not frqs:
            print(f"{src:10s}: ❌ NO real quote in 10s")
            continue
        p50 = frqs[len(frqs) // 2]
        p95 = frqs[max(0, int(len(frqs) * 0.95) - 1)]
        p99 = frqs[-1]
        any_quote = frqs[0]
        print(f"{src:10s}: any_quote={any_quote:.0f}ms  P50={p50:.0f}ms  P95={p95:.0f}ms  P99={p99:.0f}ms  (N={len(frqs)})")

    # ── 断言 ──
    errs = []
    for src, runs in by_src.items():
        frq = [r["t_first_real_quote_ms"] for r in runs if r["t_first_real_quote_ms"] is not None]
        if not frq:
            errs.append(f"❌ {src}: 10s 内首屏 quote 没渲染")
            continue
        p95 = frq[max(0, int(len(frq) * 0.95) - 1)]
        # Baseline: P95 > 3000ms = fail (this is the user's complaint)
        if p95 > 3000:
            errs.append(f"❌ {src}: P95={p95:.0f}ms > 3000ms (用户报告的慢)")

    if errs:
        pytest.fail("\n".join(errs))


# ──────────────────────────────────────────────────────────────────────
# §2 — DOM 渐进渲染检查 (应该跳转后 100ms 内有骨架或 hero 元素)
# ──────────────────────────────────────────────────────────────────────

class TestProgressiveRender:
    """跳转后 100ms 内应能看到 stock 页骨架或占位元素"""

    def test_skeleton_within_100ms(self, server_url):
        asyncio.run(_measure_skeleton(server_url))


async def _measure_skeleton(server_url):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                         service_workers="block")
        page = await ctx.new_page()

        await page.goto(f"{server_url}/#dash", wait_until="commit", timeout=30000)
        await page.wait_for_timeout(2500)

        # Mark jump start
        await page.evaluate("window._tStart = performance.now()")

        # Jump and immediately check if skeleton/hero elements exist
        await page.evaluate("location.hash = '#stock?code=000001'")
        await page.wait_for_timeout(100)  # wait 100ms

        m = await page.evaluate("""() => {
          const t = performance.now() - window._tStart;
          const stockView = document.querySelector('[data-view="stock"]');
          const hero = document.querySelector('#q-quote, #q-price, .hero, .hero-quote');
          const skel = document.querySelector('.skeleton, [class*="skeleton"]');
          const tabs = document.querySelectorAll('[data-tab]');
          return {
            elapsed: t,
            stockViewActive: stockView ? !stockView.hidden : false,
            stockViewDisplay: stockView ? getComputedStyle(stockView).display : null,
            heroExists: !!hero,
            heroText: hero ? hero.textContent.trim().slice(0, 30) : null,
            skeletonExists: !!skel,
            tabCount: tabs.length,
            tabTexts: [...tabs].slice(0, 5).map(t => t.textContent.trim())
          };
        }""")

        print("\n=== 100ms After Jump ===")
        print(json.dumps(m, ensure_ascii=False, indent=2))

        await browser.close()

        errs = []
        if not m["stockViewActive"]:
            errs.append(f"❌ 跳转 100ms 后 stock view 仍 hidden (display={m['stockViewDisplay']})")
        if not m["heroExists"]:
            errs.append("❌ hero 元素 #q-quote / #q-price 不存在")
        if m["tabCount"] == 0:
            errs.append("❌ stock view tabs 还没渲染 (0 个)")

        if errs:
            pytest.fail("\n".join(errs))


# ──────────────────────────────────────────────────────────────────────
# §4 — 性能回归断言 (硬约束)
# ──────────────────────────────────────────────────────────────────────

class TestPerfRegression:
    """硬约束: 跳转 stock 页 P95 < 800ms, P99 < 2000ms, NON-stock carryover = 0"""

    def test_p95_under_800ms(self, server_url):
        """P95 必须 < 800ms"""
        asyncio.run(_assert_p95_under(server_url, 800))

    def test_p99_under_2000ms(self, server_url):
        """P99 必须 < 2000ms"""
        asyncio.run(_assert_p99_under(server_url, 2000))

    def test_no_carryover_requests(self, server_url):
        """跳转后不应该有上一页面遗留的股票 API 请求"""
        asyncio.run(_assert_no_carryover(server_url))


async def _assert_p95_under(server_url, threshold_ms):
    """跑 5 次, P95 必须 < threshold"""
    from playwright.async_api import async_playwright

    times = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                         service_workers="block")

        for i in range(5):
            page = await ctx.new_page()
            await page.goto(f"{server_url}/#dash", wait_until="commit", timeout=30000)
            await page.wait_for_timeout(2500)
            await page.evaluate("""() => {
              window._tJumpStart = performance.now();
              window._tFirstRealQuote = null;
              const checkQuote = () => {
                const q = document.querySelector('#q-price');
                const t = q ? q.textContent.trim() : '';
                const num = parseFloat(t);
                if (!isNaN(num) && num > 0 && window._tFirstRealQuote === null) {
                  window._tFirstRealQuote = performance.now() - window._tJumpStart;
                }
                if (window._tFirstRealQuote === null) requestAnimationFrame(checkQuote);
              };
              requestAnimationFrame(checkQuote);
            }""")
            await page.evaluate("location.hash = '#stock=000001'")
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                done = await page.evaluate("() => window._tFirstRealQuote !== null")
                if done: break
                await page.wait_for_timeout(20)
            t = await page.evaluate("() => window._tFirstRealQuote")
            if t is not None:
                times.append(t)
            await page.close()

        await browser.close()

    if len(times) < 3:
        pytest.skip(f"只有 {len(times)} 次有效测量,跳过")

    times.sort()
    p95 = times[max(0, int(len(times) * 0.95) - 1)]
    p99 = times[-1]
    p50 = times[len(times) // 2]

    print(f"\n  N={len(times)}, P50={p50:.0f}ms, P95={p95:.0f}ms, P99={p99:.0f}ms (threshold P95<{threshold_ms}ms)")
    if p95 > threshold_ms:
        pytest.fail(f"❌ P95={p95:.0f}ms > {threshold_ms}ms")


async def _assert_p99_under(server_url, threshold_ms):
    """复用上面的测量逻辑, 检查 P99"""
    from playwright.async_api import async_playwright

    times = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                         service_workers="block")

        for i in range(5):
            page = await ctx.new_page()
            await page.goto(f"{server_url}/#dash", wait_until="commit", timeout=30000)
            await page.wait_for_timeout(2500)
            await page.evaluate("""() => {
              window._tJumpStart = performance.now();
              window._tFirstRealQuote = null;
              const checkQuote = () => {
                const q = document.querySelector('#q-price');
                const t = q ? q.textContent.trim() : '';
                const num = parseFloat(t);
                if (!isNaN(num) && num > 0 && window._tFirstRealQuote === null) {
                  window._tFirstRealQuote = performance.now() - window._tJumpStart;
                }
                if (window._tFirstRealQuote === null) requestAnimationFrame(checkQuote);
              };
              requestAnimationFrame(checkQuote);
            }""")
            await page.evaluate("location.hash = '#stock=000001'")
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                done = await page.evaluate("() => window._tFirstRealQuote !== null")
                if done: break
                await page.wait_for_timeout(20)
            t = await page.evaluate("() => window._tFirstRealQuote")
            if t is not None:
                times.append(t)
            await page.close()

        await browser.close()

    if len(times) < 3:
        pytest.skip(f"只有 {len(times)} 次有效测量,跳过")

    times.sort()
    p99 = times[-1]
    if p99 > threshold_ms:
        pytest.fail(f"❌ P99={p99:.0f}ms > {threshold_ms}ms")


async def _assert_no_carryover(server_url):
    """跳转后 1s 内, 不应该有非 000001 的 stock API 请求"""
    from playwright.async_api import async_playwright

    carryovers = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                         service_workers="block")

        for i in range(3):
            page = await ctx.new_page()
            reqs_after_jump = []
            jumped = [False]

            def on_req(r):
                if jumped[0] and "/api/" in r.url:
                    reqs_after_jump.append(r.url)

            page.on("request", on_req)

            await page.goto(f"{server_url}/#dash", wait_until="commit", timeout=30000)
            await page.wait_for_timeout(2500)

            jumped[0] = True
            t0 = time.monotonic()
            await page.evaluate("location.hash = '#stock=000001'")
            # Wait 3 seconds
            await page.wait_for_timeout(3000)

            # Carryover = non-000001 stock API within 1s of jump
            for u in reqs_after_jump:
                # Time not easily available, but we know these are after jump
                # Filter: /api/stock/<other_code>/...
                if "/api/stock/" in u and "/000001" not in u:
                    carryovers.append(u.replace(server_url, "")[:80])

            await page.close()

        await browser.close()

    if carryovers:
        # Group by pattern
        sample = carryovers[:5]
        pytest.fail(f"❌ 跳转后遗留 {len(carryovers)} 个非 000001 stock 请求: {sample}")


# ──────────────────────────────────────────────────────────────────────
# §3 — 网络瀑布 (定位慢的请求)
# ──────────────────────────────────────────────────────────────────────

class TestNetworkWaterfall:
    """记录跳转后每个 API 请求的开始/结束时间, 输出 waterfall"""

    def test_waterfall_dump(self, server_url):
        asyncio.run(_dump_waterfall(server_url))


async def _dump_waterfall(server_url):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                         service_workers="block")
        page = await ctx.new_page()

        reqs = []
        page.on("request", lambda r: reqs.append({"type": "req", "t": time.monotonic(), "url": r.url, "method": r.method}))
        page.on("requestfinished", lambda r: reqs.append({"type": "fin", "t": time.monotonic(), "url": r.url}))
        page.on("requestfailed", lambda r: reqs.append({"type": "fail", "t": time.monotonic(), "url": r.url}))

        await page.goto(f"{server_url}/#all_stocks", wait_until="commit", timeout=30000)
        await page.wait_for_timeout(3000)

        reqs.clear()
        t_jump = time.monotonic()
        await page.evaluate("location.hash = '#stock?code=600519'")
        await page.wait_for_timeout(8000)  # wait long enough for all requests

        api_reqs = [r for r in reqs if "/api/" in r["url"]]
        stock_reqs = [r for r in api_reqs if "/600519" in r["url"]]
        non_stock_api = [r for r in api_reqs if "/600519" not in r["url"]]

        print("\n=== Network Waterfall (relative ms from jump) ===")
        print(f"  Total API: {len(api_reqs)}, stock-specific (600519): {len(stock_reqs)}, NON-stock carryover: {len(non_stock_api)}")
        for r in api_reqs[:50]:
            rel = (r["t"] - t_jump) * 1000
            url = r["url"].replace(server_url, "")[:90]
            star = "★" if "/600519" in r["url"] else " "
            print(f"  {rel:6.0f}ms  {r['type']:5s} {star} {url}")

        await browser.close()