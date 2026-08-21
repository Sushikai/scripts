"""R245 prep: 探针 name 格 (td2) + change 格 (td4) 真实内容宽度 — 找 col2 可用余量

第一性原理: turnover 在 col2 (73px) 内无法容纳 3 信号. name 也共享 col2 (73px).
  若 name 内容实际只占 ~50px (短股票名), 剩余 20px 就是给 turnover 的余量 —
  说明 col2 足够, 问题在 nowrap 把 3 信号挤单行. 本探针量 name/change 内容宽.
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
    var nameTd = rows[i].querySelector('td:nth-child(2)');
    var chgTd = rows[i].querySelector('td:nth-child(4)');
    var out2 = {i: i};
    if (nameTd) {
      var nr = nameTd.getBoundingClientRect();
      out2.name = {text: (nameTd.textContent||'').trim(), w: Math.round(nr.width),
                   scrollW: nameTd.scrollWidth, clientW: Math.round(nr.width)};
    }
    if (chgTd) {
      var cr = chgTd.getBoundingClientRect();
      out2.change = {text: (chgTd.textContent||'').trim(), w: Math.round(cr.width), scrollW: chgTd.scrollWidth};
    }
    out.push(out2);
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
            if 'name' in row:
                print(f"r{row['i']}: name '{row['name']['text']}' w={row['name']['w']} scrollW={row['name']['scrollW']}")
            if 'change' in row:
                print(f"      change '{row['change']['text']}' w={row['change']['w']} scrollW={row['change']['scrollW']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
