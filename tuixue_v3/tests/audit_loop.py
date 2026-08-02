"""Sequential 1000-round iteration loop: probe each stock individually.

Designed for stable testing — single browser context, sequential stocks,
long waits, no parallel API hammering that causes server overload.
"""
import asyncio
import json
import sys
import time
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"
CODES = [
    "605179",  # 一鸣食品 (recent limit-up)
    "000001",  # 平安银行 (always active)
    "000428",  # 华天酒店
    "002659",  # 凯文教育
    "300750",  # 宁德时代 (big cap)
    "600519",  # 贵州茅台 (blue chip)
    "688981",  # 中芯国际 (科创板)
    "830799",  # 北证
]
TABS = ["intraday", "kline", "flow", "seats", "holders", "crash", "ai", "news", "sectors", "related"]

HISTORY_FILE = Path("/tmp/stock_audit_history.json")


def log_issue(category, msg):
    if not HISTORY_FILE.exists():
        HISTORY_FILE.write_text("[]")
    try:
        history = json.loads(HISTORY_FILE.read_text())
    except Exception:
        history = []
    history.append({"ts": time.time(), "round": category.get("round"), "category": category.get("category", "?"), "msg": msg})
    HISTORY_FILE.write_text(json.dumps(history[-500:], indent=2, ensure_ascii=False))


async def probe_stock(page, code, round_num):
    """Probe one stock page sequentially."""
    network_log = []
    page_errors = []

    def on_response(r):
        if "/api/" in r.url and "/api/_meta/" not in r.url:
            network_log.append({"url": r.url.replace(BASE, ""), "status": r.status})

    def on_console(m):
        if m.type == "error":
            page_errors.append(m.text[:200])

    page.on("response", on_response)
    page.on("console", on_console)

    issues = []

    # Load (with retry on transient connection errors)
    last_err = None
    for attempt in range(3):
        try:
            await page.goto(f"{BASE}/#stock={code}", wait_until="domcontentloaded", timeout=30000)
            break
        except Exception as e:
            last_err = e
            await asyncio.sleep(2 + attempt * 2)
    else:
        issues.append({"code": code, "issue": f"goto-fail: {str(last_err)[:60]}"})
        return {"code": code, "issues": issues, "console_errors": page_errors[:5]}
    await asyncio.sleep(2)  # Initial wait for /core to arrive (subsequent checks wait inline)

    # Hero state — wait until #stock-title is not the literal placeholder "个股"
    # (loadStockDetail replaces it once /core JSON arrives)
    hero = await page.evaluate("""async () => {
      const get = (sel) => {
        const el = document.querySelector(sel);
        if (!el) return {sel, exists: false};
        const text = (el.textContent || '').trim();
        return {sel, exists: true, text: text.slice(0, 50)};
      };
      // Wait up to 8s for #stock-title to leave the placeholder
      const start = Date.now();
      while (Date.now() - start < 8000) {
        const t = (document.querySelector('#stock-title')?.textContent || '').trim();
        if (t && t !== '个股') break;
        await new Promise(r => setTimeout(r, 200));
      }
      return [
        get('#stock-title'),
        get('#stock-code'),
        get('#q-price'),
        get('#q-change'),
      ];
    }""")
    for r in hero:
        if not r.get("exists"):
            issues.append({"code": code, "issue": f"hero-missing: {r['sel']}"})
        else:
            txt = r.get("text", "").strip()
            # Accept placeholder / dash / number — only flag if truly empty
            if not txt:
                issues.append({"code": code, "issue": f"hero-blank: {r['sel']}"})
            # title should not be the literal placeholder "个股" if name is known
            if r["sel"] == "#stock-title" and txt == "个股":
                issues.append({"code": code, "issue": "title-still-placeholder"})

    # Per tab click + check
    for tab_id in TABS:
        net_before = len(network_log)
        try:
            tab_btn = page.locator(f'button[data-tab="{tab_id}"]').first
            if await tab_btn.count() == 0:
                issues.append({"code": code, "issue": f"no-tab-btn: {tab_id}"})
                continue
            # Quick check: is it visible?
            visible = await tab_btn.is_visible()
            if not visible:
                issues.append({"code": code, "issue": f"tab-invisible: {tab_id}"})
                continue
            await tab_btn.click(timeout=5000)
            await asyncio.sleep(0.6)
        except Exception as e:
            issues.append({"code": code, "issue": f"click-fail: {tab_id} - {str(e)[:50]}"})
            continue

        # Pane check
        pane = await page.evaluate(f"""() => {{
          const p = document.querySelector('[data-tab-pane="{tab_id}"]');
          if (!p) return {{exists: false}};
          return {{
            exists: true,
            textLen: (p.textContent || '').length,
            childCount: p.children.length,
            canvasCount: p.querySelectorAll('canvas').length,
            tableCount: p.querySelectorAll('table').length,
          }};
        }}""")
        if not pane.get("exists"):
            issues.append({"code": code, "issue": f"pane-missing: {tab_id}"})
            continue
        text_len = pane.get("textLen", 0)
        canvas_count = pane.get("canvasCount", 0)
        table_count = pane.get("tableCount", 0)
        has_data = text_len > 30 or canvas_count > 0 or table_count > 0
        if not has_data:
            issues.append({"code": code, "issue": f"tab-empty: {tab_id} text={text_len}"})

        # API errors during this tab
        for r in network_log[net_before:]:
            if r["status"] >= 500 or r["status"] == 404:
                issues.append({"code": code, "issue": f"tab-api-{r['status']}: {tab_id} {r['url'][:60]}"})

    page.remove_listener("response", on_response)
    page.remove_listener("console", on_console)
    return {"code": code, "issues": issues, "console_errors": page_errors[:5]}


async def run_round(round_num, codes):
    issues = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()
        for code in codes:
            r = await probe_stock(page, code, round_num)
            for i in r["issues"]:
                issues.append({"code": r["code"], "issue": i["issue"]})
            if r["console_errors"]:
                for ce in r["console_errors"][:3]:
                    # Skip SW register 500 noise
                    if "ServiceWorker" in ce or "500" in ce:
                        continue
                    issues.append({"code": r["code"], "issue": f"console: {ce[:80]}"})
            # Small gap between stocks to let server breathe
            await asyncio.sleep(1)
        await browser.close()

    # Verify meta recommend still works (independent)
    try:
        import urllib.request
        with urllib.request.urlopen(f"{BASE}/api/meta/recommend?top_n=5", timeout=20) as resp:
            data = json.loads(resp.read())
            picks = data.get("data", {}).get("picks", [])
            if not picks:
                issues.append({"code": "-", "issue": "meta_recommend_empty"})
    except Exception as e:
        issues.append({"code": "-", "issue": f"meta_recommend_fail: {str(e)[:60]}"})

    return issues


async def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    delay = float(sys.argv[2]) if len(sys.argv) > 2 else 0
    clear_streak = 0
    for i in range(rounds):
        issues = await run_round(i + 1, CODES)
        for iss in issues:
            log_issue({"round": i + 1, "category": iss.get("issue", "?").split(":")[0]}, iss.get("issue", ""))

        print(f"\n=== Round {i+1} ===")
        print(f"Issues found: {len(issues)}")
        for iss in issues[:15]:
            print(f"  [{iss['code']}] {iss['issue']}")
        if not issues:
            clear_streak += 1
            print(f"✓ ALL CLEAR (streak={clear_streak})")
        else:
            clear_streak = 0
        await asyncio.sleep(delay)


if __name__ == "__main__":
    asyncio.run(main())