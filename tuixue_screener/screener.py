#!/usr/bin/env python3
"""
tuixue_screener/screener.py
主入口：手动触发选股流程，仅支持 run_stock_screen() 调用。

数据流：
1. 数据源拉取（多源兜底）
2. 周期判定（最高优先级闸门）
3. 主线识别
4. 四层流水线筛选
5. 排序 → 输出 ≤10 只
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import data_source as ds
import pipeline as P
import config as C

log = logging.getLogger("screener")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ROOT = Path(__file__).parent
LOGS_DIR = ROOT / "logs"
REPORTS_DIR = ROOT / "reports"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ════════════════════════════════════════════════════════════
# 顶层：手动触发入口
# ════════════════════════════════════════════════════════════
def run_stock_screen(top_n: int = C.MAX_OUTPUT,
                      trade_date: str | None = None,
                      skip_intraday: bool = False) -> list[dict]:
    """
    手动触发选股流程。
    
    Args:
        top_n: 输出最多几只
        trade_date: 回测时使用的日期（默认今天）
        skip_intraday: 是否跳过 Layer 4 分时（盘后/回测用）
    
    Returns:
        选股结果列表，按 rank_score 降序，最多 top_n 只
        空列表代表无达标（宁缺毋滥）
    """
    started = time.time()
    today = trade_date or datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"🚀 退学战法选股系统  |  {today}  |  {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")

    # ════════════════════════════════════════════════════════════
    # Step 1: 数据拉取（多源兜底）
    # ════════════════════════════════════════════════════════════
    print("\n[Step 1] 数据拉取")
    spot, src_spot = ds.get_spot()
    if not spot:
        log.error("❌ 行情数据全瘫 → 终止本次选股")
        return []
    print(f"  ✓ 行情快照: {len(spot)} 条 ({src_spot})")

    sectors, src_sec = ds.get_sector_rank()
    if not sectors:
        log.warning("⚠️ 板块数据失效 → 主线识别退化为'涨幅前 8 板块'")
    else:
        print(f"  ✓ 板块数据: {len(sectors)} 条 ({src_sec})")

    zt_pool, src_zt = ds.get_zt_pool(today)
    if not zt_pool:
        log.warning("⚠️ 涨停池获取失败 → 周期判定退化为'spot 估算'")
    else:
        print(f"  ✓ 涨停池: {len(zt_pool)} 条 ({src_zt})")

    # ════════════════════════════════════════════════════════════
    # Step 2: 周期前置闸门
    # ════════════════════════════════════════════════════════════
    print("\n[Step 2] 情绪周期判定")
    cycle = P.check_market_cycle(spot, zt_pool, sectors or [])
    print(f"  当前周期: {cycle['phase']}  | 允许选股: {cycle['allow']}")
    print(f"  详情: {cycle['detail']}")

    if not cycle["allow"]:
        print(f"\n❌ 当前周期 {cycle['phase']} 不允许开新仓 → 返回空列表")
        _save_report([], today, cycle, [], elapsed=time.time() - started)
        return []

    # ════════════════════════════════════════════════════════════
    # Step 3: 主线识别
    # ════════════════════════════════════════════════════════════
    print("\n[Step 3] 主线识别")
    mainlines = P.identify_mainlines(sectors or [], spot, None)
    if not mainlines:
        print("  ❌ 无符合条件的主线（上涨 < 40 只）→ 返回空列表")
        _save_report([], today, cycle, [], elapsed=time.time() - started)
        return []
    for m in mainlines:
        print(f"  ✓ {m['sector_name']:<14} 涨{m['change_pct']:>5.2f}%  涨股 {m['up_count']:>3}  资金 {m['fund_flow_proxy']/1e8:>6.2f}亿")

    # ════════════════════════════════════════════════════════════
    # Step 4: Layer 1 - 全局基础风险初筛
    # ════════════════════════════════════════════════════════════
    print("\n[Step 4] Layer 1 - 全局基础风险初筛")
    blacklist = P.load_blacklist()
    passed_l1 = P.layer1_basic_filter(spot, blacklist)
    print(f"  ✓ 通过 {len(passed_l1)} / {len(spot)} 只")

    # ════════════════════════════════════════════════════════════
    # Step 5: Layer 2 - 主线题材过滤
    # ════════════════════════════════════════════════════════════
    print("\n[Step 5] Layer 2 - 主线题材过滤")
    passed_l2, rejected_l2 = P.layer2_mainline_filter(passed_l1, mainlines, spot)
    print(f"  ✓ 通过 {len(passed_l2)} 只（隶属主线）")

    # ════════════════════════════════════════════════════════════
    # Step 6: Layer 3 - 日线趋势形态
    # ════════════════════════════════════════════════════════════
    print("\n[Step 6] Layer 3 - 日线趋势形态深度过滤")
    passed_l3 = P.layer3_trend_filter(passed_l2)
    print(f"  ✓ 通过 {len(passed_l3)} 只")

    # ════════════════════════════════════════════════════════════
    # Step 7: 盈亏比过滤
    # ════════════════════════════════════════════════════════════
    print("\n[Step 7] 盈亏比前置过滤 (≥ 2.5:1)")
    passed_rr = P.filter_by_risk_reward(passed_l3)
    print(f"  ✓ 通过 {len(passed_rr)} 只")

    # ════════════════════════════════════════════════════════════
    # Step 8: Layer 4 - 分时资金承接
    # ════════════════════════════════════════════════════════════
    print("\n[Step 8] Layer 4 - 分时资金承接")
    if skip_intraday:
        print("  ⊙ 跳过（盘后/回测模式）")
        passed_l4 = passed_rr
    else:
        passed_l4 = P.layer4_intraday_filter(passed_rr, strict=False)
    print(f"  ✓ 通过 {len(passed_l4)} 只")

    # ════════════════════════════════════════════════════════════
    # Step 9: 排序 + 输出
    # ════════════════════════════════════════════════════════════
    print("\n[Step 9] 排序与输出")
    ranked = P.rank_candidates(passed_l4)
    final = ranked[:top_n]

    print(f"\n{'='*60}")
    print(f"🏁 最终输出: {len(final)} 只候选标的")
    print(f"{'='*60}")
    for i, c in enumerate(final, 1):
        print(f"\n【{i}】{c['code']} {c['name']}")
        print(f"     主线: {c.get('mainline_name', '-')}  |  涨幅: {c.get('change_pct', 0):+.2f}%")
        print(f"     价格: {c.get('price', 0):.2f}  |  流通市值: {c.get('float_cap_yi', 0):.1f} 亿")
        print(f"     MA5/MA10/MA20/MA60: {c.get('ma5', 0):.2f}/{c.get('ma10', 0):.2f}/"
              f"{c.get('ma20', 0):.2f}/{c.get('ma60', 0):.2f}")
        print(f"     盈亏比: {c.get('rr_ratio', 0):.2f}:1  目标: {c.get('target_price', 0):.2f}  止损: {c.get('stop_price', 0):.2f}")
        print(f"     排名分: {c.get('rank_score', 0)}")

    # 保存报告
    _save_report(final, today, cycle, mainlines, elapsed=time.time() - started)

    elapsed = time.time() - started
    print(f"\n⏱️  总耗时: {elapsed:.1f} 秒")
    return final

def _save_report(results: list[dict], trade_date: str, cycle: dict,
                  mainlines: list[dict], elapsed: float):
    """保存选股报告（JSON + Markdown）"""
    report = {
        "trade_date": trade_date,
        "timestamp": datetime.now().isoformat(),
        "elapsed_sec": round(elapsed, 1),
        "cycle": cycle,
        "mainlines": [
            {"code": m["sector_code"], "name": m["sector_name"],
             "change_pct": m["change_pct"], "up_count": m["up_count"]}
            for m in mainlines
        ],
        "results": results,
        "result_count": len(results),
    }

    fname = f"screen_{trade_date}_{datetime.now().strftime('%H%M%S')}.json"
    (REPORTS_DIR / fname).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    log.info(f"报告已保存: {fname}")

    # 同时保存 latest 快照
    (REPORTS_DIR / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))

# ════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="退学战法选股系统")
    parser.add_argument("--top", type=int, default=C.MAX_OUTPUT, help="输出前 N 只")
    parser.add_argument("--date", type=str, default=None, help="指定日期（YYYY-MM-DD）")
    parser.add_argument("--skip-intraday", action="store_true", help="跳过分时筛选")
    args = parser.parse_args()

    results = run_stock_screen(
        top_n=args.top,
        trade_date=args.date,
        skip_intraday=args.skip_intraday
    )
    print(f"\n✅ 完成，返回 {len(results)} 只标的")