"""R245: 探针 规则行 (row3) 标签 — board/motto/hit/chips 是否完整可见

第一性原理: badge+motto 移到规则行后, row3 是唯一容纳所有标签的地方.
  需确认:
    1. board-badge 完整可见 (flex 子项不被挤)
    2. motto-badge 完整可见
    3. hit-badge + chips 整体 scrollW vs 180px 上限 — 是否横向滚动
  数据信号 (成交额/量比) 已不裁剪, 现在标签也不能藏.
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
  for (var i=0; i<Math.min(4, rows.length); i++) {
    var row = rows[i];
    var rr = row.getBoundingClientRect();
    var rc = row.querySelector('.bv-rules-cell');
    var cr = rc.getBoundingClientRect();
    var board = rc.querySelector('.bv-board-badge');
    var motto = rc.querySelector('.bv-motto-badge');
    var hit = rc.querySelector('.bv-hit-badge');
    function vis(el){
      if (!el) return null;
      var r = el.getBoundingClientRect();
      return {text: (el.textContent||'').trim(), x: Math.round(r.left - rr.left),
              right: Math.round(r.right - rr.left), w: Math.round(r.width),
              clipped: (r.right > cr.right + 0.5 || r.left < cr.left - 0.5)};
    }
    out.push({i: i,
      rulesCellW: Math.round(cr.width), scrollW: rc.scrollWidth,
      board: vis(board), motto: vis(motto), hit: vis(hit),
      chipCount: rc.querySelectorAll('.chip').length});
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
            bd = f"board='{r['board']['text'] if r['board'] else None}' {'CLIP' if r['board'] and r['board']['clipped'] else 'ok'}"
            md = f"motto='{r['motto']['text'] if r['motto'] else None}' {'CLIP' if r['motto'] and r['motto']['clipped'] else 'ok'}"
            hd = f"hit='{r['hit']['text'] if r['hit'] else None}' {'CLIP' if r['hit'] and r['hit']['clipped'] else 'ok'}"
            print(f"r{r['i']}: cellW={r['rulesCellW']} scrollW={r['scrollW']} chips={r['chipCount']} {bd} {md} {hd}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
