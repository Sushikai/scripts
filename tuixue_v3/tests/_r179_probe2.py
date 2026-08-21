"""R179 探针 2: thead/padding 浪费."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  var thead = document.querySelector('#bv-pick-table thead');
  var th = document.querySelector('#bv-pick-table thead tr');
  var thcs = th ? getComputedStyle(th) : null;
  var firstBodyRow = document.querySelector('#bv-pick-tbody tr.bv-row');
  // table wrap top
  var wrap = document.querySelector('.bv-table-wrap');
  return {
    thead: thead ? r(thead) : null,
    th: th ? r(th) : null,
    thFs: thcs ? thcs.fontSize : null,
    thPadding: thcs ? {pt: thcs.paddingTop, pb: thcs.paddingBottom} : null,
    firstBodyRow: firstBodyRow ? r(firstBodyRow) : null,
    wrapTop: wrap ? r(wrap).t : null,
    gapBetweenThAndFirstRow: (th && firstBodyRow) ? (r(firstBodyRow).t - r(th).b) : null
  };
}"""

async def load(page):
    for _ in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    for _ in range(20):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
            break
    await page.wait_for_timeout(500)

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"thead h={d['thead']['h']} top={d['thead']['t']}")
        print(f"th h={d['th']['h']} top={d['th']['t']} fs={d['thFs']} pt={d['thPadding']['pt']} pb={d['thPadding']['pb']}")
        print(f"firstBodyRow h={d['firstBodyRow']['h']} top={d['firstBodyRow']['t']}")
        print(f"wrap top={d['wrapTop']}")
        print(f"gap th_b->body_t = {d['gapBetweenThAndFirstRow']}px")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())