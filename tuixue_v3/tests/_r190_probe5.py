"""R190 probe: bv-title + sub 实际文字内容 + 容器关系."""
import asyncio
from playwright.async_api import async_playwright

PROBE = r"""() => {
  function info(el){
    if(!el) return null;
    var cs = getComputedStyle(el);
    var rect = el.getBoundingClientRect();
    return {h: Math.round(rect.height*10)/10, w: Math.round(rect.width*10)/10, mt: cs.marginTop, mb: cs.marginBottom, pt: cs.paddingTop, pb: cs.paddingBottom, pl: cs.paddingLeft, pr: cs.paddingRight, fs: cs.fontSize, lh: cs.lineHeight, ofw: cs.overflow, ofx: cs.overflowX, ws: cs.whiteSpace, txt: (el.textContent||'').trim().slice(0,40), disp: cs.display, cls: el.className.toString().slice(0,30)};
  }
  var viewHead = document.querySelector('.view-bv .view-head');
  var vhFirst = viewHead ? viewHead.querySelector('div:first-child') : null;
  var bvTitle = viewHead ? viewHead.querySelector('.bv-title, bv-title') : null;
  var sub = viewHead ? viewHead.querySelector('sub, .sub') : null;
  var meta = viewHead ? viewHead.querySelector('.bv-meta, bv-meta') : null;
  // 找出实际元素
  if(!bvTitle && vhFirst) bvTitle = vhFirst.querySelector('h1, .display, .title');
  if(!sub && vhFirst) sub = vhFirst.querySelector('sub, .caption');
  return {
    viewHead: info(viewHead),
    vhFirst: info(vhFirst),
    vhFirstHTML: vhFirst ? vhFirst.outerHTML.slice(0, 500) : null,
    bvTitle: info(bvTitle),
    sub: info(sub),
    meta: info(meta),
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
        for k, v in d.items():
            if v and isinstance(v, dict):
                print(f"{k}: h={v.get('h')} w={v.get('w')} mt={v.get('mt')} mb={v.get('mb')} pt={v.get('pt')} pb={v.get('pb')} pl={v.get('pl')} pr={v.get('pr')} fs={v.get('fs')} lh={v.get('lh')} ofw={v.get('ofw')} txt='{v.get('txt','')}' cls='{v.get('cls','')}'")
            elif v:
                print(f"{k}: {v}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())