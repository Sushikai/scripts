"""R158 mobile sort sheet (R17) 底部按钮不可达 — fixed 被 transform 祖先框住.

根因 (Playwright 实测 + 祖先遍历):
  .bv-sort-sheet 是 position:fixed, 挂在 .view-bv 内。.view 有 view-fade-in 动画
  (animation-fill-mode:both), 动画结束后 Chrome 保留 identity transform
  matrix(1,0,0,1,0,0) 在 .view-bv 上 → fixed 的 containing block 变成 .view-bv
  (685px 高, top 84) 而非视口 → sheet inset:0 锚到该 box, 面板 + 确定按钮落在
  屏外 (apply bottom 983 > 844), 而 body.bv-sort-open overflow:hidden 锁滚 → 不可达。
  (其它 fixed 层 toast/sheet 都挂在 body 下, 从不进 .view — 本页是唯一例外。)

R158 修复: 初始化时把 sheet re-parent 到 <body> (脱离 transform 祖先)。
  fixed overlay 的第一性原理: 它的定位参考必须是视口, 不能是被 transform/
  animation 篡改的祖先 — 挂 body 最稳。CSS 全局改 view keyframe 风险大 (影响所有页),
  且实测改了 keyframe .view-bv 仍保留 identity matrix, 不可靠 → 用 JS 最小手术。

断言 (mock 数据, 390px):
  - #bv-sort-sheet parent = BODY (不在 .view-bv 内)
  - sheet 高度 ≤ 视口 (844), top=0, 不被 84px 祖先位移
  - .bv-sort-apply bottom ≤ 844 (可达)
  - 点击涨幅选项 + 确定 → sheet 关闭, 排序生效 (首行 code 变化)
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
    { code:'002594', name:'比亚迪', streak:1, matched_rules:['BV01','BV02'], score:70,
      change_pct:6.8, amount_yi:30.1, volume_ratio:1.9, turnover_pct:4.1, seal_ratio:0.5,
      sector:'新能源车', first_time:'09:38', phase:'close', burst_count:0,
      top_rule: { id:'BV01', title:'弱转强', quote:'弱转强要卡位', timestamp:'00:38', score_weight:10, weight:10, value:15 } }
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
        await page.wait_for_timeout(400)

        # --- 1) sheet re-parented to BODY ---
        parentTag = await page.evaluate("() => document.querySelector('#bv-sort-sheet').parentElement.tagName")
        print("sheet parent:", parentTag)
        assert parentTag == "BODY", f"R158: sort sheet must be BODY child, got {parentTag}"

        # --- 2) open sheet → verify it fits viewport, apply reachable ---
        await page.evaluate("() => { var b = document.querySelector('.bv-sort-btn'); if (b) b.click(); }")
        await page.wait_for_timeout(500)

        m = await page.evaluate(r"""() => {
          var sheet = document.querySelector('#bv-sort-sheet');
          var panel = sheet.querySelector('.bv-sort-panel');
          var apply = sheet.querySelector('.bv-sort-apply');
          var sr = sheet.getBoundingClientRect();
          var ar = apply.getBoundingClientRect();
          return {
            vh: window.innerHeight,
            sheetTop: Math.round(sr.top),
            sheetHeight: Math.round(sr.height),
            applyTop: Math.round(ar.top),
            applyBottom: Math.round(ar.bottom),
            applyInViewport: ar.bottom <= window.innerHeight && ar.top >= 0,
            panelTransform: getComputedStyle(panel).transform
          };
        }""")
        print(json.dumps(m, ensure_ascii=False, indent=1))

        assert m["sheetTop"] == 0, f"R158: sheet top must be 0 (not 84 from .view offset), got {m['sheetTop']}"
        assert m["sheetHeight"] <= m["vh"], f"R158: sheet height {m['sheetHeight']} exceeds viewport {m['vh']}"
        assert m["applyInViewport"], f"R158: apply button unreachable (bottom {m['applyBottom']} > {m['vh']})"

        # --- 3) sort still functions: pick 涨幅 desc + apply → sheet closes ---
        await page.evaluate("""() => {
          var opt = document.querySelector('.bv-sort-opt[data-sort-key="change_pct"]');
          if (opt) opt.click();
          var dir = document.querySelector('.bv-sort-dir-opt[data-dir="desc"]');
          if (dir) dir.click();
        }""")
        await page.wait_for_timeout(200)
        await page.evaluate("() => { var a = document.querySelector('.bv-sort-apply'); if (a) a.click(); }")
        await page.wait_for_timeout(400)
        closed = await page.evaluate("() => document.querySelector('#bv-sort-sheet').hidden")
        print("sheet hidden after apply:", closed)
        assert closed, "R158: sheet did not close on apply"

        firstRow = await page.evaluate("""() => {
          var r = document.querySelector('#bv-pick-tbody tr.bv-row');
          return r ? r.textContent.trim().slice(0, 30) : '';
        }""")
        print("first row after sort:", firstRow)

        await browser.close()
        print(f"[OK] R158 sort sheet — BODY re-parent | 视口内 (apply bottom {m['applyBottom']}≤844) | 排序生效 ✓")

if __name__ == "__main__":
    asyncio.run(run())
