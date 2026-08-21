"""R249 verify: 罗马数字归一化 — 中药Ⅱ 行 sector-chg chip 可见

第一性原理: 东财 stock_zt_pool_em 所属行业用罗马数字标注二级行业 (中药Ⅱ/电机Ⅱ/综合Ⅱ),
  THS summary 用标准名 (中药/电机/综合). 同一实体不同字形 → 查表 miss → 板块涨幅
  信号静默丢失. R249 strip 罗马后缀, 归一到标准名查表.

断言 (真实服务, 390px):
  1. 中药Ⅱ 行 (sector 含 Ⅱ) 的 sector-chg chip 可见
  2. 全行 rowH 无回归 (<= 75px, 沿用 R248 基线)
  3. console 0 错误
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
    for _ in range(25):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length >= 1"):
            break
        await page.wait_for_timeout(500)

PROBE = r"""() => {
  var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
  var out = [];
  for (var i=0; i<rows.length; i++) {
    var row = rows[i];
    var name = row.querySelector('.bv-sector-name');
    var rulesCell = row.querySelector('.bv-rules-cell');
    var chg = rulesCell ? rulesCell.querySelector('.bv-sector-chg') : null;
    var sector = name ? (name.textContent||'').trim() : '';
    var chgR = chg ? chg.getBoundingClientRect() : null;
    out.push({
      sector: sector,
      isRoman: /[ⅠⅡⅢⅣⅤ]/.test(sector),
      chgTxt: chg ? (chg.textContent||'').trim() : null,
      chgVisible: chgR ? (chgR.width > 0 && chgR.height > 0) : false,
      rowH: row.offsetHeight
    });
  }
  return out;
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await load(page)
        d = await page.evaluate(PROBE)
        assert len(d) >= 1, "无推票行"
        roman_rows = [r for r in d if r['isRoman']]
        assert len(roman_rows) >= 1, "无中药Ⅱ等罗马数字板块行"
        for r in d:
            print(f"'{r['sector']}': chg='{r['chgTxt']}' vis={r['chgVisible']} rowH={r['rowH']}")
        # 1. 罗马数字板块行 chip 可见
        for r in roman_rows:
            assert r['chgTxt'], f"R249: {r['sector']} 无板块涨幅"
            assert r['chgVisible'], f"R249: {r['sector']} chip 不可见"
        # 2. rowH 无回归
        for r in d:
            assert r['rowH'] <= 75, f"R249: 卡高回归 rowH={r['rowH']}"
        # 3. console 0 错误
        real_errors = [e for e in errors if 'favicon' not in e]
        assert not real_errors, f"R249: console errors {real_errors}"
        await b.close()
        print(f"[OK] R249 罗马归一化 — {len(roman_rows)} 行罗马板块 chip 全可见, rowH 无回归, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
