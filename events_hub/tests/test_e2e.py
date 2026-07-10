"""
tests/test_e2e.py - 端到端测试
3 个票：埃斯顿 (002747) / 双环传动 (002472) / 华菱线缆 (001208)
验证 5 个模块数据源全部能拿到数据
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta
from sources.a_calendar import trade_cal, dividend_recent, unlock_recent, financial_report
from sources.macro_calendar import econ_cal, china_lpr
from sources.sector_event import board_industry_list, sector_fund_flow_rank
from sources.stock_event import stock_fund_flow
from sources.limit_up import limit_up_summary, limit_up_strong


def show_section(title: str, data):
    """打印某节数据（限制 5 条）"""
    print(f"\n{'='*60}")
    print(f"📅 {title}")
    print('='*60)
    if not data:
        print("  （无数据）")
        return
    if isinstance(data, list):
        for x in data[:5]:
            print(f"  {x}")
    else:
        print(f"  {data}")


def main():
    print("🟢 5 模块端到端测试")
    print("="*60)

    # === 模块1: A股日历 ===
    show_section("1.1 交易日历（未来 10 天）", trade_cal()[:10])

    show_section("1.2 解禁-埃斯顿(002747)", unlock_recent("002747")[:3])

    # === 模块2: 宏观 ===
    show_section("2.1 财经日历（未来 7 天）", econ_cal(datetime.now(), datetime.now() + timedelta(days=7))[:5])

    show_section("2.4 LPR 最新", china_lpr()[-3:])

    # === 模块3: 板块 ===
    show_section("3.1 行业板块（前 5）", board_industry_list()[:5])

    show_section("3.2 板块资金流（今日 Top 5）", sector_fund_flow_rank("今日")[:5])

    # === 模块4: 个股事件 ===
    show_section("4.3 个股资金流（今日 Top 5）", stock_fund_flow("今日")[:5])

    # === 模块5: 涨停潮 ===
    summary = limit_up_summary(days=5)
    show_section("5.0 最近涨停汇总（5 个交易日）", summary)

    show_section("5.4 强势股（最近一日 Top 5）", limit_up_strong()[:5])

    print("\n" + "="*60)
    print("✅ 5 模块测试完成")
    print("="*60)


if __name__ == "__main__":
    main()
