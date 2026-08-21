"""R246 prep: 探针 sector 格 (col1 track row2) 在 R245 col1 收窄后的裁剪状态

第一性原理: R245 把 board-badge 移出 col1 后, col1 auto track 从 72→45px.
  sector (grid-area:sector) 与 code 共享 col1 track, 也跟着只剩 45px.
  45px 只能装 ~4 个中文字 (10.5px×4=42). 板块名如 "医药生物"/"消费电子"
  可能被截. 全行 dump sector scrollW vs clientW.
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
    var td = row.querySelector('td:nth-child(3)');  // sector
    if (!td) continue;
    var r = td.getBoundingClientRect();
    var rng = document.createRange();
    rng.selectNodeContents(td);
    var txt = (td.textContent||'').trim();
    out.push({i: i, text: txt.slice(0,10), clientW: Math.round(r.width),
              scrollW: td.scrollWidth, clipped: td.scrollWidth > Math.round(r.width) + 1,
              chgVisible: (() => { var c = td.querySelector('.bv-sector-chg'); if(!c) return null; var cr = c.getBoundingClientRect(); return {right: Math.round(cr.right - r.left), w: Math.round(cr.width), clipped: cr.right > r.right + 0.5}; })()});
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
            chg = r['chgVisible']
            chgTxt = f" chgRight={chg['right']}/{r['clientW']} w={chg['w']} {'CLIP' if chg['clipped'] else 'ok'}" if chg else " noChg"
            print(f"r{r['i']}: '{r['text']}' clientW={r['clientW']} scrollW={r['scrollW']} {'CLIP' if r['clipped'] else 'ok'}{chgTxt}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
