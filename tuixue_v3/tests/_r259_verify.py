"""R259 verify: popover meta 权重 chip 视觉统一 — 同类信号同视觉

第一性原理: meta 区 (category + weight) 是规则的两个属性元数据, 属同类信息.
  R258 把操作区整理成 "chip 背景 + 文本" 统一; meta 区的 weight (权重) 是
  唯一漏网的裸文本 (无背景) — 与 category chip 视觉不对称, 扫一眼分不清是
  属性标签还是正文. R259: weight 与 category 同款 bg-2 chip + tabular-nums.

断言 (真实服务, 390px):
  1. popover 弹出
  2. meta 区 category chip 有背景 (bg-2)
  3. weight 有背景 (与 cat 同款 chip) — 核心断言
  4. weight 与 cat 高度对齐 (同 17px chip 高)
  5. meta 区无横向溢出 (flex-wrap 兜底)
  6. console 0 错误
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

        # Playwright 原生 click (自动等待存在/可见, 避免 30s 自动刷新重建 DOM 的竞态)
        await page.click("#bv-pick-tbody .bv-rule-chip", timeout=15000)
        await page.wait_for_timeout(600)

        d = await page.evaluate("""() => {
          var box = document.getElementById('bv-rule-popover');
          if (!box) return {popover:false};
          var meta = box.querySelector('.bv-pop-meta');
          var cat = box.querySelector('.bv-pop-cat');
          var weight = box.querySelector('.bv-pop-weight');
          function info(el){ if(!el) return null;
            var r = el.getBoundingClientRect(); var cs = getComputedStyle(el);
            return {text: el.textContent.trim(), w: Math.round(r.width), h: Math.round(r.height),
                    fs: cs.fontSize, color: cs.color, bg: cs.backgroundColor,
                    pad: cs.padding, radius: cs.borderRadius,
                    inMeta: r.left >= meta.getBoundingClientRect().left - 1 &&
                            r.right <= meta.getBoundingClientRect().right + 1};
          }
          var catInfo = info(cat), weightInfo = info(weight);
          var metaOverflowX = meta.scrollWidth > meta.clientWidth + 1;
          return {popover:true, cat: catInfo, weight: weightInfo,
                  metaOverflowX: metaOverflowX,
                  hasWeight: !!weight,
                  weightSameBg: weightInfo && catInfo && weightInfo.bg === catInfo.bg};
        }""")
        assert d['popover'], "R259: popover 未弹出"
        assert d['cat'], "R259: 无 category chip"
        assert d['hasWeight'], "R259: 无 weight"
        # 2-3. category 有背景 + weight 同款背景 (核心断言)
        assert d['cat']['bg'] != 'rgba(0, 0, 0, 0)', f"R259: cat 无背景 {d['cat']}"
        assert d['weightSameBg'], f"R259: weight 背景与 cat 不一致 {d['weight']} vs {d['cat']}"
        print(f"[1] meta 区: cat '{d['cat']['text']}' (bg {d['cat']['bg']}) + weight '{d['weight']['text']}' 同款 chip")
        # 4. 高度对齐 (同 chip 高, 容差 2px)
        assert abs(d['weight']['h'] - d['cat']['h']) <= 2, f"R259: weight/cat 高度不对齐 {d['weight']} vs {d['cat']}"
        print(f"[2] weight/cat 高度对齐: {d['weight']['h']}px = {d['cat']['h']}px (chip 节奏)")
        # 5. meta 无横向溢出
        assert not d['metaOverflowX'], "R259: meta 区横向溢出"
        print("[3] meta 区无横向溢出 (flex-wrap 兜底)")
        # 6. console 0 错误
        real_errors = [e for e in errors if 'favicon' not in e and 'ERR_CONNECTION_TIMED_OUT' not in e]
        assert not real_errors, f"R259: console errors {real_errors}"
        await b.close()
        print("[OK] R259 popover meta weight chip 视觉统一 — 同类信号同视觉, chip 节奏对齐, console 0 错误")

if __name__ == "__main__":
    asyncio.run(run())
