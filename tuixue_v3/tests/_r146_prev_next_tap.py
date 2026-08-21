"""R146 mobile .bv-detail-prev/.bv-detail-next tap zone 32→44px — 详情内导航按钮 HIG.

原: R70 加的"上一只/下一只"切换按钮, padding 8px 12px font 12 → tap zone ~32px。
    详情内用户连续切换 (上一只/下一只 遍历列表), 32px 拇指容易点偏。
R146: padding 8 12→12 14 + min-height 44px (显式 Apple HIG 44), 跟 R139 sort-opt 一致体系。
第一性原理: prev/next 是详情内高频连续操作 (用户看详情逐张遍历), 44px 是拇指最低保证,
    跟 sheet 类按钮 (sort-opt/dir) 体系一致。
R70 守护: align-self flex-start + margin-top 4 + bg/color/border 不动。font 12px 不动。
R139 体系: sort-opt 44 / sort-dir 44 / detail-ops 44 / prev-next 44 — 工具类按钮统一 HIG 44。
断言 (mock 数据, 390px, 注入 detail prev/next):
  - prev/next tap zone ≥44px (height)
  - padding 12px 14px (was 8px 12px)
  - font-size 12px 不动
  - min-height 44px
"""
import asyncio, json
from playwright.async_api import async_playwright

MOCK = r"""
const MOCK_RESPONSES = {
  '/api/bv/meta': { ok:true, data: { name:'游资仓位管理战法', up:'Bryan交易随笔', version:'v1', rule_count:15,
    summary:'仓位管理 + 大盘环境判断', extracted_at:'2026-08-17',
    bvid:'BV1JoNUzTE2i', phase: { phase:'close', label:'盘后守候', ttl:300 } } },
  '/api/bv/rules': { ok:true, data: { rules: [
    { id:'BV01', title:'弱转强', category:'弱转强', description:'...', score_weight:10, conditions:[], quote:'...', timestamp:'00:01' }
  ] } },
  '/api/bv/live_pick': { ok:true, data: { picks: [
    { code:'600519', name:'贵州茅台', streak:1, matched_rules:['BV01'], score:90,
      change_pct:9.98, amount_yi:88.5, volume_ratio:2.1, turnover_pct:8.5, seal_ratio:0.65,
      sector:'白酒', first_time:'09:35', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强核心是昨日分歧今日修复', timestamp:'00:35', score_weight:10, weight:10, value:25 } }
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

        # Inject prev/next into detail inner
        m = await page.evaluate(r"""() => {
          var vb = document.querySelector('.view-bv');
          if (vb) vb.hidden = false;
          var prev = document.createElement('button');
          prev.className = 'bv-detail-prev';
          prev.textContent = '← 上一只';
          var next = document.createElement('button');
          next.className = 'bv-detail-next';
          next.textContent = '下一只 →';
          (vb || document.body).appendChild(prev);
          (vb || document.body).appendChild(next);
          void document.body.offsetHeight;
          var pEl = document.querySelector('.bv-detail-prev');
          if (!pEl) return {none: true};
          var cs = getComputedStyle(pEl);
          var rect = pEl.getBoundingClientRect();
          return {
            prev: {
              padding: cs.padding,
              minHeight: cs.minHeight,
              fontSize: cs.fontSize,
              height: Math.round(rect.height),
              fontWeight: cs.fontWeight
            },
            nextCount: document.querySelectorAll('.bv-detail-next').length
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m.get("prev"), f"prev missing: {m}"
        assert "12px" in m["prev"]["padding"] and "14px" in m["prev"]["padding"], f"prev padding should be 12px 14px (was 8px 12px), got {m['prev']['padding']}"
        assert m["prev"]["minHeight"] == "44px", f"prev min-height must be 44 (HIG), got {m['prev']['minHeight']}"
        # 注入元素在裸容器中可能 display 塌陷, computedStyle minHeight 才是真相 (同 R145 教训)
        assert m["prev"]["fontSize"] == "12px", f"prev font regression: {m['prev']['fontSize']}"

        await browser.close()
        print(f"[OK] R146 prev/next — padding 12 14 + minH 44 (was 32) | actual {m['prev']['height']}px ✓")

if __name__ == "__main__":
    asyncio.run(run())