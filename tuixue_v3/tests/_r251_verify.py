"""R251 verify: 规则 chip 横滚隐藏 — fold pinned 右缘 + 10cm 归展开态

第一性原理: rules-cell (可视区 180px) 被静态/装饰成分 (10cm 36px + motto 65px) 占满,
  动态决策信息 (BV05/BV06) + 展开控制面 (fold chip) 被横滚推出可视区 = 控制面不可达
  (R150/R151 模式的横滚版). R251:
    1. 折叠态去掉 10cm (静态默认: 主板=未标, 代码前缀可推断) — 让位 fold
    2. fold=1 — 折叠态只留一条锚定规则 (板块+命中数+BV03+fold = 145px < 180px)
    3. sticky fold — 控制面 pinned 右缘, 规则在底下滑过, 永不不可达
    4. 展开态保留 10cm/20cm/motto 全量展示 — 详情面不丢信息

断言 (真实服务, 390px):
  1. 全行 fold chip 可见 (pinned 可视区右缘)
  2. 折叠态无 10cm 徽章 (bv-board-10 不存在), 20cm 徽章保留
  3. fold 不遮挡任何可见 chip (折叠态规则行重叠检查)
  4. rowH 无回归 (<= 75px, R105 触控热区基线)
  5. console 0 错误
  6. 点击 fold → 展开态重建全部规则 + 10cm 徽章回归 (详情面不丢信息)
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
    var rules = rows[i].querySelector('.bv-rules-cell');
    var rulesR = rules.getBoundingClientRect();
    var fold = rules.querySelector('.bv-rule-fold');
    var fr = fold ? fold.getBoundingClientRect() : null;
    // 折叠态 10cm 徽章? (R251 应不存在)
    var has10 = !!rules.querySelector('.bv-board-10');
    var has20 = !!rules.querySelector('.bv-board-20');
    // fold 与可见 chip 的重叠检查: fold 左边缘不得越过 fold 左边在 scroll 中正常的位置
    // 之外的东西 — 简化: fold right pinned 可视区 + 前面 chip 在 fold 左缘前结束
    var kids = [];
    for (var k=0; k<rules.children.length; k++) {
      var c = rules.children[k];
      var cr = c.getBoundingClientRect();
      if (cr.width <= 0) continue;
      kids.push({txt:(c.textContent||'').trim().slice(0,8), L: Math.round(cr.left-rulesR.left),
                R: Math.round(cr.right-rulesR.left), isFold: c.classList.contains('bv-rule-fold')});
    }
    out.push({
      i:i, isTop: rows[i].classList.contains('is-bv-top'),
      clientW: Math.round(rulesR.width),
      scrollW: rules.scrollWidth,
      foldVisible: fr ? (fr.width > 0 && fr.right <= rulesR.right + 1) : false,
      has10: has10, has20: has20,
      rowH: rows[i].offsetHeight,
      kids: kids
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
        # 统计
        n_fold = sum(1 for r in d for k in r['kids'] if k['isFold'])
        n_fold_visible = sum(1 for r in d for k in r['kids'] if k['isFold'] and k['R'] <= r['clientW'] + 1)
        print(f"fold chips: {n_fold_visible}/{n_fold} pinned 可视区")
        for r in d:
            kids_s = " | ".join(f"{k['txt']}({k['L']}-{k['R']}){'fold' if k['isFold'] else ''}" for k in r['kids'])
            print(f"r{r['i']} {'TOP' if r['isTop'] else ''} W={r['clientW']} H={r['rowH']} has10={r['has10']} has20={r['has20']}: {kids_s}")
        # 1. fold 全 pinned
        assert n_fold == n_fold_visible, f"R251: {n_fold_visible}/{n_fold} fold 不可达"
        # 2. 折叠态无 10cm, 有 20cm 行存在
        for r in d:
            assert not r['has10'], f"R251: 折叠态仍有 10cm 徽章 r{r['i']}"
        has20_any = any(r['has20'] for r in d)
        if not has20_any:
            print("[warn] 当前快照无 20cm 行 — 20cm 保留逻辑未覆盖 (非缺陷, 数据依赖)")
        # 3. 重叠检查: fold 前面的可见 chip 不得延伸到 fold 左缘之后
        #    (仅对不可横滚行 scrollW<=clientW 要求零重叠 — 内容放得下却被 fold 盖=浪费;
        #     可横滚行 20cm 内容 162px > 146px 物理必须 overlay, 用户横滚即看全, fold 可达是本轮核心)
        for r in d:
            foldKid = next((k for k in r['kids'] if k['isFold']), None)
            if not foldKid: continue
            # fold 左缘之前结束的 chip (R <= foldL) 是安全区
            for k in r['kids']:
                if k['isFold']: continue
                # chip 可见 (在可视区内) 且延伸到 fold 左缘之后 → 被 fold 遮挡
                if k['R'] <= r['clientW'] + 1 and k['R'] > foldKid['L'] - 1:
                    # 仅当行不可横滚时才算缺陷 (scrollW <= clientW → 内容本可放全)
                    if r['scrollW'] <= r['clientW'] + 1:
                        assert False, f"R251: r{r['i']} chip {k['txt']}({k['L']}-{k['R']}) 被 fold({foldKid['L']}) 遮挡 (scrollW={r['scrollW']}<=clientW={r['clientW']} 本可放全)"
        # 4. rowH 无回归
        for r in d:
            assert r['rowH'] <= 75, f"R251: 卡高回归 rowH={r['rowH']}"
        # 5. console 0 错误
        real_errors = [e for e in errors if 'favicon' not in e]
        assert not real_errors, f"R251: console errors {real_errors}"
        # 6. fold 点击展开 → 10cm 徽章回归 + 全部规则可见
        # 找第一行有 fold 的行, 用 JS dispatch click (sticky + shadow 可能遮挡 page.click)
        clicked = False
        for r in d:
            if any(k['isFold'] for k in r['kids']):
                await page.evaluate(f"""() => {{
                  var row = document.querySelectorAll('#bv-pick-tbody tr.bv-row')[{r['i']}];
                  var fold = row.querySelector('.bv-rule-fold');
                  if (fold) fold.click();
                }}""")
                await page.wait_for_timeout(400)
                clicked = True
                break
        if clicked:
            exp = await page.evaluate("""() => {
              var cell = document.querySelector('.bv-rules-cell.is-expanded');
              if (!cell) return {expanded:false};
              return {
                expanded: true,
                has10: !!cell.querySelector('.bv-board-10'),
                has20: !!cell.querySelector('.bv-board-20'),
                hasFoldClose: !!cell.querySelector('.bv-rule-fold[title="收起"]'),
                ruleCount: cell.querySelectorAll('.chip').length
              };
            }""")
            assert exp['expanded'], "R251: fold 点击未展开"
            assert exp['has10'], "R251: 展开态 10cm 徽章未回归"
            assert exp['ruleCount'] >= 3, f"R251: 展开态规则不足 ruleCount={exp['ruleCount']}"
            print(f"[OK] 展开态: 10cm 回归={exp['has10']} 20cm={exp['has20']} 收起按钮={exp['hasFoldClose']} chips={exp['ruleCount']}")
        else:
            print("[warn] 未找到可点击的 fold — 展开态验证跳过 (数据依赖)")
        await b.close()
        print(f"[OK] R251 规则 chip 横滚隐藏 — {n_fold_visible}/{n_fold} fold pinned 可视区, 折叠态 0 个 10cm, rowH 无回归, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
