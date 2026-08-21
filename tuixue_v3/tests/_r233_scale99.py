"""R233: mobile bv-row active scale 0.985→0.99 — 按压微缩减小

第一性原理: bv-row :active transform scale(0.985) (R51).
  1.5% 缩放在 75px 卡高下 = 1.1px 偏小, 用户几乎看不见.
  视觉边界全部优化 (R224-R231) 后用户对按压反馈预期更明确.
  scale 1% (0.75px) 仍可见但不抢镜, 跟 R232 即时反馈节奏一致.

断言 (真实服务, 390px):
  1. bv-row :active transform scale(0.99)
  2. bv-row 默认状态 scale 1
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
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
            break
        await page.wait_for_timeout(500)

PROBE = r"""() => {
  // 模拟 :active — 通过 mousedown 触发表单内任意行, 探测 transform
  var row = document.querySelector('.view-bv .bv-table tr.bv-row');
  if (!row) return null;
  // 默认 transform
  var defaultT = getComputedStyle(row).transform;
  // 模拟 active — dispatch mousedown
  var ev = new MouseEvent('mousedown', {bubbles: true, cancelable: true});
  row.dispatchEvent(ev);
  // 微等待后读取 (transition 时间很短)
  return new Promise(resolve => {
    setTimeout(() => {
      var activeT = getComputedStyle(row).transform;
      // mouseup 复位
      var ev2 = new MouseEvent('mouseup', {bubbles: true, cancelable: true});
      row.dispatchEvent(ev2);
      resolve({defaultT: defaultT, activeT: activeT});
    }, 30);
  });
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        await load(page)

        # 获取 row 屏幕坐标
        box = await page.locator('.view-bv .bv-table tr.bv-row').first.bounding_box()
        cx, cy = box['x'] + box['width']/2, box['y'] + box['height']/2

        # 默认 transform
        defaultT = await page.evaluate(f"() => getComputedStyle(document.elementFromPoint({cx}, {cy}).closest('tr.bv-row')).transform")
        print(f"default transform: {defaultT}")

        # 真实 mouse down (触发 :active)
        await page.mouse.move(cx, cy)
        await page.mouse.down()
        await page.wait_for_timeout(50)
        activeT = await page.evaluate(f"() => getComputedStyle(document.elementFromPoint({cx}, {cy}).closest('tr.bv-row')).transform")
        print(f"active transform:   {activeT}")
        await page.mouse.up()

        # active 状态下 transform 应该含 scale(0.99)
        assert activeT != defaultT, f"R233: active transform ({activeT}) 应跟 default ({defaultT}) 不同"
        assert '0.99' in activeT, f"R233: active transform 应含 0.99 (scale), got {activeT}"
        assert '0.985' not in activeT, f"R233: active transform 不应含 0.985, got {activeT}"

        await b.close()
        print(f"[OK] R233 bv-row active scale 0.985→0.99 — 按压微缩减小, 即时可见 ✓")

if __name__ == "__main__":
    asyncio.run(run())