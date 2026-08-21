"""R94 操作控件不能被长文本挤出工位 — 长 count 不得把排序按钮挤到第二行.

原: #bv-pick-count 是 h3 内裸 inline span → flex 换行按"假设主轴尺寸"(max-content)
    判断: count 撑满 h3 (~330px) → 排序按钮失去同行工位, flex-wrap 把它挤到
    第二行 (head 28→61→82px). 元信息(计数)抢占了操作(排序)的位置.
R94: ≤768px h3 flex:1 1 0 (假设主轴尺寸为 0, 永不触发换行) + count overflow-wrap:
    anywhere (长 count 在 h3 内部断行, 不撑大 max-content) → 短 count 同行
    28px 不变, 长 count 行内换行不挤走排序按钮.
"""
import asyncio
from playwright.async_api import async_playwright

CSS = """
body { background:#0e1116; margin:0; font-family:-apple-system,'PingFang SC',sans-serif; }
.card { background:#151a21; border:1px solid #2a2f3a; border-radius:12px; padding:12px; margin:8px; }
.card-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
h3 { margin:0; font-size:15px; font-weight:600; color:#eee; line-height:1.35; flex:1 1 0; min-width:0; }
/* R94 fix */
.bv-sort-btn { font-size:12px; padding:4px 10px; border-radius:6px; border:1px solid #444; background:#222; color:#00e0ff; font-weight:600; cursor:pointer; margin-left:6px; flex-shrink:0; white-space:nowrap; }
.bv-pick-count { font-size:11px; line-height:1.5; color:#888; overflow-wrap:anywhere; }
"""

HTML = """
<!DOCTYPE html><html><head><style>__CSS__</style></head><body>
<article class="card">
  <div class="card-head">
    <h3>🎯 实时推票 <span class="bv-pick-count" id="bv-pick-count">__COUNT__</span></h3>
    <button class="bv-sort-btn" id="bv-sort-btn"><span id="bv-sort-label">⇅ score</span></button>
  </div>
</article>
</body></html>
"""

# 真实最坏情况: 过滤 + 午休阶段 + 快照时间 + 陈旧 5 分钟 (R29/R28 拼接产物)
CASES = [
    ("short", "(扫描 ≥50 / 命中 50)"),
    ("filtered-stale", "(过滤后 12 / 全部 50 · 🟡午休 快照 12:00 ⚠️ 陈旧 5分钟)"),
    ("rule-filter", "(命中 <b>6</b> / 50 · 🔍 BV02 弱转强) <a class=\"bv-rule-clear\" href=\"javascript:void(0)\">清除</a>"),
]


async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for label, count in CASES:
            page = await browser.new_page(viewport={"width": 390, "height": 500})
            await page.set_content(HTML.replace("__CSS__", CSS).replace("__COUNT__", count))
            geo = await page.evaluate("""() => {
              var head = document.querySelector('.card-head').getBoundingClientRect();
              var h3 = document.querySelector('h3').getBoundingClientRect();
              var cnt = document.querySelector('#bv-pick-count').getBoundingClientRect();
              var btn = document.querySelector('#bv-sort-btn').getBoundingClientRect();
              return {
                headH: Math.round(head.height),
                btnAtTitleRow: btn.top < h3.top + 22,           // 按钮顶部在标题行带内 (未被挤到下方)
                noHOverlap: btn.right <= cnt.left + 2 || btn.left >= cnt.right - 2,  // 与计数无水平重叠
                btnCenteredOk: Math.abs((btn.top + btn.bottom)/2 - (h3.top + h3.bottom)/2) < 12
              };
            }""")
            print(f"[{label}] {geo}")
            assert geo["btnAtTitleRow"], f"R94: sort btn evicted from title row ({label})"
            assert geo["noHOverlap"], f"R94: sort btn overlaps count ({label})"
            assert geo["btnCenteredOk"], f"R94: sort btn not centered against h3 ({label})"
            await page.close()
        await browser.close()
        print("[OK] R94 count never evicts sort button")


if __name__ == "__main__":
    asyncio.run(run())
