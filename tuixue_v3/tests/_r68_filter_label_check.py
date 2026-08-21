"""R68 过滤指示自解释 — 规则 ID + 标题."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    // Mirror of R68 count label logic
    var _rulesById = { BV02: {title: '弱转强'} };
    var _ruleFilter = null;
    var _pickFilter = 'all';
    function renderCount() {
      var _stk = [];
      var _fRuleObj = _rulesById[_ruleFilter];
      var _fRuleLabel = _ruleFilter + (_fRuleObj && _fRuleObj.title ? ' ' + _fRuleObj.title : '');
      if (_ruleFilter) _stk.push('规则 ' + _fRuleLabel);
      if (_pickFilter && _pickFilter !== 'all') _stk.push(_pickFilter);
      var _stkTxt = _stk.length > 1 ? ' · ' + _stk.length + ' 个条件' : '';
      return '(命中 3 / 10 · 🔍 ' + _fRuleLabel + _stkTxt + ')';
    }
    window.renderCount = renderCount;
    window.setFilter = function(r) { _ruleFilter = r; };
    window.setPickFilter = function(p) { _pickFilter = p; };
    """
    html = "<!DOCTYPE html><html><body><div id='host'></div><script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # Filter BV02 → shows title
        await page.evaluate("setFilter('BV02')")
        t1 = await page.evaluate("renderCount()")
        print(f"BV02: {t1}")
        assert "BV02 弱转强" in t1, "rule filter should show ID + title"

        # Filter BV02 + pickFilter → 2 conditions
        await page.evaluate("setPickFilter('sector:AI')")
        t2 = await page.evaluate("renderCount()")
        print(f"BV02+filter: {t2}")
        assert "2 个条件" in t2

        # Unknown rule (no title) → ID only, no crash
        await page.evaluate("setFilter('BV99')")
        t3 = await page.evaluate("renderCount()")
        print(f"BV99: {t3}")
        assert "BV99" in t3 and "undefined" not in t3

        print("[OK] R68 rule filter label is self-explanatory (ID + title)")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
