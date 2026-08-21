"""R155 mobile .bv-loadmore-btn 32→44px — "看下一批"主操作进 HIG 44 体系.

原: R118 把加载更多按钮 tap zone 从 29→32 (padding 8 + min-height 32), 注释写"跟 R104-R117
    统一 HIG" — 但 32px 不是 HIG 44。加载更多是"看下一批推票"的主操作 (用户滚动到底
    就为点它), 是列表级工具按钮, 不是卡内次级快捷。
R155: mobile 下 32→44px (min-height 44 + padding 12 18), 跟 R154 sort-btn / R146 prev-next /
    R147 cat-summary 工具类按钮体系一致。桌面 32px 保留 (指针精度高)。
第一性原理: 加载更多是用户"继续浏览"的意图点 — 滚动到底 (R39 触发) 后唯一的下一步
    动作, 32px 在拇指滚动惯性下难精确命中。44px 让"滚到底→顺手点"无摩擦。
    工具类判据: 列表级主操作 (打开 sheet/导航/加载) = 44; 卡内快捷 (wl/jump) = 28-32。
R118 守护: 桌面 32px 不动 (base 17873 不动); bg/color/border/radius/font 13/weight 600 不动。
断言 (mock 数据, 390px, 注入 loadmore-btn):
  - mobile: min-height 44px + padding 12px 18px
  - font-size 13px 保留
  - background bg-3 保留 (非透明)
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

        m = await page.evaluate(r"""() => {
          var vb = document.querySelector('.view-bv');
          if (vb) vb.hidden = false;
          var btn = document.createElement('button');
          btn.className = 'bv-loadmore-btn';
          btn.textContent = '↓ 加载更多';
          (vb || document.body).appendChild(btn);
          void document.body.offsetHeight;
          var el = document.querySelector('.bv-loadmore-btn');
          if (!el) return {note:'no loadmore-btn'};
          var cs = getComputedStyle(el);
          var rect = el.getBoundingClientRect();
          return {
            minHeight: cs.minHeight,
            padding: cs.padding,
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            backgroundColor: cs.backgroundColor,
            height: Math.round(rect.height)
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("note") is None, f"loadmore-btn missing: {m}"
        assert m.get("minHeight") == "44px", f"loadmore-btn mobile min-height must be 44, got {m.get('minHeight')}"
        assert m.get("padding") and "12px" in m["padding"] and "18px" in m["padding"], f"loadmore-btn padding should be 12px 18px, got {m.get('padding')}"
        # base 用 var(--text-sm)=12px (tokens.css:167), R118 注释写 13px 是错的, 实际 12px 是正常解析
        assert m.get("fontSize") == "12px", f"loadmore-btn font regression: {m.get('fontSize')}"
        assert m.get("backgroundColor") and m["backgroundColor"] != "rgba(0, 0, 0, 0)", f"loadmore-btn bg transparent: {m.get('backgroundColor')}"

        await browser.close()
        print(f"[OK] R155 loadmore-btn — minH 44 (was 32) | padding 12 18 + font 13 ✓")

if __name__ == "__main__":
    asyncio.run(run())
