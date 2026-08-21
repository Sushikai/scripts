"""R178 探针: 卡片之间垂直空间浪费在哪.
- 卡内 padding (top/bottom)
- 卡间 gap (flex gap or margin)
- 卡片行高 (R177 已压到 93px)
- 首屏可见卡数 / 第一屏滚动后浪费多少空间"""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  var cards = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
  var rects = cards.slice(0, 8).map(function(tr){
    var r1 = r(tr);
    var cs = getComputedStyle(tr);
    var inner = tr.querySelector('td.bv-grid, td:nth-child(2)');
    return {
      code: tr.dataset.code,
      h: r1.h,
      t: r1.t,
      b: r1.b,
      mt: cs.marginTop,
      mb: cs.marginBottom,
      pt: cs.paddingTop,
      pb: cs.paddingBottom,
      gap: getComputedStyle(tr.parentElement).gap,
      gapRow: getComputedStyle(tr.parentElement).rowGap
    };
  });
  // pick-card padding-top (整个 card 的内边距, R77+ 测过)
  var pickCard = document.querySelector('.view-bv > .bv-pick-card');
  var pcs = pickCard ? getComputedStyle(pickCard) : null;
  return {
    cards: rects,
    pickCard: pickCard ? {
      h: pickCard.getBoundingClientRect().height,
      pt: pcs.paddingTop, pb: pcs.paddingBottom,
      gap: pcs.gap, rowGap: pcs.rowGap,
      bdr: pcs.borderRadius
    } : null,
    container: cards.length ? {
      tag: cards[0].parentElement.tagName,
      gap: getComputedStyle(cards[0].parentElement).gap,
      rowGap: getComputedStyle(cards[0].parentElement).rowGap
    } : null
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
        print(f"container gap={d['container']['gap']} rowGap={d['container']['rowGap']}")
        print(f"pickCard pt={d['pickCard']['pt']} pb={d['pickCard']['pb']} gap={d['pickCard']['gap']} bdr={d['pickCard']['bdr']}")
        for c in d['cards']:
            print(f"  {c['code']}  h={c['h']}  t={c['t']}->{c['b']}  mt={c['mt']} mb={c['mb']}")
        if len(d['cards']) >= 2:
            for i in range(1, min(4, len(d['cards']))):
                gap = d['cards'][i]['t'] - d['cards'][i-1]['b']
                print(f"  gap[{i-1}->{i}] = {gap}px")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())