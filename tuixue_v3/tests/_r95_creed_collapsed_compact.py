"""R95 折叠态必须真正紧凑 — 战法哲学卡折叠后不浪费首屏垂直空间.

原: R91 已折叠哲学列表为 1 行, 但:
  1. 全局 @media(max-width:720px) button { min-height:40px } 把"展开 N 条"按钮
     撑到 56px (40 min-height + 8px×2 padding + content-box) — 折叠卡 175px.
  2. card-head eyebrow (UP主/BV号/时间) 换行独占第二行 26px — head 64px.
R95: button min-height:28px + box-sizing:border-box (56→35px);
     eyebrow 单行省略 (head 64→51px) → 折叠卡 175→~140px, 首屏推票卡提前.
"""
import asyncio
from playwright.async_api import async_playwright


def make_html(btn_css, eyebrow_css, collapsed=True):
    li_html = """
      <ul class="bv-philo-list">
        <li><span class="bv-philo-dot">•</span>风险控制永远是第一位的</li>
      </ul>
      <button class="bv-creed-more" data-creed-toggle>展开 4 条 ▾</button>
    """ if collapsed else """
      <ul class="bv-philo-list">
        <li><span class="bv-philo-dot">•</span>风险控制永远是第一位的</li>
        <li><span class="bv-philo-dot">•</span>踏空也是成功的交易</li>
      </ul>
      <button class="bv-creed-more" data-creed-toggle>收起 ▴</button>
    """
    return f"""<!DOCTYPE html><html><head><style>
      body {{ background:#0e1116; margin:0; font-family:-apple-system,'PingFang SC',sans-serif; }}
      .card {{ background:#151a21; border:1px solid #2a2f3a; border-radius:12px; padding:12px; margin:8px; }}
      .card-head {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
      .card-head h3 {{ margin:0; font-size:16px; font-weight:600; color:#eee; line-height:1.35; }}
      .card-head .card-eyebrow {{ font-size:10px; color:#888; margin:0 0 5px; display:flex; }}
      .bv-philo-list {{ margin:0; padding-left:1.2rem; color:#eee; font-size:12px; line-height:1.7; }}
      .bv-philo-list li {{ padding:2px 0; }}
      {btn_css}
      {eyebrow_css}
    </style></head><body>
      <article class="card bv-creed-card">
        <div class="card-head">
          <h3>📜 战法哲学</h3>
          <span class="card-eyebrow" id="bv-up-meta">UP主: Bryan交易随笔 · BV1JoNUzTE2i · 2026-08-17</span>
        </div>
        <div class="bv-creed-list" id="bv-creed-list">{li_html}</div>
      </article>
    </body></html>"""

# 生产修复 (R95)
R95_BTN = """
.bv-creed-more { display:block; margin:6px 0 0; padding:6px 12px; border-radius:6px;
  background:var(--bg-3,#222); color:#00e0ff; border:1px solid var(--line-1,#444);
  font-size:12px; font-weight:600; cursor:pointer; width:100%; text-align:left;
  min-height:28px; box-sizing:border-box; }
"""
R95_EYEBROW = """
.card-head .card-eyebrow { flex:1 1 100%; order:2; margin:0; max-width:100%;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
"""

# 复现旧 bug: 全局 button min-height:40px
OLD_BTN = """
button { min-height:40px; padding:8px 12px; }
.bv-creed-more { display:block; margin:6px 0 0; padding:8px 12px; border-radius:6px;
  background:#222; color:#00e0ff; border:1px solid #444; font-size:12px; font-weight:600;
  cursor:pointer; width:100%; text-align:left; }
"""


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # ── old bug reproduction ──
        page = await browser.new_page(viewport={"width": 390, "height": 844})
        await page.set_content(make_html(OLD_BTN, ""))
        old = await page.evaluate("""() => {
          var card = document.querySelector('.bv-creed-card');
          var btn = document.querySelector('.bv-creed-more');
          var head = document.querySelector('.card-head');
          return { cardH: Math.round(card.getBoundingClientRect().height),
                   btnH: Math.round(btn.getBoundingClientRect().height),
                   headH: Math.round(head.getBoundingClientRect().height) };
        }""")
        print(f"old (bug): {old}")
        assert old["btnH"] >= 40, f"expected buggy oversized button (≥40px min-height), got {old['btnH']}"
        await page.close()

        # ── R95 fixed ──
        fpage = await browser.new_page(viewport={"width": 390, "height": 844})
        await fpage.set_content(make_html(R95_BTN, R95_EYEBROW))
        fixed = await fpage.evaluate("""() => {
          var card = document.querySelector('.bv-creed-card');
          var btn = document.querySelector('.bv-creed-more');
          var head = document.querySelector('.card-head');
          var eyebrow = document.querySelector('.card-eyebrow');
          return { cardH: Math.round(card.getBoundingClientRect().height),
                   btnH: Math.round(btn.getBoundingClientRect().height),
                   headH: Math.round(head.getBoundingClientRect().height),
                   eyebrowSingleLine: eyebrow.scrollWidth <= eyebrow.clientWidth + 2 };
        }""")
        print(f"fixed (R95): {fixed}")
        # 断言: 按钮被压缩到 ≤36px (非 40px+ min-height); eyebrow 单行;
        #        head 不换行 (≤44px) — 生产基线 head 64px/btn 56px.
        assert fixed["btnH"] <= 36, f"R95: btn should be ≤36px, got {fixed['btnH']}"
        assert fixed["headH"] <= 48, f"R95: head should stay compact ≤48px, got {fixed['headH']}"
        assert fixed["eyebrowSingleLine"], "R95: eyebrow must be single line"
        # 折叠卡整体 ≤ 155px (生产基线 175px, 修复后 140px)
        assert fixed["cardH"] <= 155, f"R95: collapsed card should be ≤155px, got {fixed['cardH']}"

        # ── expanded state still fine (button exists, larger) ──
        epage = await browser.new_page(viewport={"width": 390, "height": 844})
        await epage.set_content(make_html(R95_BTN, R95_EYEBROW, collapsed=False))
        ebtn = await epage.evaluate("() => Math.round(document.querySelector('.bv-creed-more').getBoundingClientRect().height)")
        print(f"expanded btn: {ebtn}px")
        assert ebtn >= 28, "expanded toggle still tappable"
        await epage.close()

        await browser.close()
        print("[OK] R95 creed collapsed state compact")


if __name__ == "__main__":
    asyncio.run(run())
