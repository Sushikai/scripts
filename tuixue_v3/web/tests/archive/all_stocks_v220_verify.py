import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://study-tuition-nylon.ngrok-free.dev/#all_stocks"
OUT = Path("/Users/kaikai/scripts/tuixue_v3/web/tests/artifacts/all_stocks_v220_verify")
OUT.mkdir(parents=True, exist_ok=True)


async def run_one(browser, name, width, height, mobile, ua):
    ctx = await browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=2 if mobile else 1,
        is_mobile=mobile,
        has_touch=mobile,
        user_agent=ua,
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

    # Clean SW + cache first load to ensure v220 is picked
    try:
        await page.goto(URL, wait_until="domcontentloaded", timeout=120000)
    except Exception as e:
        # tunnel may be flaky; retry once
        await page.wait_for_timeout(3000)
        await page.goto(URL, wait_until="domcontentloaded", timeout=120000)
    await page.evaluate("""
        async () => {
            for (const key of await caches.keys()) await caches.delete(key);
            for (const reg of await navigator.serviceWorker.getRegistrations()) {
                try { await reg.unregister(); } catch (_) {}
            }
            localStorage.clear(); sessionStorage.clear();
        }
    """)
    # Hard reload to install new SW v220
    try:
        await page.reload(wait_until="domcontentloaded", timeout=120000)
    except Exception:
        await page.wait_for_timeout(3000)
        await page.reload(wait_until="domcontentloaded", timeout=120000)
    await page.wait_for_timeout(6000)
    await page.evaluate("location.hash = '#all_stocks'")
    await page.wait_for_timeout(10000)

    # Wait for table populated (headers + rows + trend column present)
    try:
        await page.wait_for_function("""() => {
            const t = document.querySelector('.view-all_stocks table');
            if (!t) return false;
            const rows = t.querySelectorAll('tbody tr').length;
            if (rows < 5) return false;
            const headers = [...t.querySelectorAll('thead th')].map(th => th.dataset.col);
            if (!headers.includes('趋势') || !headers.includes('龙头属性')) return false;
            if (/加载中/.test(t.innerText)) return false;
            return true;
        }""", timeout=45000)
    except Exception as e:
        pass
    await page.wait_for_timeout(3500)

    data = await page.evaluate("""() => {
        const table = document.querySelector('.view-all_stocks table');
        const headers = table ? [...table.querySelectorAll('thead th')].map((th, i) => ({
            i, text: th.innerText.trim(), col: th.dataset.col,
            priority: th.dataset.priority,
            display: getComputedStyle(th).display,
        })) : [];
        const rows = table ? [...table.querySelectorAll('tbody tr')].slice(0, 5) : [];
        const wanted = ['趋势', '龙头属性'];
        const cells = Object.fromEntries(wanted.map(w => {
            const i = headers.find(h => h.col === w)?.i ?? -1;
            return [w, i >= 0 ? rows.map(r => ({
                text: r.children[i]?.innerText.trim() || '',
                cls: r.children[i]?.querySelector('span')?.className || '',
                tdPriority: r.children[i]?.dataset?.priority || '',
                tdDisplay: getComputedStyle(r.children[i]).display,
            })) : null];
        }));
        const sw_url = navigator.serviceWorker?.controller?.scriptURL || '';
        // Extract SW version from URL or cache name
        return {
            hash: location.hash, sw_url,
            sw_caches: 'check_below',
            headerCount: headers.length,
            headerCols: headers.map(h => h.col || h.text),
            headersWithPriority: headers.filter(h => h.priority).map(h => ({col: h.col, priority: h.priority})),
            rowCount: table?.querySelectorAll('tbody tr').length || 0,
            cells,
            docWidth: document.documentElement.clientWidth,
            bodyScrollWidth: document.body.scrollWidth,
        };
    }""")
    # Get SW cache keys separately
    sw_info = await page.evaluate("""async () => {
        const keys = await caches.keys();
        const controller = navigator.serviceWorker?.controller?.scriptURL || '';
        return {cache_keys: keys, controller_url: controller};
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
            "new_requests": new_requests[:5],
        }
    await ctx.close()
    return {"data": data, "sw_info": sw_info, "sorts": sort_checks,
            "console_errors": console_errors, "page_errors": page_errors}


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        results = {}
        cases = [
            ("iphone13", 390, 844, True,
             "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"),
            ("desktop1280", 1280, 800, False, None),
            ("ipad768", 768, 1024, True,
             "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"),
        ]
        for name, w, h, mobile, ua in cases:
            try:
                results[name] = await run_one(browser, name, w, h, mobile, ua)
            except Exception as e:
                results[name] = {"error": str(e)}
        (OUT / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(results, ensure_ascii=False, indent=2))
        await browser.close()


asyncio.run(main())