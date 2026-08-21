"""R187 probe 2: pickHead 子元素 — sortBtn / h3 / count 细节."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = r(el);
    return {h: rect.h, t: rect.t, b: rect.b, fs: cs.fontSize, lh: cs.lineHeight, mt: cs.marginTop, mb: cs.marginBottom, pt: cs.paddingTop, pb: cs.paddingBottom, gap: cs.gap};
  }
  var pickCard = document.querySelector('.bv-pick-card');
  var pickHead = document.querySelector('.bv-pick-card .card-head');
  var sortBtn = document.querySelector('.bv-pick-card .bv-sort-btn');
  var count = document.querySelector('.bv-pick-card .card-head #bv-pick-count');
  var h3 = document.querySelector('.bv-pick-card .card-head h3');
  return {
    pickCard: info(pickCard),
    pickHead: info(pickHead),
    h3: info(h3),
    count: info(count),
    sortBtn: info(sortBtn),
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
                print(f"{k}: h={v['h']} t={v['t']} fs={v['fs']} lh={v['lh']} pt={v.get('pt')} pb={v.get('pb')} gap={v.get('gap')}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
