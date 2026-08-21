"""R203 probe3: deeply inspect change cell, find what makes it 30px"""
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
  var rect = ch.getBoundingClientRect();
  // Walk children
  var childInfo = [];
  for (var c of ch.children) {
    var ccs = getComputedStyle(c);
    var crect = c.getBoundingClientRect();
    childInfo.push({
      tag: c.tagName, cls: c.className,
      h: Math.round(crect.height*10)/10,
      fs: ccs.fontSize, lh: ccs.lineHeight, fw: ccs.fontWeight,
      mt: ccs.marginTop, mb: ccs.marginBottom, pt: ccs.paddingTop, pb: ccs.paddingBottom,
      display: ccs.display
    });
  }
  return {
    h: Math.round(rect.height*10)/10,
    fs: cs.fontSize, lh: cs.lineHeight, fw: cs.fontWeight,
    mt: cs.marginTop, mb: cs.marginBottom, pt: cs.paddingTop, pb: cs.paddingBottom,
    display: cs.display, alignItems: cs.alignItems,
    childInfo: childInfo
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"change cell: h={d['h']} fs={d['fs']} lh={d['lh']} fw={d['fw']}")
        print(f"             mt={d['mt']} mb={d['mb']} pt={d['pt']} pb={d['pb']}")
        print(f"             display={d['display']} alignItems={d['alignItems']}")
        print(f"  children:")
        for c in d['childInfo']:
            print(f"    <{c['tag']} .{c['cls']}> h={c['h']} fs={c['fs']} lh={c['lh']} fw={c['fw']}")
            print(f"      mt={c['mt']} mb={c['mb']} pt={c['pt']} pb={c['pb']} display={c['display']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())