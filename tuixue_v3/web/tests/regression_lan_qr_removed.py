"""LAN QR 删除验证 — 跑视觉回归确认 LAN 卡片不再出现"""
import asyncio, time
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://localhost:7799"
OUT = Path(f"/tmp/tuixue_lan_qr_gone_{int(time.time())}")
OUT.mkdir(parents=True, exist_ok=True)

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                         # 强制不走 SW cache, 拿最新 HTML
                                         service_workers="block")
        page = await ctx.new_page()

        errs = []
        page.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
        page.on("console", lambda m: errs.append(f"{m.type}: {m.text}") if m.type == "error" else None)

        print("STEP 1: 打开首页")
        await page.goto(f"{BASE}/?view=dash", wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_selector(".view-dash:not([hidden])", timeout=8000, state="visible")
        await asyncio.sleep(3)

        # 探针: LAN 元素应该全部不存在
        probe = await page.evaluate("""() => {
            const ids = ['tunnel-lan-card', 'tunnel-lan-url', 'tunnel-lan-qr-btn',
                         'tunnel-lan-qr-wrap', 'tunnel-lan-qr-img'];
            const found = {};
            for (const id of ids) {
                const el = document.getElementById(id);
                found[id] = !!el;
            }
            // 检查页面文字有没有 "扫码直进"
            const hasScanText = (document.body.textContent || '').includes('扫码直进');
            const hasLanQrText = (document.body.textContent || '').includes('LAN');
            const hasTunnelLan = (document.body.textContent || '').includes('tunnel-lan');
            return {
                ids_in_dom: found,
                has_scan_text: hasScanText,
                has_lan_qr_text: hasLanQrText,
                has_tunnel_lan_str: hasTunnelLan,
                cta_tunnel_exists: !!document.getElementById('cta-tunnel'),
                public_tunnel_btn_exists: !!document.getElementById('tunnel-btn'),
            };
        }""")

        print("\n=== 探针 ===")
        import json as _json
        print(_json.dumps(probe, ensure_ascii=False, indent=2))

        # 滚动到底看 cta-tunnel
        await page.evaluate("() => { document.getElementById('cta-tunnel')?.scrollIntoView(); }")
        await asyncio.sleep(1)
        await page.screenshot(path=str(OUT / "01_desktop_cta_tunnel.png"), full_page=False)
        await page.screenshot(path=str(OUT / "02_desktop_full.png"), full_page=True)

        # mobile
        await page.set_viewport_size({"width": 390, "height": 844})
        await asyncio.sleep(2)
        await page.evaluate("() => { document.getElementById('cta-tunnel')?.scrollIntoView(); }")
        await asyncio.sleep(1)
        await page.screenshot(path=str(OUT / "03_mobile_cta_tunnel.png"), full_page=False)

        print(f"\n=== console errors: {len(errs)} ===")
        for e in errs[:5]: print(f"  ERR: {e[:120]}")
        print(f"\n截图: {OUT}")
        await browser.close()

asyncio.run(main())