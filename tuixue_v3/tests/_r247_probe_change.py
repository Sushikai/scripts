"""R247 prep: 探针 change 格 (row1 col3) 裁剪状态 — 涨幅文本是否被 max-width 截断

第一性原理: R246 row2 grid map 显示 change x=159 w=56 但 scrollW=61 —
  涨幅文本 "xx.xx%" 或 "涨停" 在 56px 处被截 5px. 涨幅是 row1 的
  关键决策信号 (红涨绿跌), 裁剪 = 信号损坏. 全行 dump change 格:
  clientW vs scrollW + 文本可见度 (涨跌停图标/百分比是否完整).
"""
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
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length >= 1"):
            break
        await page.wait_for_timeout(500)

PROBE = r"""() => {
  var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
  var out = [];
  for (var i=0; i<Math.min(10, rows.length); i++) {
    var row = rows[i];
    var td = row.querySelector('td:nth-child(4)');  // change
    if (!td) continue;
    var r = td.getBoundingClientRect();
    var txt = (td.textContent||'').trim();
    var rng = document.createRange();
    rng.selectNodeContents(td);
    var rects = rng.getClientRects();
    var scrollW = td.scrollWidth;
    var clipped = scrollW > Math.round(r.width) + 1;
    // 看每个子元素是否超出右边界
    var kids = [];
    td.querySelectorAll('*').forEach(function(k){
      if (k.children.length) return;
      var kr = k.getBoundingClientRect();
      kids.push({cls: (k.className||'').toString().slice(0,20), text: (k.textContent||'').slice(0,8),
                 right: Math.round(kr.right - r.left), w: Math.round(kr.width),
                 overflow: kr.right > r.right + 0.5});
    });
    out.push({i: i, text: txt, clientW: Math.round(r.width), scrollW: scrollW,
              clipped: clipped, kids: kids});
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
        for r in d:
            kstr = ' '.join([f"{k['cls']}:'{k['text']}'@{k['right']}/w{k['w']}{'!' if k['overflow'] else ''}" for k in r['kids']])
            print(f"r{r['i']}: '{r['text']}' clientW={r['clientW']} scrollW={r['scrollW']} {'CLIP' if r['clipped'] else 'ok'}")
            if kstr:
                print(f"    {kstr}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
