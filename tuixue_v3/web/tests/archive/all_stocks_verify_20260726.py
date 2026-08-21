import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://study-tuition-nylon.ngrok-free.dev/#all_stocks"
OUT = Path("/Users/kaikai/scripts/tuixue_v3/web/tests/artifacts/all_stocks_verify_20260726")
OUT.mkdir(parents=True, exist_ok=True)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        results = {}
        for name, width, height, mobile in (("iphone13", 390, 844, True), ("desktop1280", 1280, 900, False)):
            ctx = await browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=2 if mobile else 1,
                is_mobile=mobile,
                has_touch=mobile,
                user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
                            "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1") if mobile else None,
            )
            await ctx.add_cookies([{
                "name": "abuse_interstitial",
                "value": "study-tuition-nylon.ngrok-free.dev",
                "domain": ".ngrok-free.dev",
                "path": "/",
                "secure": True,
                "sameSite": "None",
            }])
            page = await ctx.new_page()
            console_errors, page_errors, requests = [], [], []
            page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)
            page.on("pageerror", lambda err: page_errors.append(str(err)))
            page.on("request", lambda req: requests.append(req.url))
            await page.goto(URL, wait_until="domcontentloaded", timeout=120000)
            await page.wait_for_timeout(1000)
            await page.evaluate("""
                async () => {
                    for (const key of await caches.keys()) await caches.delete(key);
                    for (const reg of await navigator.serviceWorker.getRegistrations()) {
                        try { await reg.unregister(); } catch (_) {}
                    }
                    localStorage.clear(); sessionStorage.clear();
                }
            """)
            await page.goto(URL, wait_until="domcontentloaded", timeout=120000)
            await page.wait_for_timeout(5000)
            await page.evaluate("location.hash = '#all_stocks'")
            try:
                await page.wait_for_timeout(8000)
            except Exception:
                pass
            try:
                await page.wait_for_function("""() => {
                    const t = document.querySelector('.view-all_stocks table');
                    return t && t.querySelectorAll('tbody tr').length >= 5 && !/加载中/.test(t.innerText);
                }""", timeout=30000)
            except Exception:
                pass
            try:
                await page.wait_for_timeout(2500)
            except Exception as exc:
                results[name] = {"data": {"navigation_error": str(exc)}, "sorts": {}, "console_errors": console_errors, "page_errors": page_errors}
                await ctx.close()
                continue
            data = await page.evaluate("""() => {
                const root = document.querySelector('.view-all_stocks') || document;
                const table = root.querySelector('table');
                const headers = table ? [...table.querySelectorAll('thead th')].map((th, i) => ({i, text: th.innerText.trim(), col: th.dataset.col, sort: th.dataset.sort, display: getComputedStyle(th).display})) : [];
                const rows = table ? [...table.querySelectorAll('tbody tr')].slice(0, 5) : [];
                const wanted = ['趋势', '龙头属性'];
                const cells = Object.fromEntries(wanted.map(w => {
                    const i = headers.find(h => h.col === w)?.i ?? -1;
                    return [w, rows.map(r => i >= 0 ? {text: r.children[i]?.innerText.trim() || '', cls: r.children[i]?.querySelector('span')?.className || '', display: getComputedStyle(r.children[i]).display} : null)];
                }));
                return {
                    hash: location.hash, sw: navigator.serviceWorker?.controller?.scriptURL || '',
                    headers, headerCount: headers.length, rowCount: table?.querySelectorAll('tbody tr').length || 0,
                    cells, docWidth: document.documentElement.clientWidth, bodyScrollWidth: document.body.scrollWidth,
                    tableScrollWidth: table?.parentElement?.scrollWidth || 0, tableClientWidth: table?.parentElement?.clientWidth || 0,
                    visiblePriority3: headers.filter(h => h.col && getComputedStyle(table.rows[0]?.cells[h.i] || table.rows[0]?.cells[0]).display !== 'none' && ['趋势','龙头属性'].includes(h.col)).map(h => h.col),
                };
            }""")
            await page.screenshot(path=str(OUT / f"{name}_full.png"), full_page=True)
            await page.screenshot(path=str(OUT / f"{name}_viewport.png"), full_page=False)

            sort_checks = {}
            for col in ["趋势", "龙头属性", "风险", "联动", "量价", "角色"]:
                before = set(requests)
                h = page.locator(f'thead th[data-col="{col}"]')
                if await h.count() == 0:
                    sort_checks[col] = {"header": False}
                    continue
                await h.click()
                await page.wait_for_timeout(250)
                new_requests = [u for u in requests if u not in before]
                sort_checks[col] = {
                    "header": True,
                    "new_all_stocks_board_requests": [u for u in new_requests if "/api/all_stocks/board" in u],
                    "new_requests": new_requests,
                }
            results[name] = {"data": data, "sorts": sort_checks, "console_errors": console_errors, "page_errors": page_errors}
            await ctx.close()
        (OUT / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(results, ensure_ascii=False, indent=2))
        await browser.close()

asyncio.run(main())
