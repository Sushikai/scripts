"""R66 规则过滤时口诀徽章跟随过滤规则."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    // Mirror of R64+R66 motto logic
    var _rulesById = {
      BV02: {title: '弱转强', quote: '看封单'},
      BV05: {title: '反包', quote: '反包需放量'}
    };
    var _ruleFilter = null;
    function renderNameCell(name, p) {
      var html = name;
      var motto = p.top_rule;
      if (_ruleFilter && (p.matched_rules || []).indexOf(_ruleFilter) !== -1) {
        var fr = _rulesById[_ruleFilter];
        if (fr) motto = fr;
      }
      var title = motto && motto.title ? motto.title : '';
      if (title) html += ' <span class="bv-motto-badge" title="' + (motto.quote || title) + '">' + title + '</span>';
      return html;
    }
    window.renderNameCell = renderNameCell;
    window.setFilter = function(r) { _ruleFilter = r; };
    window._rulesById = _rulesById;
    """
    html = "<!DOCTYPE html><html><body><div id='host'></div><script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        stock = {"top_rule": {"title": "反包", "quote": "反包需放量"}, "matched_rules": ["BV05", "BV02"]}

        # No filter → motto = top_rule (反包)
        r1 = await page.evaluate("(s) => renderNameCell('平安银行', s)", stock)
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r1)
        t1 = await page.evaluate("document.querySelector('.bv-motto-badge').textContent")
        print(f"no filter motto={t1}")
        assert t1 == "反包"

        # Filter BV02 → motto follows to 弱转强
        await page.evaluate("setFilter('BV02')")
        r2 = await page.evaluate("(s) => renderNameCell('平安银行', s)", stock)
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r2)
        t2 = await page.evaluate("document.querySelector('.bv-motto-badge').textContent")
        print(f"filter BV02 motto={t2}")
        assert t2 == "弱转强"

        # Filter BV05 (already top) → stays 反包
        await page.evaluate("setFilter('BV05')")
        r3 = await page.evaluate("(s) => renderNameCell('平安银行', s)", stock)
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r3)
        t3 = await page.evaluate("document.querySelector('.bv-motto-badge').textContent")
        print(f"filter BV05 motto={t3}")
        assert t3 == "反包"

        # Filter rule stock doesn't match → top_rule kept
        await page.evaluate("setFilter('BV09')")
        r4 = await page.evaluate("(s) => renderNameCell('平安银行', s)", stock)
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r4)
        t4 = await page.evaluate("document.querySelector('.bv-motto-badge').textContent")
        print(f"filter BV09 (no match) motto={t4}")
        assert t4 == "反包"

        print("[OK] R66 motto follows active rule filter")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
