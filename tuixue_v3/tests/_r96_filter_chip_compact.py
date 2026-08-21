"""R96 紧凑 pill 不是主操作 — 筛选 chip 高度 52→30px (bar 70→48px).

原: 全局 @media(max-width:720px) button { min-height:40px } 后声明覆盖
    .bv-filter-chip 的紧凑样式 → 6 个筛选 chip 每个 52px, 筛选条 70px
    (占推票卡上方 70px 垂直空间).
R96: .bv-filter-chip min-height:0 !important (复用 .menu-btn 的覆盖模式)
     + box-sizing:border-box → chip 30px, bar 48px. 筛选 chip 是横向滑动
     的紧凑 pill, 不是主操作, 不应继承全局 40px 触控目标.
"""
import asyncio
from playwright.async_api import async_playwright

# 生产修复 (R96)
R96_CSS = """
.bv-filter-bar { display:flex; gap:6px; padding:8px 8px 10px; overflow-x:auto; white-space:nowrap; }
.bv-filter-chip { flex-shrink:0; padding:5px 10px; border-radius:14px; background:#222;
  border:1px solid rgba(255,255,255,0.1); color:#aaa; font-size:11px; font-weight:500;
  cursor:pointer; white-space:nowrap; min-height:0 !important; box-sizing:border-box; }
.bv-filter-count { display:inline-block; margin-left:4px; padding:0 4px; border-radius:8px;
  background:rgba(0,0,0,0.2); font-size:10px; min-width:16px; text-align:center; }
/* 复现旧 bug: 全局 button 40px */
@media (max-width:720px){ button { min-height:40px; padding:8px 12px; } }
"""

BUG_CSS = """
.bv-filter-bar { display:flex; gap:6px; padding:8px 8px 10px; overflow-x:auto; white-space:nowrap; }
.bv-filter-chip { flex-shrink:0; padding:5px 10px; border-radius:14px; background:#222;
  border:1px solid rgba(255,255,255,0.1); color:#aaa; font-size:11px; font-weight:500;
  cursor:pointer; white-space:nowrap; }
.bv-filter-count { display:inline-block; margin-left:4px; padding:0 4px; border-radius:8px;
  background:rgba(0,0,0,0.2); font-size:10px; min-width:16px; text-align:center; }
@media (max-width:720px){ button { min-height:40px; padding:8px 12px; } }
"""

CHIPS = ['全部 15', '命中 ≥3 6', '连板 ≥2 4', '首板 3', '涨幅 ≥5% 7', '封单 ≥30% 2']


def make_html(css):
    chips = "".join(f'<button class="bv-filter-chip">{c}</button>' for c in CHIPS)
    return f"""<!DOCTYPE html><html><head><style>{css}</style></head><body>
    <div class="bv-filter-bar" id="bv-filter-bar">{chips}</div></body></html>"""


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # ── bug: global 40px leaks into chips ──
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.set_content(make_html(BUG_CSS))
        bug = await page.evaluate("""() => {
          var bar = document.querySelector('#bv-filter-bar');
          var chip = document.querySelector('.bv-filter-chip');
          return { barH: Math.round(bar.getBoundingClientRect().height),
                   chipH: Math.round(chip.getBoundingClientRect().height),
                   chipMinH: getComputedStyle(chip).minHeight };
        }""")
        print(f"bug: {bug}")
        assert bug["chipH"] >= 40, f"expected buggy chip ≥40, got {bug['chipH']}"
        await page.close()

        # ── R96 fix ──
        fpage = await browser.new_page(viewport={"width": 390, "height": 844})
        await fpage.set_content(make_html(R96_CSS))
        fixed = await fpage.evaluate("""() => {
          var bar = document.querySelector('#bv-filter-bar');
          var chip = document.querySelector('.bv-filter-chip');
          return { barH: Math.round(bar.getBoundingClientRect().height),
                   chipH: Math.round(chip.getBoundingClientRect().height),
                   chipMinH: getComputedStyle(chip).minHeight };
        }""")
        print(f"fixed: {fixed}")
        assert fixed["chipH"] <= 30, f"R96: chip should be ≤30px, got {fixed['chipH']}"
        assert fixed["barH"] < bug["barH"], f"R96: bar should shrink, {bug['barH']}→{fixed['barH']}"
        assert fixed["chipMinH"] == "0px", "R96: min-height overridden to 0"

        # chips still horizontally scrollable (not clipped)
        scrollable = await fpage.evaluate("""() => {
          var bar = document.querySelector('#bv-filter-bar');
          return bar.scrollWidth > bar.clientWidth;
        }""")
        print(f"horizontally scrollable: {scrollable}")
        await fpage.close()

        await browser.close()
        print("[OK] R96 filter chip compact (52→30px)")


if __name__ == "__main__":
    asyncio.run(run())
