"""个股页 AI 深度判断卡片视觉验证 — desktop + mobile 双视口"""
import sys
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://localhost:7799"
OUT = Path("/tmp/deep_analysis_visual")


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        results = []
        for label, viewport, mobile in [
            ("desktop", {"width": 1280, "height": 900}, False),
            ("mobile",  {"width": 390,  "height": 844}, True),
        ]:
            ctx = browser.new_context(viewport=viewport, is_mobile=mobile, has_touch=mobile, ignore_https_errors=True, service_workers="block")
            page = ctx.new_page()
            console_msgs = []
            page.on("console", lambda m: console_msgs.append(f"{m.type}: {m.text[:200]}"))
            page.on("pageerror", lambda e: console_msgs.append(f"PAGEERROR: {e}"))

            # 1. 加载个股页
            page.goto(BASE + "?code=600519#stock", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".view-stock", timeout=10000)
            page.wait_for_timeout(3000)
            page.evaluate("document.body.classList.remove('sidebar-open')")

            # 2. 等 deep-analysis card 出现 (默认 hidden false)
            deep_card_visible = page.is_visible("#stock-deep-analy-card")
            print(f"[{label}] #stock-deep-analy-card visible: {deep_card_visible}")
            results.append((f"{label}.card_visible", deep_card_visible))

            # 3. 滚动到 deep-analysis card
            page.evaluate("document.querySelector('#stock-deep-analy-card').scrollIntoView({behavior: 'instant', block: 'center'})")
            page.wait_for_timeout(800)

            # 4. 等 deep-analysis 数据 ready (轮询 wait_for_function)
            ready = False
            try:
                page.wait_for_function(
                    "() => { const el = document.querySelector('#deep-action-chip'); if (!el) return false; const txt = el.textContent || ''; return txt && !txt.includes('分析中') && !txt.includes('—'); }",
                    timeout=20000
                )
                ready = True
            except Exception:
                ready = False
            print(f"[{label}] deep-analysis ready: {ready}")
            results.append((f"{label}.ready", ready))

            # 5. 读各 section 状态
            status = page.evaluate("""() => {
                const sections = {};
                const get = (id) => {
                    const el = document.querySelector(id);
                    return el ? (el.textContent || '').trim().slice(0, 200) : null;
                };
                sections.action_chip = get('#deep-action-chip');
                sections.score = get('#deep-score');
                sections.status_chip = get('#deep-status');
                sections.profile_text = get('#deep-profile-text');
                sections.earnings_rows = document.querySelectorAll('#deep-earnings-body tr').length;
                sections.holding_text = get('#deep-holding-view');
                sections.tech_grid_items = document.querySelectorAll('#deep-tech-view .deep-tech-row, #deep-tech-view > *').length;
                sections.summary_text = get('#deep-summary-text');
                sections.jump_chip_visible = !document.querySelector('#deep-jump-chip').hidden;
                return sections;
            }""")
            print(f"[{label}] status: {json.dumps(status, ensure_ascii=False, indent=2)}")
            results.append((f"{label}.status", status))

            # 6. 截图 (full page + zoomed on card)
            page.screenshot(path=str(OUT / f"{label}_full.png"), full_page=True)
            card_box = page.locator("#stock-deep-analy-card").bounding_box()
            if card_box:
                page.locator("#stock-deep-analy-card").screenshot(path=str(OUT / f"{label}_card.png"))

            # 7. console errors
            err_msgs = [m for m in console_msgs if "error" in m.lower() or "PAGEERROR" in m]
            if err_msgs:
                print(f"[{label}] console errors:")
                for m in err_msgs[:5]:
                    print(f"  {m}")
                results.append((f"{label}.errors", err_msgs[:5]))
            else:
                print(f"[{label}] console: clean")

            ctx.close()
        browser.close()

        # 输出汇总
        print("\n=== SUMMARY ===")
        ok_count = sum(1 for k, v in results if isinstance(v, bool) and v)
        total_count = sum(1 for k, v in results if isinstance(v, bool))
        print(f"boolean checks: {ok_count}/{total_count} PASS")
        return 0 if ok_count == total_count else 1


if __name__ == "__main__":
    sys.exit(main())