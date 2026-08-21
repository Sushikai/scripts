"""R178 debug: 验证 pickCard padding 实际值."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  var pickCard = document.querySelector('.view-bv > .bv-pick-card');
  if (!pickCard) return null;
  var cs = getComputedStyle(pickCard);
  return {
    pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight,
    fullPad: cs.padding,
    cls: pickCard.className,
    rules: window.getMatchedCSSRules ? 'old-api' : 'no-rules-api',
    // 列出 pickCard 的祖先 .card / .view-bv 看 cascade
    ancestors: (function(){
      var arr = [], el = pickCard;
      while (el && el !== document.body) {
        var s = getComputedStyle(el);
        arr.push({tag: el.tagName, cls: (el.className||'').toString().slice(0,30), pt: s.paddingTop, pl: s.paddingLeft});
        el = el.parentElement;
      }
      return arr;
    })()
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
        print(f"pickCard: pt={d['pt']} pb={d['pb']} pl={d['pl']} pr={d['pr']} fullPad={d['fullPad']}")
        print(f"cls={d['cls']}")
        for a in d['ancestors']:
            print(f"  {a['tag']} '{a['cls']}'  pt={a['pt']} pl={a['pl']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())