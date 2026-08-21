"""R187 probe 4: 找 sortBtn 内部 padding 紧凑点 — 18px text 在 12px+12px padding = 36+18=54? No sortBtn h=44."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height),w:Math.round(x.width)}; }
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = r(el);
    return {h: rect.h, w: rect.w, fs: cs.fontSize, lh: cs.lineHeight, pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight};
  }
  var pickCard = document.querySelector('.bv-pick-card');
  var head = document.querySelector('.bv-pick-card .card-head');
  var h3 = document.querySelector('.bv-pick-card .card-head h3');
  var count = document.querySelector('.bv-pick-card .card-head #bv-pick-count');
  var sortBtn = document.querySelector('.bv-pick-card .bv-sort-btn');
  return {
    pickCard: {h: pickCard.getBoundingClientRect().height, t: pickCard.getBoundingClientRect().top},
    head: {h: head.getBoundingClientRect().height, t: head.getBoundingClientRect().top},
    h3: {h: h3.getBoundingClientRect().height, t: h3.getBoundingClientRect().top},
    count: {h: count.getBoundingClientRect().height, t: count.getBoundingClientRect().top, b: count.getBoundingClientRect().bottom},
    sortBtn: {h: sortBtn.getBoundingClientRect().height, t: sortBtn.getBoundingClientRect().top, b: sortBtn.getBoundingClientRect().bottom},
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
            print(f"{k}: {v}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
