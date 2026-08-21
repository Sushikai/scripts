"""R245: 验证 turnover 格量比/成交额不再裁剪 + board badge 完整可见

第一性原理: R243 探针抓出 turnover 格 clientW=73 scrollW=117 硬裁剪
  (nowrap+overflow:hidden 把 R78/R79 加的量比/成交额切掉). 根因 grid col2
  只有 73px, 而 col1 (code) 被 board-badge 撑到 72px 上限还没装下 badge
  (badge 自身也被裁剪). 修复 (R245):
    1. board-badge 从 col1 移到规则行 (row3) — 静态股票属性不占身份列稀缺宽度
    2. motto-badge 也从 name 格移到规则行, 且只给 top-1 (全行同质时 8× 重复是噪声)
    3. turnover 去 nowrap+overflow — 换手率+量比+成交额 三信号单行完整可见
  结果: col2 (1fr) 101px, 三信号单行, row h 71px (比 75px 基线还紧)

断言 (真实服务, 390px):
  1. turnover td: 量比/成交额 span 完整可见 (right <= td right)
  2. turnover 三信号单行: 量比与成交额同 y (不再换行)
  3. board-badge 在规则行完整可见 (不再被 col1 72px 裁剪)
  4. motto-badge 只出现在 top-1 行 (决策锚点)
  5. name 格不被裁剪 (scrollW <= clientW + 1)
  6. bv-row h <= 75px (不膨胀)
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
  var out = {rows: [], rowH: rows[0] ? rows[0].offsetHeight : null};
  var n = Math.min(5, rows.length);
  for (var i=0; i<n; i++) {
    var row = rows[i];
    var rr = row.getBoundingClientRect();
    var td = row.querySelector('td:nth-child(5)');
    var tr = td.getBoundingClientRect();
    var vr = td.querySelector('.bv-vr');
    var amt = td.querySelector('.bv-vr-amt');
    var rulesCell = row.querySelector('.bv-rules-cell');
    var rc = rulesCell.getBoundingClientRect();
    var badge = rulesCell.querySelector('.bv-board-badge');
    var badgeR = badge ? badge.getBoundingClientRect() : null;
    var motto = rulesCell.querySelector('.bv-motto-badge');
    var mottoR = motto ? motto.getBoundingClientRect() : null;
    var nameTd = row.querySelector('td:nth-child(2)');
    var nr = nameTd.getBoundingClientRect();
    var item = {
      i: i,
      turnoverClipped: td.scrollWidth > Math.round(tr.width) + 1,
      clientW: Math.round(tr.width), scrollW: td.scrollWidth,
      vrVisible: vr ? (vr.getBoundingClientRect().right <= tr.right + 0.5) : null,
      amtVisible: amt ? (amt.getBoundingClientRect().right <= tr.right + 0.5) : null,
      sameLine: vr && amt ? Math.abs(vr.getBoundingClientRect().top - amt.getBoundingClientRect().top) < 3 : null,
      badgeClipped: badgeR ? (badgeR.right > rc.right + 0.5 || badgeR.left < rc.left - 0.5) : null,
      badgeText: badgeR ? (badge.textContent||'').trim() : null,
      mottoText: motto ? (motto.textContent||'').trim() : null,
      nameClipped: nameTd.scrollWidth > Math.round(nr.width) + 1,
      nameScrollW: nameTd.scrollWidth, nameClientW: Math.round(nr.width)
    };
    out.rows.push(item);
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
        print(f"rowH={d['rowH']}")
        for r in d['rows']:
            print(f"r{r['i']}: turnoverClipped={r['turnoverClipped']} clientW={r['clientW']} scrollW={r['scrollW']}"
                  f" vrVisible={r['vrVisible']} amtVisible={r['amtVisible']} sameLine={r['sameLine']}"
                  f" badge='{r['badgeText']}' badgeClipped={r['badgeClipped']}"
                  f" motto='{r['mottoText']}' nameClipped={r['nameClipped']} name={r['nameScrollW']}/{r['nameClientW']}")

        assert d['rowH'] <= 75, f"R245: row h {d['rowH']} 应 ≤75px (不膨胀)"
        for r in d['rows']:
            assert not r['turnoverClipped'], f"R245: r{r['i']} turnover 仍裁剪 scrollW={r['scrollW']} > clientW={r['clientW']}"
            assert r['vrVisible'], f"R245: r{r['i']} 量比被裁剪"
            assert r['amtVisible'], f"R245: r{r['i']} 成交额被裁剪"
            assert r['sameLine'], f"R245: r{r['i']} 量比/成交额未单行 (换行膨胀)"
            assert r['badgeClipped'] is False, f"R245: r{r['i']} board badge 被裁剪"
            assert r['badgeText'] in ('10cm', '20cm'), f"R245: r{r['i']} 无板块徽章"
            assert not r['nameClipped'], f"R245: r{r['i']} name 格被裁剪 ({r['nameScrollW']}>{r['nameClientW']})"
        # motto 只在 top-1
        mottoRows = [r for r in d['rows'] if r['mottoText']]
        assert len(mottoRows) <= 1, f"R245: motto 出现 {len(mottoRows)} 行, 应只 top-1"
        if mottoRows:
            assert mottoRows[0]['i'] == 0, "R245: motto 应在第一行 (top-1)"

        await b.close()
        print(f"[OK] R245 三信号单行 + badge 完整 + name 不裁剪 — row h {d['rowH']}px ✓")

if __name__ == "__main__":
    asyncio.run(run())
