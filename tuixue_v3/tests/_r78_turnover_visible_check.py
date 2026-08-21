"""R78 换手格不再 display:none — R77 量比小字必须真可见.

R77 把量比塞进 td:nth-child(5), 但该列在 mobile 是 display:none.
第一性原理: 可见性 > 存在性 — 一个 display:none 里的信号等于没做.
本测试断言: mobile 下 td:nth-child(5) 有 grid-area=turnover 且 computed display != none.
"""
import asyncio
from playwright.async_api import async_playwright

CSS = """
.view-bv .bv-table, .view-bv .bv-table tbody { display: block; width: 100%; }
.view-bv .bv-table tr.bv-row {
  display: grid;
  grid-template-areas:
    "code  name  change"
    "turnover sector streak"
    "rules rules  rules"
    "seal  time  burst";
  grid-template-columns: auto 1fr auto;
  gap: 2px 8px;
  padding: 8px 12px;
}
.view-bv .bv-table td:nth-child(1) { grid-area: code; }
.view-bv .bv-table td:nth-child(2) { grid-area: name; }
.view-bv .bv-table td:nth-child(3) { grid-area: sector; }
.view-bv .bv-table td:nth-child(4) { grid-area: change; }
.view-bv .bv-table td:nth-child(5) { grid-area: turnover; font-size: 10px; color: var(--ink-2, #888); }
.view-bv .bv-table td:nth-child(6) { grid-area: streak; }
.view-bv .bv-table td:nth-child(7) { grid-area: seal; }
.view-bv .bv-table td:nth-child(8) { grid-area: time; }
.view-bv .bv-table td:nth-child(9) { grid-area: burst; }
.view-bv .bv-table td:nth-child(10) { grid-area: rules; }
.bv-vr { display: inline-block; margin-left: 4px; padding: 0 3px; border-radius: 3px;
         font-size: 9px; font-weight: 700; color: #aaa; background: rgba(128,128,128,0.12); }
.bv-vr.bv-vr-hot { color: #4ade80; background: rgba(74,222,128,0.12); }
.bv-vr.bv-vr-cold { color: #94a3b8; background: rgba(148,163,184,0.10); }
"""

HTML = """
<!DOCTYPE html><html><head><style>__CSS__</style></head><body>
<div class="view-bv">
<table class="bv-table"><tbody>
  <tr class="bv-row">
    <td>600000</td><td>浦发银行</td><td class="bv-sector">银行</td>
    <td>+3.21%</td><td>8.50%<span class="bv-vr bv-vr-hot" title="量比 2.40">量2.4</span></td>
    <td>首板</td><td>0.8</td><td>14:32</td><td>—</td>
    <td><span>2</span>弱转强</td>
  </tr>
</tbody></table>
</div>
</body></html>
"""


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 390, "height": 500})
        await page.set_content(HTML.replace("__CSS__", CSS))

        # R78: turnover cell must be VISIBLE (not display:none like pre-R78)
        disp = await page.evaluate("getComputedStyle(document.querySelector('tr.bv-row td:nth-child(5)')).display")
        print(f"turnover td display = {disp}")
        assert disp != "none", "R78: turnover cell must be visible"

        # grid-area resolved to turnover slot
        ga = await page.evaluate("getComputedStyle(document.querySelector('tr.bv-row td:nth-child(5)')).gridArea")
        print(f"turnover grid-area = {ga}")
        assert "turnover" in ga

        # vol ratio label inside cell actually rendered & visible
        vr = await page.query_selector(".bv-vr")
        assert vr is not None
        vr_disp = await page.evaluate("getComputedStyle(document.querySelector('.bv-vr')).display")
        vr_rect = await page.evaluate("document.querySelector('.bv-vr').getBoundingClientRect()")
        print(f"bv-vr display={vr_disp} rect={vr_rect['width']}x{vr_rect['height']}")
        assert vr_disp != "none"
        assert vr_rect["width"] > 0 and vr_rect["height"] > 0

        # row is still a grid, all four rows laid out
        row = await page.evaluate("document.querySelector('tr.bv-row').getBoundingClientRect()")
        print(f"row = {row['width']}x{row['height']}")
        assert row["height"] > 0

        print("[OK] R78 turnover cell + vol_ratio label visible on mobile")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
