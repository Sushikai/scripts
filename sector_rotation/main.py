#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py - 板块轮动复盘 CLI 入口

用法：
  python3 main.py                              # 默认 T 日（最近交易日）
  python3 main.py --date 2026-07-11            # 指定单日
  python3 main.py --date 2026-07-11 --compare  # T vs T-1 双日对比
  python3 main.py --date 2026-07-11 --compare --date2 2026-07-10
  python3 main.py --date 2026-07-11 --report-md  # 输出 Markdown 报告
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from data_fetcher import is_trading_day
from scorer import score_all_sectors, load_config
from visualizer import plot_single_day, plot_compare, generate_text_report


def last_trading_day() -> str:
    """最近一个交易日（往前推 5 天找）"""
    d = datetime.now()
    for i in range(10):
        d = d - timedelta(days=i)
        s = d.strftime("%Y-%m-%d")
        if is_trading_day(s):
            return s
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def prev_trading_day(date_str: str) -> str:
    """指定日期前一个交易日"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    for i in range(1, 10):
        prev = (d - timedelta(days=i)).strftime("%Y-%m-%d")
        if is_trading_day(prev):
            return prev
    return (d - timedelta(days=1)).strftime("%Y-%m-%d")


def load_or_score(date_str: str, config: dict, use_cache: bool = True) -> dict:
    """加载已有评分或重新计算"""
    cache_file = Path(__file__).parent / "reports" / f"score_{date_str}.json"
    if use_cache and cache_file.exists():
        # 当日数据 1 小时过期
        age = datetime.now().timestamp() - cache_file.stat().st_mtime
        if age < 3600:
            print(f"⚡ 加载缓存: {cache_file}")
            return json.loads(cache_file.read_text())
    # 重新计算
    if not is_trading_day(date_str):
        print(f"❌ {date_str} 不是交易日（节假日或周末），终止")
        sys.exit(1)
    return score_all_sectors(date_str, config=config)


def main():
    parser = argparse.ArgumentParser(description="板块轮动量化复盘系统")
    parser.add_argument("--date", help="指定日期 YYYY-MM-DD（默认最近交易日）")
    parser.add_argument("--date2", help="对比日期（默认 T-1 交易日）")
    parser.add_argument("--compare", action="store_true", help="双日对比模式")
    parser.add_argument("--no-cache", action="store_true", help="强制重算")
    parser.add_argument("--report-md", help="输出 Markdown 报告到指定路径")
    parser.add_argument("--no-plot", action="store_true", help="不生成图表")
    parser.add_argument("--period", type=int, default=3, help="多日滚动评分（默认 3 日）")
    args = parser.parse_args()

    config = load_config()
    date_str = args.date or last_trading_day()

    print("=" * 60)
    print(f"板块轮动复盘系统 · {date_str}")
    print("=" * 60)

    # 单日
    report = load_or_score(date_str, config, use_cache=not args.no_cache)
    if not report:
        print("评分失败")
        sys.exit(1)

    # 缓存到 reports
    out_file = Path(__file__).parent / "reports" / f"score_{date_str}.json"
    out_file.parent.mkdir(exist_ok=True)
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    # 双日
    prev_report = None
    if args.compare:
        date2 = args.date2 or prev_trading_day(date_str)
        print(f"\n[双日对比] {date2} → {date_str}")
        prev_report = load_or_score(date2, config, use_cache=not args.no_cache)

    # 输出图表
    if not args.no_plot:
        chart_dir = Path(__file__).parent / "charts"
        chart_dir.mkdir(exist_ok=True)
        plot_single_day(report, chart_dir / f"single_{date_str}.png")
        if prev_report:
            plot_compare([prev_report, report], chart_dir / f"compare_{date2}_vs_{date_str}.png")

    # 文字复盘
    text_report = generate_text_report(report, prev_report)
    print("\n" + text_report)

    # 输出 Markdown 报告
    if args.report_md:
        md_path = Path(args.report_md)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(text_report, encoding="utf-8")
        print(f"\n📝 Markdown 报告: {md_path}")

    print("\n" + "=" * 60)
    print("✅ 完成")
    print("=" * 60)


if __name__ == "__main__":
    main()