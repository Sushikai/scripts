"""bt-tab 切换测试 — 跑双策略 + 注入 + 点 tab"""
import asyncio, json, time, urllib.request
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://localhost:7799"
OUT = Path(f"/tmp/tuixue_bt_tab_{int(time.time())}")
OUT.mkdir(parents=True, exist_ok=True)


def wait_run(rid, timeout=300):
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(2)
        try:
            x = json.loads(urllib.request.urlopen(f"{BASE}/api/screener/backtest?run_id={rid}").read())
            d = x.get('data') or {}
            if d.get('status') == 'done' and d.get('result'):
                return d['result']
            if d.get('status') == 'error':
                return None
        except Exception as e:
            print(f"  poll err: {e}")
    return None


def kick(body):
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                f"{BASE}/api/screener/backtest",
                data=json.dumps(body).encode(),
                headers={'Content-Type': 'application/json'},
            )
            r = json.loads(urllib.request.urlopen(req, timeout=10).read())
            d = r.get('data') or {}
            if d.get('running'):
                return d['holder']
            return d.get('run_id')
        except Exception as e:
            print(f"  POST retry {attempt}: {e}")
            time.sleep(2)
    return None


async def main():
    base_body = {"sample": 200, "periods": ["半年"], "hold_days": 3, "top_n": 1,
                 "breadth_min": 1500, "breadth_min_soft": 3000, "sector_hot_topn": 5,
                 "strategy_id": "baseline"}

    print("STEP 1: 跑 baseline")
    rid_b = kick(base_body)
    print(f"  rid_b = {rid_b}")
    base_res = wait_run(rid_b, 240)
    print(f"  baseline: trades={base_res.get('summary',{}).get('trades') if base_res else 'FAIL'}")

    print("STEP 2: 跑 WR1000 (等 baseline 完全释放 worker)")
    time.sleep(3)
    rid_w = kick({**base_body, "strategy_id": "WIN_RATE_1000"})
    print(f"  rid_w = {rid_w}")
    wr_res = wait_run(rid_w, 240)
    print(f"  WR1000: trades={wr_res.get('summary',{}).get('trades') if wr_res else 'FAIL'}")

    if not (base_res and wr_res):
        print("ABORT")
        return

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        errs = []
        page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        page.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type == "error" else None)

        print("STEP 3: 打开 + 展开")
        await page.goto(f"{BASE}/?view=screener", wait_until="domcontentloaded", timeout=15000)
        try:
            await page.wait_for_selector(".view-screener:not([hidden])", timeout=8000, state="visible")
        except Exception:
            await page.evaluate("""() => { try { showView && showView('screener'); } catch(e){} }""")
        await asyncio.sleep(3)
        await page.click("#backtest-panel h3")
        await asyncio.sleep(1)

        print("STEP 4: 注入双结果")
        await page.evaluate("""([base, wr]) => {
            window._BT_RESULTS = window._BT_RESULTS || {};
            window._BT_RESULTS['baseline'] = base;
            window._BT_RESULTS['WIN_RATE_1000'] = wr;
            window._BT_ACTIVE_STRATEGY = 'baseline';
            btRenderV4(base);
            document.querySelectorAll('.bt-tab').forEach(t => {
                const active = t.dataset.strategy === 'baseline';
                t.style.background = active ? '#2a241c' : '#1c1a14';
            });
        }""", [base_res, wr_res])
        await asyncio.sleep(3)

        # 抓 baseline 状态的 KPI 数字
        baseline_kpi = await page.evaluate("""() => {
            const kpis = [];
            for (const k of document.querySelectorAll('.kpi')) {
                if (k.offsetWidth > 50 && k.offsetHeight > 20) kpis.push(k.textContent.replace(/\\s+/g,' ').trim());
            }
            return kpis.slice(0, 8);
        }""")
        await page.screenshot(path=str(OUT / "01_baseline_view.png"), full_page=False)
        print(f"\nbaseline KPI:\n  " + "\n  ".join(baseline_kpi[:6]))

        print("STEP 5: 点 WIN_RATE_1000 tab")
        await page.click(".bt-tab[data-strategy='WIN_RATE_1000']")
        await asyncio.sleep(3)
        wr_kpi = await page.evaluate("""() => {
            const kpis = [];
            for (const k of document.querySelectorAll('.kpi')) {
                if (k.offsetWidth > 50 && k.offsetHeight > 20) kpis.push(k.textContent.replace(/\\s+/g,' ').trim());
            }
            return kpis.slice(0, 8);
        }""")
        await page.screenshot(path=str(OUT / "02_wr1000_view.png"), full_page=False)
        print(f"\nWR1000 KPI:\n  " + "\n  ".join(wr_kpi[:6]))

        # 对比
        print("\n=== 对比 baseline vs WR1000 ===")
        for i in range(min(len(baseline_kpi), len(wr_kpi))):
            b, w = baseline_kpi[i], wr_kpi[i]
            tag = "SAME" if b == w else "DIFF"
            print(f"  [{tag}] {b[:70]}")
            if b != w:
                print(f"       WR: {w[:70]}")

        print("\nSTEP 6: 点回 baseline")
        await page.click(".bt-tab[data-strategy='baseline']")
        await asyncio.sleep(2)
        back_kpi = await page.evaluate("""() => {
            const kpis = [];
            for (const k of document.querySelectorAll('.kpi')) {
                if (k.offsetWidth > 50 && k.offsetHeight > 20) kpis.push(k.textContent.replace(/\\s+/g,' ').trim());
            }
            return kpis.slice(0, 8);
        }""")
        await page.screenshot(path=str(OUT / "03_back_to_baseline.png"), full_page=False)
        print(f"\n回到 baseline KPI:\n  " + "\n  ".join(back_kpi[:6]))

        # 验证回到 baseline 是否与最初一致
        print("\n=== baseline 切回一致性 ===")
        diff_count = sum(1 for a, c in zip(baseline_kpi, back_kpi) if a != c)
        print(f"  baseline 切回后变化 KPI 数: {diff_count}/{len(baseline_kpi)}")

        # mobile
        await page.set_viewport_size({"width": 390, "height": 844})
        await asyncio.sleep(2)
        await page.screenshot(path=str(OUT / "04_mobile_wr1000.png"), full_page=False)

        print(f"\n=== console errors: {len(errs)} ===")
        for e in errs[:5]: print(f"  ERR: {e[:120]}")
        print(f"\n截图: {OUT}")
        await browser.close()

asyncio.run(main())