"""R183 probe: view-head + phase banner layout."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = r(el);
    return {h: rect.h, t: rect.t, b: rect.b, mt: cs.marginTop, mb: cs.marginBottom, pt: cs.paddingTop, pb: cs.paddingBottom};
  }
  return {
    viewHead: info(document.querySelector('.view-bv > .view-head, .view-bv > .bv-view-head, .view-bv .bv-view-head')),
    pinned: info(document.querySelector('.bv-phase-banner.is-pinned')),
    phase: info(document.querySelector('.view-bv .bv-phase-banner')),
    strip: info(document.querySelector('.bv-stale-strip')),
    filterBar: info(document.querySelector('.view-bv .bv-filter-bar')),
    sectorBar: info(document.querySelector('.view-bv .bv-sector-bar')),
    pickCard: info(document.querySelector('.bv-pick-card')),
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
        for k,v in d.items():
            if v:
                print(f"{k}: h={v['h']} t={v['t']} mt={v['mt']} mb={v['mb']} pt={v['pt']} pb={v['pb']}")
            else:
                print(f"{k}: None")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
