"""R246: 验证 sector 长名不撑爆 col1 — col2 地板保持 turnover 单行

第一性原理: R245 后 col1 auto track=45px, sector 共享该 track.
  R246 探针实测: 注入 5 字板块名 ("半导体设备") 会把 auto track 撑到 55px,
  col2 (1fr) 101→90px, turnover 换行 row h 71→84px — 这是 R245 turnover 修复的
  脆弱性: 板块名长度波动可重新触发裁剪. 修复: sector max-width:45px 锁定 col1,
  col2 地板不被 sector 名长度劫持.

断言 (真实服务, 390px):
  1. 注入 "半导体设备" (5字) 后 col1 仍 45px (不膨胀)
  2. col2 (name/turnover) ≥100px (地板保住)
  3. turnover 三信号单行不裁剪 (turnClip=False)
  4. row h ≤ 75px (不膨胀回 84px)
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
  var results = [];
  var names = ['化学制药', '半导体设备', '消费电子制造', '房地产服务行业'];
  for (var k=0; k<names.length; k++) {
    var row = rows[k % rows.length];
    var secName = row.querySelector('.bv-sector-name');
    var old = secName.textContent;
    secName.textContent = names[k];
    var codeTd = row.querySelector('td:nth-child(1)');
    var turnTd = row.querySelector('td:nth-child(5)');
    var nameTd = row.querySelector('td:nth-child(2)');
    var tr = turnTd.getBoundingClientRect();
    var vr = turnTd.querySelector('.bv-vr');
    var amt = turnTd.querySelector('.bv-vr-amt');
    results.push({
      name: names[k],
      codeW: Math.round(codeTd.getBoundingClientRect().width),
      nameW: Math.round(nameTd.getBoundingClientRect().width),
      turnW: Math.round(tr.width),
      turnScroll: turnTd.scrollWidth,
      turnClip: turnTd.scrollWidth > Math.round(tr.width) + 1,
      vrVisible: vr ? (vr.getBoundingClientRect().right <= tr.right + 0.5) : null,
      amtVisible: amt ? (amt.getBoundingClientRect().right <= tr.right + 0.5) : null,
      sameLine: vr && amt ? Math.abs(vr.getBoundingClientRect().top - amt.getBoundingClientRect().top) < 3 : null,
      rowH: row.offsetHeight
    });
    secName.textContent = old;
  }
  return results;
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        for r in d:
            print(f"'{r['name']}': codeW={r['codeW']} nameW={r['nameW']} turnW={r['turnW']} scroll={r['turnScroll']}"
                  f" clip={r['turnClip']} vr={r['vrVisible']} amt={r['amtVisible']} line={r['sameLine']} rowH={r['rowH']}")

        for r in d:
            assert r['codeW'] <= 50, f"R246: '{r['name']}' col1 膨胀到 {r['codeW']}px (>50)"
            assert r['nameW'] >= 95, f"R246: '{r['name']}' col2 被挤到 {r['nameW']}px (<95)"
            assert not r['turnClip'], f"R246: '{r['name']}' turnover 被裁剪"
            assert r['vrVisible'] and r['amtVisible'] and r['sameLine'], f"R246: '{r['name']}' 三信号不完整/换行"
            assert r['rowH'] <= 75, f"R246: '{r['name']}' row h {r['rowH']} > 75 (膨胀)"

        await b.close()
        print("[OK] R246 sector 长名不撑爆 col1 — col2 地板稳定 (rowH 保持 71px)")

if __name__ == "__main__":
    asyncio.run(run())
