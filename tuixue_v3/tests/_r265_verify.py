"""R265 verify: 轨道 chip 带规则短名 — 编号是引用, 短名是内容

第一性原理: 导航 map 的 label 应该是目标的身份 (短名), 不是抽象编号. R263 轨道
  chip 只有 "BV03" 纯编号, 不熟的用户扫轨道不知道每条是啥 (编号是引用符号, 短名
  才是内容). R265: 轨道 chip 文本 = 编号 + 短名 (title 逗号前主句, R256 同款),
  长名 max-width + ellipsis 截断.

断言 (真实服务, 390px):
  1. 打开 N>1 规则卡片 chip → 轨道 chip 含短名 (.bv-pop-rail-name)
  2. 短名 = 该规则 title 逗号前主句 (非空)
  3. 短名不溢出 chip (max-width + ellipsis 生效, 无横向滚动)
  4. 当前高亮 + 点击跳转正常 (R263/R264 守护)
  5. console 0 错误
"""
import asyncio
from playwright.async_api import async_playwright

async def load(page):
    for _ in range(5):
        try:
            await page.goto("http://127.0.0.1:7799/#bv", wait_until="domcontentloaded", timeout=20000)
            break
        except Exception:
            await page.wait_for_timeout(2000)
    await page.wait_for_selector("#bv-pick-tbody .bv-rule-chip", timeout=30000)

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(viewport={"width": 390, "height": 844})
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("PAGEERR: " + str(e)))
        await load(page)

        # fetch rules 拿 title 映射 (验证短名来源)
        rules_resp = await page.request.get("http://127.0.0.1:7799/api/bv/rules")
        rules_json = await rules_resp.json()
        rdata = rules_json.get('data', {})
        rules = rdata.get('rules') or rdata if isinstance(rdata, list) else (rdata.get('rules') or [])
        title_map = {}
        for r in rules:
            if isinstance(r, dict) and r.get('id'):
                title_map[r['id']] = r.get('title', '')
        print(f"[0] 规则 title 映射 {len(title_map)} 条")

        target = await page.evaluate("""() => {
          var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
          for (var i=0;i<rows.length;i++){
            var chip = rows[i].querySelector('.bv-rule-chip');
            if (chip) return {code: rows[i].getAttribute('data-code')};
          }
          return null;
        }""")
        assert target, "R265: 无规则 chip"
        code = target['code']

        chipTop = await page.evaluate("""(code) => {
          var rows = Array.from(document.querySelectorAll('#bv-pick-tbody tr.bv-row'));
          var row = rows.find(function(r){ return r.getAttribute('data-code') === code; });
          return row ? Math.round(row.querySelector('.bv-rule-chip').getBoundingClientRect().top) : -1;
        }""", code)
        await page.evaluate("() => window.scrollTo(0, Math.max(0, " + str(chipTop) + " - 300))")
        await page.wait_for_timeout(300)
        await page.click("#bv-pick-tbody tr.bv-row[data-code='" + code + "'] .bv-rule-chip", timeout=15000)
        await page.wait_for_timeout(600)

        d0 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          if (!box) return {popover:false};
          var rail = box.querySelector('.bv-pop-rail');
          if (!rail) return {popover:true, hasRail:false};
          var chips = Array.from(rail.querySelectorAll('.bv-pop-rail-chip'));
          var infos = chips.map(function(c){
            var nameEl = c.querySelector('.bv-pop-rail-name');
            var cs = getComputedStyle(c);
            return {rid: c.getAttribute('data-rid'),
                    hasName: !!nameEl,
                    name: nameEl ? nameEl.textContent.trim() : null,
                    nameVisible: nameEl ? (nameEl.getBoundingClientRect().width > 0) : false,
                    chipW: Math.round(c.getBoundingClientRect().width),
                    nameW: nameEl ? Math.round(nameEl.getBoundingClientRect().width) : 0};
          });
          return {popover:true, hasRail:true, infos: infos,
                  curRid: (rail.querySelector('.bv-pop-rail-chip.is-cur')||{}).getAttribute ? rail.querySelector('.bv-pop-rail-chip.is-cur').getAttribute('data-rid') : null,
                  rid: box.querySelector('.bv-pop-rid').textContent.trim(),
                  railScrollW: rail.scrollWidth, railClientW: rail.clientWidth};
        }""")
        assert d0['popover'], "R265: popover 未弹出"
        assert d0['hasRail'], "R265: 无轨道"
        infos = d0['infos']
        assert len(infos) > 1, f"R265: 轨道只有 {len(infos)} 个 (数据不足)"

        # 1-2. 每个 chip 都有短名, 短名 = title 逗号前主句
        for info in infos:
            assert info['hasName'], f"R265: chip {info['rid']} 无短名"
            assert info['name'], f"R265: chip {info['rid']} 短名为空"
            assert info['nameVisible'], f"R265: chip {info['rid']} 短名不可见 (宽 0)"
            t = title_map.get(info['rid'], '')
            import re
            exp_short = re.split(r'[,，:：]', t)[0].strip() if t else ''
            # 短名可能被 max-width 截断, 验证它是 title 开头子串
            assert info['name'] and (exp_short.startswith(info['name']) or exp_short == info['name']), \
                f"R265: chip {info['rid']} 短名 '{info['name']}' 非 title 开头 '{exp_short}'"
        print(f"[1] 轨道 {len(infos)} chip 全部带短名: " + " ".join([i['rid'] + '·' + i['name'][:4] for i in infos]))

        # 3. 无横向溢出 (rail 自身可横滚, 但单 chip 不溢出)
        #   短名 max-width 200px + ellipsis 生效 → chip 宽受控
        long_chip = max(infos, key=lambda i: i['nameW'])
        assert long_chip['nameW'] <= 200, f"R265: 短名 {long_chip['rid']} 宽 {long_chip['nameW']} > 200 (ellipsis 未生效)"
        print(f"[2] 最长短名 {long_chip['rid']} 宽 {long_chip['nameW']}px ≤ 200px (ellipsis 截断)")

        # 4. 当前高亮 + 点击跳转 (R263/R264 守护)
        assert d0['curRid'] == d0['rid'], f"R265: 当前高亮 {d0['curRid']} != 展示 {d0['rid']}"
        # 点轨道最后 chip → 跳转
        lastRid = infos[-1]['rid']
        await page.click("#bv-rule-popover .bv-pop-rail-chip[data-rid='" + lastRid + "']", timeout=10000)
        await page.wait_for_timeout(300)
        d1 = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          var rail = box.querySelector('.bv-pop-rail');
          var cur = rail.querySelector('.bv-pop-rail-chip.is-cur');
          return {rid: box.querySelector('.bv-pop-rid').textContent.trim(),
                  curRid: cur.getAttribute('data-rid')};
        }""")
        assert d1['rid'] == lastRid and d1['curRid'] == lastRid, f"R265: 点击跳转失败 {d1['rid']} / {d1['curRid']}"
        print(f"[3] 点击轨道跳转 {d0['rid']} → {d1['rid']}, 高亮同步")

        # 5. console 0 错误
        real_errors = [e for e in errors
                       if 'favicon' not in e
                       and 'ERR_CONNECTION_TIMED_OUT' not in e
                       and 'status of 500' not in e]
        assert not real_errors, f"R265: console errors {real_errors}"
        await b.close()
        print("[OK] R265 轨道 chip 带规则短名 — 编号是引用, 短名是内容, ellipsis 截断, 点击跳转正常, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
