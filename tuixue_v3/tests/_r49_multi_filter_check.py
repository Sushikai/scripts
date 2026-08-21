"""R49 多过滤组合显示 n 个条件."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    html = "<!DOCTYPE html><html><body>"
    html += '<span id="bv-pick-count"></span>'
    html += "<script>"
    html += "var _picks = ["
    html += "  {code:'600000', matched_rules:['BV01']},"
    html += "  {code:'000001', matched_rules:['BV01']},"
    html += "  {code:'002142', matched_rules:['BV03']}"
    html += "];"
    html += "var _ruleFilter = null, _pickFilter = 'all';"
    html += "var _tsStr = '', _dataTs = 0;"
    html += "function esc(s){return String(s);}"
    html += "function renderPicks() {"
    html += "  var count = document.querySelector('#bv-pick-count');"
    html += "  if (_ruleFilter) {"
    html += "    var fc = 0;"
    html += "    for (var i=0;i<_picks.length;i++) if ((_picks[i].matched_rules||[]).indexOf(_ruleFilter)!==-1) fc++;"
    html += "    var stk = [];"
    html += "    if (_ruleFilter) stk.push('规则 ' + _ruleFilter);"
    html += "    if (_pickFilter && _pickFilter !== 'all') stk.push(_pickFilter);"
    html += "    var stkTxt = stk.length > 1 ? ' · ' + stk.length + ' 个条件' : '';"
    html += "    count.innerHTML = '(命中 <b>' + fc + '</b> / ' + _picks.length + ' · 🔍 ' + _ruleFilter + stkTxt + ') <a class=\"bv-rule-clear\" href=\"javascript:void(0)\">清除</a>';"
    html += "  } else {"
    html += "    count.innerHTML = '(扫描 0 / 命中 ' + _picks.length + ')';"
    html += "  }"
    html += "}"
    html += "window.renderPicks = renderPicks; renderPicks();"
    html += "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # rule filter only
        await page.evaluate("_ruleFilter='BV01'; _pickFilter='all'; renderPicks();")
        t1 = await page.text_content("#bv-pick-count")
        print(f"rule only: {t1!r}")
        assert "🔍 BV01" in t1
        assert "个条件" not in t1, "single filter should not show n 个条件"

        # rule + sector filter
        await page.evaluate("_ruleFilter='BV01'; _pickFilter='sector:汽车'; renderPicks();")
        t2 = await page.text_content("#bv-pick-count")
        print(f"rule+sector: {t2!r}")
        assert "🔍 BV01" in t2
        assert "2 个条件" in t2, "two filters should show 2 个条件"

        # clear link exists
        has_clear = await page.evaluate("!!document.querySelector('.bv-rule-clear')")
        print(f"has clear: {has_clear}")
        assert has_clear

        print("[OK] R49 multi-filter n 个条件 works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())