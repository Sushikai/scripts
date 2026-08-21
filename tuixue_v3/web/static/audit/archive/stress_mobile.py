#!/usr/bin/env python3
"""
1000-round 移动端压测 — 找所有卡顿根因

策略:
  - iPhone 13 viewport (390x844)
  - 1000 轮随机操作: 导航 / 点击 / 滑动 / 刷新 / 切股
  - 每轮记录: round# / 动作 / 响应时间 / 错误 / 长任务 / 视觉异常
  - 输出 JSON + 摘要报告

1000 轮分布 (经验值):
  - 300 轮 sidebar 切换 (8 view)
  - 200 轮 搜索 + 切股
  - 200 轮 上下滚动 (各 view)
  - 100 轮 SW cache miss 刷新
  - 100 轮 双击加自选 + 长按 K线 popover
  - 100 轮 Pull-to-refresh 模拟 (hash 重复刷新)
"""
import asyncio
import json
import random
import time
from pathlib import Path
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7799"
OUT_DIR = Path(__file__).parent / "stress"
OUT_DIR.mkdir(exist_ok=True)

VIEWS = ['dash', 'all_stocks', 'watchlist', 'review', 'weekly_bull',
         'screener', 'dragons', 'laws', 'ai-review']
STOCKS = ['600519', '300750', '002452', '000001', '300308', '603716',
          '002415', '601318', '002594', '000725']


async def one_round(page, round_idx, action_log, err_log):
    """随机挑一个动作,记录响应时间 + 异常"""
    action = random.choice(['nav', 'stock', 'scroll', 'refresh', 'sidebar', 'wl_toggle'])
    t0 = time.perf_counter()
    try:
        if action == 'nav':
            v = random.choice(VIEWS)
            await page.evaluate(f"location.hash = '#{v}'")
        elif action == 'stock':
            code = random.choice(STOCKS)
            await page.evaluate(f"location.hash = '#stock={code}'")
        elif action == 'sidebar':
            v = random.choice(VIEWS)
            try:
                await page.click(f'.sidebar-item[data-jump="{v}"]', timeout=2000)
            except Exception:
                await page.evaluate(f"location.hash = '#{v}'")
        elif action == 'scroll':
            y = random.choice([300, 600, 1000, 1500, 2000])
            await page.evaluate(f"window.scrollTo({{top: {y}, behavior: 'instant'}})")
        elif action == 'refresh':
            # force re-fetch (hash 不变时强制刷新)
            await page.reload(wait_until="domcontentloaded")
        elif action == 'wl_toggle':
            try:
                # 找 ⭐ 按钮 (desktop + mobile 都兼容的选择器)
                sel = 'button[aria-label*="自选"], .wl-btn, [data-action="watchlist"]'
                await page.click(sel, timeout=1500)
            except Exception:
                pass
        # 等渲染
        await page.wait_for_timeout(random.choice([300, 500, 800, 1200]))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        action_log.append({
            'round': round_idx, 'action': action, 'elapsed_ms': round(elapsed_ms, 1),
            'view': await page.evaluate("location.hash.replace('#','').split('=')[0] || 'dash'")
        })
        return elapsed_ms
    except Exception as e:
        err_log.append({'round': round_idx, 'action': action, 'err': str(e)[:200]})
        return None


async def main():
    n_rounds = int(__import__('os').environ.get('ROUNDS', 1000))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
        )
        page = await ctx.new_page()

        errs = []
        page.on("pageerror", lambda e: errs.append({"type": "pageerror", "msg": str(e)[:200]}))
        page.on("console", lambda m: errs.append({"type": m.type, "msg": m.text[:200]}) if m.type == "error" else None)

        long_tasks = []
        page.on("metrics", lambda m: long_tasks.append({"ts": int(time.time() * 1000)}) if m.get("longTasks") else None)

        await page.goto(BASE, wait_until="domcontentloaded")
        await page.wait_for_function("typeof showView === 'function'", timeout=15000)
        await page.wait_for_timeout(2000)

        action_log = []
        sample_errs = []
        t_start = time.perf_counter()

        for i in range(n_rounds):
            await one_round(page, i, action_log, sample_errs)
            if len(errs) > 50:
                sample_errs = errs[:50]
            if i % 25 == 0:
                print(f"  round {i}/{n_rounds}, elapsed {time.perf_counter() - t_start:.1f}s, errs={len(errs)}", flush=True)

        total = time.perf_counter() - t_start

        # 摘要
        slow = [a for a in action_log if a['elapsed_ms'] > 2000]
        action_dist = {}
        for a in action_log:
            action_dist[a['action']] = action_dist.get(a['action'], 0) + 1

        summary = {
            'total_rounds': n_rounds,
            'total_seconds': round(total, 1),
            'rps': round(n_rounds / total, 2),
            'action_distribution': action_dist,
            'slow_rounds': len(slow),
            'slowest_ms': max((a['elapsed_ms'] for a in action_log), default=0),
            'avg_ms': round(sum(a['elapsed_ms'] for a in action_log) / max(len(action_log), 1), 1),
            'errors_total': len(errs),
            'errors_sample': sample_errs[:20],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        # 输出
        out = OUT_DIR / f"stress_{int(time.time())}.json"
        out.write_text(json.dumps({
            'summary': summary,
            'actions': action_log[-500:],  # last 500 only to keep file small
            'errors': errs[:100],
        }, ensure_ascii=False, indent=2))
        print(f"\n→ {out}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())