"""R50 过滤过深空态."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-empty { padding: 2.5rem; }
    .bv-reset-filter, .bv-reset-rule {
      padding: 8px 20px; background: #00f0ff; color: #000;
      border: 0; border-radius: 6px; font-weight: 700; cursor: pointer;
    }
    """
    html = "<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
    html += '<table><tbody id="bv-pick-tbody"></tbody></table>'
    html += '<span id="bv-pick-count"></span>'
    html += "<script>"
    html += "var _picks = ["
    html += "  {code:'600000', name:'A', matched_rules:['BV01'], sector:'汽车'},"
    html += "  {code:'000001', name:'B', matched_rules:['BV03'], sector:'汽车'},"
    html += "  {code:'002142', name:'C', matched_rules:['BV07'], sector:'医药'}"
    html += "];"
    html += "var _ruleFilter = null, _pickFilter = 'all';"
    html += "function esc(s){return String(s).replace(/[&<>\"']/g,'')}"
    html += "function _filterPicks(arr){"
    html += "  if (_pickFilter === 'all') return arr;"
    html += "  if (_pickFilter.indexOf('sector:') === 0) {"
    html += "    var s = _pickFilter.slice(7);"
    html += "    return arr.filter(function(p){return (p.sector||'').indexOf(s)!==-1;});"
    html += "  }"
    html += "  return arr;"
    html += "}"
    html += "function renderPicks() {"
    html += "  var tb = document.querySelector('#bv-pick-tbody');"
    html += "  var count = document.querySelector('#bv-pick-count');"
    html += "  var f = _filterPicks(_picks);"
    html += "  if (_ruleFilter) f = f.filter(function(p){return (p.matched_rules||[]).indexOf(_ruleFilter)!==-1;});"
    html += "  if (_picks && _picks.length && f.length === 0) {"
    html += "    var stk = [];"
    html += "    if (_ruleFilter) stk.push('规则「' + _ruleFilter + '」');"
    html += "    if (_pickFilter && _pickFilter !== 'all') stk.push('板块「' + esc(_pickFilter) + '」');"
    html += "    var desc = stk.length ? stk.join(' + ') : '当前过滤';"
    html += "    var resetCls = _ruleFilter ? 'bv-reset-rule' : 'bv-reset-filter';"
    html += "    var resetLbl = _ruleFilter ? (_pickFilter && _pickFilter !== 'all' ? '清除全部' : '清除规则过滤') : '↺ 重置过滤';"
    html += "    tb.innerHTML = '<tr><td class=\"bv-empty\">' +"
    html += "      '<div style=\"font-size:26px\">🔍</div>' +"
    html += "      '<div>当前过滤条件下无命中</div>' +"
    html += "      '<div>' + desc + ' 筛掉全部</div>' +"
    html += "      '<button class=\"' + resetCls + '\">' + resetLbl + '</button></td></tr>';"
    html += "    count.textContent = '(扫描 3 / 过滤后 0)';"
    html += "  } else {"
    html += "    tb.innerHTML = '';"
    html += "    count.textContent = '(扫描 0 / 命中 ' + f.length + ')';"
    html += "  }"
    html += "}"
    html += "window.renderPicks = renderPicks; renderPicks();"
    html += "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # Only sector filter (no rule filter)
        await page.evaluate("_pickFilter = 'sector:通信'; renderPicks();")
        text = await page.text_content(".bv-empty")
        print(f"sector only empty: {text!r}")
        assert "当前过滤条件下无命中" in text
        assert "bv-reset-filter" in await page.evaluate("document.querySelector('button').className")
        assert "重置过滤" in await page.evaluate("document.querySelector('button').textContent")

        # Rule filter + sector combo
        await page.evaluate("_ruleFilter = 'BV99'; _pickFilter = 'sector:通信'; renderPicks();")
        text2 = await page.text_content(".bv-empty")
        cls2 = await page.evaluate("document.querySelector('button').className")
        lbl2 = await page.evaluate("document.querySelector('button').textContent")
        print(f"combo empty: {text2!r}")
        assert "规则「BV99」" in text2
        assert "板块「sector:通信」" in text2
        assert "bv-reset-rule" in cls2
        assert "清除全部" in lbl2

        # Rule only (no sector)
        await page.evaluate("_ruleFilter = 'BV99'; _pickFilter = 'all'; renderPicks();")
        text3 = await page.text_content(".bv-empty")
        lbl3 = await page.evaluate("document.querySelector('button').textContent")
        print(f"rule only empty: {text3!r}")
        assert "规则「BV99」" in text3
        assert "板块" not in text3
        assert "清除规则过滤" in lbl3

        print("[OK] R50 filter empty state distinguishes 3 cases")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())