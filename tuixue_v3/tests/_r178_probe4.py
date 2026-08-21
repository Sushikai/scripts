"""R178 探针 4: pickCard 真实 padding 边界."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),l:Math.round(x.left),r:Math.round(x.right),h:Math.round(x.height)}; }
  var pickCard = document.querySelector('.view-bv > .bv-pick-card');
  if (!pickCard) return null;
  var cs = getComputedStyle(pickCard);
  var firstChild = pickCard.children[0];  // card-head
  var firstChildRect = r(firstChild);
  var cardRect = r(pickCard);
  var tableWrap = pickCard.querySelector('.bv-table-wrap');
  var wrapRect = r(tableWrap);
  // padding-box 偏移 = card top + padding-top
  var contentTop = cardRect.t + parseFloat(cs.paddingTop);
  // 第一行
  var firstRow = document.querySelector('#bv-pick-tbody tr.bv-row');
  var firstRowRect = r(firstRow);
  return {
    card: {t: cardRect.t, b: cardRect.b, l: cardRect.l, r: cardRect.r, h: cardRect.h},
    cardPt: cs.paddingTop, cardPb: cs.paddingBottom,
    cardPl: cs.paddingLeft, cardPr: cs.paddingRight,
    cardGap: cs.gap,
    headTop: firstChildRect.t,
    expectedContentTop: contentTop,
    headActualGap: firstChildRect.t - contentTop,
    tableWrapTop: wrapRect.t,
    firstRowTop: firstRowRect.t
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
        print(f"card: t={d['card']['t']} b={d['card']['b']} h={d['card']['h']}")
        print(f"  padding: t={d['cardPt']} b={d['cardPb']} l={d['cardPl']} r={d['cardPr']}")
        print(f"  gap: {d['cardGap']}")
        print(f"head top={d['headTop']}, expected content-top={d['expectedContentTop']}, gap={d['headActualGap']}px")
        print(f"tableWrap top={d['tableWrapTop']}, firstRow top={d['firstRowTop']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())