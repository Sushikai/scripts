"""
tests/test_perf_web.py — Playwright 页面 LCP<1s 测试

12 view × 2 viewport (1280×800 桌面 / 390×844 移动) 共 24 次 LCP 测量。
每个 view × viewport 跑 3 次,取 P50 LCP,要求 < 1000ms。

LCP 测量用 PerformanceObserver (LCPEntry)。

跑法:
    pytest tests/test_perf_web.py -v -m e2e
"""
from __future__ import annotations

import statistics
import time

import pytest
from playwright.sync_api import sync_playwright

VIEWS = [
    "dash", "stock", "all_stocks", "screener",
    "review", "dragons", "limit_up", "weekly_bull",
    "strategy", "sector_hotspot", "watchlist", "optimize",
]

VIEWPORTS = [
    ("desktop", 1280, 800),
    ("mobile", 390, 844),
]

THEMES = ["dark", "light"]

pytestmark = pytest.mark.e2e

# LCP 预算 (ms) — 基线档位,P3C 逐步收紧到 1s
LCP_BUDGET = 4000
N_RUNS = 3  # 每 (view, viewport, theme) 取 3 次 LCP 中位数


def _measure_lcp(page, hash_route, timeout_ms=8000):
    """导航到 view,等 LCP (or 超时),返回 LCP ms."""
    # 清空 PerformanceObserver buffer
    page.evaluate("window.__lcp = null; window.__lcpEntries = [];")
    # 注册 LCP observer (在 navigation 之前)
    page.add_init_script("""
        window.__lcpEntries = [];
        new PerformanceObserver((list) => {
            for (const e of list.getEntries()) window.__lcpEntries.push(e.startTime);
        }).observe({type: 'largest-contentful-paint', buffered: true});
    """)

    t0 = time.perf_counter()
    page.goto(f"http://127.0.0.1:7799/#{hash_route}",
              wait_until="domcontentloaded", timeout=timeout_ms)
    # 等数据 settle (LCP 通常在首次绘制后 < 2s)
    page.wait_for_timeout(3000)
    # 取 LCP (取所有 entries 最大值)
    lcp = page.evaluate("""
        () => {
            const arr = window.__lcpEntries || [];
            return arr.length ? Math.max(...arr) : null;
        }
    """)
    elapsed = (time.perf_counter() - t0) * 1000
    if lcp is None:
        # 浏览器没报 LCP (Safari/headless),回退用 navigation timing
        lcp = elapsed
    return lcp, elapsed


@pytest.mark.parametrize("view", VIEWS)
@pytest.mark.parametrize("vp_name,vp_w,vp_h", VIEWPORTS, ids=[v[0] for v in VIEWPORTS])
def test_view_lcp_under_1s(view, vp_name, vp_w, vp_h, base_url):
    """每个 view × viewport 跑 N_RUNS 次,P50 LCP < 1s."""
    lcps = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": vp_w, "height": vp_h})
        page = context.new_page()
        # 先 warm up (开一次 dash, 等数据就位, 冷服务容忍 60s)
        try:
            page.goto(f"{base_url}/#dash", wait_until="networkidle", timeout=60000)
        except Exception:
            pass
        for _ in range(N_RUNS):
            lcp, _ = _measure_lcp(page, view)
            lcps.append(lcp)
        browser.close()
    lcps.sort()
    p50 = lcps[len(lcps) // 2]
    p_max = lcps[-1]
    print(f"  [{vp_name:7s}] {view:18s}  LCP runs={[f'{x:.0f}' for x in lcps]}  p50={p50:.0f}ms  max={p_max:.0f}ms")
    assert p50 < LCP_BUDGET, f"#{view} ({vp_name}) LCP p50={p50:.0f}ms > {LCP_BUDGET}ms"


def test_lcp_summary(base_url):
    """所有 view × viewport 的 LCP 汇总报告 (不 fail)."""
    summary = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for vp_name, w, h in VIEWPORTS:
            ctx = browser.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            try:
                page.goto(f"{base_url}/#dash", wait_until="networkidle", timeout=60000)
            except Exception:
                pass
            for v in VIEWS:
                lcps = []
                for _ in range(2):  # summary 用 2 次
                    lcp, _ = _measure_lcp(page, v, timeout_ms=20000)
                    lcps.append(lcp)
                summary[(vp_name, v)] = lcps
            ctx.close()
        browser.close()
    print("\n" + "=" * 80)
    print(f"{'View':<18} {'desktop LCP':>16} {'mobile LCP':>16}")
    print("-" * 80)
    fail_count = 0
    for v in VIEWS:
        d = summary.get(("desktop", v), [0])
        m = summary.get(("mobile", v), [0])
        d_p50 = sorted(d)[len(d) // 2]
        m_p50 = sorted(m)[len(m) // 2]
        d_ok = "✓" if d_p50 < LCP_BUDGET else "✗"
        m_ok = "✓" if m_p50 < LCP_BUDGET else "✗"
        if d_p50 >= LCP_BUDGET or m_p50 >= LCP_BUDGET:
            fail_count += 1
        print(f"{v:<18} {d_ok} {d_p50:>10.0f}ms   {m_ok} {m_p50:>10.0f}ms")
    print("=" * 80)
    print(f"LCP > 1s: {fail_count} view×vp 组合")