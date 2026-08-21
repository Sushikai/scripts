"""R178 探针 3: 找出 82px head->wrap gap 的来源."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height)}; }
  var pickCard = document.querySelector('.view-bv > .bv-pick-card');
  if (!pickCard) return null;
  // 列出 pickCard 下所有直接子元素和它们的 rect+margin
  var children = Array.from(pickCard.children).map(function(el, idx){
    var cs = getComputedStyle(el);
    return {
      idx: idx,
      tag: el.tagName,
      cls: (el.className || '').toString().slice(0,40),
      h: Math.round(el.getBoundingClientRect().height),
      t: Math.round(el.getBoundingClientRect().top),
      b: Math.round(el.getBoundingClientRect().bottom),
      mt: cs.marginTop, mb: cs.marginBottom,
      pt: cs.paddingTop, pb: cs.paddingBottom
    };
  });
  return {card: r(pickCard), children: children};
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
        for c in d['children']:
            print(f"  [{c['idx']}] {c['tag']} '{c['cls']}'  h={c['h']}  t={c['t']}->{c['b']}  mt={c['mt']} mb={c['mb']}  pt={c['pt']} pb={c['pb']}")
        # gaps
        for i in range(1, len(d['children'])):
            g = d['children'][i]['t'] - d['children'][i-1]['b']
            print(f"  gap[{i-1}->{i}] = {g}px")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())