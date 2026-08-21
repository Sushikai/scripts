"""R245: 探针 所有 grid 列宽 + code/name/motto 真实裁剪状态

第一性原理: 徽章移到规则行后 col1 从 72→45px (负 margin 让 grid auto track 按
  max-content 收缩). 需确认:
    1. code-link 57px box 在 45px td 内 — 代码文本 "600613" 是否被截
    2. motto-badge 在 name td (overflow:hidden) 内是否被截
  只有量出 scrollWidth vs clientWidth + 文本 bbox 才能判定真裁剪.
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
  var cols = [];
  row.querySelectorAll('td').forEach(function(td, i){
    var r = td.getBoundingClientRect();
    cols.push({i: i+1, x: Math.round(r.left - rr.left), w: Math.round(r.width),
               scrollW: td.scrollWidth, clientW: td.clientWidth});
  });
  // code 文本 bbox
  var codeA = row.querySelector('td:nth-child(1) a.code-link');
  var codeTd = row.querySelector('td:nth-child(1)');
  var cr = codeTd.getBoundingClientRect();
  var ar = codeA.getBoundingClientRect();
  // 文本节点范围: 用 Range 量 "600613" 真实宽
  var rng = document.createRange();
  rng.selectNodeContents(codeA);
  var textRect = rng.getBoundingClientRect();
  // motto badge
  var mb = row.querySelector('.bv-motto-badge');
  var mbR = mb ? mb.getBoundingClientRect() : null;
  var nameTd = row.querySelector('td:nth-child(2)');
  var nR = nameTd.getBoundingClientRect();
  return {
    rowH: Math.round(rr.height),
    cols: cols,
    code: {linkBox: Math.round(ar.width), tdW: Math.round(cr.width),
           textRight: Math.round(textRect.right - rr.left), tdRight: Math.round(cr.right - rr.left),
           textW: Math.round(textRect.width)},
    name: {tdW: Math.round(nR.width), scrollW: nameTd.scrollWidth},
    motto: mb ? {x: Math.round(mbR.left - rr.left), right: Math.round(mbR.right - rr.left), w: Math.round(mbR.width), tdRight: Math.round(nR.right - rr.left)} : null
  };
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        print(f"rowH={d['rowH']}")
        for c in d['cols']:
            print(f"  col{c['i']}: x={c['x']} w={c['w']} scrollW={c['scrollW']} clientW={c['clientW']} clipped={c['scrollW'] > c['clientW'] + 1}")
        print(f"  code: linkBox={d['code']['linkBox']} tdW={d['code']['tdW']} textRight={d['code']['textRight']} tdRight={d['code']['tdRight']} textW={d['code']['textW']}")
        if d['motto']:
            print(f"  motto: x={d['motto']['x']} right={d['motto']['right']} tdRight={d['motto']['tdRight']} w={d['motto']['w']} clipped={d['motto']['right'] > d['motto']['tdRight'] + 0.5}")
        print(f"  name: tdW={d['name']['tdW']} scrollW={d['name']['scrollW']} clipped={d['name']['scrollW'] > d['name']['tdW'] + 1}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
