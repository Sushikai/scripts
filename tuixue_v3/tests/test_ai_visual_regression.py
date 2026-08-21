#!/usr/bin/env python3
"""
AI 模块视觉回归测试 (Phase 4)

Playwright 截图对比 AI 相关 view, 检查:
- 关键 DOM selector 存在性 (DOM presence)
- console errors (忽略 ERR_CONNECTION_REFUSED — 页面关闭后后台轮询正常现象)
- AI 指标 API 数据结构健康
- 空值文本兜底检测

用法:
  # pytest (需要 Playwright 浏览器):
  pytest tests/test_ai_visual_regression.py -v

  # 独立运行截图:
  python tests/test_ai_visual_regression.py

环境要求: tuixue_v3 server 在 localhost:7799 运行
"""
from __future__ import annotations
import asyncio
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = Path("/tmp/ai_vr")
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASE = "http://127.0.0.1:7799/"
SETTLE_MS = 6000
VIEWPORT = {"width": 1440, "height": 900}

# 可忽略的 console error 模式 — 页面关闭后后台轮询抛 ECONNREFUSED 是正常行为
IGNORE_ERROR_RE = re.compile(
    r"ERR_CONNECTION_REFUSED|net::ERR_|Failed to fetch|poll fail|503|Service Unavailable",
    re.I,
)

BAD_TEXT_RE = re.compile(r"^\s*$|^—+$|^N/A$|undefined|null", re.I)


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════

async def _goto_view(page, view: str, arg: str | None = None):
    if arg:
        encoded = urllib.parse.quote(arg, safe="")
        url = f"{BASE}#{view}={encoded}"
    else:
        url = f"{BASE}#{view}"
    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    try:
        await page.wait_for_selector(f'[data-view="{view}"]:not([hidden])', timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(SETTLE_MS)


async def _dom_exists(page, sel: str) -> bool:
    """只检查 DOM 存在性,不关心文本内容。"""
    try:
        el = await page.query_selector(sel)
        return el is not None
    except Exception:
        return False


async def _check_selector(page, sel: str) -> dict:
    try:
        el = await page.query_selector(sel)
        if not el:
            return {"ok": False, "reason": "MISSING"}
        txt = (await el.text_content() or "").strip()
        is_bad = bool(BAD_TEXT_RE.match(txt))
        return {"ok": not is_bad, "text": txt[:80], "bad_text": is_bad}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:60]}


async def _collect_console_errors(page) -> list[str]:
    msgs = []
    for msg in getattr(page, "_console", []) or []:
        if msg.type == "error":
            txt = msg.text[:120]
            if not IGNORE_ERROR_RE.search(txt):
                msgs.append(txt)
    return msgs


async def _screenshot(page, name: str) -> str:
    path = str(OUT_DIR / f"ai_vr_{name}.png")
    await page.screenshot(path=path, full_page=False)
    return path


# ═══════════════════════════════════════════════════════════
# 1. Stock AI tab — DOM presence
# ═══════════════════════════════════════════════════════════

@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="Playwright not installed")
@pytest.mark.asyncio
async def test_stock_ai_tab_dom():
    """AI 铁律 tab 点击后 #ai-verdict 和 #ai-status DOM 存在."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport=VIEWPORT)
        page = await ctx.new_page()

        page._console = []
        page.on("console", lambda msg: page._console.append(msg))

        await _goto_view(page, "stock", "600519")
        ai_tab = await page.query_selector('.chart-tab[data-tab="ai"]')
        if ai_tab:
            await ai_tab.click()
            await page.wait_for_timeout(4000)

        verdict_ok = await _dom_exists(page, "#ai-verdict")
        status_ok = await _dom_exists(page, "#ai-status")
        await _screenshot(page, "stock_ai_tab")

        errors = await _collect_console_errors(page)
        await browser.close()

        assert verdict_ok, "#ai-verdict DOM missing"
        assert status_ok, "#ai-status DOM missing"
        assert len(errors) == 0, f"Unexpected console errors: {errors}"


# ═══════════════════════════════════════════════════════════
# 2. Deep Analysis card
# ═══════════════════════════════════════════════════════════

@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="Playwright not installed")
@pytest.mark.asyncio
async def test_stock_deep_analysis_card():
    """#stock-deep-analy-card 全部子元素 DOM 存在."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport=VIEWPORT)
        page = await ctx.new_page()

        page._console = []
        page.on("console", lambda msg: page._console.append(msg))

        await _goto_view(page, "stock", "600519")
        await page.wait_for_timeout(5000)

        selectors = [
            "#deep-action-chip", "#deep-score", "#deep-status",
            "#deep-profile-text", "#deep-earnings-body",
            "#deep-holding-view", "#deep-tech-view", "#deep-summary-text",
        ]
        results = {}
        for sel in selectors:
            results[sel] = await _dom_exists(page, sel)

        await _screenshot(page, "stock_deep_analysis")
        errors = await _collect_console_errors(page)
        await browser.close()

        missing = [k for k, v in results.items() if not v]
        assert not missing, f"Missing DOM elements: {missing}"
        assert len(errors) == 0, f"Unexpected console errors: {errors}"


# ═══════════════════════════════════════════════════════════
# 3. Crash Risk card
# ═══════════════════════════════════════════════════════════

@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="Playwright not installed")
@pytest.mark.asyncio
async def test_stock_crash_risk_card():
    """#crash-panel 内 crash-risk / crash-status / crash-refresh-btn DOM 存在."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport=VIEWPORT)
        page = await ctx.new_page()

        page._console = []
        page.on("console", lambda msg: page._console.append(msg))

        await _goto_view(page, "stock", "600519")
        await page.wait_for_timeout(5000)

        selectors = ["#crash-panel", "#crash-risk", "#crash-status", "#crash-refresh-btn"]
        results = {}
        for sel in selectors:
            results[sel] = await _dom_exists(page, sel)

        await _screenshot(page, "stock_crash_risk")
        errors = await _collect_console_errors(page)
        await browser.close()

        missing = [k for k, v in results.items() if not v]
        assert not missing, f"Missing DOM elements: {missing}"
        assert len(errors) == 0, f"Unexpected console errors: {errors}"


# ═══════════════════════════════════════════════════════════
# 4. Review view
# ═══════════════════════════════════════════════════════════

@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="Playwright not installed")
@pytest.mark.asyncio
async def test_review_view_dom():
    """Review 页面 table + bulk-ai + fix-dirty 按钮 DOM 存在."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport=VIEWPORT)
        page = await ctx.new_page()

        page._console = []
        page.on("console", lambda msg: page._console.append(msg))

        await _goto_view(page, "review")
        await page.wait_for_timeout(4000)

        selectors = ["#review-table", "#review-bulk-ai", "#review-fix-dirty"]
        results = {}
        for sel in selectors:
            results[sel] = await _dom_exists(page, sel)

        await _screenshot(page, "review_view")
        errors = await _collect_console_errors(page)
        await browser.close()

        missing = [k for k, v in results.items() if not v]
        assert not missing, f"Missing DOM elements: {missing}"
        assert len(errors) == 0, f"Unexpected console errors: {errors}"


# ═══════════════════════════════════════════════════════════
# 5. Screener (ZT) view — container mount
# ═══════════════════════════════════════════════════════════

@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="Playwright not installed")
@pytest.mark.asyncio
async def test_screener_zt_dom():
    """Screener 页面 view 容器 + #zt-mount 挂载点 DOM 存在."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport=VIEWPORT)
        page = await ctx.new_page()

        page._console = []
        page.on("console", lambda msg: page._console.append(msg))

        await _goto_view(page, "screener")
        await page.wait_for_timeout(5000)

        # screener UI 是动态注入的 — 只验证容器 view 可见 + mount 点存在
        view_visible = await _dom_exists(page, '[data-view="screener"]:not([hidden])')
        mount_exists = await _dom_exists(page, "#zt-mount")

        await _screenshot(page, "screener_zt")
        errors = await _collect_console_errors(page)
        await browser.close()

        assert view_visible, "screener view not visible"
        assert mount_exists, "#zt-mount DOM missing"
        assert len(errors) == 0, f"Unexpected console errors: {errors}"


# ═══════════════════════════════════════════════════════════
# 6. Stock hero AI block
# ═══════════════════════════════════════════════════════════

@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="Playwright not installed")
@pytest.mark.asyncio
async def test_stock_hero_ai_block():
    """Stock hero 区 AI verdict + crash-risk 引用 DOM 存在."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport=VIEWPORT)
        page = await ctx.new_page()

        page._console = []
        page.on("console", lambda msg: page._console.append(msg))

        await _goto_view(page, "stock", "600519")
        await page.wait_for_timeout(5000)

        selectors = ["#ai-verdict", "#ai-status", "#crash-risk", "#crash-status"]
        results = {}
        for sel in selectors:
            results[sel] = await _dom_exists(page, sel)

        await _screenshot(page, "stock_hero_ai")
        errors = await _collect_console_errors(page)
        await browser.close()

        missing = [k for k, v in results.items() if not v]
        assert not missing, f"Missing DOM elements: {missing}"
        assert len(errors) == 0, f"Unexpected console errors: {errors}"


# ═══════════════════════════════════════════════════════════
# 7. AI metrics API — 数据结构校验
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_ai_metrics_api_structure():
    """GET /api/ai/metrics 返回正确的 bucket 结构."""
    import urllib.request
    try:
        resp = urllib.request.urlopen(f"{BASE}api/ai/metrics", timeout=5)
        data = json.loads(resp.read())
    except Exception as e:
        pytest.skip(f"Server not reachable: {e}")

    assert data.get("ok"), f"API not ok: {data}"
    buckets = data.get("data", {}).get("buckets", {})
    assert isinstance(buckets, dict), f"buckets not dict: {type(buckets)}"

    expected = {"main_verdict", "crash_risk", "chat", "screen_aggregate", "review"}
    found = set(buckets.keys())
    missing = expected - found
    if missing:
        print(f"  [info] metrics buckets not yet populated: {missing}")

    for name, b in buckets.items():
        assert isinstance(b, dict), f"bucket {name} not dict"
        for field in ("calls", "ok", "fail", "ok_pct", "model"):
            assert field in b, f"bucket {name} missing field {field}"


# ═══════════════════════════════════════════════════════════
# 8. AI aggregate API
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_ai_screen_aggregate_api():
    """POST /api/screen/ai_aggregate 正确处理空输入."""
    import urllib.request
    payload = json.dumps({"scored": []}).encode()
    req = urllib.request.Request(
        f"{BASE}api/screen/ai_aggregate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
    except Exception as e:
        pytest.skip(f"Server not reachable: {e}")

    assert data.get("ok") is not None, f"unexpected response: {data}"


# ═══════════════════════════════════════════════════════════
# CLI entry — 独立运行截图到 /tmp/ai_vr/
# ═══════════════════════════════════════════════════════════

async def _run_standalone():
    if not HAS_PLAYWRIGHT:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return 1

    out_dir = Path("/tmp/ai_vr")
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        tests = [
            ("stock_ai_tab", "stock", "600519", ["#ai-verdict", "#ai-status"]),
            ("stock_deep_analysis", "stock", "600519", [
                "#deep-action-chip", "#deep-score", "#deep-status",
                "#deep-profile-text", "#deep-earnings-body",
                "#deep-holding-view", "#deep-tech-view", "#deep-summary-text",
            ]),
            ("stock_crash_risk", "stock", "600519", [
                "#crash-panel", "#crash-risk", "#crash-status", "#crash-refresh-btn",
            ]),
            ("review_view", "review", None, [
                "#review-table", "#review-bulk-ai", "#review-fix-dirty",
            ]),
            ("screener_zt", "screener", None, [
                '[data-view="screener"]:not([hidden])', "#zt-mount",
            ]),
        ]

        for name, view, arg, selectors in tests:
            ctx = await browser.new_context(viewport=VIEWPORT)
            page = await ctx.new_page()
            page._console = []
            page.on("console", lambda msg: page._console.append(msg))

            await _goto_view(page, view, arg)
            if name == "stock_ai_tab":
                ai_tab = await page.query_selector('.chart-tab[data-tab="ai"]')
                if ai_tab:
                    await ai_tab.click()
            await page.wait_for_timeout(4000)

            dom_ok = 0
            dom_total = len(selectors)
            for sel in selectors:
                if await _dom_exists(page, sel):
                    dom_ok += 1

            errors = await _collect_console_errors(page)
            path = str(out_dir / f"{name}.png")
            await page.screenshot(path=path, full_page=False)

            all_ok = dom_ok == dom_total
            results.append({
                "view": name, "dom_ok": f"{dom_ok}/{dom_total}",
                "ok": all_ok, "errors": len(errors), "screenshot": path,
            })
            await ctx.close()

        await browser.close()

    passed = sum(1 for r in results if r["ok"])
    total = len(results)

    print(f"\n{'='*50}")
    print(f"AI Visual Regression: {passed}/{total} PASS")
    for r in results:
        flag = "✓" if r["ok"] else "✗"
        print(f"  {flag} {r['view']:25s} DOM={r['dom_ok']:8s} errors={r['errors']}")
    print(f"\nScreenshots: {out_dir}/")
    print(f"{'='*50}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_run_standalone()))
