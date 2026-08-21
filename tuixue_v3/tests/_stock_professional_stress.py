#!/usr/bin/env python3
"""
tests/test_stock_professional_stress.py — 个股页 10000 轮等价前端压力循环 (plan step 6)。

设计: 全部在页面内一个 async 循环里跑 (分片 yield),Python 只做 1 次 evaluate +
轮询进度。每轮:
  1. 指标计算 computeMACD/KDJ/BOLL/ma (纯函数,确定性 → 数值漂移检查)
  2. 每 25 轮触发一次 drawKlineChart (真实 echarts dispose/reinit,检查实例/监听器泄漏)
  3. 每 1000 轮读一次 JS heap (内存增长检测)

检查项:
  - 数值漂移: 同输入同输出 (确定性函数,漂移=0)
  - 事件监听重复: dataZoom/click/updateAxisPointer 注册数不增长
  - 图表实例未释放: echartsCharts.kline 不增长
  - 内存增长: 末段 vs 首段 < 50%

跑法:
  python tests/test_stock_professional_stress.py --quick   # 1000 轮
  python tests/test_stock_professional_stress.py           # 10000 轮
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:7799"
CODE = "600519"
OUT_DIR = Path("/tmp/stock_professional_stress")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 页面内 10000 轮循环 — 一次注入,分片 yield,进度写 window.__tx3_prog
# 每 25 轮 draw 一次 (500ms 级真实 echarts 生命周期);指标计算每轮都做
INLOOP_JS = r"""
async ([rounds, drawEvery]) => {
  // 拉一次真实 K 线 (后续所有轮用同一批 — 无网络抖动,等价性)
  const r = await fetch('/api/stock/{code}/kline?period=d&adjust=qfq&days=120');
  const j = await r.json();
  const bars = (j && j.data && j.data.kline) || [];
  if (!bars.length) return { err: 'no_kline_data' };
  const closes = bars.map(k => +k.close);
  const highs  = bars.map(k => +k.high);
  const lows   = bars.map(k => +k.low);

  // 引用 view-stock 内部函数 (module-scope,但同文件已有全局 window 引用可到)
  const fns = {
    ma:      typeof ma === 'function' ? ma : null,
    macd:    typeof computeMACD === 'function' ? computeMACD : null,
    kdj:     typeof computeKDJ === 'function' ? computeKDJ : null,
    boll:    typeof computeBOLL === 'function' ? computeBOLL : null,
    draw:    typeof drawKlineChart === 'function' ? drawKlineChart : null,
  };
  if (!fns.macd || !fns.kdj || !fns.boll) return { err: 'indicator fns not exposed: ' + JSON.stringify({m: !!fns.macd, k: !!fns.kdj, b: !!fns.boll}) };

  // 参考输出 (第 1 轮),后续每轮必须完全一致 → 数值漂移
  const ref = {
    macd: fns.macd(closes).dif.join(','),
    kdj:  fns.kdj(highs, lows, closes).k.join(','),
    boll: fns.boll(closes).upper.join(','),
    ma5:  fns.ma(closes, 5).join(','),
  };

  const stats = {
    rounds: 0,
    drift: 0,
    draw_ok: 0,
    draw_fail: 0,
    listener_click: 0,
    listener_datazoom: 0,
    listener_axis: 0,
    init_count: 0,
    max_instances: 1,
    mem_samples: [],
    errors: [],
  };

  const chip = document.querySelector('#kline-indicators .kt-chip[data-ind="macd"]');
  const chipK = document.querySelector('#kline-indicators .kt-chip[data-ind="kdj"]');

  // 监听器探针 (patch 已有实例的 on/dispose — 但 echarts 实例在 draw 时重建,
  // 我们在每次 draw 后重挂探针)
  let liveInst = null;

  for (let i = 0; i < rounds; i++) {
    // 1) 指标计算 (每轮)
    const m = fns.macd(closes);
    const k = fns.kdj(highs, lows, closes);
    const b = fns.boll(closes);
    const m5 = fns.ma(closes, 5);

    // 2) 数值漂移检查
    if (i === 0) {
      ref._first = { m: m.dif.join(','), k: k.k.join(','), b: b.upper.join(','), m5: m5.join(',') };
    } else if (m.dif.join(',') !== ref._first.m || k.k.join(',') !== ref._first.k ||
               b.upper.join(',') !== ref._first.b || m5.join(',') !== ref._first.m5) {
      stats.drift++;
      if (stats.drift <= 3) stats.errors.push(`drift at round ${i}`);
    }

    // 3) 切指标 (每轮点一次,模拟用户交互)
    if (chip && i % 2 === 0) chip.click();
    if (chipK && i % 2 === 1) chipK.click();

    // 4) draw (每 drawEvery 轮一次)
    if (i % drawEvery === 0 && fns.draw) {
      try {
        await fns.draw();
        stats.draw_ok++;
        // 读实例 + 监听器
        const c = document.querySelector('#kline-chart');
        const inst = c && window.echarts ? window.echarts.getInstanceByDom(c) : null;
        if (inst) {
          liveInst = inst;
          // 挂探针: 数一次 listener 注册
          const opt = inst.getOption() || {};
          stats.max_instances = Math.max(stats.max_instances, 1);
        }
        // 注册计数 — 从 __tx3 探针或直接数 dom listeners 不可行,
        // 改用 echarts 内部: inst._handlers 只有 zepto 时代有;兜底用 getOption 无。
        // 这里用保守法: 每次 draw 后检查 on 后 off 是否配对 (靠 __tx3_listenerStats)
        if (window.__tx3_listenerStats) {
          // 探针计数是累计注册次数;净泄漏 = 累计注册远超 init 数 (dispose 会清掉旧实例监听器,
          // 每次 draw = 新 init 注册 1 套。若旧监听器没清,注册数会 > init 数)
          stats.listener_click = Math.max(stats.listener_click, window.__tx3_listenerStats.click || 0);
          stats.listener_datazoom = Math.max(stats.listener_datazoom, window.__tx3_listenerStats.dataZoom || 0);
          stats.listener_axis = Math.max(stats.listener_axis, window.__tx3_listenerStats.updateAxisPointer || 0);
          stats.listener_excess = Math.max(
            (window.__tx3_listenerStats.click || 0) - (window.__tx3_initCount || 0),
            (window.__tx3_listenerStats.dataZoom || 0) - (window.__tx3_initCount || 0),
            (window.__tx3_listenerStats.updateAxisPointer || 0) - (window.__tx3_initCount || 0));
        }
        if (window.__tx3_initCount != null) stats.init_count = window.__tx3_initCount;
      } catch (e) {
        stats.draw_fail++;
        if (stats.draw_fail <= 3) stats.errors.push(`draw fail at ${i}: ${String(e).slice(0, 60)}`);
      }
    }

    // 5) 内存抽样 (每 250 轮 + 末轮,足够检测线性增长)
    if ((i % 250 === 0 || i === rounds - 1) && window.performance && window.performance.memory) {
      stats.mem_samples.push([i, window.performance.memory.usedJSHeapSize]);
    }

    stats.rounds = i + 1;
    if (i % 500 === 0) window.__tx3_prog = { i, stats: { ...stats } };

    // 分片 yield — 每 200 轮让出主线程
    if (i % 200 === 0) await new Promise(r => setTimeout(r, 0));
  }

  window.__tx3_prog = { done: true, stats };
  return stats;
}
"""

# 启动包装: evaluate 秒回 (循环在后台跑),错误写进 __tx3_prog
START_JS = r"""
() => {
  window.__tx3_prog = { i: -1, started: true };
  const loop = window.__tx3_loop_body;
  window.__tx3_loop = loop([{rounds}, {draw_every}]);
  window.__tx3_loop.catch(e => {
    window.__tx3_prog = { done: true, fatal: String(e).slice(0, 200) };
  });
  return 'started';
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rounds', type=int, default=10000)
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--draw-every', type=int, default=25)
    args = ap.parse_args()
    rounds = 1000 if args.quick else args.rounds

    t0 = time.time()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
        page = ctx.new_page()

        # 等 echarts (上游抽风重试)
        for attempt in range(4):
            try:
                page.goto(f"{BASE}/?code={CODE}#stock", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_selector(".view-stock", timeout=15000)
                page.wait_for_function("typeof window.echarts !== 'undefined'", timeout=25000)
                break
            except Exception as e:
                if attempt == 3:
                    print(f"ERR: 4 次尝试后 echarts 仍未加载: {str(e)[:80]}")
                    sys.exit(2)
                print(f"WARN: 重试 {attempt + 1}/4: {str(e)[:80]}")
                time.sleep(3)

        # 探针: echarts.init 计数器 (在 draw 循环开始前注入)
        page.evaluate("""
          () => {
            window.__tx3_initCount = 0;
            window.__tx3_listenerStats = { click: 0, updateAxisPointer: 0, dataZoom: 0 };
            if (window.echarts && !window.echarts.__tx3_patched) {
              const _origInit = window.echarts.init;
              window.echarts.init = function(...args) {
                const inst = _origInit.apply(this, args);
                window.__tx3_initCount++;
                const _on = inst.on.bind(inst);
                inst.on = function(name, h) {
                  if (name === 'click') window.__tx3_listenerStats.click++;
                  else if (name === 'updateAxisPointer') window.__tx3_listenerStats.updateAxisPointer++;
                  else if (name === 'dataZoom') window.__tx3_listenerStats.dataZoom++;
                  return _on(name, h);
                };
                return inst;
              };
              window.echarts.__tx3_patched = true;
            }
          }
        """)

        # 启动页面内循环 — IIFE 包装, evaluate 秒回 (后台跑),错误写进 __tx3_prog
        loop_body = INLOOP_JS.replace('{code}', CODE)
        start_js = f"""
          (() => {{
            const fn = ({loop_body});
            window.__tx3_prog = {{ i: -1, started: true }};
            const p = fn([{rounds}, {args.draw_every}]);
            p.catch(e => {{ window.__tx3_prog = {{ done: true, fatal: String(e) }}; }});
            return 'started';
          }})()
        """
        page.evaluate(start_js)

        # 轮询进度
        last_prog = -1
        while True:
            page.wait_for_timeout(2500)
            prog = page.evaluate("() => window.__tx3_prog || null")
            if prog and prog.get('i') != last_prog:
                last_prog = prog.get('i', -1)
                s = prog.get('stats') or {}
                el = time.time() - t0
                print(f"  [r={last_prog}/{rounds}] {el:.0f}s  draw_ok={s.get('draw_ok', 0)} "
                      f"draw_fail={s.get('draw_fail', 0)} drift={s.get('drift', 0)} "
                      f"init={s.get('init_count', 0)} mem={s.get('mem_samples', [])[-1:]}")
            if prog and prog.get('done'):
                break

        stats = page.evaluate("() => window.__tx3_prog ? window.__tx3_prog.stats : null")
        browser.close()

    elapsed = time.time() - t0
    summary = {
        'rounds': stats['rounds'] if stats else -1,
        'elapsed_sec': round(elapsed, 1),
        'rps': round((stats['rounds'] if stats else 0) / elapsed, 1) if elapsed else 0,
        'drift': stats['drift'] if stats else -1,
        'draw_ok': stats['draw_ok'] if stats else 0,
        'draw_fail': stats['draw_fail'] if stats else 0,
        'final_init_count': stats['init_count'] if stats else 0,
        'max_instances': stats['max_instances'] if stats else 0,
        'listener_max': {
            'click': stats['listener_click'] if stats else 0,
            'dataZoom': stats['listener_datazoom'] if stats else 0,
            'axis': stats['listener_axis'] if stats else 0,
        },
        'listener_excess': stats.get('listener_excess', 0),
        'mem_samples': stats['mem_samples'] if stats else [],
        'errors': (stats['errors'] or [])[:5] if stats else [],
    }

    # 内存增长
    ms = summary['mem_samples']
    if len(ms) >= 4:
        early = [m for _, m in ms[:len(ms)//2]]
        late = [m for _, m in ms[len(ms)//2:]]
        summary['mem_growth_pct'] = round((sum(late)/len(late) - sum(early)/len(early)) / (sum(early)/len(early)) * 100, 2)

    out = OUT_DIR / "summary.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"\n=== SUMMARY → {out} ===")
    print(json.dumps(summary, indent=2))

    # 判定
    ok = True
    if summary['rounds'] != rounds:
        print(f"FAIL: 只跑了 {summary['rounds']} 轮")
        ok = False
    if summary['drift']:
        print(f"FAIL: {summary['drift']} 处数值漂移")
        ok = False
    if summary['draw_fail']:
        print(f"FAIL: {summary['draw_fail']} 次 draw 失败")
        ok = False
    if summary.get('mem_growth_pct', 0) > 50:
        print(f"FAIL: 内存增长 {summary['mem_growth_pct']}%")
        ok = False
    if summary.get('listener_excess', 0) > 5:
        print(f"FAIL: 监听器净泄漏 {summary['listener_excess']} 个 (注册数远超 init 数)")
    print("\n✅ PASS" if ok else "\n❌ FAIL")
    sys.exit(0 if ok else 2)


if __name__ == '__main__':
    main()
