"""R178 探针 2: pickCard 内部垂直空间分配."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  var pickCard = document.querySelector('.view-bv > .bv-pick-card');
  if (!pickCard) return null;
  var head = pickCard.querySelector('.bv-pick-head, .card-head, .card-eyebrow');
  var tableWrap = pickCard.querySelector('.bv-pick-table-wrap, .bv-table-wrap, .pick-table-wrap, table');
  var headRect = r(head);
  var wrapRect = r(tableWrap);
  var cardRect = r(pickCard);
  var headCs = head ? getComputedStyle(head) : null;
  return {
    card: cardRect,
    head: headRect,
    headText: head ? head.textContent.replace(/\s+/g,' ').trim().slice(0,60) : '',
    headFs: headCs ? headCs.fontSize : null,
    headMb: headCs ? headCs.marginBottom : null,
    headPb: headCs ? headCs.paddingBottom : null,
    wrap: wrapRect,
    wrapMt: tableWrap ? getComputedStyle(tableWrap).marginTop : null,
    wrapMb: tableWrap ? getComputedStyle(tableWrap).marginBottom : null
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
        print(f"card h={d['card']['h']}")
        print(f"head h={d['head']['h'] if d['head'] else 'n/a'}  text='{d['headText']}'  fs={d['headFs']}  mb={d['headMb']}  pb={d['headPb']}")
        print(f"wrap h={d['wrap']['h'] if d['wrap'] else 'n/a'}  mt={d['wrapMt']}  mb={d['wrapMb']}")
        if d['head'] and d['wrap']:
            gap = d['wrap']['t'] - d['head']['b']
            print(f"head->wrap gap = {gap}px")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())