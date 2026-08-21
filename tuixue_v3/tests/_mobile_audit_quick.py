"""移动端全视图快速审计 — Playwright iPhone 13 viewport"""
import asyncio, sys, time
from playwright.async_api import async_playwright

BASE = "http://localhost:7799"
VIEWS = [
    ("首页", "/#dash"),
    ("全A风向", "/#all_stocks"),
    ("自选", "/#watchlist"),
    ("复盘", "/#review"),
    ("龙头", "/#dragons"),
    ("板块热点", "/#sector_hotspot"),
    ("得鑫", "/#dexin"),
    ("周线擒牛", "/#weekly_bull"),
    ("策略选股", "/#strategy_picker"),
    ("数据源健康", "/#sources"),
    ("心法", "/#laws"),
    ("优化", "/#optimize"),
]

async def main():
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 390, "height": 844})
        page = await context.new_page()
        errors = []

        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

        for name, hash_url in VIEWS:
            errors.clear()
            t0 = time.time()
            try:
                await page.goto(BASE + hash_url, wait_until="networkidle", timeout=25000)
                await asyncio.sleep(0.5)

                # Check for horizontal overflow
                overflow = await page.evaluate("""() => {
                    const vw = window.innerWidth;
                    const bodyW = document.body.scrollWidth;
                    return bodyW > vw + 5 ? (bodyW - vw) : 0;
                }""")

                # Check for visible content
                has_content = await page.evaluate("""() => {
                    const main = document.querySelector('main') || document.body;
                    return main.innerText.trim().length > 20;
                }""")

                elapsed = int((time.time() - t0) * 1000)
                console_errs = len([e for e in errors if 'favicon' not in e.lower()])
                status = "OK" if (has_content and overflow == 0 and console_errs == 0) else (
                    f"overflow={overflow}px" if overflow else ""
                    + (" no-content" if not has_content else "")
                    + (f" errs={console_errs}" if console_errs else "")
                )
                results.append((name, status.strip() or "OK", elapsed))

            except Exception as e:
                results.append((name, f"FAIL: {e}", 0))

        await browser.close()

    print(f"{'View':<16} {'Status':<30} {'Time'}")
    print("-" * 62)
    for name, status, ms in results:
        ok = "✓" if status == "OK" else "✗"
        print(f"{ok} {name:<14} {status:<30} {ms}ms")

    ok_count = sum(1 for _, s, _ in results if s == "OK")
    print(f"\n{ok_count}/{len(VIEWS)} PASS")

    return 0 if ok_count == len(VIEWS) else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
