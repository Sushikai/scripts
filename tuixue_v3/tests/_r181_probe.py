"""R181 probe: filter-bar + sector-bar + card-head vertical waste."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  var filterBar = document.querySelector('.view-bv .bv-filter-bar');
  var sectorBar = document.querySelector('.view-bv .bv-sector-bar');
  var filterBarCS = filterBar ? getComputedStyle(filterBar) : null;
  var sectorBarCS = sectorBar ? getComputedStyle(sectorBar) : null;
  var filterChip = document.querySelector('.view-bv .bv-filter-bar .bv-filter-chip');
  var sectorPill = document.querySelector('.view-bv .bv-sector-bar .bv-sector-pill');
  return {
    filterBar: filterBar ? r(filterBar) : null,
    filterBarPad: filterBarCS ? {pt:filterBarCS.paddingTop, pb:filterBarCS.paddingBottom, mt:filterBarCS.marginTop, mb:filterBarCS.marginBottom} : null,
    sectorBar: sectorBar ? r(sectorBar) : null,
    sectorBarPad: sectorBarCS ? {pt:sectorBarCS.paddingTop, pb:sectorBarCS.paddingBottom, mt:sectorBarCS.marginTop, mb:sectorBarCS.marginBottom} : null,
    filterChip: filterChip ? r(filterChip) : null,
    sectorPill: sectorPill ? r(sectorPill) : null,
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
        print(f"filterBar h={d['filterBar']['h']} top={d['filterBar']['t']}")
        print(f"  pad: {d['filterBarPad']}")
        print(f"  chip h={d['filterChip']['h']}")
        print(f"sectorBar h={d['sectorBar']['h']} top={d['sectorBar']['t']}")
        print(f"  pad: {d['sectorBarPad']}")
        print(f"  pill h={d['sectorPill']['h']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
