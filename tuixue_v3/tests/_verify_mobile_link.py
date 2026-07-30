"""
tests/_verify_mobile_link.py — 手机端链接验收 (2026-07-30)

iPhone 13 viewport 模拟, 5 只不同板块股票:
  600519 (主板大盘), 000001 (主板金融), 300750 (创业板), 688981 (科创板), 830799 (北证)

验收:
  1. 首屏 < 15s (ngrok 阈值 31s, 给安全余量)
  2. deep-analysis 卡片 ready < 25s
  3. 6 section (advice / profile / earnings / holding / tech / summary) 都非空

URL 优先级: ngrok (cookie 验通) → LAN (192.168.101.50:7799) → loca.lt 试
截图落 /tmp/verify_mobile_link/{code}_{iphone|desktop}.png
"""
from __future__ import annotations
import json
import socket
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

TUNNEL_URL_FILE = "/Users/kaikai/scripts/tuixue_v3/tunnel_url.txt"
LAN_URL = "http://192.168.101.50:7799"
OUT = Path("/tmp/verify_mobile_link")
OUT.mkdir(exist_ok=True)

# 5 只不同板块股票
TEST_CODES = [
    ("600519", "主板·大盘"),    # 茅台
    ("000001", "主板·金融"),    # 平安银行
    ("300750", "创业板"),       # 宁德
    ("688981", "科创板"),       # 中芯国际
    ("830799", "北证"),          # 北证
]


def _is_listening(url: str, timeout: float = 4.0) -> bool:
    """验证 URL 可访问 (200)。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _pick_url() -> tuple[str, str]:
    """优先 LAN (本地网络稳) → ngrok (跨网段) 兜底。返回 (label, base_url)。"""
    tunnel = ""
    try:
        tunnel = Path(TUNNEL_URL_FILE).read_text().strip()
    except Exception:
        pass

    # 1. LAN 优先 (本地 7799, 78ms 稳)
    if _is_listening(LAN_URL):
        return "lan", LAN_URL
    # 2. ngrok 跨网段 (需 cookie 才能过 warning 页)
    if tunnel and "ngrok" in tunnel and _is_listening(tunnel):
        return "tunnel", tunnel
    # 3. tunnel 文件里非 ngrok URL (loca.lt 等)
    if tunnel and _is_listening(tunnel):
        return "tunnel", tunnel
    return "lan", LAN_URL  # 默认 LAN, 测试端会 FAIL


def _get_ngrok_cookie_domain(tunnel_url: str) -> str:
    """从 tunnel URL 提取 ngrok domain。"""
    try:
        from urllib.parse import urlparse
        return urlparse(tunnel_url).hostname or "ngrok-free.app"
    except Exception:
        return "ngrok-free.app"


def main() -> int:
    src, base = _pick_url()
    print(f"[setup] using {src} base: {base}")

    ngrok_cookie_domain = _get_ngrok_cookie_domain(base) if "ngrok" in base else None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])

        # iPhone 13 viewport
        iphone_ctx = browser.new_context(
            viewport={"width": 390, "height": 844},
            is_mobile=True, has_touch=True, device_scale_factor=3,
            ignore_https_errors=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
            service_workers="block",
        )
        if ngrok_cookie_domain:
            iphone_ctx.add_cookies([{
                "name": "ngrok-skip-browser-warning",
                "value": "true",
                "domain": ngrok_cookie_domain,
                "path": "/",
            }])

        results = []
        for code, label in TEST_CODES:
            print(f"\n[{code} ({label})] 加载 + 验证 deep-analysis…")
            page = iphone_ctx.new_page()
            console_msgs = []
            page.on("console", lambda m: console_msgs.append(f"{m.type}: {m.text[:120]}"))
            page.on("pageerror", lambda e: console_msgs.append(f"PAGEERROR: {e}"))

            t0 = time.time()
            try:
                page.goto(f"{base}/?code={code}#stock", wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"  ❌ goto fail: {e}")
                results.append({"code": code, "label": label, "first_paint_ms": -1, "deep_ready_ms": -1, "passed": False, "err": str(e)[:200]})
                page.close()
                continue

            try:
                page.wait_for_selector(".view-stock", timeout=10000)
            except Exception as e:
                print(f"  ❌ view-stock 渲染超时: {e}")
                results.append({"code": code, "label": label, "first_paint_ms": int((time.time()-t0)*1000), "deep_ready_ms": -1, "passed": False, "err": "view-stock 渲染超时"})
                page.close()
                continue

            first_paint_ms = int((time.time() - t0) * 1000)
            print(f"  ✓ 首屏 {first_paint_ms}ms (视图出现)")
            page.evaluate("document.body.classList.remove('sidebar-open')")
            page.wait_for_timeout(800)

            # 滚动到 deep-analysis card
            page.evaluate("document.querySelector('#stock-deep-analy-card').scrollIntoView({behavior: 'instant', block: 'center'})")
            page.wait_for_timeout(500)

            # 等 deep-analysis ready (chip 非拉取中 且 sections 至少 1 个非空 — 避免 race)
            deep_ready = False
            deep_start = time.time()
            try:
                page.wait_for_function(
                    """() => {
                        const chip = document.querySelector('#deep-action-chip');
                        if (!chip) return false;
                        const txt = (chip.textContent || '').trim();
                        if (!txt || txt.includes('分析中') || txt.includes('拉取中') || txt === '—') return false;
                        // 至少 1 个 section 真正有内容(profile 或 earnings 任一非空)
                        const profile = document.querySelector('#deep-profile-text');
                        const profileLen = profile ? (profile.textContent || '').trim().length : 0;
                        const finRows = document.querySelectorAll('#deep-earnings-body tr').length;
                        return profileLen > 5 || finRows >= 1;
                    }""",
                    timeout=25000
                )
                deep_ready = True
            except Exception:
                pass
            deep_ready_ms = int((time.time() - deep_start) * 1000)
            deep_total_ms = int((time.time() - t0) * 1000)

            # 读 section 状态
            sections = page.evaluate("""() => {
                const get = (id) => {
                    const el = document.querySelector(id);
                    return el ? (el.textContent || '').trim() : null;
                };
                const profile = get('#deep-profile-text') || '';
                const rows = document.querySelectorAll('#deep-earnings-body tr').length;
                const holding = get('#deep-holding-view') || '';
                // tech 区域:有数据时渲染 8 个子 div;无数据时显示 "技术数据不足" 占位
                const techView = document.querySelector('#deep-tech-view');
                const techChildren = techView ? techView.querySelectorAll(':scope > div > div').length : 0;
                const techHasData = techChildren >= 4;
                const techPlaceholder = techView ? ((techView.textContent || '').includes('技术数据不足') || (techView.textContent || '').includes('暂无')) : false;
                const summary = get('#deep-summary-text') || '';
                return {
                    action_chip: get('#deep-action-chip'),
                    score: get('#deep-score'),
                    profile_len: profile.length,
                    profile_non_empty: profile.length > 5,
                    earnings_rows: rows,
                    earnings_ok: rows >= 1,
                    holding_non_empty: holding.length > 5,
                    tech_rows: techChildren,
                    tech_ok: techHasData || techPlaceholder,  // 有数据 OR 正确显示兜底
                    tech_placeholder: techPlaceholder,
                    summary_non_empty: summary.length > 5,
                };
            }""")
            all_ok = (deep_ready and sections["profile_non_empty"] and sections["earnings_ok"]
                      and sections["holding_non_empty"] and sections["tech_ok"] and sections["summary_non_empty"])
            passed = (first_paint_ms < 15000 and deep_ready and all_ok)

            # 截图
            page.locator("#stock-deep-analy-card").screenshot(path=str(OUT / f"{code}_iphone.png"))

            err_msgs = [m for m in console_msgs if "PAGEERROR" in m]
            status = {
                "code": code,
                "label": label,
                "first_paint_ms": first_paint_ms,
                "deep_ready_ms": deep_ready_ms,
                "deep_total_ms": deep_total_ms,
                "deep_ready": deep_ready,
                "sections": sections,
                "passed": passed,
                "console_errors": err_msgs[:3],
            }
            results.append(status)
            print(f"  {'✅' if passed else '❌'} deep ready: {deep_ready} ({deep_ready_ms}ms) | sections: profile={sections['profile_non_empty']} earnings={sections['earnings_ok']} holding={sections['holding_non_empty']} tech={sections['tech_ok']} summary={sections['summary_non_empty']}")
            page.close()

        # Desktop 1280 viewport 截图 (1 张就够, 主验 mobile)
        desktop_ctx = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block", ignore_https_errors=True)
        if ngrok_cookie_domain:
            desktop_ctx.add_cookies([{
                "name": "ngrok-skip-browser-warning",
                "value": "true",
                "domain": ngrok_cookie_domain,
                "path": "/",
            }])
        page = desktop_ctx.new_page()
        try:
            page.goto(f"{base}/?code=600519#stock", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".view-stock", timeout=10000)
            page.wait_for_timeout(2500)
            page.evaluate("document.body.classList.remove('sidebar-open')")
            try:
                page.wait_for_function(
                    """() => {
                        const chip = document.querySelector('#deep-action-chip');
                        if (!chip) return false;
                        const txt = (chip.textContent || '').trim();
                        if (!txt || txt.includes('分析中') || txt.includes('拉取中') || txt === '—') return false;
                        const profile = document.querySelector('#deep-profile-text');
                        const profileLen = profile ? (profile.textContent || '').trim().length : 0;
                        const finRows = document.querySelectorAll('#deep-earnings-body tr').length;
                        return profileLen > 5 || finRows >= 1;
                    }""",
                    timeout=25000
                )
                page.evaluate("document.querySelector('#stock-deep-analy-card').scrollIntoView({behavior: 'instant', block: 'center'})")
                page.wait_for_timeout(500)
                page.locator("#stock-deep-analy-card").screenshot(path=str(OUT / "600519_desktop.png"))
                print(f"\n[desktop 600519] ✓ 截图保存")
            except Exception as e:
                print(f"\n[desktop 600519] ❌ deep-analysis ready fail: {e}")
        except Exception as e:
            print(f"\n[desktop 600519] ❌ goto fail: {e}")
        page.close()

        browser.close()

        # 输出报告
        n_pass = sum(1 for r in results if r["passed"])
        n_total = len(results)
        report = {
            "base_url": base,
            "source": src,
            "tunnel_url_file": Path(TUNNEL_URL_FILE).read_text().strip() if Path(TUNNEL_URL_FILE).exists() else "",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "passed": n_pass,
            "total": n_total,
            "results": results,
        }
        (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))

        # summary.md
        md = ["# 手机端链接验收\n",
              f"基准 URL: `{base}` ({src})  \n",
              f"通过: **{n_pass}/{n_total}**\n",
              "| 代码 | 板块 | 首屏 (ms) | deep ready (ms) | profile | earnings | holding | tech | summary | PASS |",
              "|------|------|-----------|-----------------|---------|----------|---------|------|---------|------|"]
        for r in results:
            if "sections" in r:
                sec = r["sections"]
                tech_disp = f"{sec['tech_rows']}{'(空占位)' if sec.get('tech_placeholder') else ''}"
                md.append(f"| {r['code']} | {r['label']} | {r['first_paint_ms']} | {r.get('deep_ready_ms', '—')} | {'✓' if sec['profile_non_empty'] else '✗'} | {'✓' if sec['earnings_ok'] else '✗'} | {'✓' if sec['holding_non_empty'] else '✗'} | {tech_disp} | {'✓' if sec['summary_non_empty'] else '✗'} | {'✅' if r['passed'] else '❌'} |")
            else:
                md.append(f"| {r['code']} | {r['label']} | {r['first_paint_ms']} | — | — | — | — | — | — | ❌ |")
        (OUT / "summary.md").write_text("\n".join(md))

        print(f"\n=== 总览: {n_pass}/{n_total} PASS ===")
        print(f"截图: {OUT}/{{code}}_iphone.png + 600519_desktop.png")
        print(f"报告: {OUT}/{{report.json,summary.md}}")
        if n_pass < n_total:
            print("\n❌ FAIL 详情:")
            for r in results:
                if not r["passed"]:
                    print(f"  - {r['code']} ({r['label']}): first_paint={r['first_paint_ms']}ms deep_ready={r.get('deep_ready_ms', 'N/A')}ms")
                    if r.get("console_errors"):
                        for e in r["console_errors"][:2]:
                            print(f"      console: {e}")
        return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())