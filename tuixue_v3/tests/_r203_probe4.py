"""R203 probe4: compare row1 cells (code/name/change/seal/btn) - find which drives row height"""
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
  // Pick children with gridArea = code|name|change|seal|btn
  var targets = ['code', 'name', 'change', 'seal', 'btn'];
  var out = {};
  for (var c of row.children) {
    var cs = getComputedStyle(c);
    var ga = cs.gridArea.split(' ')[0];  // first = row-start
    if (targets.includes(ga)) {
      var rect = c.getBoundingClientRect();
      // Walk children
      var childs = [];
      for (var cc of c.children) {
        var ccs = getComputedStyle(cc);
        var cr = cc.getBoundingClientRect();
        childs.push({
          tag: cc.tagName, cls: cc.className,
          h: Math.round(cr.height*10)/10,
          fs: ccs.fontSize, lh: ccs.lineHeight,
          mt: ccs.marginTop, mb: ccs.marginBottom, pt: ccs.paddingTop, pb: ccs.paddingBottom
        });
      }
      out[ga] = {
        h: Math.round(rect.height*10)/10,
        fs: cs.fontSize, lh: cs.lineHeight,
        mt: cs.marginTop, mb: cs.marginBottom, pt: cs.paddingTop, pb: cs.paddingBottom,
        display: cs.display, alignItems: cs.alignItems,
        txt: (c.textContent||'').trim().slice(0, 30),
        childs: childs
      };
    }
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
        for k in ['code', 'name', 'change', 'seal', 'btn']:
            c = d.get(k)
            if not c: continue
            print(f"[{k}] h={c['h']} fs={c['fs']} lh={c['lh']} mt={c['mt']} mb={c['mb']} txt='{c['txt']}'")
            for cc in c['childs']:
                print(f"     <{cc['tag']} .{cc['cls']}> h={cc['h']} fs={cc['fs']} lh={cc['lh']} mt={cc['mt']} mb={cc['mb']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())