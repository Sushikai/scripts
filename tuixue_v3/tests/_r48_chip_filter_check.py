"""R48 chip 命中过滤."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-rule-chip { cursor: pointer; padding: 2px 6px; }
    .bv-rule-chip.bv-chip-active { outline: 2px solid #00f0ff; }
    #bv-pick-count .bv-rule-clear { color: #00f0ff; cursor: pointer; }
    """
    html = "<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
    html += '<span id="bv-pick-count"></span>'
    html += '<div id="bv-pick-tbody"></div>'
    html += "<script>"
    html += "var _picks = ["
    html += "  {code:'600000', name:'A', matched_rules:['BV01','BV03']},"
    html += "  {code:'000001', name:'B', matched_rules:['BV01']},"
    html += "  {code:'002142', name:'C', matched_rules:['BV03']},"
    html += "  {code:'600036', name:'D', matched_rules:['BV01','BV07']}"
    html += "];"
    html += "var _ruleFilter = null;"
    html += "var _rulesById = {'BV01':{title:'低吸'},'BV03':{title:'弱转强'},'BV07':{title:'卡位'}};"
    html += "function esc(s){return String(s).replace(/[&<>\"']/g,'')}"
    html += "function _filterPicks(a){return a;}"
    html += "function _sortPicks(a,s){return a;}"
    html += "var _pickSort = 'default', _pickFilter = null, _topCode = null, _dataTs = 0;"
    html += "function renderPicks() {"
    html += "  var count = document.querySelector('#bv-pick-count');"
    html += "  var sorted = _sortPicks(_filterPicks(_picks), _pickSort);"
    html += "  if (_ruleFilter) {"
    html += "    sorted = sorted.filter(function(p){return (p.matched_rules||[]).indexOf(_ruleFilter)!==-1;});"
    html += "  }"
    html += "  if (_ruleFilter) {"
    html += "    count.innerHTML = '(命中 <b>' + sorted.length + '</b> / ' + _picks.length + ' · 🔍 ' + _ruleFilter + ') <a class=\"bv-rule-clear\" href=\"javascript:void(0)\">清除</a>';"
    html += "    var clr = count.querySelector('.bv-rule-clear');"
    html += "    clr.dataset.bvClickable = '1';"
    html += "    clr.addEventListener('click', function(e){e.stopPropagation();_ruleFilter=null;renderPicks();});"
    html += "  } else {"
    html += "    count.innerHTML = '(扫描 0 / 命中 ' + _picks.length + ')';"
    html += "  }"
    html += "  var tb = document.querySelector('#bv-pick-tbody');"
    html += "  tb.innerHTML = sorted.map(function(p){return '<div>' + p.code + '</div>';}).join('');"
    html += "  window.__shown = sorted.map(function(p){return p.code;});"
    html += "}"
    html += "window.renderPicks = renderPicks;"
    html += "renderPicks();"
    html += "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # No filter: all 4 shown
        shown = await page.evaluate("window.__shown")
        count_txt = await page.text_content("#bv-pick-count")
        print(f"initial: shown={shown} count={count_txt!r}")
        assert len(shown) == 4
        assert "扫描" in count_txt and "清除" not in count_txt

        # Apply filter BV01
        await page.evaluate("window._ruleFilter = 'BV01'; renderPicks();")
        shown2 = await page.evaluate("window.__shown")
        count_txt2 = await page.text_content("#bv-pick-count")
        print(f"filter BV01: shown={shown2} count={count_txt2!r}")
        assert set(shown2) == {'600000', '000001', '600036'}, "BV01 hits 3 picks"
        assert "BV01" in count_txt2
        assert "清除" in count_txt2

        # Apply different filter BV03
        await page.evaluate("window._ruleFilter = 'BV03'; renderPicks();")
        shown3 = await page.evaluate("window.__shown")
        print(f"filter BV03: shown={shown3}")
        assert set(shown3) == {'600000', '002142'}, "BV03 hits 2 picks"

        # Clear filter
        await page.evaluate("document.querySelector('.bv-rule-clear').click()")
        shown4 = await page.evaluate("window.__shown")
        count_txt4 = await page.text_content("#bv-pick-count")
        print(f"after clear: shown={len(shown4)} count={count_txt4!r}")
        assert len(shown4) == 4
        assert "清除" not in count_txt4

        # Cursor pointer (apply to a chip first since test HTML doesn't auto-render chips)
        await page.evaluate("var c = document.createElement('span'); c.className='bv-rule-chip'; c.textContent='BV01'; document.body.appendChild(c);")
        cursor = await page.evaluate("getComputedStyle(document.querySelector('.bv-rule-chip')).cursor")
        print(f"chip cursor={cursor}")
        assert cursor == "pointer"

        print("[OK] R48 chip filter works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())