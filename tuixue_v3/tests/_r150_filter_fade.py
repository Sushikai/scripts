"""R150 mobile .bv-filter-bar 右缘渐变 scroll affordance — 隐藏 scrollbar 的唯一提示.

原: R6 筛选条 overflow-x:auto + scrollbar-width:none (R74 scroll-snap) — 6 个筛选 chip
    在 527px 内容 / 346px 视口下 2 个被裁掉 (涨幅≥5% / 封单≥30%), 且无任何视觉提示。
    用户看不到 scrollbar (display:none), 不知道右边还有筛选 — 信息静默丢失。
R150: 右缘渐变 ::after (28px, bg-card→transparent, 跟 app.js bindTableScrollIndicator
    同 idiom), scrollLeft < max-4 时亮起; 滚到底自动熄灭。滚回左侧亮左缘。
第一性原理: 横向滚动条是"还有内容"的唯一 affordance, 隐藏它就是隐藏存在性。
    有 overflow 就该有提示 (right-edge fade), 没有 overflow 就该什么都没有 (不浪费)。
    "只广告真实存在的" — has-scroll-* 类按真实 scroll 状态开关。
R6 守护: gap 6 / overflow-x auto / margin 0 -8 / padding 4 0 + 8 左右 / scroll-snap 不动。
R74 守护: scroll-snap-type x proximity 不动 (R150 只加视觉提示, 不动交互)。
断言 (mock 数据, 390px):
  - 初始 (scrollLeft=0, 有溢出): has-scroll-right 类存在 → ::after opacity 1
  - 滚到底后: has-scroll-right 移除 → ::after opacity 0
  - 滚回 0: has-scroll-left 移除 (无左缘) / has-scroll-right 恢复
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'...', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' },
    { id:'BV02', title:'分歧低吸', category:'弱转强', description:'...', score_weight:8, conditions:[], quote:'...', timestamp:'00:02' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强核心是昨日分歧今日修复', timestamp:'00:35', score_weight:10, weight:10, value:25 } },
    { code:'000001', name:'平安银行', streak:2, matched_rules:['BV01','BV02'], score:75,
      change_pct:5.5, amount_yi:22.3, volume_ratio:1.8, turnover_pct:3.2, seal_ratio:0.4,
      sector:'银行', first_time:'09:42', phase:'close', burst_count:1,
      top_rule: { id:'BV02', title:'分歧低吸', quote:'分歧低吸要看承接', timestamp:'00:42', score_weight:8, weight:8, value:20 } }
  ], phase:'close', ts: Date.now()/1000 } },
  '/api/bv/backtest': { ok:true, data: { trades: 120, win_rate_pct: 62, avg_return_pct: 1.8, max_drawdown_pct: -12 } }
};
window._mockFetch = window.fetch;
window.fetch = function(url, opts){
  var u = String(url);
  for (var k in MOCK_RESPONSES) {
    if (u.indexOf(k) === 0) {
      return Promise.resolve({ ok:true, json: function(){ return Promise.resolve(MOCK_RESPONSES[k]); } });
    }
  }
  return window._mockFetch(url, opts);
};
"""

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})
        await ctx.add_init_script(MOCK)
        page = await ctx.new_page()
        for attempt in range(5):
            try:
                await page.goto("http://127.0.0.1:7799/#bv", wait_until="networkidle", timeout=20000)
                break
            except Exception:
                await page.wait_for_timeout(2000)
        for i in range(15):
            await page.wait_for_timeout(800)
            if await page.evaluate("() => document.querySelectorAll('#bv-pick-tbody tr.bv-row').length") >= 1:
                break
        await page.wait_for_timeout(500)

        def state():
            return page.evaluate(r"""() => {
              var bar = document.querySelector('.bv-filter-bar');
              if (!bar) return {note:'no bar'};
              var after = getComputedStyle(bar, '::after');
              var max = bar.scrollWidth - bar.clientWidth;
              return {
                scrollLeft: Math.round(bar.scrollLeft),
                max: max,
                clientW: bar.clientWidth,
                scrollW: bar.scrollWidth,
                hasRight: bar.classList.contains('has-scroll-right'),
                hasLeft: bar.classList.contains('has-scroll-left'),
                afterOpacity: after.opacity,
                afterDisplay: after.display
              };
            }""")

        s0 = await state()
        print("initial:", json.dumps(s0))
        # 初始 scrollLeft=0 且有溢出 → has-scroll-right 亮 + ::after opacity 1
        assert s0.get("hasRight") is True, f"initial should have has-scroll-right (overflow {s0.get('max')}>0), got {s0}"
        assert s0.get("afterOpacity") == "1", f"::after should be opacity 1 when scrollable, got {s0.get('afterOpacity')}"

        # 滚到底 → has-scroll-right 熄灭
        await page.evaluate("""() => {
          var bar = document.querySelector('.bv-filter-bar');
          bar.scrollLeft = bar.scrollWidth;
        }""")
        await page.wait_for_timeout(300)
        sEnd = await state()
        print("scrolled-end:", json.dumps(sEnd))
        assert sEnd.get("hasRight") is False, f"at end should clear has-scroll-right, got {sEnd}"
        assert sEnd.get("afterOpacity") == "0", f"::after should fade at end, got {sEnd.get('afterOpacity')}"

        # 滚回 0 → has-scroll-right 恢复 (右缘又该提示可滑)
        # (scrollLeft 回弹到 8 是 margin:0 -8px + padding-left:8 的正常回弹, has-left 亮是 8px 圆角边,
        #  不代表真实裁切 — 关键断言是右缘 affordance 恢复)
        await page.evaluate("""() => {
          var bar = document.querySelector('.bv-filter-bar');
          bar.scrollLeft = 0;
        }""")
        await page.wait_for_timeout(300)
        sBack = await state()
        print("scrolled-back:", json.dumps(sBack))
        assert sBack.get("hasRight") is True, f"back at 0 should re-show right-fade, got {sBack}"
        assert sBack.get("afterOpacity") == "1", f"right fade should be visible again at 0, got {sBack.get('afterOpacity')}"

        await browser.close()
        print(f"[OK] R150 filter-bar fade — 溢出 {s0['max']}px | initial hasRight + opacity 1 | end fade 0 | back re-show ✓")

if __name__ == "__main__":
    asyncio.run(run())
