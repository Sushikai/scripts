"""R203 probe5: parse change cell exact box-model"""
import asyncio
from playwright.async_api import async_playwright

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

PROBE = r"""() => {
  var ch = document.querySelector('.view-bv .bv-table tr.bv-row td.bv-pos');
  if(!ch) return null;
  var cs = getComputedStyle(ch);
  // Get line metrics via Range API
  var range = document.createRange();
  range.selectNodeContents(ch);
  var rects = range.getClientRects();
  var rb = range.getBoundingClientRect();
  // Also: outerHTML
  return {
    h: ch.getBoundingClientRect().height,
    classes: ch.className,
    fs: cs.fontSize, lh: cs.lineHeight, fw: cs.fontWeight,
    pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight,
    mt: cs.marginTop, mb: cs.marginBottom, ml: cs.marginLeft, mr: cs.marginRight,
    bd: cs.border, bg: cs.background.slice(0, 80),
    display: cs.display, position: cs.position,
    range: { h: rb.height, w: rb.width },
    rectCount: rects.length,
    text: ch.textContent
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        for k, v in d.items():
            print(f"  {k}: {v}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())