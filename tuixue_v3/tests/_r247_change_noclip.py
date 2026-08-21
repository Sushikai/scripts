"""R247: 验证 change 格裁剪修复 — 涨幅去 % 后 56px 内完整显示, 零精度损失

第一性原理: '+10.03%' 7 字形渲染 61px > 56px 盒 → text-overflow ellipsis 截成
  '+10.0…' (末位+% 丢失 5px). '%' 是涨幅专属列的同列重复单位, 列头语义 (thead
  隐藏但 DOM 仍在) 已声明单位; 方向由 +/- 与红绿双编码. 去 % → 6 字形 48px
  完整显示, 不偷 col2 地板.

断言 (真实服务, 390px):
  1. change 文本以 +/- 开头 (方向保留)
  2. 所有行 change 格无裁剪 (scrollW <= clientW+1)
  3. 正数格式 '+10.03' 保留 2 位小数 (精度零损失)
  4. 无 '…' 省略号泄漏 (text-overflow 未触发)
  5. 20cm 行 '+20.00' 也不裁剪 (最宽文本边界)
"""
import asyncio
from playwright.async_api import async_playwright

async def load(page):
    for _ in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    for _ in range(20):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length >= 1"):
            break
        await page.wait_for_timeout(500)

PROBE = r"""() => {
  var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
  var out = [];
  for (var i=0; i<rows.length; i++) {
    var td = rows[i].querySelector('td:nth-child(4)');
    if (!td) continue;
    var r = td.getBoundingClientRect();
    var t = (td.textContent||'').trim();
    out.push({
      text: t,
      clientW: Math.round(r.width),
      scrollW: td.scrollWidth,
      clip: td.scrollWidth > Math.round(r.width) + 1,
      hasEllipsis: t.indexOf('…') !== -1 || t.indexOf('...') !== -1,
      hasSign: t.indexOf('+') === 0 || t.indexOf('-') === 0
    });
  }
  return out;
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)
        d = await page.evaluate(PROBE)
        assert len(d) >= 1, "无 change 行"
        for c in d:
            print(f"'{c['text']}': clientW={c['clientW']} scrollW={c['scrollW']} clip={c['clip']} ellipsis={c['hasEllipsis']} sign={c['hasSign']}")
        for c in d:
            assert c['hasSign'], f"R247: 方向丢失 '{c['text']}'"
            assert not c['clip'], f"R247: change 仍裁剪 '{c['text']}' ({c['scrollW']}>{c['clientW']})"
            assert not c['hasEllipsis'], f"R247: 省略号泄漏 '{c['text']}'"
            # 精度: 正数带 2 位小数 (无 %) 格式 +XX.XX
            if c['text'][0] == '+':
                assert c['text'].count('.') == 1 and len(c['text'].split('.')[1]) == 2, \
                    f"R247: 精度损失 '{c['text']}'"
        # 最宽边界: 20cm 行应显示 +20.xx
        max20 = [c for c in d if c['text'].startswith('+20')]
        if max20:
            for c in max20:
                assert not c['clip'], f"R247: +20.xx 被裁剪 '{c['text']}'"
        await b.close()
        print(f"[OK] R247 change 格去 % 无裁剪 — {len(d)} 行方向/精度全保留, 最宽 '{max(d, key=lambda c: c['scrollW'])['text']}' scrollW={max(d, key=lambda c: c['scrollW'])['scrollW']}px")

if __name__ == "__main__":
    asyncio.run(run())
