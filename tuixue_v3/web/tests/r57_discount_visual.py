#!/usr/bin/env python3
"""
R57 3 档 trigger + late_high_discount 开关 · 视觉验证
  1) late_high_discount UI 控件 (3 档按钮)
  2) 3 档 trigger 分布卡片 (exit_breakdown)
  3) KPI 卡 3 档对比 (1.0 / 0.7 / 0.5)
  4) 月度表 6 套 (3 档平均收益率)
  5) 退场模型解释 3 列版本
"""
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:7799/#screener"
ART = "/Users/kaikai/scripts/tuixue_v3/web/tests/artifacts/r57_discount"
Path(ART).mkdir(parents=True, exist_ok=True)


async def post_bt(page, strategy_id="baseline", sample=60, late_high_discount=1.0):
    body = {
        "periods": ["半年"], "hold_days": 1, "top_n": 2, "sample": sample,
        "breadth_min": 0, "breadth_min_soft": 0,
        "sector_hot_topn": 0, "sector_inflow_topn": 0,
        "require_surge_label": False, "enable_actual_10": False,
        "index_late_up": False, "sector_late_up": False,
        "tail_vol_ratio_min": 0, "strategy_id": strategy_id,
        "late_high_discount": late_high_discount,
        "require_vwap_strict": False,
    }
    # 重试锁占用
    for attempt in range(30):
        r = await page.evaluate(
            """async (b) => {
                const r = await fetch('/api/screener/backtest', {
                    method: 'POST', headers: {'Content-Type':'application/json'},
                    body: JSON.stringify(b)
                });
                return await r.json();
            }""", body)
        if r.get("data", {}).get("run_id"):
            rid = r["data"]["run_id"]
            print(f"  [discount={late_high_discount}] rid: {rid} (尝试 {attempt+1})")
            break
        if "已有回测在跑" in str(r):
            print(f"  [discount={late_high_discount}] BT 锁占用, 等 3s 重试…")
            await page.wait_for_timeout(3000)
            continue
        print(f"  POST fail: {r}")
        return None
    else:
        print(f"  30 次重试都失败")
        return None
    for i in range(60):
        await page.wait_for_timeout(3000)
        s = await page.evaluate(
            """async (rid) => {
                const r = await fetch('/api/screener/backtest?run_id=' + rid);
                return await r.json();
            }""", rid)
        st = s.get("data", {}).get("status")
        if st == "done":
            return s["data"].get("result")
        if st == "error":
            print(f"    err: {s.get('data', {}).get('error')}")
            return None
    print(f"    timeout")
    return None


async def render_to(page, result):
    """直接走前端 btRenderV4 渲染"""
    await page.evaluate(
        """(r) => {
            window._BT_RESULTS = window._BT_RESULTS || {};
            window._BT_RESULTS[r.config.strategy_id] = r;
            btRenderV4(r);
            window._BT_RESULT_SIG = window._BT_RESULT_SIG || {};
            window._BT_RESULT_SIG[r.config.strategy_id] = JSON.stringify({
                periods: r.config.period_keys, hold: r.config.hold_days, top: r.config.top_n,
                sample: r.config.sample_size
            });
            if (typeof window.btRenderCompare === 'function') {
                window.btRenderCompare('manual');
            }
        }""", result)


async def crop_section(page, selector, out_path, padding=8):
    el = page.locator(selector).first
    if not await el.count():
        print(f"  ⚠ selector missing: {selector}")
        return False
    await el.scroll_into_view_if_needed()
    await page.wait_for_timeout(150)
    try:
        await el.screenshot(path=out_path)
        print(f"  ✓ {selector} → {os.path.basename(out_path)}")
        return True
    except Exception as e:
        print(f"  ✗ screenshot fail: {e}")
        return False


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        page.on("pageerror", lambda e: print(f"  [pageerror] {e}"))

        print("→ 打开", URL)
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector(".bt-tab", state="attached", timeout=15000)
        await page.evaluate("""() => {
            typeof showView === 'function' && showView('screener', {push: false});
            const p = document.getElementById('backtest-panel');
            if (p) p.classList.remove('collapsed');
            const tabs = document.querySelectorAll('.bt-tab');
            tabs.forEach(t => t.classList.remove('active'));
            const baseTab = document.querySelector(".bt-tab[data-strategy='baseline']");
            if (baseTab) baseTab.classList.add('active');
        }""")
        await page.wait_for_timeout(1500)

        # ===== 截图 1: late_high_discount UI 控件 =====
        print("\n=== 1) late_high_discount 3 档按钮 (UI) ===")
        # 把按钮区域截下来
        btns = page.locator(".bt-lhd")
        n = await btns.count()
        print(f"  found {n} .bt-lhd buttons")
        if n > 0:
            # 用 evaluate 注入 wrapper 让按钮 + 标签整体可截
            await page.evaluate("""() => {
                // 创建一个 wrapper div 包住所有 .bt-lhd 按钮 + 标签
                const btns = document.querySelectorAll('.bt-lhd');
                if (!btns.length) return;
                const wrap = document.createElement('div');
                wrap.id = '_lhd-capture';
                wrap.style.cssText = 'display:inline-flex;gap:6px;align-items:center;padding:6px 10px;background:#1a1612;border:1px solid #3a3024;border-radius:6px';
                const label = document.createElement('span');
                label.textContent = 'late_high 折算:';
                label.style.cssText = 'font-size:11px;color:#d4b87a;margin-right:6px';
                wrap.appendChild(label);
                btns[0].parentNode.insertBefore(wrap, btns[0]);
                btns.forEach(b => wrap.appendChild(b));
            }""")
            await page.wait_for_timeout(300)
            await crop_section(page, "#_lhd-capture", f"{ART}/01_discount_buttons.png")

        # ===== 跑 3 档 discount =====
        results = {}
        for D in [1.0, 0.7, 0.5]:
            print(f"\n=== 跑 discount={D} ===")
            r = await post_bt(page, "baseline", sample=60, late_high_discount=D)
            if not r:
                print(f"  discount={D} 失败")
                continue
            results[D] = r
            await render_to(page, r)
            await page.wait_for_timeout(800)

        if not results:
            print("所有 discount 都没跑出来")
            await browser.close()
            return

        # ===== 截图 2: KPI 卡 1.0 满格 =====
        print("\n=== 2) KPI 卡 (1.0 满格 baseline) ===")
        await crop_section(page, "#bt-kpis", f"{ART}/02_kpis_full.png")

        # ===== 截图 3: 6 套退场胜率表 (满格) =====
        print("\n=== 3) 6 套退场胜率表 (满格) ===")
        await crop_section(page, "#bt-exits-compare-host", f"{ART}/03_exits_full.png")

        # ===== 截图 4: 月度表 12 列 (满格) =====
        print("\n=== 4) 月度表 (满格) ===")
        await crop_section(page, "#bt-monthly-host, #bt-monthly", f"{ART}/04_monthly_full.png")

        # ===== 截图 5: 退场模型解释 3 列 (满格) =====
        print("\n=== 5) 退场模型解释 (3 档) ===")
        await crop_section(page, "#bt-exit-model-doc", f"{ART}/05_exit_model_doc.png")

        # ===== 截图 6: 全页 baseline 满格 =====
        print("\n=== 6) 全页 baseline (满格) ===")
        await page.screenshot(path=f"{ART}/06_fullpage_baseline_full.png", full_page=True)

        # ===== 截图 7: 全页 baseline 0.7 =====
        if 0.7 in results:
            print("\n=== 7) 全页 baseline (0.7 保守) ===")
            await page.screenshot(path=f"{ART}/07_fullpage_baseline_07.png", full_page=True)

        # ===== 截图 8: 全页 baseline 0.5 =====
        if 0.5 in results:
            print("\n=== 8) 全页 baseline (0.5 极保守) ===")
            await page.screenshot(path=f"{ART}/08_fullpage_baseline_05.png", full_page=True)

        # ===== 写入 JSON summary =====
        print("\n=== 9) JSON summary 写入 ===")
        summary = {}
        for D, r in results.items():
            s = r.get("summary", {})
            sc = r.get("scenarios", {})
            eb = r.get("exit_breakdown", {})
            summary[f"discount_{D}"] = {
                "trades": s.get("trades", 0),
                "win_rate_pct": round(s.get("win_rate_pct", 0), 2),
                "avg_daily_return_pct": round(s.get("avg_daily_return_pct", 0), 3),
                "cum_return_pct": round(s.get("cum_return_pct", 0), 3),
                "monthly_return_pct_user": round(s.get("monthly_return_pct_user", 0), 2),
                "yearly_return_pct_user": round(s.get("yearly_return_pct_user", 0), 2),
                "amount_after_1_month_yuan": s.get("amount_after_1_month_yuan", 0),
                "amount_after_1_year_yuan": s.get("amount_after_1_year_yuan", 0),
                "scenarios": {
                    k: {
                        "win_rate_pct": round(sc.get(k, {}).get("win_rate_pct", 0), 2),
                        "avg_pct": round(sc.get(k, {}).get("avg_pct", 0), 3),
                        "cum_return_pct": round(sc.get(k, {}).get("cum_return_pct", 0), 3),
                    }
                    for k in ["trail_80", "trail_50", "trail_20", "water_avg", "force_10", "force_close"]
                },
                "exit_breakdown": eb,
            }
        import json
        out_json = f"{ART}/discount_summary.json"
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"  ✓ {out_json}")
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        await browser.close()
        print("\n=== R57 视觉验证完成 → ", ART)


if __name__ == "__main__":
    asyncio.run(main())