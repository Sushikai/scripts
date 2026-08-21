"""R45 footer 错误+重试."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-loadmore-end { padding: 16px; text-align: center; font-size: 11px; }
    .bv-loadmore-end .bv-end-err { color: #ff6b6b; font-weight: 600; margin-right: 4px; }
    .bv-loadmore-end .bv-loadmore-btn {
      background: rgba(0, 240, 255, 0.08); color: #00f0ff;
      border: 1px solid rgba(0, 240, 255, 0.4); border-radius: 16px;
      padding: 6px 16px; font-size: 11px; font-weight: 600; cursor: pointer;
    }
    """
    js = """
    window.__retried = 0;
    window.__renderError = function(msg) {
      var end = document.querySelector('#bv-loadmore-end');
      end.innerHTML = '<span class="bv-end-err">⚠ ' + msg + '</span> · <button class="bv-loadmore-btn bv-retry-btn">↻ 重试</button>';
      end.hidden = false;
      var btn = end.querySelector('.bv-retry-btn');
      btn.dataset.bvClickable = '1';
      btn.addEventListener('click', function(){ window.__retried++; });
    };
    """
    html = "<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
    html += '<div class="bv-loadmore-end" id="bv-loadmore-end" hidden></div>'
    html += "<script>" + js + "</script></body></html>"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        await page.evaluate("window.__renderError('网络异常')")
        text = await page.text_content("#bv-loadmore-end")
        print(f"text={text!r}")
        assert "网络异常" in text
        assert "重试" in text

        # Error color
        err_color = await page.evaluate("getComputedStyle(document.querySelector('.bv-end-err')).color")
        print(f"err_color={err_color}")
        assert "255, 107, 107" in err_color

        # Retry button click → counter
        before = await page.evaluate("window.__retried")
        await page.evaluate("document.querySelector('.bv-retry-btn').click()")
        after = await page.evaluate("window.__retried")
        print(f"retried: {before} -> {after}")
        assert after == before + 1

        print("[OK] R45 error retry works")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())