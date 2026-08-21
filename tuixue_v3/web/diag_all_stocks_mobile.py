"""
Diagnose "only first 2 columns" bug at /view-all_stocks on iPhone 13.
Connects via localhost (no ngrok) since the page is reachable from local.
"""
import asyncio
import json
import sys
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # iPhone 13 viewport
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=3,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
            is_mobile=True,
            has_touch=True,
        )
        page = await ctx.new_page()
        console_msgs = []
        page.on("console", lambda m: console_msgs.append((m.type, m.text)))
        try:
            await page.goto("http://127.0.0.1:7799/#all_stocks", wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            print(f"NAV ERR: {e}", file=sys.stderr)
        # Wait for table render
        await page.wait_for_timeout(2500)

        diag = await page.evaluate(
            r"""
() => {
    const out = {errors: [], notes: []};
    const tab = document.querySelector('.view-all_stocks .stocks-table');
    const wrap = document.querySelector('.view-all_stocks .table-wrap');
    out.viewport = {w: window.innerWidth, h: window.innerHeight};
    out.docW = document.documentElement.clientWidth;
    out.bodyScrollW = document.body.scrollWidth;
    if (!tab) { out.errors.push('no .stocks-table'); return out; }
    const tabR = tab.getBoundingClientRect();
    out.tableRect = {left: Math.round(tabR.left), right: Math.round(tabR.right), top: Math.round(tabR.top), w: Math.round(tabR.width)};
    out.tableScrollW = tab.scrollWidth;
    out.tableScrollH = tab.scrollHeight;
    if (wrap) {
        const wR = wrap.getBoundingClientRect();
        out.wrapRect = {left: Math.round(wR.left), right: Math.round(wR.right), top: Math.round(wR.top), w: Math.round(wR.width)};
        out.wrapScrollW = wrap.scrollWidth;
        out.wrapClientW = wrap.clientWidth;
        out.wrapHasHScroll = wrap.scrollWidth > wrap.clientWidth + 1;
    } else {
        out.errors.push('no .table-wrap');
    }
    out.tableLayout = getComputedStyle(tab).tableLayout;

    // th
    const ths = Array.from(tab.querySelectorAll('thead th'));
    out.heads = ths.map(th => {
        const cs = getComputedStyle(th);
        const r = th.getBoundingClientRect();
        return {
            col: th.getAttribute('data-col'),
            text: (th.innerText || '').trim().slice(0, 16),
            display: cs.display,
            visibility: cs.visibility,
            inlineW: th.getAttribute('width') || th.style.width || '',
            inlineStyleW: th.style.width || '',
            w: Math.round(r.width),
            l: Math.round(r.left),
            r: Math.round(r.right),
            stickyLeft: cs.left,
            position: cs.position,
            classes: th.className,
        };
    });

    // first row tds
    const tr0 = tab.querySelector('tbody tr');
    if (tr0) {
        const tds = Array.from(tr0.querySelectorAll('td'));
        out.firstRowTds = tds.map(td => {
            const cs = getComputedStyle(td);
            const r = td.getBoundingClientRect();
            return {
                col: td.getAttribute('data-col'),
                text: (td.innerText || '').trim().slice(0, 12),
                display: cs.display,
                w: Math.round(r.width),
                l: Math.round(r.left),
                r: Math.round(r.right),
                stickyLeft: cs.left,
                position: cs.position,
                classes: td.className,
            };
        });
    }

    // sticky-left check
    out.stickyLeftThs = ths.filter(th => /sticky-left/.test(th.className)).length;
    out.stickyLeftTds = tr0 ? Array.from(tr0.querySelectorAll('td')).filter(td => /sticky-left/.test(td.className)).length : 0;
    out.tableMinWidth = getComputedStyle(tab).minWidth;
    out.tableStyleMinWidth = tab.style.minWidth;

    // topbar / main
    const tbEl = document.querySelector('header.topbar, .topbar');
    if (tbEl) {
        const r = tbEl.getBoundingClientRect();
        out.topbar = {h: Math.round(r.height), bottom: Math.round(r.bottom)};
    }
    const main = document.querySelector('main');
    if (main) {
        const r = main.getBoundingClientRect();
        out.main = {l: Math.round(r.left), t: Math.round(r.top), w: Math.round(r.width)};
    }

    return out;
}
            """
        )
        await page.screenshot(path="/tmp/diag_all_stocks_full.png", full_page=False)
        # Screenshot just the table region
        try:
            tab_box = await page.evaluate("() => { const t = document.querySelector('.view-all_stocks .stocks-table'); if(!t) return null; const r = t.getBoundingClientRect(); return {x: r.left, y: r.top, width: r.width, height: Math.min(r.height, 600)}; }")
            if tab_box:
                await page.screenshot(
                    path="/tmp/diag_all_stocks_table.png",
                    clip={"x": max(0, tab_box["x"]), "y": tab_box["y"], "width": min(390, tab_box["width"] + 50), "height": min(600, tab_box["height"])},
                )
        except Exception as e:
            print(f"clip screenshot err: {e}", file=sys.stderr)
        diag["console_errors"] = [(t, x[:200]) for t, x in console_msgs if t == "error"][:10]
        print(json.dumps(diag, indent=2, ensure_ascii=False))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
