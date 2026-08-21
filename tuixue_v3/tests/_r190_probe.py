"""R190 probe: bv-row border-radius 候选评估 — bv-row 实际圆角感知度."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height),w:Math.round(x.width)}; }
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = r(el);
    return {h: rect.h, w: rect.w, br: cs.borderRadius, bdw: cs.borderTopWidth, bdc: cs.borderTopColor, bg: cs.backgroundColor};
  }
  var row = document.querySelector('.view-bv .bv-table tr.bv-row');
  var row2 = document.querySelectorAll('.view-bv .bv-table tr.bv-row')[2];
  var sortBtn = document.querySelector('.view-bv .bv-sort-btn');
  var pickHead = document.querySelector('.view-bv .bv-pick-card .card-head');
  var pickCard = document.querySelector('.view-bv .bv-pick-card');
  var filterChip = document.querySelector('.view-bv .bv-filter-chip');
  var filterBar = document.querySelector('.view-bv .bv-filter-bar');
  var sectorPill = document.querySelector('.view-bv .bv-sector-pill');
  return {
    row: info(row),
    row2: info(row2),
    sortBtn: info(sortBtn),
    pickHead: info(pickHead),
    pickCard: info(pickCard),
    filterChip: info(filterChip),
    filterBar: info(filterBar),
    sectorPill: info(sectorPill),
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
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 3:
            break
    await page.wait_for_timeout(500)

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        for k, v in d.items():
            if v:
                print(f"{k}: h={v['h']} w={v['w']} br={v['br']} bdw={v['bdw']} bdc={v['bdc']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())