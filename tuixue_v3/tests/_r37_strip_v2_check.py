"""R37 strip text v2 — icon + bold + hint."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-stale-strip .bv-stale-hint {
      font-weight: 400; opacity: 0.7; margin-left: 6px;
      padding-left: 6px; border-left: 1px solid currentColor;
    }
    """
    js = """
    function renderStrip(age) {
      var s = document.querySelector('#bv-stale-strip');
      s.hidden = false;
      if (age > 300) {
        s.className = 'bv-stale-strip is-very-stale';
        s.innerHTML = '📉 <b>数据已陈旧</b> · ' + Math.floor(age/60) + ' 分钟前 <span class="bv-stale-hint">点击刷新</span>';
      } else {
        s.className = 'bv-stale-strip is-stale';
        s.innerHTML = '📊 <b>数据略旧</b> · ' + age + 's <span class="bv-stale-hint">点击刷新</span>';
      }
    }
    """
    html = f"""<!DOCTYPE html><html><head><style>{css}</style></head>
<body>
<div class="bv-stale-strip" id="bv-stale-strip" hidden></div>
<script>{js}</script>
</body></html>"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 200})
        await page.set_content(html)

        # 120s case
        await page.evaluate("renderStrip(120)")
        html1 = await page.inner_html("#bv-stale-strip")
        text1 = await page.text_content("#bv-stale-strip")
        print(f"120s html={html1!r}")
        print(f"120s text={text1!r}")
        assert "📊" in html1
        assert "数据略旧" in html1
        assert "120s" in html1
        assert "点击刷新" in html1
        assert "bv-stale-hint" in html1

        # hint opacity
        op = await page.evaluate("getComputedStyle(document.querySelector('.bv-stale-hint')).opacity")
        print(f"hint opacity={op}")
        assert float(op) < 0.9

        # bold present
        b_weight = await page.evaluate("getComputedStyle(document.querySelector('b')).fontWeight")
        print(f"b weight={b_weight}")
        assert int(b_weight) >= 600

        # 600s case
        await page.evaluate("renderStrip(600)")
        html2 = await page.inner_html("#bv-stale-strip")
        print(f"600s html={html2!r}")
        assert "📉" in html2
        assert "数据已陈旧" in html2
        assert "10" in html2  # 600/60 = 10

        await page.evaluate("renderStrip(120)")
        await page.screenshot(path="/tmp/bv_r37_strip.png", full_page=True)
        print("[OK] R37 strip text v2 with icon + bold + hint")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())