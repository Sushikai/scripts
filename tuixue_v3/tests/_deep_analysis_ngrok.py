"""deep-analysis 卡片 ngrok 跨网段视觉验证 — iPhone 13 模拟"""
import sys
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

import os
import socket
TUNNEL_URL = open("/Users/kaikai/scripts/tuixue_v3/tunnel_url.txt").read().strip()
LAN_URL = "http://192.168.101.50:7799"
OUT = Path("/tmp/deep_analysis_ngrok")
OUT.mkdir(exist_ok=True)


def _pick_url():
    """Pick ngrok if reachable (DNS not blocked), else fall back to LAN."""
    import urllib.request
    for label, base in [("lan", LAN_URL), ("tunnel", TUNNEL_URL)]:
        try:
            with urllib.request.urlopen(base, timeout=4) as r:
                if r.status == 200:
                    return label, base
        except Exception:
            continue
    return None, LAN_URL


def main():
    src, base = _pick_url()
    print(f"[setup] using {src} base: {base}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        # iPhone 13 viewport
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844},
            is_mobile=True, has_touch=True, device_scale_factor=3,
            ignore_https_errors=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
            service_workers="block",
        )
        page = ctx.new_page()
        console_msgs = []
        page.on("console", lambda m: console_msgs.append(f"{m.type}: {m.text[:200]}"))
        page.on("pageerror", lambda e: console_msgs.append(f"PAGEERROR: {e}"))

        # Set ngrok bypass cookie if tunnel
        if "ngrok" in base:
            ctx.add_cookies([{
                "name": "ngrok-skip-browser-warning",
                "value": "true",
                "domain": "study-tuition-nylon.ngrok-free.dev",
                "path": "/",
            }])

        url = f"{base}/?code=600519#stock"
        print(f"[iPhone13] Loading: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(".view-stock", timeout=15000)
        page.wait_for_timeout(3000)
        page.evaluate("document.body.classList.remove('sidebar-open')")

        # Wait for deep-analysis card to render
        deep_card_visible = page.is_visible("#stock-deep-analy-card")
        print(f"[iPhone13] deep-analysis card visible: {deep_card_visible}")

        ready = False
        try:
            page.wait_for_function(
                "() => { const el = document.querySelector('#deep-action-chip'); if (!el) return false; const txt = el.textContent || ''; return txt && !txt.includes('分析中') && !txt.includes('—'); }",
                timeout=20000
            )
            ready = True
        except Exception:
            ready = False
        print(f"[iPhone13] deep-analysis ready: {ready}")

        # Scroll card into view
        page.evaluate("document.querySelector('#stock-deep-analy-card').scrollIntoView({behavior: 'instant', block: 'center'})")
        page.wait_for_timeout(800)

        # Read sections
        status = page.evaluate("""() => {
            const get = (id) => {
                const el = document.querySelector(id);
                return el ? (el.textContent || '').trim().slice(0, 100) : null;
            };
            return {
                action_chip: get('#deep-action-chip'),
                score: get('#deep-score'),
                status_chip: get('#deep-status'),
                profile_text_first80: (get('#deep-profile-text') || '').slice(0, 80),
                earnings_rows: document.querySelectorAll('#deep-earnings-body tr').length,
                tech_rows: document.querySelectorAll('#deep-tech-view > div > div').length,
                summary_first80: (get('#deep-summary-text') || '').slice(0, 80),
            };
        }""")
        print(f"[iPhone13] status: {json.dumps(status, ensure_ascii=False, indent=2)}")

        # Screenshot
        page.screenshot(path=str(OUT / "iphone13_full.png"), full_page=True)
        page.locator("#stock-deep-analy-card").screenshot(path=str(OUT / "iphone13_card.png"))

        # Console errors
        err_msgs = [m for m in console_msgs if "PAGEERROR" in m or ("error" in m.lower() and "warning" not in m.lower())]
        if err_msgs:
            print(f"[iPhone13] console errors:")
            for m in err_msgs[:3]:
                print(f"  {m}")
        else:
            print(f"[iPhone13] console: clean")

        ctx.close()
        browser.close()
        return 0 if (deep_card_visible and ready) else 1


if __name__ == "__main__":
    sys.exit(main())