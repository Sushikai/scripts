"""R190 probe: sector-pill 内部子元素 + 内边距微观."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = el.getBoundingClientRect();
    return {h: Math.round(rect.height), w: Math.round(rect.width), pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight, gap: cs.gap, fs: cs.fontSize, tag: el.tagName, txt: (el.textContent||'').slice(0,20).trim()};
  }
  var pill = document.querySelector('.view-bv .bv-sector-pill');
  if(!pill) return {err: 'no pill'};
  var kids = Array.from(pill.children).map(info);
  return {
    pill: info(pill),
    kids: kids,
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
        print(f"pill: h={d['pill']['h']} w={d['pill']['w']} pt={d['pill']['pt']} pb={d['pill']['pb']} pl={d['pill']['pl']} pr={d['pill']['pr']} gap={d['pill']['gap']}")
        print("kids:")
        for i, k in enumerate(d['kids']):
            print(f"  [{i}] <{k['tag']}> '{k['txt']}' h={k['h']} w={k['w']} pl={k['pl']} pr={k['pr']} fs={k['fs']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())