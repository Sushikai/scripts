"""R250 verify: 代码列完整 + turnover 单行 + crown 完整可见 + rowH 无回归

第一性原理: 卡片是"身份 + 信号"的水平零和游戏. 身份 (代码) 和 row2 信号
  (换手三信号) 都必须在任意行内容波动下不被挤压裁剪:
    1) col1 auto=45px 被 R246 sector max-width 钉死 → 6 位代码 (49px) 被裁
    2) col2 minmax(0,1fr) 无地板 → 20cm 行 change 宽 9px 把 col2 压到 96.8
       < turnover 内容 99px → flex-wrap 换行 → rowH 84 回归
  R250 双地板: col1 minmax(52px,56px) 保代码, col2 minmax(99px,1fr) 保单行.
  crown 移出 td (absolute 装饰不占身份列 scrollWidth).

断言 (真实服务, 390px):
  1. 全行 code 完整 (codeScroll <= col1 宽 + 1) — 含 TOP1 行
  2. 全行 rowH <= 75 (无 wrap 回归)
  3. turnover 单行 (turnH <= 24)
  4. crown 完整可见 (right <= 卡片右缘, 不被裁)
  5. console 0 错误
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
    for _ in range(25):
        await page.wait_for_timeout(800)
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length >= 1"):
            break
        await page.wait_for_timeout(500)

PROBE = r"""() => {
  var rows = document.querySelectorAll('#bv-pick-tbody tr.bv-row');
  var out = [];
  for (var i=0; i<rows.length; i++) {
    var row = rows[i];
    var codeTd = row.querySelector('td:nth-child(1)');
    var codeR = codeTd.getBoundingClientRect();
    // 代码文本宽度 (link 内文本节点) — scrollWidth 会被 absolute crown 污染 (65), 文本自身才是裁剪判定
    var codeLink = row.querySelector('.code-link');
    var codeTextW = 0;
    if (codeLink) {
      for (var ck=0; ck<codeLink.childNodes.length; ck++) {
        if (codeLink.childNodes[ck].nodeType === 3) {
          var cspan = document.createElement('span');
          cspan.textContent = codeLink.childNodes[ck].textContent;
          cspan.style.cssText = getComputedStyle(codeLink).cssText;
          cspan.style.padding = '0'; cspan.style.display = 'inline';
          codeLink.appendChild(cspan);
          codeTextW = cspan.getBoundingClientRect().width;
          codeLink.removeChild(cspan);
        }
      }
    }
    var turn = row.querySelector('td:nth-child(5)');
    var turnR = turn.getBoundingClientRect();
    var name = row.querySelector('td:nth-child(2)');
    var crown = row.querySelector('.bv-top-crown');
    var crownR = crown ? crown.getBoundingClientRect() : null;
    var rowR = row.getBoundingClientRect();
    out.push({
      i: i,
      codeTextW: Math.round(codeTextW),
      codeW: Math.round(codeR.width),
      codeClip: codeTextW > codeR.width + 0.5,
      turnH: Math.round(turnR.height),
      rowH: row.offsetHeight,
      isTop: !!crown,
      crownClip: crownR ? (crownR.left < rowR.left - 0.5 || crownR.right > rowR.right + 0.5) : null,
      crownW: crownR ? Math.round(crownR.width) : null,
      crownVisible: crownR ? (crownR.width > 0 && crownR.height > 0) : null
    });
  }
  return out;
}"""

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await load(page)
        d = await page.evaluate(PROBE)
        assert len(d) >= 1, "无推票行"
        for r in d:
            mark = ""
            if r['codeClip']: mark += " [CODE CLIP]"
            if r['turnH'] > 24: mark += " [TURN WRAP]"
            if r['rowH'] > 75: mark += " [ROWH]"
            if r['crownClip']: mark += " [CROWN CLIP]"
            print(f"r{r['i']:2d} codeTextW={r['codeTextW']:3d} codeW={r['codeW']:3d} turnH={r['turnH']:2d} rowH={r['rowH']} top={r['isTop']} crownW={r['crownW']} crownVis={r['crownVisible']}{mark}")
        # 1. code 完整
        for r in d:
            assert not r['codeClip'], f"R250: code 裁剪 r{r['i']} textW={r['codeTextW']} w={r['codeW']}"
        # 2. rowH <= 75
        for r in d:
            assert r['rowH'] <= 75, f"R250: 卡高回归 r{r['i']} rowH={r['rowH']}"
        # 3. turnover 单行
        for r in d:
            assert r['turnH'] <= 24, f"R250: turnover 换行 r{r['i']} turnH={r['turnH']}"
        # 4. crown 完整
        tops = [r for r in d if r['isTop']]
        assert tops, "无 TOP1 行"
        for r in tops:
            assert not r['crownClip'], f"R250: crown 裁剪 r{r['i']}"
            assert r['crownVisible'], f"R250: crown 不可见 r{r['i']}"
        # 5. console 0 错误
        real_errors = [e for e in errors if 'favicon' not in e]
        assert not real_errors, f"R250: console errors {real_errors}"
        # 截图
        await page.evaluate("() => document.querySelector('#bv-pick-tbody tr.bv-row').scrollIntoView({block:'center'})")
        await page.wait_for_timeout(300)
        await page.screenshot(path="/tmp/_r250_final.png")
        await b.close()
        print(f"[OK] R250 双地板 — {len(d)} 行 code 全完整 (含 TOP1), turnover 全单行, rowH 全 75, crown 完整, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
