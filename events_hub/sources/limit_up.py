"""
sources/limit_up.py - 模块5: 涨停潮 / 龙头异动
- 涨停池
- 炸板股
- 强势股
"""
import akshare as ak
from typing import List, Dict
from datetime import datetime, timedelta
from core.cache import cached
from core.utils import to_yyyymmdd


@cached("limit_up_pool", ttl=1800)
def limit_up_pool(date=None) -> List[Dict]:
    """涨停池"""
    if date is None:
        date = (datetime.now() - timedelta(days=1))
    try:
        df = ak.stock_zt_pool_em(date=to_yyyymmdd(date))
        return df.to_dict("records")
    except Exception as e:
        return [{"error": str(e)}]


@cached("limit_up_zbgc", ttl=1800)
def limit_up_zbgc(date=None) -> List[Dict]:
    """炸板股（封板失败）"""
    if date is None:
        date = (datetime.now() - timedelta(days=1))
    try:
        df = ak.stock_zt_pool_zbgc_em(date=to_yyyymmdd(date))
        return df.to_dict("records")
    except Exception as e:
        return [{"error": str(e)}]


@cached("limit_up_strong", ttl=1800)
def limit_up_strong(date=None) -> List[Dict]:
    """强势股（含多次涨停/60日新高）"""
    if date is None:
        date = (datetime.now() - timedelta(days=1))
    try:
        df = ak.stock_zt_pool_strong_em(date=to_yyyymmdd(date))
        return df.to_dict("records")
    except Exception as e:
        return [{"error": str(e)}]


def limit_up_summary(days: int = 5) -> List[Dict]:
    """最近 N 个交易日的涨停汇总"""
    today = datetime.now()
    summary = []
    cur = today
    for _ in range(days * 2):  # 多跳几天（排除周末）
        cur -= timedelta(days=1)
        try:
            df_records = limit_up_pool(cur)
            if df_records and isinstance(df_records, list) and len(df_records) > 0:
                first = df_records[0]
                if "error" not in first:
                    summary.append({
                        "date": to_yyyymmdd(cur),
                        "count": len(df_records),
                        "sample": df_records[:5],
                    })
                    if len(summary) >= days:
                        break
        except Exception:
            pass
    return summary


if __name__ == "__main__":
    print("=== 最近 5 个交易日涨停汇总 ===")
    summary = limit_up_summary(days=5)
    for s in summary:
        print(f"  {s['date']}: {s['count']} 条涨停")
        for x in s["sample"][:2]:
            print(f"    {x.get('名称')}({x.get('代码')}) 涨{x.get('涨跌幅')}%  连板{x.get('连板数')}")
