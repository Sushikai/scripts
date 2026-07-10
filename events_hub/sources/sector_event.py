"""
sources/sector_event.py - 模块3: 板块/题材催化
- 板块列表（行业+概念）
- 板块资金流入排行
- 板块新闻（板块异动的潜在原因）
"""
import akshare as ak
import socket
import time
from typing import List, Dict
from core.cache import cached


def _safe_ak(fn, *args, retries: int = 2, **kwargs):
    """带超时/重试的 akshare 调用"""
    for attempt in range(retries):
        try:
            socket.setdefaulttimeout(15)
            result = fn(*args, **kwargs)
            return result
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return None
        finally:
            socket.setdefaulttimeout(None)
    return None


@cached("board_industry", ttl=86400)
def board_industry_list() -> List[Dict]:
    """行业板块列表"""
    df = _safe_ak(ak.stock_board_industry_name_em)
    if df is None or len(df) == 0:
        return []
    return df.to_dict("records")  


@cached("board_concept", ttl=86400)
def board_concept_list() -> List[Dict]:
    """概念板块列表"""
    try:
        df = ak.stock_board_concept_name_em()
        return df.to_dict("records")
    except Exception as e:
        return [{"error": str(e)}]


def sector_fund_flow_rank(indicator: str = "今日") -> List[Dict]:
    """板块资金流向排行"""
    try:
        import socket
        socket.setdefaulttimeout(8)
        df = ak.stock_sector_fund_flow_rank(indicator=indicator, sector_type="行业资金流")
        return df.head(20).to_dict("records")
    except Exception as e:
        return [{"error": str(e)}]
    finally:
        socket.setdefaulttimeout(None)


if __name__ == "__main__":
    print("=== 行业板块 ===")
    industries = board_industry_list()
    print(f"  {len(industries)} 个行业板块")
    if len(industries) > 0 and "名称" in industries[0]:
        for x in industries[:3]:
            print(f"  {x.get('名称')}: {x.get('最新价', '?')}")
