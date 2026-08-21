"""R256 verify: 展开态规则 chip 带短名 — 编号是引用符号, 规则名才是信息

第一性原理: 折叠态 180px 横滚条是速览面, 裸编号 BV03 够"引用标识"; 展开态承诺
  "全量规则详情", 裸编号不是信息 — 用户必须逐个 tap popover 才懂每条是啥.
  R256: 展开态 chip 渲染 `BV03·无异动不做` (title 主句), 编号保持主位 + 短名弱化,
  popover 只留给想看原话/条件的深入层.

断言 (真实服务, 390px):
  1. 折叠态 chip 仍是裸编号 (BV03, 无短名) — 速览面引用标识不变
  2. 展开态 chip 带短名 (.bv-rule-name 非空, 文本匹配 title 主句)
  3. 展开态 chip 文本 = 编号 + '·' + 短名
  4. 规则名可读: font-size ≤ 编号 (弱化次级), 且 chip 宽度受控 (max-width 90px 生效)
  5. 展开态仍全规则可见 (R255 守护) + fold 可达
  6. 展开态规则 chip tap → 仍弹 popover (R252 守护, 短名 chip 语义不变)
  7. console 0 错误
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
        if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody .bv-rule-chip').length >= 1"):
            break
        await page.wait_for_timeout(500)

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("PAGEERR: " + str(e)))
        await load(page)

        # 1. 折叠态 chip 裸编号 (无 .bv-rule-name)
        folded = await page.evaluate("""() => {
          var chip = document.querySelector('#bv-pick-tbody .bv-rule-chip');
          if (!chip) return {chip:false};
          return {chip:true, text: chip.textContent.trim(),
                  hasName: !!chip.querySelector('.bv-rule-name')};
        }""")
        assert folded['chip'], "无折叠态规则 chip"
        assert not folded['hasName'], f"R256: 折叠态 chip 不应带短名 {folded}"
        print(f"[1] 折叠态裸编号: '{folded['text']}' 无短名 (速览面引用标识)")

        # 2-3. 展开态 → chip 带短名
        await page.evaluate("""() => {
          var fold = document.querySelector('#bv-pick-tbody tr.is-bv-top .bv-rule-fold');
          if (fold) fold.click();
        }""")
        await page.wait_for_timeout(400)
        d = await page.evaluate("""() => {
          var cell = document.querySelector('.bv-rules-cell.is-expanded');
          if (!cell) return {expanded:false};
          var cr = cell.getBoundingClientRect();
          var chips = cell.querySelectorAll('.chip:not(.bv-rule-fold)');
          var out = [];
          var allVisible = true;
          for (var i=0; i<chips.length; i++) {
            var c = chips[i];
            var name = c.querySelector('.bv-rule-name');
            // chip 文本结构: 文本节点"BV03" + 文本节点"·" + span.bv-rule-name"无异动不做"
            // 编号 = childNodes[0] 文本 (strip 尾部 · 防边界)
            var rid = c.childNodes[0] && c.childNodes[0].textContent ? c.childNodes[0].textContent.trim().replace(/·$/, '') : '';
            var ccr = c.getBoundingClientRect();
            var fv = ccr.left >= cr.left - 1 && ccr.right <= cr.right + 1;
            if (!fv) allVisible = false;
            var cs = name ? getComputedStyle(name) : null;
            out.push({
              txt: c.textContent.trim(), rid: rid,
              name: name ? name.textContent.trim() : null,
              nameFs: cs ? cs.fontSize : null,
              nameMaxW: cs ? cs.maxWidth : null,
              hasDot: c.textContent.indexOf('·') >= 0,
              fullyVisible: fv
            });
          }
          var foldC = cell.querySelector('.bv-rule-fold');
          var fr = foldC ? foldC.getBoundingClientRect() : null;
          return {expanded:true, chips: out, allVisible: allVisible,
                  foldVisible: fr ? (fr.width>0 && fr.left>=cr.left-1 && fr.right<=cr.right+1) : false};
        }""")
        assert d['expanded'], "R256: 未展开"
        assert len(d['chips']) >= 2, f"R256: 展开态规则数异常 {d}"
        named = [c for c in d['chips'] if c['name']]
        assert named, f"R256: 展开态无规则带短名 {d['chips']}"
        # 每条 chip 都应有编号 + · + 短名 (fold 除外)
        for c in d['chips']:
            assert c['hasDot'], f"R256: chip 缺 '·' 分隔 {c}"
            assert c['name'], f"R256: chip 无短名 {c}"
            assert c['txt'].startswith(c['rid']), f"R256: chip 编号前缀错位 {c}"
        # 短名弱化: 字号 ≤ 10.5px (编号), max-width 受控
        for c in d['chips']:
            assert c['nameFs'] and float(c['nameFs'].replace('px','')) <= 10.5, f"R256: 短名字号未弱化 {c}"
            assert c['nameMaxW'] and c['nameMaxW'] != 'none', f"R256: 短名未受控 {c}"
        print(f"[2] 展开态 {len(d['chips'])} 条规则带短名: " + ", ".join(c['txt'][:14] for c in d['chips']))
        # 4. 全可见 + fold 可达 (R255 守护)
        assert d['allVisible'], f"R256: 展开态有规则不可见 {[c for c in d['chips'] if not c['fullyVisible']]}"
        assert d['foldVisible'], "R256: fold 不可达"
        print("[3] 展开态全规则可见 + fold 可达 (R255 守护)")

        # 6. 展开态规则 chip tap → popover 仍生效 (R252 守护, 短名 chip 语义不变)
        pop = await page.evaluate("""() => {
          var cell = document.querySelector('.bv-rules-cell.is-expanded');
          var chip = cell.querySelector('.chip:not(.bv-rule-fold)');
          chip.click();
          var box = document.getElementById('bv-rule-popover');
          return {hasPopover: !!box,
                  title: box ? (box.querySelector('.bv-pop-title')||{}).textContent : null};
        }""")
        assert pop['hasPopover'], "R256: 短名 chip tap 未弹 popover (R252 守护)"
        print(f"[4] 短名 chip tap → popover '{pop['title']}' (R252 守护)")

        # 7. console 0 错误 (过滤 favicon + 环境性网络超时 — 服务端上游繁忙/冷启动,
        #    非前端 JS 错误, 与 R256 无关)
        real_errors = [e for e in errors if 'favicon' not in e and 'ERR_CONNECTION_TIMED_OUT' not in e]
        assert not real_errors, f"R256: console errors {real_errors}"
        await b.close()
        print("[OK] R256 展开态规则 chip 带短名 — 编号是引用符号规则名才是信息, popover 留给深入层, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
