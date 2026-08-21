"""R151 mobile .bv-sector-bar 右缘渐变 scroll affordance — R18 聚合条 2 pill 被裁 133px.

原: R18 板块命中聚合条 overflow-x:auto + scrollbar-width:none — 4 个板块 pill 463px 内容 /
    330px 视口下 2 个被裁掉 (133px), 无 scrollbar 无提示 = 用户不知道"电池/新能源车"板块存在。
    R93 已保证空时不占首屏, 但有时 (≥3 板块) 内容就是超宽。
R151: 右缘渐变 ::after (28px, bg-card→transparent), 跟 R150 filter-bar 完全同 idiom —
    scrollLeft<max-4 亮 / 滚到底灭 / 滚回亮左缘。has-scroll-* 按真实 scroll 状态开关。
第一性原理: 聚合条的意义是"一眼扫完最强板块" (R86 pill 可视化), 被裁的板块 = 存在但不可见。
    横向滚动条是唯一 affordance, 隐藏它就是隐藏板块的存在性。
    R150 修的 filter-bar 和 R151 修的 sector-bar 是同一 first-principles gap 的两个实例 —
    所有 hidden-scrollbar 横向容器都要有 right-edge fade。
R18/R24 守护: flex/gap/overflow/padding/pill 样式/onclick 过滤 不动。scroll-snap 无。
断言 (mock 数据 4 板块, 390px):
  - sector-bar 有溢出 (max>0), 初始 has-scroll-right + ::after opacity 1
  - 滚到底 → has-scroll-right 灭 + opacity 0
  - 滚回 0 → 右缘恢复 opacity 1
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
      top_rule: { id:'BV02', title:'分歧低吸', quote:'分歧低吸要看承接', timestamp:'00:42', score_weight:8, weight:8, value:20 } },
    { code:'300750', name:'宁德时代', streak:1, matched_rules:['BV01'], score:65,
      change_pct:3.2, amount_yi:66.1, volume_ratio:1.2, turnover_pct:1.8, seal_ratio:0.2,
      sector:'电池', first_time:'09:50', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'...', timestamp:'00:50', score_weight:10, weight:10, value:15 } },
    { code:'002594', name:'比亚迪', streak:1, matched_rules:['BV02'], score:60,
      change_pct:2.1, amount_yi:44.8, volume_ratio:1.5, turnover_pct:2.2, seal_ratio:0.15,
      sector:'新能源车', first_time:'09:55', phase:'close', burst_count:0,
      top_rule: { id:'BV02', title:'分歧低吸', quote:'...', timestamp:'00:55', score_weight:8, weight:8, value:12 } }
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
              var bar = document.querySelector('.bv-sector-bar');
              if (!bar) return {note:'no sector-bar'};
              var after = getComputedStyle(bar, '::after');
              var max = bar.scrollWidth - bar.clientWidth;
              var br = bar.getBoundingClientRect();
              var pills = Array.from(bar.querySelectorAll('.bv-sector-pill'));
              return {
                scrollLeft: Math.round(bar.scrollLeft),
                max: max,
                hasRight: bar.classList.contains('has-scroll-right'),
                hasLeft: bar.classList.contains('has-scroll-left'),
                afterOpacity: after.opacity,
                totalPills: pills.length,
                visiblePills: pills.filter(function(p){ return p.getBoundingClientRect().right <= br.right + 1; }).length
              };
            }""")

        s0 = await state()
        print("initial:", json.dumps(s0))
        assert s0.get("note") is None, f"sector-bar missing: {s0}"
        assert s0.get("max", 0) > 0, f"sector-bar should overflow, got max={s0.get('max')}"
        assert s0.get("hasRight") is True, f"initial should have has-scroll-right, got {s0}"
        assert s0.get("afterOpacity") == "1", f"::after should be opacity 1 when scrollable, got {s0.get('afterOpacity')}"

        await page.evaluate("""() => {
          var bar = document.querySelector('.bv-sector-bar');
          bar.scrollLeft = bar.scrollWidth;
        }""")
        await page.wait_for_timeout(300)
        sEnd = await state()
        print("scrolled-end:", json.dumps(sEnd))
        assert sEnd.get("hasRight") is False, f"at end should clear right-fade, got {sEnd}"
        assert sEnd.get("afterOpacity") == "0", f"::after should fade at end, got {sEnd.get('afterOpacity')}"

        await page.evaluate("""() => {
          var bar = document.querySelector('.bv-sector-bar');
          bar.scrollLeft = 0;
        }""")
        await page.wait_for_timeout(300)
        sBack = await state()
        print("scrolled-back:", json.dumps(sBack))
        assert sBack.get("hasRight") is True, f"back at 0 should re-show right-fade, got {sBack}"

        await browser.close()
        print(f"[OK] R151 sector-bar fade — 溢出 {s0['max']}px | {s0['totalPills']} pills 仅 {s0['visiblePills']} 可见 | fade on/end off/back on ✓")

if __name__ == "__main__":
    asyncio.run(run())
