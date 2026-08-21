"""Audit every stock page sub-feature: probe API + assert UI shows data.

Returns per-endpoint and per-section status. Designed to be invoked iteratively.
"""
import asyncio
import json
import sys
import time
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"
CODE = "605179"  # 一鸣食品 (recent limit-up, has all data)


async def probe_api(page, label, url):
    """Probe a single API endpoint and capture status/size/timing."""
    try:
        t0 = time.time()
        resp = await page.evaluate(
            f"async () => {{ const r = await fetch('{url}', {{signal: AbortSignal.timeout(8000)}}); "
            f"const text = await r.text(); const j = JSON.parse(text); "
            f"return {{status: r.status, size: text.length, ok: j.ok, has_data: !!j.data, "
            f"err: j.error || null, sample_keys: j.data ? Object.keys(j.data).slice(0,5) : []}}; }}"
        )
        elapsed = int((time.time() - t0) * 1000)
        return {
            "label": label,
            "url": url,
            "status": resp.get("status"),
            "size": resp.get("size"),
            "ok": resp.get("ok"),
            "has_data": resp.get("has_data"),
            "err": resp.get("err"),
            "sample_keys": resp.get("sample_keys"),
            "elapsed_ms": elapsed,
        }
    except Exception as e:
        return {"label": label, "url": url, "error": str(e)[:160]}


async def probe_ui(page, selector, attr=None):
    """Check if a UI element exists and has content."""
    try:
        loc = page.locator(selector).first
        if await loc.count() == 0:
            return {"selector": selector, "exists": False}
        text = await loc.text_content()
        return {
            "selector": selector,
            "exists": True,
            "text": (text or "").strip()[:80],
            "empty": not (text or "").strip()
        }
    except Exception as e:
        return {"selector": selector, "error": str(e)[:120]}


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        # Capture console errors
        console_errors = []
        page.on("console", lambda m: console_errors.append(f"[{m.type}] {m.text[:200]}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"[pageerror] {str(e)[:200]}"))

        # Capture all network requests with their status
        net_errors = []
        def on_response(r):
            if r.status >= 500:
                net_errors.append(f"{r.status} {r.request.method} {r.url[:150]}")
            elif r.status == 404 and "/api/" in r.url:
                net_errors.append(f"404 {r.request.method} {r.url[:150]}")
        page.on("response", on_response)

        # Unregister SW first for fresh code
        await page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=15000)
        await page.evaluate("""() => {
          if ('serviceWorker' in navigator) {
            navigator.serviceWorker.getRegistrations().then(regs => {
              regs.forEach(r => r.unregister());
            });
          }
        }""")
        await asyncio.sleep(1)

        # Reload with stock hash
        await page.goto(f"{BASE}/#stock?code={CODE}", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(4)

        # === 1. Probe every API endpoint ===
        endpoints = [
            ("core", f"/api/stock/{CODE}/core"),
            ("full", f"/api/stock/{CODE}/full"),
            ("kline-30d", f"/api/stock/{CODE}/kline?days=30"),
            ("kline-120d", f"/api/stock/{CODE}/kline?days=120"),
            ("intraday", f"/api/stock/{CODE}/intraday"),
            ("intraday-5d", f"/api/stock/{CODE}/intraday?days=5"),
            ("limit_up_ctx", f"/api/stock/{CODE}/limit_up_context"),
            ("strong_stocks", f"/api/stock/{CODE}/strong_stocks"),
            ("related_news", f"/api/stock/{CODE}/related_news"),
            ("sector", f"/api/stock/{CODE}/sector"),
            ("profile", f"/api/stock/{CODE}/profile"),
            ("seat_breakdown", f"/api/stock/{CODE}/seat_breakdown"),
            ("role", f"/api/stock/{CODE}/role"),
            ("strategy_match", f"/api/stock/{CODE}/strategy_match"),
            ("ai_analysis", f"/api/stock/{CODE}/ai_analysis"),
            ("ai_crash_risk", f"/api/stock/{CODE}/ai_crash_risk"),
            ("deep_analysis", f"/api/stock/{CODE}/deep_analysis"),
            ("dragons", "/api/dragons"),
            ("news", f"/api/news/related?code={CODE}"),
        ]

        api_results = []
        for label, url in endpoints:
            r = await probe_api(page, label, url)
            api_results.append(r)
            await asyncio.sleep(0.1)

        # === 2. Probe UI sections ===
        ui_sections = [
            ("#stock-title", "Header title"),
            ("#stock-code", "Stock code"),
            ("#stock-sub", "Header subtitle"),
            ("#stock-tags-host", "Tags host"),
            ("#stock-quote-price", "Quote price"),
            ("#stock-quote-change", "Quote change"),
            ("#stock-quote-volume", "Quote volume"),
            ("#stock-kline", "K-line chart container"),
            ("#stock-intraday", "Intraday chart container"),
            ("#stock-news", "News section"),
            ("#stock-sectors", "Sector chips"),
            ("#stock-streak-10d", "Streak 10d grid"),
            ("#stock-fund-flow", "Fund flow"),
            ("#stock-ai-status", "AI analysis status"),
            ("#stock-tabs", "Tab container"),
            ("[data-tab=intraday]", "Intraday tab"),
            ("[data-tab=kline]", "Kline tab"),
            ("[data-tab=news]", "News tab"),
            ("[data-tab=capital]", "Capital flow tab"),
            ("[data-tab=limit_up]", "Limit-up tab"),
            ("[data-tab=streak]", "Streak tab"),
            ("[data-tab=dragon]", "Dragon tab"),
        ]

        ui_results = []
        for selector, label in ui_sections:
            r = await probe_ui(page, selector)
            if "label" not in r:
                r["label"] = label
            ui_results.append(r)

        # Report
        print("\n" + "="*80)
        print(f"STOCK PAGE AUDIT — {CODE}")
        print("="*80)

        # API failures
        api_bad = [r for r in api_results if r.get("status") != 200 or r.get("ok") is False or r.get("has_data") is False]
        print(f"\n📡 API ENDPOINTS ({len(api_results)} tested, {len(api_bad)} broken)")
        for r in api_results:
            status = r.get("status", "?")
            ok = r.get("ok")
            has = r.get("has_data")
            err = r.get("err") or r.get("error")
            elapsed = r.get("elapsed_ms", "?")
            mark = "✗" if (status != 200 or ok is False or has is False or err) else "✓"
            print(f"  {mark} {r['label']:20s} status={status} ok={ok} data={has} {elapsed}ms  {err or ''}")

        # UI broken sections
        ui_bad = [r for r in ui_results if r.get("exists") is False or r.get("empty") is True or r.get("error")]
        print(f"\n🖼  UI SECTIONS ({len(ui_results)} tested, {len(ui_bad)} broken)")
        for r in ui_results:
            mark = "✗" if (r.get("exists") is False or r.get("empty") is True or r.get("error")) else "✓"
            text = r.get("text", r.get("error", ""))
            print(f"  {mark} {r.get('label', r['selector']):20s} {r['selector']:30s} {text}")

        # Network errors
        print(f"\n🌐 NETWORK ERRORS ({len(net_errors)})")
        for e in net_errors[:20]:
            print(f"  {e}")

        # Console errors
        print(f"\n💻 CONSOLE ERRORS ({len(console_errors)})")
        for e in console_errors[:20]:
            print(f"  {e}")

        await browser.close()

        # Summary
        print("\n" + "="*80)
        print(f"TOTAL ISSUES: {len(api_bad) + len(ui_bad) + len(net_errors) + len(console_errors)}")
        return 0 if (len(api_bad) + len(ui_bad) + len(net_errors) + len(console_errors)) == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
