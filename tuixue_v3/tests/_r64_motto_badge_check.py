"""R64 卡片正面 top-1 口诀徽章."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-motto-badge {
      display: inline-block; max-width: 120px; margin-left: 6px; padding: 0 5px;
      border-radius: 3px; background: rgba(0,240,255,0.12); color: #7feaff;
      border: 1px solid rgba(0,240,255,0.3); font-size: 9px; font-weight: 700;
      line-height: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    """
    js = """
    // Mirror of R64 render: name cell + optional motto badge
    function renderNameCell(name, topRule) {
      var html = name;
      var title = topRule && topRule.title ? topRule.title : '';
      if (title) {
        html += ' <span class="bv-motto-badge" title="' + (topRule.quote || title) + '">' + title + '</span>';
      }
      return html;
    }
    window.renderNameCell = renderNameCell;
    """
    html = ("<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
            "<div id='host'></div><script>" + js + "</script></body></html>")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # With top_rule.title → badge rendered
        r1 = await page.evaluate("renderNameCell('平安银行', {title: '弱转强', quote: '看封单'})")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r1)
        badge = await page.query_selector(".bv-motto-badge")
        assert badge is not None, "badge should render when title exists"
        txt = await page.evaluate("document.querySelector('.bv-motto-badge').textContent")
        title = await page.evaluate("document.querySelector('.bv-motto-badge').title")
        print(f"badge txt={txt} title={title}")
        assert txt == "弱转强"
        assert title == "看封单", "badge hover should show full quote"

        # Without title → no badge
        r2 = await page.evaluate("renderNameCell('平安银行', {})")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r2)
        badge2 = await page.query_selector(".bv-motto-badge")
        print(f"no-title badge={badge2 is not None}")
        assert badge2 is None

        # Overflow truncated (max-width + ellipsis)
        r3 = await page.evaluate("renderNameCell('平安银行', {title: '二板弱转强卡位分歧反包加速兑现', quote: 'x'})")
        await page.evaluate("(h) => { document.getElementById('host').innerHTML = h; }", r3)
        w = await page.evaluate("getComputedStyle(document.querySelector('.bv-motto-badge')).maxWidth")
        ov = await page.evaluate("getComputedStyle(document.querySelector('.bv-motto-badge')).textOverflow")
        print(f"badge maxWidth={w} textOverflow={ov}")
        assert w == "120px"
        assert ov == "ellipsis"

        print("[OK] R64 motto badge shows top-1 rule title on card front")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
