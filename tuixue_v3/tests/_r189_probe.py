"""R189 probe: view-head 内部细节 + 其他可压缩点."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function r(el){ if(!el) return null; var x=el.getBoundingClientRect(); return {t:Math.round(x.top),b:Math.round(x.bottom),h:Math.round(x.height),w:Math.round(x.width)}; }
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = r(el);
    return {h: rect.h, w: rect.w, fs: cs.fontSize, lh: cs.lineHeight, mt: cs.marginTop, mb: cs.marginBottom, pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight};
  }
  var viewHead = document.querySelector('.view-bv .view-head');
  var vhFirst = viewHead ? viewHead.querySelector('div:first-child') : null;
  var refreshBtn = document.querySelector('.view-bv .view-head .btn-refresh');
  var vhActions = document.querySelector('.view-bv .view-head .view-actions');
  // view-head 子元素
  var vhChildren = viewHead ? Array.from(viewHead.children).map(info) : [];
  return {
    viewHead: info(viewHead),
    vhFirst: info(vhFirst),
    refreshBtn: info(refreshBtn),
    vhActions: info(vhActions),
    vhChildren: vhChildren
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
        for k,v in d.items():
            if isinstance(v, list):
                print(f"{k}:")
                for i, c in enumerate(v):
                    print(f"  [{i}]: h={c['h']} w={c['w']} fs={c['fs']} mb={c.get('mb')} mt={c.get('mt')}")
            else:
                print(f"{k}: h={v['h']} w={v['w']} fs={v.get('fs')} mb={v.get('mb')} mt={v.get('mt')} pl={v.get('pl')} pr={v.get('pr')}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
