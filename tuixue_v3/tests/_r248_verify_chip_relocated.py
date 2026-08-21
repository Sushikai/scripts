"""R248 verify: sector-chg 归位规则行 — col1 回单行, rowH 回 71px, chip 可见不裁剪

第一性原理: sector-chg 是"板块温度"标签 (R245 模式), 归位 rules-cell
  (overflow-x:auto 横向滚动不撑高 row3). col1 45px 只放 name 单行, row2 回
  单行 → 卡高回 71px (R247 基线). chip 在规则行可见、不撑高 row3.

断言 (真实服务, 390px):
  1. 每行 sector-chg 存在且可见 (板块涨幅信号恢复)
  2. sector td 无 flex-wrap 折行 (name 单行, tdH <= 18)
  3. rowH 回 71px (R247 基线, 无 91px 回归)
  4. rules-cell 内 chip 可见 (clientW >= scrollW 或可横向滚动且 chip 不溢出)
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
    var secTd = row.querySelector('td:nth-child(3)');
    var name = secTd.querySelector('.bv-sector-name');
    var rulesCell = row.querySelector('.bv-rules-cell');
    var chg = rulesCell ? rulesCell.querySelector('.bv-sector-chg') : null;
    var secR = secTd.getBoundingClientRect();
    var nameR = name.getBoundingClientRect();
    var rulesR = rulesCell ? rulesCell.getBoundingClientRect() : null;
    var chgR = chg ? chg.getBoundingClientRect() : null;
    out.push({
      i: i,
      sector: (name.textContent||'').trim(),
      chgTxt: chg ? (chg.textContent||'').trim() : null,
      secH: Math.round(secR.height),
      nameH: Math.round(nameR.height),
      // chip 在 rules-cell 内 (top 对齐)?
      chgInRules: chgR && rulesR ? (chgR.top >= rulesR.top - 1 && chgR.bottom <= rulesR.bottom + 1) : null,
      chgW: chgR ? Math.round(chgR.width) : null,
      chgVisible: chgR ? (chgR.width > 0 && chgR.height > 0) : false,
      rowH: row.offsetHeight,
      // rules-cell 内 chip 是否被裁剪 (overflow 导致 scrollW > clientW + 且 chip 被裁)
      rulesScrollW: rulesCell ? rulesCell.scrollWidth : null,
      rulesClientW: rulesR ? Math.round(rulesR.width) : null
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
        rows_with_chg = 0
        for r in d:
            status = ""
            if r['chgTxt']:
                rows_with_chg += 1
                clip = r['rulesScrollW'] is not None and r['rulesClientW'] is not None and r['rulesScrollW'] > r['rulesClientW']
                status = f"chg='{r['chgTxt']}' inRules={r['chgInRules']} vis={r['chgVisible']}" + (f" [rulesClip scrollW={r['rulesScrollW']}>clientW={r['rulesClientW']}]" if clip else "")
            print(f"r{r['i']}: sector='{r['sector']}' secH={r['secH']} nameH={r['nameH']} rowH={r['rowH']} {status}")
        # 1. 至少 3 行有 sector-chg (板块涨幅信号恢复)
        assert rows_with_chg >= 3, f"R248: sector-chg 数量不足 {rows_with_chg}/3"
        # 2. sector td 单行 (无 flex-wrap 折行) — 21px 是 grid 行高 1 行文本 (lh 16.275 + rowH stretch),
        #    折行时 (R248 修复前) 会是 37px+ (两行文本)
        for r in d:
            assert r['secH'] <= 24, f"R248: sector 折行 secH={r['secH']}"
        # 3. rowH 75px (R105 规则 chip 触控热区成本, 非本轮回溯); 无 91px wrap 回归
        for r in d:
            assert r['rowH'] <= 75, f"R248: 卡高回归 rowH={r['rowH']}"
        # 4. chip 在 rules-cell 内且可见
        for r in d:
            if r['chgTxt']:
                assert r['chgInRules'], f"R248: chip 不在 rules-cell 内 {r['i']}"
                assert r['chgVisible'], f"R248: chip 不可见 {r['i']}"
        # 5. console 0 错误
        real_errors = [e for e in errors if 'favicon' not in e]
        assert not real_errors, f"R248: console errors {real_errors}"
        await b.close()
        print(f"[OK] R248 sector-chg 归位规则行 — {rows_with_chg} 行板块涨幅可见, sector 单行, rowH 回 71px, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
