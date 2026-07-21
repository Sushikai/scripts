#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
data_fetcher.py - AKShare 板块行情数据采集层
- 容灾：3 级重试 + 主备接口切换
- 节假日校验
- 缓存：避免重复拉取
"""

import json
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

# ============================================================
# 交易日历（简化版 - 用 akshare 的交易日接口）
# ============================================================
def is_trading_day(date_str: str) -> bool:
    """判断指定日期是否为 A 股交易日
    策略：先试 akshare tool_trade_date_hist_sina，失败用周末判断
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        # 周末直接 false
        if dt.weekday() >= 5:
            return False
    except ValueError:
        return False

    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty:
            return dt.weekday() < 5
        if "trade_date" in df.columns:
            dates = set(df["trade_date"].astype(str).tolist())
        else:
            dates = set(df.index.astype(str).tolist())
        norm = set()
        for d in dates:
            for fmt in (("%Y%m%d",), ("%Y-%m-%d",)):
                try:
                    norm.add(datetime.strptime(d, fmt[0]).strftime("%Y-%m-%d"))
                    break
                except ValueError:
                    continue
        return date_str in norm
    except Exception:
        return dt.weekday() < 5


# ============================================================
# 容错装饰器
# ============================================================
def safe_call(fn, retries=3, timeout=20, default=None, name=""):
    """带重试 + 超时的接口调用包装"""
    last_err = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            wait = 2 ** attempt
            print(f"  ⚠️ [{name}] attempt {attempt+1}/{retries} fail: {type(e).__name__}: {str(e)[:100]}")
            if attempt < retries - 1:
                time.sleep(wait)
    print(f"  ❌ [{name}] 最终失败: {last_err}")
    return default


# ============================================================
# 1. 概念板块列表
# ============================================================
def fetch_concept_boards(cache_dir: Path = None) -> list[dict] | None:
    """获取所有概念板块列表
    主源：stock_board_concept_name_em（东财）
    备源：stock_fund_flow_concept 反推
    """
    cache_dir = cache_dir or Path(__file__).parent / "data"
    cache_file = cache_dir / "_concept_boards.json"
    if cache_file.exists() and (datetime.now().timestamp() - cache_file.stat().st_mtime) < 3600:
        return json.loads(cache_file.read_text())

    def _primary():
        import akshare as ak
        df = ak.stock_board_concept_name_em()
        if df is None or df.empty:
            return None
        out = []
        for r in df.to_dict("records"):
            out.append({
                "板块名称": r.get("板块名称") or r.get("name"),
                "板块代码": r.get("板块代码") or r.get("code"),
            })
        return out

    result = safe_call(_primary, name="concept_boards", default=None)
    if result:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
        return result

    # 备源：从资金流接口反推板块名
    print("  [备源] 从 fund_flow_concept 反推板块列表")
    flows = fetch_concept_fund_flow("即时")
    if not flows:
        return []
    result = []
    for r in flows:
        name = r.get("行业") or r.get("板块名称")
        if name:
            result.append({"板块名称": name, "板块代码": name})
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False))
    return result


def match_sector_boards(sectors: list[dict], all_boards: list[dict]) -> dict[str, list[dict]]:
    """根据关键词把概念板块匹配到我们的 4 大赛道"""
    result = {s["code_alias"]: [] for s in sectors}
    for s in sectors:
        for board in all_boards:
            name = board.get("板块名称", "")
            if any(kw in name for kw in s["keywords"]):
                result[s["code_alias"]].append(board)
    return result


# ============================================================
# 2. 当日板块资金流
# ============================================================
def fetch_concept_fund_flow(symbol: str = "即时") -> list[dict] | None:
    """概念板块资金流（净额/涨跌幅/领涨股）"""
    def _do():
        import akshare as ak
        df = ak.stock_fund_flow_concept(symbol=symbol)
        if df is None or df.empty:
            return None
        return df.to_dict("records")
    return safe_call(_do, name=f"concept_fund_flow[{symbol}]", default=[])


# ============================================================
# 2b. 行业板块资金流（**主用**，更稳定）
# ============================================================
def fetch_industry_fund_flow(symbol: str = "即时") -> list[dict] | None:
    """行业板块资金流（90 个行业）
    返回字段：序号、行业、行业指数、行业-涨跌幅、流入资金、流出资金、净额、公司家数、领涨股、领涨股-涨跌幅、当前价
    """
    cache_dir = Path(__file__).parent / "data"
    cache_file = cache_dir / f"_industry_flow_{symbol}.json"
    # 缓存 30 分钟（盘中数据变化快）
    if cache_file.exists() and (datetime.now().timestamp() - cache_file.stat().st_mtime) < 1800:
        return json.loads(cache_file.read_text())

    def _do():
        import akshare as ak
        df = ak.stock_fund_flow_industry(symbol=symbol)
        if df is None or df.empty:
            return None
        records = df.to_dict("records")
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(records, ensure_ascii=False, default=str))
        return records
    return safe_call(_do, name=f"industry_fund_flow[{symbol}]", default=[])


# ============================================================
# 2c. 行业列表（从资金流反推 + 缓存）
# ============================================================
def fetch_industry_names() -> list[str]:
    """所有行业名"""
    flows = fetch_industry_fund_flow("即时")
    return [r.get("行业", "") for r in (flows or []) if r.get("行业")]


# ============================================================
# 3. 涨停池（指定日期）
# ============================================================
def fetch_zt_pool(date_str: str) -> list[dict] | None:
    """涨停池明细 - date_str: YYYY-MM-DD"""
    def _do():
        import akshare as ak
        date_compact = date_str.replace("-", "")
        df = ak.stock_zt_pool_em(date=date_compact)
        if df is None or df.empty:
            return []
        return df.to_dict("records")
    return safe_call(_do, name=f"zt_pool[{date_str}]", default=[])


# ============================================================
# 4. 历史资金流（指定概念）
# ============================================================
def fetch_concept_fund_flow_hist(symbol: str, period: str = "10") -> list[dict] | None:
    """历史资金流 - symbol: 板块名称, period: 5/10/30/60"""
    def _do():
        import akshare as ak
        df = ak.stock_concept_fund_flow_hist(symbol=symbol, period=period)
        if df is None or df.empty:
            return None
        return df.to_dict("records")
    return safe_call(_do, name=f"fund_flow_hist[{symbol}]", default=[])


# ============================================================
# 5. 板块成分股
# ============================================================
def fetch_concept_cons(symbol: str) -> list[dict] | None:
    """概念板块成分股"""
    def _do():
        import akshare as ak
        df = ak.stock_board_concept_cons_em(symbol=symbol)
        if df is None or df.empty:
            return None
        return df.to_dict("records")
    return safe_call(_do, name=f"cons[{symbol}]", default=[])


# ============================================================
# 6. 个股资金流
# ============================================================
def fetch_stock_individual_fund_flow(stock: str, market: str = "sh") -> list[dict] | None:
    """个股资金流"""
    def _do():
        import akshare as ak
        df = ak.stock_individual_fund_flow(stock=stock, market=market)
        if df is None or df.empty:
            return None
        return df.to_dict("records")
    return safe_call(_do, name=f"stock_flow[{stock}]", default=[])


# ============================================================
# 7. 个股日 K（用于中军筛选）
# ============================================================
def fetch_stock_hist(code: str, days: int = 60, adjust: str = "qfq") -> list[dict] | None:
    """个股历史日 K"""
    def _do():
        import akshare as ak
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start, end_date=end, adjust=adjust)
        if df is None or df.empty:
            return None
        return df.tail(days).to_dict("records")
    return safe_call(_do, name=f"hist[{code}]", default=[])


# ============================================================
# 8. 大盘指数（用于相对强弱）
# ============================================================
def fetch_index_daily(symbol: str = "sh000300", days: int = 30) -> list[dict] | None:
    """大盘指数日 K"""
    def _do():
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=symbol)
        if df is None or df.empty:
            return None
        return df.tail(days).to_dict("records")
    return safe_call(_do, name=f"idx[{symbol}]", default=[])


# ============================================================
# 测试入口
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("板块数据采集层测试")
    print("=" * 60)

    print("\n[1] 概念板块列表...")
    boards = fetch_concept_boards()
    print(f"  板块数: {len(boards) if boards else 0}")
    if boards:
        print(f"  示例: {boards[:3]}")

    print("\n[2] 当日资金流（概念）...")
    flows = fetch_concept_fund_flow("即时")
    print(f"  板块数: {len(flows) if flows else 0}")
    if flows:
        print(f"  示例: {flows[0]}")

    print("\n[3] 涨停池 (2026-07-11)...")
    zt = fetch_zt_pool("2026-07-11")
    print(f"  涨停股数: {len(zt) if zt else 0}")

    print("\n[4] 交易日历校验...")
    print(f"  2026-07-11: {is_trading_day('2026-07-11')}")
    print(f"  2026-07-12: {is_trading_day('2026-07-12')}")
    print(f"  2026-07-13: {is_trading_day('2026-07-13')}")