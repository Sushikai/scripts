"""
退学 v3 · R41-50 视觉压测 harness
==================================
使用 Playwright 自动化测试 3 场景: normal / zero_trades / view_leave

用法:
  /Users/kaikai/.hermes/hermes-agent/venv/bin/python3 \\
      web/tests/playwright_stress.py \\
      --scenario normal          # 单场景
      --scenario all             # 全部
      --mobile                    # mobile viewport
      --sample 200                # backtest sample (default 200)

输出: PNG 截图 + JSON 报告
"""
import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# Playwright Python API
from playwright.async_api import async_playwright

BASE = "http://localhost:7799"
OUT = Path("/Users/kaikai/scripts/tuixue_v3/web/tests/artifacts")
OUT.mkdir(parents=True, exist_ok=True)


# ═══════════════ 工具函数 ═══════════════

async def shot(page, name, dir_):
    p = dir_ / f"{name}.png"
    await page.screenshot(path=str(p), full_page=False)
    return p


async def scroll_to_bt(page):
    await page.evaluate("""() => {
        const el = document.getElementById('bt-mount');
        if (el) el.scrollIntoView({ behavior: 'instant', block: 'start' });
    }""")
    await asyncio.sleep(0.3)


async def get_bt_state(page):
    return await page.evaluate("""() => {
        const r = (sel) => {
            const el = document.querySelector(sel);
            return el ? { text: el.textContent?.trim().slice(0, 80), hidden: el.hidden } : null;
        };
        return {
            kpis:       r('#bt-kpis'),
            progress:   r('#bt-progress'),
            monthly:    r('#bt-monthly tbody tr'),
            scenarios9: r('#bt-scenarios-9-host'),
            equity:     r('#bt-equity-chart'),
            exitHost:   r('#bt-exit-host'),
            sectorHost: r('#bt-sector-host'),
            windows:    r('#bt-windows-host'),
            fivemin:    r('#bt-5min-host'),
            actual10:   r('#bt-actual10-host'),
            exits:      r('#bt-exits-compare-host'),
        };
    }""")


def _restart_server_via_launchd():
    """通过 launchd 重启 server, 让 _BT_RUNS 清空 (R51 已知: cancel 不能立即终止长 _cb 间期)"""
    import subprocess
    try:
        subprocess.run(["launchctl", "kickstart", "-k", "gui/501/com.kaikai.tuixue.server"],
                       timeout=5, capture_output=True)
        return True
    except Exception as e:
        print(f"  ⚠️ launchctl kickstart err: {e}")
        return False


async def wait_no_running_bt(timeout_s=300):
    """通过 launchd 重启 server 清空 _BT_RUNS, 然后等 server 恢复"""
    print(f"[init] launchctl kickstart 重启 server 清空 _BT_RUNS...")
    _restart_server_via_launchd()
    # 等 server 起来 (healthz OK)
    for i in range(30):
        try:
            with urllib.request.urlopen(f"{BASE}/api/healthz", timeout=2) as r:
                if r.status == 200:
                    print(f"[init] ✓ server 已重启就绪 (+{i}s)")
                    await asyncio.sleep(1)  # 稳一下
                    return True
        except Exception:
            pass
        await asyncio.sleep(1)
    print("[init] ✗ server 启动超时")
    return False


# ═══════════════ 场景 ═══════════════

async def scenario_normal(page, dir_, sample=200):
    print(f"\n[场景] 正常回测 · 半年 + {sample} 只")
    await page.goto(BASE, wait_until="domcontentloaded")
    await page.evaluate("""async () => {
        if (navigator.serviceWorker) {
            const regs = await navigator.serviceWorker.getRegistrations();
            for (const r of regs) { try { await r.unregister(); } catch {} }
        }
        localStorage.clear();
        sessionStorage.clear();
    }""")
    await page.context.clear_cookies()
    await page.goto(f"{BASE}/?view=screener", wait_until="domcontentloaded")
    await page.wait_for_selector('.view-screener:not([hidden])', timeout=10000)
    await page.evaluate("document.getElementById('backtest-panel')?.classList.remove('collapsed')")
    await page.evaluate(f"""() => {{
        const f = (id, v) => {{ const e = document.getElementById(id); if (e) e.value = v; }};
        f('bt-sample', '{sample}');
        f('bt-hold', '3');
        f('bt-topn', '1');
        document.querySelectorAll('input[name="bt-p"]').forEach(cb => {{ cb.checked = cb.value === '半年'; }});
    }}""")
    await shot(page, "01_screener_normal_idle", dir_)
    await shot(page, "02_screener_normal_before_run", dir_)
    await page.click('#bt-run')
    print(f"  ⏳ 等待回测完成 (≤ 240s)...")
    rendered = False
    done = False
    for i in range(240):
        st = await page.evaluate("""() => ({
            progress: document.getElementById('bt-progress')?.textContent || '',
            elapsed: document.getElementById('bt-elapsed')?.textContent || '',
            kpiLen: document.getElementById('bt-kpis')?.innerHTML.trim().length || 0,
            btnDisabled: !!document.querySelector('#bt-run')?.disabled,
        })""")
        if i % 15 == 0:
            print(f"    +{i}s elapsed={st['elapsed']!r} progress={st['progress'][:40]!r} kpiLen={st['kpiLen']}")
        if st['kpiLen'] > 100 and not st['btnDisabled']:
            rendered = True
            done = True
            print(f"  ✓ KPI 渲染完成 ({i}s, kpiLen={st['kpiLen']})")
            break
        if '失败' in st['progress'] or '错误' in st['progress'] or '超时' in st['progress']:
            print(f"  ✗ 回测失败: {st['progress'][:80]}")
            break
        await asyncio.sleep(1)
    await asyncio.sleep(1)
    await scroll_to_bt(page)
    await shot(page, "03_screener_normal_done", dir_)
    state = await get_bt_state(page)
    return {
        "name": "normal",
        "done": done,
        "kpis_filled": bool(state['kpis'] and state['kpis']['text']),
        "scenarios9_filled": bool(state['scenarios9'] and state['scenarios9']['text']),
        "monthly_filled": bool(state['monthly']),
        "equity_filled": bool(state['equity']),
        "exit_filled": bool(state['exitHost'] and state['exitHost']['text']),
        "sector_filled": bool(state['sectorHost'] and state['sectorHost']['text']),
        "windows_filled": bool(state['windows'] and state['windows']['text']),
    }


async def scenario_zero_trades(page, dir_):
    print("\n[场景] 0 笔交易占位 — 直接注入空结果")
    await page.goto(f"{BASE}/?view=screener", wait_until="domcontentloaded")
    await page.evaluate("localStorage.clear(); sessionStorage.clear();")
    await page.reload(wait_until="domcontentloaded")
    await page.wait_for_selector('.view-screener:not([hidden])', timeout=10000)
    await page.evaluate("document.getElementById('backtest-panel')?.classList.remove('collapsed')")
    await page.evaluate("""() => {
        const emptyResult = {
            summary: { trades: 0, win_rate_pct: 0, cum_return_pct: 0 },
            scenarios: { _best_strategy: 'S1' },
            scenarios_hold: {},
            monthly: [],
            equity_curve: [],
            exit_breakdown: {},
            sector: [],
            windows: [],
            recovery_5min: { n_underwater: 0 },
            actual_10_stats: { n_underwater: 0 },
            trades: [],
            config: { period_keys: ['半年'], hold_days: 3, top_n: 1, sample_size: 500, universe_size: 5000 },
            engine_version: 'v4-test',
            took_sec: 12.3,
            trades_count: 0,
        };
        if (typeof window.btRenderV4 === 'function') {
            window.btRenderV4(emptyResult);
        } else {
            console.warn('[stress] window.btRenderV4 not exposed');
        }
    }""")
    await asyncio.sleep(1)
    await scroll_to_bt(page)
    await shot(page, "04_screener_zero_trades", dir_)
    state = await get_bt_state(page)
    return {
        "name": "zero_trades",
        "kpis_has_banner": bool(state['kpis'] and ('未产生' in state['kpis']['text'] or '无交易' in state['kpis']['text'])),
        "exit_has_placeholder": bool(state['exitHost'] and ('退出原因' in state['exitHost']['text'] or '无交易' in state['exitHost']['text'])),
    }


async def scenario_view_leave(page, dir_, mobile=False):
    print("\n[场景] view leave 清理 SSE/timer")
    await page.goto(f"{BASE}/?view=screener", wait_until="domcontentloaded")
    await page.evaluate("localStorage.clear(); sessionStorage.clear();")
    await page.reload(wait_until="domcontentloaded")
    await page.wait_for_selector('.view-screener:not([hidden])', timeout=10000)
    await page.evaluate("document.getElementById('backtest-panel')?.classList.remove('collapsed')")
    es_state_before = await page.evaluate("""() => ({
        inScreener: !!document.querySelector('.view-screener:not([hidden])'),
        runBtnDisabled: !!document.querySelector('#bt-run')?.disabled,
    })""")
    # Mobile: 侧栏收起, 先点 hamburger 再点跳转; Desktop: 直接点
    if mobile:
        try:
            await page.click('#menu-btn', timeout=3000)
            await asyncio.sleep(0.5)
        except Exception:
            pass
    await page.click('[data-jump="dash"]')
    await asyncio.sleep(1)
    es_state_after = await page.evaluate("""() => ({
        inScreener: !!document.querySelector('.view-screener:not([hidden])'),
        runBtnDisabled: !!document.querySelector('#bt-run')?.disabled,
    })""")
    await shot(page, "05_after_view_leave", dir_)
    # 切回 screener (mobile 也需要打开 sidebar)
    if mobile:
        try:
            await page.click('#menu-btn', timeout=3000)
            await asyncio.sleep(0.5)
        except Exception:
            pass
    await page.click('[data-jump="screener"]')
    await asyncio.sleep(1)
    await shot(page, "06_back_to_screener", dir_)
    return {
        "name": "view_leave",
        "left_view_ok": not es_state_after['inScreener'],
        "bt_run_enabled_after_return": not es_state_after['runBtnDisabled'],
        "before_in_screener": es_state_before['inScreener'],
    }


# ═══════════════ 主流程 ═══════════════

async def run(args):
    results = []
    async with async_playwright() as pw:
        if args.mobile:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(
                viewport={"width": 390, "height": 844},
                device_scale_factor=2,
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0) AppleWebKit/605.1.15",
            )
        else:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(viewport={"width": 1440, "height": 900})

        page = await ctx.new_page()
        page.on("pageerror", lambda e: print(f"  ⚠️ pageerror: {e}", file=sys.stderr))
        page.on("console", lambda m: print(f"  [console.{m.type}] {m.text[:200]}") if m.type == "error" else None)

        if not await wait_no_running_bt():
            print("[init] ⚠️ server 仍有 running BT, 测试可能受影响")

        suffix = "_mobile" if args.mobile else "_desk"
        dir_ = OUT / f"stress_{args.scenario}{suffix}_{int(time.time())}"
        dir_.mkdir(exist_ok=True)

        if args.scenario in ("all", "normal"):
            try:
                r = await scenario_normal(page, dir_, sample=args.sample)
                results.append(r)
            except Exception as e:
                import traceback
                traceback.print_exc()
                results.append({"name": "normal", "error": str(e)})
                print(f"  ! normal fail: {e}", file=sys.stderr)
        if args.scenario in ("all", "zero"):
            try:
                r = await scenario_zero_trades(page, dir_)
                results.append(r)
            except Exception as e:
                import traceback
                traceback.print_exc()
                results.append({"name": "zero_trades", "error": str(e)})
        if args.scenario in ("all", "leave"):
            try:
                r = await scenario_view_leave(page, dir_, mobile=args.mobile)
                results.append(r)
            except Exception as e:
                import traceback
                traceback.print_exc()
                results.append({"name": "view_leave", "error": str(e)})

        await browser.close()

    report = OUT / f"stress_{int(time.time())}.json"
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n报告: {report}")
    print(f"截图: {dir_}/")
    print("\n=== 汇总 ===")
    for r in results:
        if 'error' in r:
            print(f"  ✗ {r['name']}: {r['error']}")
        else:
            ok_keys = [k for k, v in r.items() if k not in ('name', 'done', 'left_view_ok', 'before_in_screener') and v]
            done_str = '✓' if r.get('done', True) else '✗'
            total = len([k for k in r if k not in ('name',)])
            print(f"  {done_str} {r['name']}: {len(ok_keys)}/{total-1} pass")

    return 0 if not any('error' in r for r in results) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="all", choices=["all", "normal", "zero", "leave"])
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--view", default=None, help="(unused, only screener supported)")
    ap.add_argument("--sample", type=int, default=200, help="backtest sample size")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()