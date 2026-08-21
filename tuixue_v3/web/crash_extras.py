"""
砸盘风险 4 块 — 真实数据接入:
  - 融资融券: 东财 / akshare stock_margin_detail_szse (深圳) + sse (上海)
  - 股权质押: 东财 stock_gpzy_pledge_ratio_em (2276 只,秒返)

TTL: 12h (融资日数据; 质押每周一次)
"""
from __future__ import annotations
import logging
import time as systime
from typing import Any

import requests

log = logging.getLogger("tuixue_v3.web.crash_extras")

_TTL = 12 * 3600
_cache: dict[str, tuple[float, dict]] = {}
_TIMEOUT = 8


def _is_sz(code: str) -> bool:
    return code.startswith(("0", "2", "3"))


def _is_sh(code: str) -> bool:
    return code.startswith(("6", "9", "5"))


def _find_recent_trade_date(code: str) -> str | None:
    """扫描近 30 个候选交易日, 找到第一个有数据的日子。"""
    from datetime import datetime, timedelta
    # szse dates only work for szse codes (0/3)
    if _is_sh(code):
        # SSE 截至 2023/9/22 后停更, 改走东方财富 push2his 兜底 — 此处返回 None 让上层返回
        return None
    for back in range(30):
        d = (datetime.now() - timedelta(days=back)).strftime("%Y%m%d")
        try:
            import akshare as ak
            df = ak.stock_margin_detail_szse(date=d)
            if df is not None and len(df) > 100:
                code6 = code.zfill(6)
                hit = df[df["证券代码"].astype(str).str.zfill(6) == code6]
                if len(hit):
                    return d
        except Exception:
            continue
    return None


def fetch_margin_balance(code: str) -> dict | None:
    """融资融券 — 个股最新一日余额 + 前日变化。"""
    code = code.strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        return None
    cached = _cache.get(("margin", code))
    if cached and (systime.time() - cached[0]) < _TTL:
        return cached[1]
    out = {"code": code, "available": False}
    # 1) 深圳 → akshare stock_margin_detail_szse (东财抓的 sse 深交所每日)
    if _is_sz(code):
        try:
            import akshare as ak
            trade_date = _find_recent_trade_date(code)
            if trade_date:
                df = ak.stock_margin_detail_szse(date=trade_date)
                code6 = code.zfill(6)
                row = df[df["证券代码"].astype(str).str.zfill(6) == code6]
                if len(row):
                    r = row.iloc[0]
                    bal_yi = round(float(r.get("融资余额") or 0) / 1e8, 2)  # 元 → 亿
                    bal_wan = round(float(r.get("融资余额") or 0) / 1e4, 2)
                    out.update({
                        "available": True,
                        "trade_date": str(r.get("交易日期") or trade_date),
                        "margin_balance_yi": bal_yi,
                        "margin_balance_wan": bal_wan,
                        "short_balance_yi": round(float(r.get("融券余额") or 0) / 1e8, 2),
                        "total_balance_yi": round(float(r.get("融资融券余额") or 0) / 1e8, 2),
                        "today_buy_yi": round(float(r.get("融资买入额") or 0) / 1e8, 2),
                        "today_short_sell_wan": round(float(r.get("融券卖出量") or 0) / 1e4, 2),
                        "source": "akshare_szse",
                    })
        except Exception as e:
            log.warning(f"crash_extras.margin {code} 失败: {e}")
    # 2) 上海 fallback → 东财 push2his (历史 fflow 同一台服务器, R31 fix 已稳)
    if not out.get("available") and _is_sh(code):
        try:
            secid = "1." + code
            url = "https://push2his.eastmoney.com/api/qt/stock/get"
            params = {
                "secid": secid,
                "fields": "f116,f117,f161,f117,f152",
                # f116 融资余额, f117 融资买入, f152 融券余额, f161 融资融券余额
            }
            r = requests.get(url, params=params, timeout=5,
                             headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
            d = r.json().get("data") or {}
            if d.get("f116"):
                out.update({
                    "available": True,
                    "trade_date": str(d.get("f152") or "")[:10] or "—",
                    "margin_balance_yi": round(float(d["f116"]) / 1e8, 2),
                    "margin_balance_wan": round(float(d["f116"]) / 1e4, 2),
                    "total_balance_yi": round(float(d.get("f161") or 0) / 1e8, 2),
                    "today_buy_yi": round(float(d.get("f117") or 0) / 1e8, 2),
                    "source": "eastmoney_push2his",
                })
        except Exception as e:
            log.warning(f"crash_extras.margin sh {code} push2his 失败: {e}")
    if out.get("available"):
        _cache[("margin", code)] = (systime.time(), out)
    return out if out.get("available") else None


def fetch_pledge_ratio(code: str) -> dict | None:
    """股权质押 — 最新一日累计质押比例 + 笔数 + 市值。"""
    code = code.strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        return None
    cached = _cache.get(("pledge", code))
    if cached and (systime.time() - cached[0]) < _TTL:
        return cached[1]
    try:
        import akshare as ak
        from datetime import datetime, timedelta
        for back in range(14):
            d = (datetime.now() - timedelta(days=back)).strftime("%Y%m%d")
            try:
                df = ak.stock_gpzy_pledge_ratio_em(date=d)
            except Exception:
                continue
            if df is None or len(df) == 0:
                continue
            sub = df[df["股票代码"].astype(str).str.zfill(6) == code]
            if len(sub) == 0:
                continue
            r = sub.iloc[0]
            ratio = float(r.get("质押比例") or 0)
            pledge_wan = float(r.get("质押股数") or 0)
            pledge_yi = round(pledge_wan * float(r.get("最新价") or 0) / 1e8, 2)
            out = {
                "available": True,
                "trade_date": str(r.get("交易日期") or d),
                "ratio_pct": round(ratio, 2),
                "pledge_count": int(r.get("质押笔数") or 0),
                "pledge_value_yi": pledge_yi,
                "industry": r.get("所属行业") or "",
                "1y_pct": float(r.get("近一年涨跌幅") or 0),
                "source": "akshare_gpzy",
            }
            _cache[("pledge", code)] = (systime.time(), out)
            return out
    except Exception as e:
        log.warning(f"crash_extras.pledge {code} 失败: {e}")
    return None


if __name__ == "__main__":
    import sys, json
    c = sys.argv[1] if len(sys.argv) > 1 else "300308"
    print("margin:", json.dumps(fetch_margin_balance(c), ensure_ascii=False))
    print("pledge:", json.dumps(fetch_pledge_ratio(c), ensure_ascii=False))