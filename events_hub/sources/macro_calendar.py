"""
sources/macro_calendar.py - 模块2: 宏观财经日历
- 财经日历（百度）
- CPI/PMI/LPR
"""
import akshare as ak
from datetime import datetime, timedelta
from typing import List, Dict
from core.cache import cached
from core.utils import to_yyyymmdd


def econ_cal(start_date=None, end_date=None) -> List[Dict]:
    """财经日历（百度接口，单日期循环）

    Note: 百度接口偶尔超时，单日期请求做 try/except + 5s 超时
    """
    if start_date is None:
        start_date = datetime.now()
    if end_date is None:
        end_date = start_date + timedelta(days=7)

    all_events = []
    cur = start_date
    end_date = min(end_date, start_date + timedelta(days=14))  # 最多 14 天
    while cur <= end_date:
        key = f"econ_cal:{to_yyyymmdd(cur)}"
        cached_val = None
        try:
            from core.cache import cache
            cached_val = cache().get(key)
        except Exception:
            pass
        if cached_val is not None:
            all_events.extend(cached_val)
            cur += timedelta(days=1)
            continue
        try:
            import socket
            socket.setdefaulttimeout(8)  # 8s 超时
            df = ak.news_economic_baidu(date=to_yyyymmdd(cur))
            if len(df) > 0:
                df["date"] = to_yyyymmdd(cur)
                records = df.to_dict("records")
                all_events.extend(records)
                try:
                    from core.cache import cache
                    cache().set(key, records, ttl=3600)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            socket.setdefaulttimeout(None)
        cur += timedelta(days=1)
    return all_events


@cached("cpi", ttl=86400 * 7)
def china_cpi() -> List[Dict]:
    """CPI 历史"""
    try:
        df = ak.macro_china_cpi()
        return df.tail(24).to_dict("records")
    except Exception as e:
        return [{"error": str(e)}]


@cached("pmi", ttl=86400 * 7)
def china_pmi() -> List[Dict]:
    """PMI 历史"""
    try:
        df = ak.macro_china_pmi()
        return df.tail(24).to_dict("records")
    except Exception as e:
        return [{"error": str(e)}]


@cached("lpr", ttl=86400 * 7)
def china_lpr() -> List[Dict]:
    """LPR 历史"""
    try:
        df = ak.macro_china_lpr()
        return df.tail(12).to_dict("records")
    except Exception as e:
        return [{"error": str(e)}]


if __name__ == "__main__":
    print("=== 财经日历（未来 7 天）===")
    events = econ_cal(datetime.now(), datetime.now() + timedelta(days=7))
    print(f"  {len(events)} 条")
    for e in events[:5]:
        print(f"  {e.get('date', '?')}: {e}")

    print("\n=== LPR ===")
    lpr = china_lpr()
    for r in lpr[-3:]:
        print(f"  {r}")
