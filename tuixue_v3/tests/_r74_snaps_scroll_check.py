"""R74 筛选条横向滚动 snap 对齐完整 chip."""
import asyncio
from playwright.async_api import async_playwright


async def run():
    css = """
    .bv-filter-bar {
      display: flex; gap: 6px; overflow-x: auto;
      scroll-snap-type: x proximity;
      padding: 8px;
    }
    .bv-filter-chip {
      flex-shrink: 0; padding: 5px 10px; border-radius: 14px;
      font-size: 11px;
      scroll-snap-align: start;
    }
    """
    html = ("<!DOCTYPE html><html><head><style>" + css + "</style></head><body>"
            "<div class='bv-filter-bar'>"
            "<button class='bv-filter-chip'>全部</button>"
            "<button class='bv-filter-chip'>命中≥3</button>"
            "<button class='bv-filter-chip'>连板≥2</button>"
            "<button class='bv-filter-chip'>首板</button>"
            "<button class='bv-filter-chip'>涨幅≥5%</button>"
            "<button class='bv-filter-chip'>封单≥30%</button>"
            "</div></body></html>")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(html)

        # scroll-snap-type on container (computed returns 'x')
        sst = await page.evaluate("getComputedStyle(document.querySelector('.bv-filter-bar')).scrollSnapType")
        print(f"scroll-snap-type={sst}")
        assert "x" in sst, "snap axis x must be set"

        # scroll-snap-align on chips
        ssa = await page.evaluate("getComputedStyle(document.querySelectorAll('.bv-filter-chip')[2]).scrollSnapAlign")
        print(f"chip scroll-snap-align={ssa}")
        assert ssa == "start"

        # Overflow: 6 chips should overflow a 390px bar (scrollable)
        scrol = await page.evaluate("el => el.scrollWidth > el.clientWidth",
                                    await page.query_selector(".bv-filter-bar"))
        print(f"scrollable={scrol}")
        assert scrol == True, "bar should be horizontally scrollable with 6 chips"

        # Scroll right → first chip starts at left edge (snapped)
        await page.evaluate("el => { el.scrollLeft = 120; }", await page.query_selector(".bv-filter-bar"))
        await page.wait_for_timeout(100)
        left = await page.evaluate("el => Math.round(el.querySelectorAll('.bv-filter-chip')[1].getBoundingClientRect().left - el.getBoundingClientRect().left)",
                                   await page.query_selector(".bv-filter-bar"))
        print(f"chip[1] left offset={left}")
        assert left >= 0, "chip should not be half-clipped on the left"

        print("[OK] R74 filter bar snap scroll keeps chips whole")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
