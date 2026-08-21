"""R203 probe2: identify what cell 3 is + measure its internals"""
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
  var row = document.querySelector('.view-bv .bv-table tr.bv-row');
  if(!row) return null;
  // Direct children
  var children = row.children;
  var out = [];
  for (var i = 0; i < children.length; i++) {
    var c = children[i];
    var rect = c.getBoundingClientRect();
    var cs = getComputedStyle(c);
    var gridArea = cs.gridArea;
    out.push({
      i: i,
      tag: c.tagName,
      cls: c.className,
      gridArea: gridArea,
      h: Math.round(rect.height*10)/10,
      w: Math.round(rect.width*10)/10,
      text: (c.textContent||'').trim().slice(0, 30),
      childrenCount: c.children.length,
      innerHTML: c.innerHTML.slice(0, 200)
    });
  }
  return out;
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        for c in d:
            print(f"[{c['i']}] {c['tag']} .{c['cls']} gridArea={c['gridArea']} h={c['h']} w={c['w']}")
            print(f"     text='{c['text']}'")
            print(f"     html={c['innerHTML'][:150]}")
            print()
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())