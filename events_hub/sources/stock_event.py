"""
sources/stock_event.py - 模块4: 个股事件
- 个股公告
- 大宗交易
- 个股资金流向
- 解禁（已放在 a_calendar，这里做单股包）
"""
import akshare as ak
from typing import List, Dict
from datetime import datetime, timedelta
from core.cache import cached
from core.utils import to_yyyymmdd, normalize_code


@cached("stock_fund_flow", ttl=1800)
def stock_fund_flow(indicator: str = "今日") -> List[Dict]:
    """个股资金流向排行"""
    try:
        df = ak.stock_individual_fund_flow_rank(indicator=indicator)
        return df.head(30).to_dict("records")
    except Exception as e:
        return [{"error": str(e)}]


@cached("block_trade", ttl=1800)
def block_trade(symbol: str = None) -> List[Dict]:
    """大宗交易（按股票 or 全市场）"""
    try:
        if symbol:
            # 全市场拉，再过滤
            df = ak.stock_market_billboard()  # 间接接口
            df = df[df["代码"] == normalize_code(symbol)]
        else:
            df = ak.stock_market_billboard()
        return df.head(20).to_dict("records")
    except Exception as e:
        return [{"error": str(e)}]


def stock_events(symbol: str) -> Dict[str, List]:
    """单只股票的"近期大事"聚合"""
    code = normalize_code(symbol)
    return {
        "symbol": code,
        "unlock": [],           # 引自 a_calendar 模块（避免循环）
        "fund_flow": [],
    }


if __name__ == "__main__":
    print("=== 个股资金流（今日）===")
    ff = stock_fund_flow()
    print(f"  {len(ff)} 条")
    for x in ff[:3]:
        print(f"  {x.get('名称', '?')}: {x.get('涨跌幅', '?')}")
