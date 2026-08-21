"""R87 已加载 footer 数量必须等于用户实际看到 — 过滤后不撒谎.

原: renderEndFooter('loaded') 用 _picks.length (未过滤), 但列表是 _filterPicks(_picks).
    过滤生效时显示"已加载全部 50 只"而只看到 12 只 = 说谎.
R87: 计算 _filterPicks(_picks).length, 过滤时显示 "过滤后 N / 全部 M".
"""
import asyncio
from playwright.async_api import async_playwright


async def run():
    js = """
    var _pickFilter = 'all';
    var _picks = [];
    for (var i = 0; i < 50; i++) {
      _picks.push({ code: '6000' + (100+i), streak: i < 12 ? 2 : 1, matched_rules: i < 12 ? ['BV01','BV02','BV03'] : ['BV01'] });
    }
    function _filterPicks(arr) {
      if (_pickFilter === 'all') return arr;
      if (_pickFilter === 'streak2') return arr.filter(function(p){ return (p.streak || 0) >= 2; });
      if (_pickFilter === 'hot') return arr.filter(function(p){ return (p.matched_rules || []).length >= 3; });
      return arr;
    }
    function renderLoaded() {
      var visCount = _filterPicks(_picks).length;
      var visTxt = visCount < _picks.length ? ('过滤后 ' + visCount + ' / 全部 ' + _picks.length) : String(_picks.length);
      document.getElementById('foot').innerHTML = '已加载全部 ' + visTxt + ' 只';
    }
    window.renderLoaded = renderLoaded;
    window.setFilter = function(f){ _pickFilter = f; };
    """
    html = ("<!DOCTYPE html><html><body><div id='foot'></div>"
            "<script>" + js + "</script></body></html>")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # No filter → "已加载全部 50 只"
        await page.evaluate("renderLoaded()")
        t0 = await page.evaluate("document.getElementById('foot').textContent")
        print(f"no filter: {t0!r}")
        assert t0 == "已加载全部 50 只"

        # streak2 filter (12 of 50) → "过滤后 12 / 全部 50"
        await page.evaluate("setFilter('streak2')")
        await page.evaluate("renderLoaded()")
        t1 = await page.evaluate("document.getElementById('foot').textContent")
        print(f"filter streak2: {t1!r}")
        assert "过滤后 12 / 全部 50" in t1

        # hot filter (also 12) → same
        await page.evaluate("setFilter('hot')")
        await page.evaluate("renderLoaded()")
        t2 = await page.evaluate("document.getElementById('foot').textContent")
        print(f"filter hot: {t2!r}")
        assert "过滤后 12 / 全部 50" in t2

        print("[OK] R87 footer count reflects filtered visibility")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
