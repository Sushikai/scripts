"""
资金流向：今日实时主力/超大/大/中/小单 + 近 N 日历史。

3 种逃生数据源:
  今日 (today):
    1) 东财 push2his (lc.fetch_main_fund_flow)         — 主，最精准
    2) akshare stock_individual_fund_flow(stock)      — 备 1
    3) 从日线成交额推算净额代理(只有 total_net，没有分单) — 兜底

  历史 (history):
    1) akshare stock_individual_fund_flow(stock)     — 主
    2) akshare stock_individual_fund_flow_rank       — 备 1（不同接口，可能互通）
    3) 从日线推算（amount * volume_ratio proxy）      — 兜底
"""
from __future__ import annotations
import logging
from typing import Any

log = logging.getLogger("tuixue_v3.web.fund_flow")


# ═══════════════════════════════════════════════════
# 今日实时主力/超大/大/中/小单
# ═══════════════════════════════════════════════════
def get_main_flow(code: str) -> dict | None:
    """今日实时主力/超大/大/中/小单 净流入（万元）。"""
    # ─── 主源 1: 东财 push2his ───
    try:
        from .. import lib_common as lc
        r = lc.fetch_main_fund_flow(code)
        if r and isinstance(r, dict) and r.get("main_net") is not None:
            r["source"] = "eastmoney_push2"
            return r
    except Exception as e:
        log.warning(f"东财 push2his {code} 失败: {e}")

    # ─── 备 1: akshare (5s 硬超时,东财限频时 RemoteDisconnected 会 hang 数十秒) ───
    try:
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
        market = "sh" if code.startswith(("6", "5", "9")) else "sz"

        def _ak_today():
            import akshare as ak
            return ak.stock_individual_fund_flow(stock=code, market=market)

        ex = ThreadPoolExecutor(max_workers=1)
        try:
            df = ex.submit(_ak_today).result(timeout=5)
        except FutTimeout:
            log.warning(f"akshare 今日资金流 {code} 5s 超时")
            df = None
        finally:
            ex.shutdown(wait=False)
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            out = {
                "main_net": float(latest.get("主力净流入-净额", 0) or 0),
                "super_net": float(latest.get("超大单净流入-净额", 0) or 0),
                "big_net": float(latest.get("大单净流入-净额", 0) or 0),
                "mid_net": float(latest.get("中单净流入-净额", 0) or 0),
                "small_net": float(latest.get("小单净流入-净额", 0) or 0),
                "source": "akshare",
            }
            return out
    except Exception as e:
        log.warning(f"akshare 今日资金流 {code} 失败: {e}")

    # ─── 备 2: efinance (2026-07-16 新增,东财轻封装)
    #    底层走 push2his,沙箱 DNS 劫持可能 hang,5s 硬超时;列名可能不同,
    #    做容错:取包含"主力"+"净额" / "超大单"+"净额" 等多种别名
    try:
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
        def _ef_today():
            import efinance as ef
            return ef.stock.get_today_bill(code)
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            df = ex.submit(_ef_today).result(timeout=5)
        except FutTimeout:
            log.warning(f"efinance 今日资金流 {code} 5s 超时")
            df = None
        finally:
            ex.shutdown(wait=False)
        if df is not None and not df.empty:
            # 兼容 efinance 列名变体
            def _pick(*keys):
                for k in keys:
                    for col in df.columns:
                        if k in str(col):
                            return df[col]
                import pandas as _pd
                return _pd.Series([0] * len(df))
            latest_idx = df.iloc[-1] if len(df) == 1 else df.iloc[0]
            out = {
                "main_net":  float(_pick("主力净额", "主力净流入").iloc[-1] if len(df) > 1 else latest_idx.get(_pick("主力净额", "主力净流入").name, 0) or 0),
                "super_net": float(_pick("超大单净额").iloc[-1] if len(df) > 1 else 0),
                "big_net":   float(_pick("大单净额").iloc[-1] if len(df) > 1 else 0),
                "mid_net":   float(_pick("中单净额").iloc[-1] if len(df) > 1 else 0),
                "small_net": float(_pick("小单净额").iloc[-1] if len(df) > 1 else 0),
                "source": "efinance",
            }
            # 修正: efinance get_today_bill 返回的是 dict-of-series, 取列名按 row
            # 简化: 直接对每个 cell 取 (df 是 1 行 × N 列结构)
            row = df.iloc[0] if len(df) >= 1 else None
            if row is not None:
                def _gv(*keys, default=0.0):
                    for k in keys:
                        for col in df.columns:
                            cn = str(col)
                            if k in cn:
                                try:
                                    return float(row[col] or 0)
                                except Exception:
                                    return default
                    return default
                out.update({
                    "main_net":  _gv("主力净额", "主力净流入-净额", "主力净流入"),
                    "super_net": _gv("超大单净额", "超大单净流入-净额", "超大单净流入"),
                    "big_net":   _gv("大单净额", "大单净流入-净额", "大单净流入"),
                    "mid_net":   _gv("中单净额", "中单净流入-净额", "中单净流入"),
                    "small_net": _gv("小单净额", "小单净流入-净额", "小单净流入"),
                })
                return out
    except Exception as e:
        log.warning(f"efinance 今日资金流 {code} 失败: {e}")

    # ─── 兜底 2: 从实时行情成交额 + 估算 ───
    try:
        from .. import lib_common as lc
        rt = lc.fetch_realtime(code)
        if rt and rt.get("成交额"):
            amount_wan = float(rt["成交额"]) / 1e4
            # 没有真实分单数据：返 None 而不是 0，避免前端误以为"主力流出 0"
            return {
                "main_net":   None,
                "super_net":  None,
                "big_net":    None,
                "mid_net":    None,
                "small_net":  None,
                "total_amount_wan": round(amount_wan, 2),
                "source": "realtime_proxy_no_split",
                "note": "分单数据不可达，仅给总成交额",
            }
    except Exception as e:
        log.warning(f"实时行情兜底 {code} 失败: {e}")

    return None


# ═══════════════════════════════════════════════════
# 近 N 日历史资金流
# ═══════════════════════════════════════════════════
def get_history_flow(code: str, days: int = 60) -> list[dict]:
    """
    近 N 日资金流（含收盘价）。
    3 种逃生：akshare individual → akshare rank → 日线推算。
    """
    market = "sh" if code.startswith(("6", "5", "9")) else "sz"

    # ─── 主源 1: akshare 个股资金流 ───
    rows = _try_ak_individual(code, market, days)
    if rows:
        return rows

    # ─── 备 1: akshare 个股资金流排名 ───
    rows = _try_ak_rank(code, days)
    if rows:
        return rows

    # ─── 兜底 2: 从日线 cache 推算 ───
    rows = _try_daily_proxy(code, days)
    if rows:
        return rows

    return []


def _try_ak_individual(code: str, market: str, days: int) -> list[dict]:
    """
    2026-07-11 加固：akshare stock_individual_fund_flow 在东财限频窗口经常
    RemoteDisconnected / 卡死（默认 akshare session timeout 是 None,
    可以 hang 数分钟）。外面包 5s ThreadPool 硬超时，超时返 [] 让上层走
    daily_proxy（本地 SQLite, 0.01s 出）。
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        try:
            future = ex.submit(_ak_individual_inner, code, market, days)
            return future.result(timeout=5)
        except FutTimeout:
            log.warning(f"akshare_individual {code} 5s 超时,降级 daily_proxy")
            return []
    except Exception as e:
        log.warning(f"akshare_individual {code} 失败: {e}")
        return []
    finally:
        ex.shutdown(wait=False)


def _ak_individual_inner(code: str, market: str, days: int) -> list[dict]:
    try:
        import akshare as ak
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is None or df.empty:
            return []
        df = df.tail(days).reset_index(drop=True)

        def _g(*keys):
            for k in keys:
                if k in df.columns:
                    return df[k]
            import pandas as pd
            return pd.Series([0] * len(df))

        date_s = _g("日期", "date")
        main_s = _g("主力净流入-净额", "主力净额", "main_net")
        super_s = _g("超大单净流入-净额", "超大单净额", "super_net")
        big_s = _g("大单净流入-净额", "大单净额", "big_net")
        mid_s = _g("中单净流入-净额", "中单净额", "mid_net")
        small_s = _g("小单净流入-净额", "小单净额", "small_net")
        close_s = _g("收盘价", "close")

        rows = []
        for i in range(len(df)):
            try:
                rows.append({
                    "date": str(date_s.iloc[i])[:10],
                    "main_net": float(main_s.iloc[i] or 0),
                    "super_net": float(super_s.iloc[i] or 0),
                    "big_net": float(big_s.iloc[i] or 0),
                    "mid_net": float(mid_s.iloc[i] or 0),
                    "small_net": float(small_s.iloc[i] or 0),
                    "close": float(close_s.iloc[i] or 0),
                    "source": "akshare_individual",
                })
            except Exception:
                continue
        return rows
    except Exception as e:
        log.warning(f"akshare_individual_inner {code} 失败: {e}")
        return []


def _try_ak_rank(code: str, days: int) -> list[dict]:
    try:
        import akshare as ak
        # rank 接口返回的是排行榜，无法针对个股。直接返回空，让上层走兜底
        return []
    except Exception as e:
        log.warning(f"akshare_rank {code} 失败: {e}")
        return []


def _try_daily_proxy(code: str, days: int) -> list[dict]:
    """从 cache_db daily 表的成交额 / 涨跌幅推算代理净额。"""
    try:
        from .. import cache_db
        df = cache_db.daily().get(code, days)
        if df is None or df.empty:
            return []
        # 兼容中英 column
        def col(*names):
            for n in names:
                if n in df.columns:
                    return df[n]
            return None

        date_s = col("date", "日期")
        open_s = col("open", "开盘")
        close_s = col("close", "收盘")
        amt_s = col("amount", "成交额")

        if date_s is None or close_s is None or open_s is None:
            return []

        rows = []
        for i in range(len(df)):
            try:
                o = float(open_s.iloc[i] or 0)
                c = float(close_s.iloc[i] or 0)
                a = float(amt_s.iloc[i] or 0) if amt_s is not None else 0.0
                if o <= 0:
                    continue
                chg = (c - o) / o
                # 代理：净额 = 成交额 × 涨幅 × 0.1（经验系数），转万元
                main_proxy = a * chg * 0.1 / 1e4
                rows.append({
                    "date": str(date_s.iloc[i])[:10],
                    "main_net": round(main_proxy, 2),
                    "super_net": 0.0,
                    "big_net": 0.0,
                    "mid_net": 0.0,
                    "small_net": 0.0,
                    "close": c,
                    "source": "daily_proxy_estimate",
                    "note": "代理值，仅作图表参考",
                })
            except Exception:
                continue
        return rows
    except Exception as e:
        log.warning(f"daily_proxy {code} 失败: {e}")
        return []


# ═══════════════════════════════════════════════════
# 今日 + 历史 一并返回
# ═══════════════════════════════════════════════════
def get_combined(code: str, days: int = 60) -> dict:
    """今日 + 历史 一并返回。"""
    return {
        "code": code,
        "today": get_main_flow(code),
        "history": get_history_flow(code, days=days),
    }