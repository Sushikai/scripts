"""R246 prep: 证明 R245 的脆弱性 — 注入 5 字板块名 看 col1 是否膨胀压垮 col2

第一性原理: R245 后 col1 auto track=45px (code-link 文本宽). sector 共享 col1 track.
  当前 4 字板块名 ("化学制药" 42px) 刚好 ≤45px 不膨胀. 但真实板块 "半导体设备"
  (5 字 ≈52px) 会把 auto track 撑到 52px → col2 (1fr) 101→94px → turnover
  (101px 内容) 再次裁剪. 探针直接改 sector 文本, 量 col1/col2/裁剪.
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
  var row = document.querySelector('#bv-pick-tbody tr.bv-row');
  function measure() {
    var codeTd = row.querySelector('td:nth-child(1)');
    var secTd = row.querySelector('td:nth-child(3)');
    var turnTd = row.querySelector('td:nth-child(5)');
    var nameTd = row.querySelector('td:nth-child(2)');
    return {
      codeW: Math.round(codeTd.getBoundingClientRect().width),
      sectorW: Math.round(secTd.getBoundingClientRect().width),
      nameW: Math.round(nameTd.getBoundingClientRect().width),
      turnW: Math.round(turnTd.getBoundingClientRect().width),
      turnScroll: turnTd.scrollWidth,
      turnClip: turnTd.scrollWidth > Math.round(turnTd.getBoundingClientRect().width) + 1,
      rowH: row.offsetHeight
    };
  }
  var before = measure();
  // 注入 5 字板块名
  var secName = row.querySelector('.bv-sector-name');
  secName.textContent = '半导体设备';
  var after = measure();
  return {before: before, after: after};
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        for label, m in d.items():
            print(f"{label}: codeW={m['codeW']} sectorW={m['sectorW']} nameW={m['nameW']} turnW={m['turnW']} turnScroll={m['turnScroll']} turnClip={m['turnClip']} rowH={m['rowH']}")
        await b.close()

if __name__ == "__main__":
    asyncio.run(run())
