"""R245 prep: 探针 col1 (code 格) 内容宽度构成 — 找可回收空间

第一性原理: 修 turnover 裁剪需要给 col2 更多 1fr 预算. col1 是 auto 列,
  由内容撑宽. 本探针量 col1 内 code-link / board-badge / crown 各自宽度,
  判断哪里能收.
"""
import asyncio, json
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
  var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
  var out = [];
  for (var i=0; i<Math.min(4, rows.length); i++) {
    var codeTd = rows[i].querySelector('td:nth-child(1)');
    if (!codeTd) continue;
    var r = codeTd.getBoundingClientRect();
    var items = [];
    codeTd.querySelectorAll('a, span').forEach(function(el){
      var er = el.getBoundingClientRect();
      items.push({tag: el.tagName, cls: (el.className||'').slice(0,22),
                  text: (el.textContent||'').trim().slice(0,12),
                  w: Math.round(er.width), h: Math.round(er.height),
                  left: Math.round(er.left - r.left)});
    });
    out.push({codeTdW: Math.round(r.width), scrollW: codeTd.scrollWidth, items: items});
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
        for row in d:
            print(f"codeTdW={row['codeTdW']} scrollW={row['scrollW']}")
            for it in row['items']:
                print(f"   <{it['tag']}> {it['cls']:<22} '{it['text']}' w={it['w']} left={it['left']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
