"""
sources/a_calendar.py - 模块1: A股日历事件
- 交易日历
- 分红配送
- 解禁
- 财报披露
"""
import akshare as ak
import pandas as pd
from typing import List, Dict, Any
from datetime import datetime, timedelta
from core.cache import cached
from core.utils import to_yyyymmdd, to_iso, normalize_code


@cached("trade_cal", ttl=86400)
def trade_cal(start: str = None, end: str = None) -> List[Dict]:
    """交易日历（未来 N 天）"""
    if start is None:
        start = (datetime.now()).strftime("%Y%m%d")
    if end is None:
        end = (datetime.now() + timedelta(days=60)).strftime("%Y%m%d")
    try:
        df = ak.tool_trade_date_hist_sina()
        # 一定要转 str，因为 pandas 比较 datetime.date 和 str 会报错
        df["trade_date"] = df["trade_date"].astype(str).str.replace("-", "")
        # 过滤
        mask = (df["trade_date"] >= str(start)) & (df["trade_date"] <= str(end))
        df = df[mask]
        # 只保留交易日
        if "is_open" in df.columns:
            df = df[df["is_open"] == 1] if df["is_open"].dtype != str else df[df["is_open"] == "1"]
        elif len(df.columns) == 1:
            pass  # 只有 trade_date 一列，说明全部是交易日
        return df.to_dict("records")
    except Exception as e:
        return [{"error": str(e)}]  


@cached("unlock", ttl=86400)
def unlock_recent(symbol: str = None) -> List[Dict]:
    """解禁(指定股票 or 全市场近 30 天)"""
    try:
        if symbol:
            df = ak.stock_restricted_release_queue_em(symbol=normalize_code(symbol))
        else:
            df = ak.stock_restricted_release_queue_em()
        return df.head(50).to_dict("records")
    except Exception as e:
        return [{"error": str(e)}]


@cached("financial_report", ttl=86400)
def financial_report(date) -> List[Dict]:
    """财报披露(指定日期)"""
    try:
        df = ak.stock_yjkb_em(date=to_yyyymmdd(date))
        if len(df) == 0:
            return []
        return df.head(100).to_dict("records")
    except Exception as e:
        return [{"error": str(e)}]


if __name__ == "__main__":
    # 烟雾测试
    print("=== 交易日历 ===")
    print(trade_cal()[:3])
    print("\n=== 日盈电子解禁 ===")
    print(unlock_recent("603286")[:3])
    print("\n=== 7/9 财报披露 ===")
    print(financial_report("20260709")[:3] if financial_report("20260709") else "无")
