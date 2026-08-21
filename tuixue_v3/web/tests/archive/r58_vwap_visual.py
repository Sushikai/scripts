#!/usr/bin/env /Users/kaikai/.hermes/hermes-agent/venv/bin/python3
"""
R58 VWAP 严格过滤实装 · 视觉验证
  1) UI 复选框 #bt-vwap-strict (新)
  2) baseline 满格 KPI
  3) strict=True + discount=1.0 KPI (cum 8366x → 92x)
  4) strict=True + discount=0.7 KPI (推荐 UI 默认 — cum 26x)
  5) 退场模型解释 v2 标题 (R57 + R58)
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:7799/#screener"
ART = "/Users/kaikai/scripts/tuixue_v3/web/tests/artifacts/r58_vwap"
Path(ART).mkdir(parents=True, exist_ok=True)


async def post_bt(page, late_high_discount=1.0, require_vwap_strict=False, sample=60):
    body = {
        "periods": ["半年"], "hold_days": 1, "top_n": 2, "sample": sample,
        "breadth_min": 0, "breadth_min_soft": 0,
        "sector_hot_topn": 0, "sector_inflow_topn": 0,
        "require_surge_label": False, "enable_actual_10": False,
        "index_late_up": False, "sector_late_up": False,
        "tail_vol_ratio_min": 0, "strategy_id": "baseline",
        "late_high_discount": late_high_discount,
        "require_vwap_strict": require_vwap_strict,
    }
    for attempt in range(30):
        r = await page.evaluate("""async (b) => {
            const r = await fetch('/api/screener/backtest', {
                method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify(b)
            });
            return await r.json();
        }""", body)
        if r.get("data", {}).get("run_id"):
            rid = r["data"]["run_id"]
            print(f"  [d={late_high_discount} strict={require_vwap_strict}] rid: {rid} (try {attempt+1})")
            break
        if "已有回测在跑" in str(r):
            await page.wait_for_timeout(3000)
            continue
        print(f"  POST fail: {r}")
        return None
    else:
        return None
    for i in range(150):
        await page.wait_for_timeout(2000)
        s = await page.evaluate(f"""async () => {{
            const r = await fetch('/api/screener/backtest?run_id={rid}');
            return await r.json();
        }}""")
        st = s.get("data", {}).get("status")
        if st == "done":
            print(f"    done at i={i} ({i*2}s)")
            return s["data"].get("result")
        if st == "error":
            print(f"    err: {s.get('data', {}).get('error')}")
            return None
    return None


async def render_to(page, result):
    await page.evaluate("""(r) => {
        window._BT_RESULTS = window._BT_RESULTS || {};
        window._BT_RESULTS[r.config.strategy_id] = r;
        btRenderV4(r);
        window._BT_RESULT_SIG = window._BT_RESULT_SIG || {};
        window._BT_RESULT_SIG[r.config.strategy_id] = JSON.stringify({
            periods: r.config.period_keys, hold: r.config.hold_days, top: r.config.top_n,
            sample: r.config.sample_size, ts: r.ts
        });
    }""", result)
    await page.wait_for_timeout(1500)


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        ctx = await browser.new_context(viewport={"width": 1440, "height": 1080})
        page = await ctx.new_page()
        page.on("pageerror", lambda e: print(f"  [JS error] {e}"))
        page.on("console", lambda m: m.type == "error" and print(f"  [console.error] {m.text}"))

        print("[1/5] navigation...")
        await page.goto(URL, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(2500)

        # Screenshot 1: bt-vwap-strict checkbox (new UI element)
        print("[1/5] 截屏 01_vwap_checkbox.png")
        await page.screenshot(path=f"{ART}/01_vwap_checkbox.png", full_page=False, clip={"x": 0, "y": 0, "width": 1440, "height": 900})

        # Run 2: baseline (1.0, false)
        print("[2/5] baseline 满格...")
        r2 = await post_bt(page, late_high_discount=1.0, require_vwap_strict=False)
        if r2:
            await render_to(page, r2)
            await page.wait_for_timeout(800)
            await page.screenshot(path=f"{ART}/02_baseline_kpis.png", full_page=True)

        # Run 3: strict=True, discount=1.0
        print("[3/5] strict=True discount=1.0...")
        r3 = await post_bt(page, late_high_discount=1.0, require_vwap_strict=True)
        if r3:
            await render_to(page, r3)
            await page.wait_for_timeout(800)
            await page.screenshot(path=f"{ART}/03_strict_d1_kpis.png", full_page=True)

        # Run 4: strict=True, discount=0.7 (recommended)
        print("[4/5] strict=True discount=0.7 (推荐)...")
        r4 = await post_bt(page, late_high_discount=0.7, require_vwap_strict=True)
        if r4:
            await render_to(page, r4)
            await page.wait_for_timeout(800)
            await page.screenshot(path=f"{ART}/04_strict_d07_kpis.png", full_page=True)

        # Screenshot 5: scroll down to see exit model doc with v2 title
        print("[5/5] 截图 退场模型解释 v2...")
        # find exit doc section
        await page.evaluate("""() => {
            const el = document.querySelector('[data-bt-exit-doc]') ||
                       [...document.querySelectorAll('div')].find(d =>
                         d.textContent.includes('退场模型解释') && d.textContent.includes('R57'));
            if (el) el.scrollIntoView({ block: 'start' });
        }""")
        await page.wait_for_timeout(800)
        await page.screenshot(path=f"{ART}/05_exit_model_doc_v2.png", full_page=False)

        await browser.close()

        # Summary
        import json
        summary = {}
        for label, r in [("baseline", r2), ("strict_d1", r3), ("strict_d07", r4)]:
            if r:
                cfg = r.get("config", {})
                sm = r.get("summary", {})
                summary[label] = {
                    "trades": sm.get("trades"),
                    "win_rate_pct": sm.get("win_rate_pct"),
                    "avg_return_pct": sm.get("avg_return_pct"),
                    "cum_return_pct": sm.get("cum_return_pct"),
                    "vwap_below_skipped": cfg.get("vwap_below_skipped"),
                    "vwap_strict_mode": cfg.get("vwap_strict_mode"),
                    "late_high_discount": cfg.get("late_high_discount"),
                }
        with open(f"{ART}/r58_summary.json", "w") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"\n=== R58 Visual Summary ===")
        for k, v in summary.items():
            print(f"  {k}: {v}")
        print(f"saved to {ART}")


asyncio.run(main())
