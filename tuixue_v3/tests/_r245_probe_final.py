"""R245 prep: 全量 dump 首行 col1 + col2 内部布局 (含 y) — 决定 badge/turnover 终局

第一性原理: 314px 卡片列宽零和. col1(code+badge) 与 col2(name/turnover)
  互相抢空间. 必须知道每个元素的实际 (x,y,w) 才能判断:
    - badge 在哪行被剪 (y 与 code-link 同排还是下排?)
    - turnover 三信号如何换行 (哪几个在同 y 行)
 有了 y 才能把行成本说清, 决定 88px 是否诚实成本.
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
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
            break
        await page.wait_for_timeout(500)

PROBE = r"""() => {
  var row = document.querySelector('#bv-pick-tbody tr.bv-row');
  var rr = row.getBoundingClientRect();
  var cells = {};
  function dump(sel, label) {
    var td = row.querySelector(sel);
    if (!td) return;
    var tr = td.getBoundingClientRect();
    var items = [];
    function walk(el) {
      if (el.nodeType !== 1) return;
      var r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {
        items.push({tag: el.tagName, cls: (el.className||'').toString().slice(0,24),
                    text: (el.textContent||'').trim().slice(0,10),
                    x: Math.round(r.left - rr.left), y: Math.round(r.top - rr.top),
                    w: Math.round(r.width), h: Math.round(r.height)});
      }
      for (var i=0;i<el.children.length;i++) walk(el.children[i]);
    }
    walk(td);
    return {tdW: Math.round(tr.width), tdH: Math.round(tr.height), tdX: Math.round(tr.left - rr.left), items: items};
  }
  cells.code  = dump('td:nth-child(1)', 'code');
  cells.name  = dump('td:nth-child(2)', 'name');
  cells.turn  = dump('td:nth-child(5)', 'turnover');
  return {rowH: Math.round(rr.height), cells: cells};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"rowH={d['rowH']}")
        for name, c in d['cells'].items():
            print(f"\n[{name}] tdX={c['tdX']} tdW={c['tdW']} tdH={c['tdH']}")
            for it in c['items']:
                print(f"   {it['tag']:<6} {it['cls']:<24} '{it['text']}' x={it['x']} y={it['y']} w={it['w']} h={it['h']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
