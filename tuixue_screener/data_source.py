#!/usr/bin/env python3
"""
tuixue_screener/data_source.py
三级行情/舆情数据源 + 本地缓存 + 接口熔断逃生。

设计要点（来自用户提示词）：
- 主接口 → 一级备 → 二级备；任何一级失败自动重试 3 次后切换
- 单接口超时 5s
- 本地内存缓存（当日 09:30 - 触发节点）
- 行情全瘫 → 终止本次选股并禁止新开仓
- 仅舆情失效 → 跳过题材核验，继续选股
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

import config as C

log = logging.getLogger("data_source")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ROOT = Path(__file__).parent
CACHE = ROOT / C.CACHE_DIR_NAME
CACHE.mkdir(parents=True, exist_ok=True)

# ════════════════════════════════════════════════════════════
# HTTP 层（带超时 + 退避）
# ════════════════════════════════════════════════════════════
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://quote.eastmoney.com/",
}

def _http_get(url: str, params: dict | None = None,
              timeout: int = C.HTTP_TIMEOUT,
              retries: int = C.HTTP_RETRIES) -> Any | None:
    """单接口请求 + 指数退避 + gzip。失败返回 None。"""
    last_err = ""
    for i in range(retries):
        try:
            r = requests.get(url, params=params or {}, headers=DEFAULT_HEADERS, timeout=timeout)
            r.raise_for_status()
            content = r.content
            if r.headers.get("Content-Encoding") == "gzip" or content[:2] == b"\x1f\x8b":
                import gzip
                try:
                    content = gzip.decompress(content)
                except Exception:
                    pass
            return json.loads(content.decode("utf-8", errors="ignore"))
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:80]}"
            if i < retries - 1:
                wait = min(C.HTTP_BACKOFF_BASE * (2 ** i), 15.0)
                time.sleep(wait)
    log.debug("HTTP GET %s 失败: %s", url[:80], last_err)
    return None

# ════════════════════════════════════════════════════════════
# 缓存层（JSON 文件）
# ════════════════════════════════════════════════════════════
def _cache_path(name: str) -> Path:
    return CACHE / f"{name}.json"

def _load_cache(name: str, ttl_sec: int) -> Any | None:
    p = _cache_path(name)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        ts = d.get("ts")
        if ts and (time.time() - ts) < ttl_sec:
            return d.get("data")
    except Exception:
        return None
    return None

def _save_cache(name: str, data: Any) -> None:
    try:
        _cache_path(name).write_text(json.dumps(
            {"ts": time.time(), "data": data},
            ensure_ascii=False, default=str))
    except Exception as e:
        log.warning("缓存写入失败 %s: %s", name, e)

# ════════════════════════════════════════════════════════════
# 数据源 1：东方财富 push2（主行情源）
# ════════════════════════════════════════════════════════════
def _em_push2_spot() -> list[dict] | None:
    """全市场快照（东方财富 push2delay，多页拉取）"""
    url = "https://push2delay.eastmoney.com/api/qt/clist/get"
    all_diff = []
    for pn in range(1, 50):
        params = {
            "pn": pn, "pz": 100, "po": 1, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2, "invt": 2, "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # 沪深主板
            "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,"
                      "f21,f23,f24,f25,f115,f128",
        }
        data = _http_get(url, params=params, timeout=10)
        if not data or not isinstance(data, dict) or "data" not in data:
            break
        diff = data["data"].get("diff", []) or []
        if not diff:
            break
        all_diff.extend(diff)
        if len(diff) < 100:
            break
    return all_diff if all_diff else None

def _em_push2_kline(code: str, days: int = 120) -> list | None:
    """K 线（日线）"""
    if code.startswith("6"):
        secid = f"1.{code}"
    else:
        secid = f"0.{code}"
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": 101, "fqt": 1, "end": "20500101",
        "lmt": days,
    }
    data = _http_get(url, params=params, timeout=10)
    if not data or not isinstance(data, dict) or "data" not in data:
        return None
    klines = data["data"].get("klines", []) or []
    return klines if klines else None

def _tencent_qfq_kline(code: str, days: int = 500) -> list | None:
    """腾讯前复权日线（最稳定，无频率限制）"""
    mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
    symbol = f"{mkt}{code}"
    n = max(days + 10, 60)
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    try:
        r = requests.get(url, params=[("param", f"{symbol},day,,,{n},qfq")],
                         timeout=10,
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://gu.qq.com/"})
        r.raise_for_status()
        d = r.json()
    except Exception as e:
        log.debug("tencent %s 失败: %s", code, e)
        return None

    if not d or d.get("code") != 0:
        return None
    arr = (d.get("data") or {}).get(symbol, {}).get("qfqday", [])
    if not arr:
        return None

    # 转换为统一 CSV 格式（与 em_push2 一致）
    out = []
    prev_close = None
    for x in arr[-days:]:
        try:
            date_s, op, cl, hi, lo, vol = x[0], x[1], x[2], x[3], x[4], x[5]
            op, cl, hi, lo, vol = float(op), float(cl), float(hi), float(lo), float(vol)
        except (TypeError, ValueError, IndexError):
            continue
        # 换手率无法从腾讯获取（设为 0，后续会被过滤掉）
        turnover = 0.0
        # 涨跌额 / 涨跌幅
        if prev_close is not None and prev_close > 0:
            change_amt = round(cl - prev_close, 2)
            change_pct = round((cl / prev_close - 1) * 100, 2)
        else:
            change_amt = 0.0
            change_pct = 0.0
        # 振幅
        if prev_close is not None and prev_close > 0:
            amplitude = round((hi - lo) / prev_close * 100, 2)
        else:
            amplitude = 0.0

        out.append(f"{date_s},{op},{cl},{hi},{lo},{vol},{op * vol * 100},"
                   f"{amplitude},{change_pct},{change_amt},{turnover}")
        prev_close = cl
    return out if out else None

def _em_push2_intraday(code: str) -> list | None:
    """分时（5 分钟）"""
    if code.startswith("6"):
        secid = f"1.{code}"
    else:
        secid = f"0.{code}"
    url = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
    params = {
        "secid": secid,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "iscr": 0, "ndays": 1,
    }
    data = _http_get(url, params=params, timeout=10)
    if not data or not isinstance(data, dict) or "data" not in data:
        return None
    trends = data["data"].get("trends", []) or []
    return trends if trends else None

# ════════════════════════════════════════════════════════════
# 数据源 2：东方财富 datacenter（一级备）
# ════════════════════════════════════════════════════════════
def _em_dc_zt_pool(date_str: str) -> list[dict] | None:
    """涨停板池（datacenter）"""
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": "RPT_LIMIT_BOARD",
        "columns": "ALL",
        "filter": f"(TRADE_DATE='{date_str}')(LIST_SZ='-1')",
        "pageNumber": 1, "pageSize": 500,
        "sortTypes": "-1", "sortColumns": "LIMIT_TIME",
    }
    data = _http_get(url, params=params)
    if not data or not isinstance(data, dict):
        return None
    if "result" not in data or data["result"] is None:
        return None
    rows = data["result"].get("data", []) or []
    return rows if rows else None

def _em_dc_sector_rank() -> list[dict] | None:
    """板块涨幅榜（datacenter）"""
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": "RPT_SECTOR_PERFORMANCE",
        "columns": "ALL",
        "pageNumber": 1, "pageSize": 100,
        "sortTypes": "-1", "sortColumns": "CHANGE_RATE",
    }
    data = _http_get(url, params=params)
    if not data or not isinstance(data, dict):
        return None
    if "result" not in data or data["result"] is None:
        return None
    rows = data["result"].get("data", []) or []
    return rows if rows else None

def _em_push2_sector_rank() -> list[dict] | None:
    """行业板块涨幅榜（push2 BK，多页拉取）"""
    url = "https://push2delay.eastmoney.com/api/qt/clist/get"
    all_rows = []
    for pn in range(1, 10):
        params = {
            "pn": pn, "pz": 50, "po": 1, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2, "invt": 2, "fid": "f3",
            "fs": "m:90+t:2",  # 行业板块
            "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f20,f23,f104,f128",
        }
        data = _http_get(url, params=params, timeout=10)
        if not data or not isinstance(data, dict) or "data" not in data:
            break
        diff = data["data"].get("diff", []) or []
        if not diff:
            break
        all_rows.extend(diff)
        if len(diff) < 50:
            break
    return all_rows if all_rows else None

def _em_push2_sector_constituents(sector_code: str) -> list[str] | None:
    """获取某板块的成分股（push2 BK）"""
    url = "https://push2delay.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1, "pz": 200, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2, "fid": "f3",
        "fs": f"b:{sector_code}+f:!50",  # 板块成分（不含 ST）
        "fields": "f12,f14",
    }
    data = _http_get(url, params=params, timeout=10)
    if not data or not isinstance(data, dict) or "data" not in data:
        return None
    diff = data["data"].get("diff", []) or []
    return [str(r.get("f12", "")).zfill(6) for r in diff if r.get("f12")]

# ════════════════════════════════════════════════════════════
# 数据源 3：akshare（二级备）
# ════════════════════════════════════════════════════════════
def _ak_spot() -> list[dict] | None:
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return None
        return df.to_dict("records")
    except Exception as e:
        log.debug("akshare spot 失败: %s", e)
        return None

def _ak_zt_pool(date_str: str) -> list[dict] | None:
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=date_str.replace("-", ""))
        if df is None or df.empty:
            return None
        return df.to_dict("records")
    except Exception as e:
        log.debug("akshare zt_pool 失败: %s", e)
        return None

def _ak_kline(code: str, days: int = 120) -> Any | None:
    try:
        import akshare as ak
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start, end_date=end, adjust="qfq")
        if df is None or df.empty:
            return None
        return df.tail(days).to_dict("records")
    except Exception as e:
        log.debug("akshare kline 失败 %s: %s", code, e)
        return None

# ════════════════════════════════════════════════════════════
# 统一对外接口（多源兜底）
# ════════════════════════════════════════════════════════════
def get_spot() -> tuple[list[dict] | None, str]:
    """全市场快照 → (数据, 来源)"""
    cache_name = "spot_today"
    cached = _load_cache(cache_name, C.INTRADAY_CACHE_TTL_SEC)
    if cached is not None:
        return cached, "cache"

    for name, fn in [("em_push2", _em_push2_spot),
                      ("akshare", _ak_spot)]:
        try:
            data = fn()
            if data and len(data) > 100:
                _save_cache(cache_name, data)
                return data, name
        except Exception as e:
            log.warning("spot source %s failed: %s", name, e)
    return None, "none"

def get_kline(code: str, days: int = 120) -> tuple[Any | None, str]:
    """K 线（带缓存）"""
    cache_name = f"kline_{code}_{days}"
    cached = _load_cache(cache_name, C.HISTORY_CACHE_TTL_DAYS * 86400)
    if cached is not None:
        return cached, "cache"
    # 优先级：腾讯（无频限，最快）→ 东财 → akshare
    for name, fn in [("tencent", lambda: _tencent_qfq_kline(code, days)),
                      ("em_push2", lambda: _em_push2_kline(code, days)),
                      ("akshare", lambda: _ak_kline(code, days))]:
        try:
            data = fn()
            if data and len(data) > 20:
                _save_cache(cache_name, data)
                return data, name
        except Exception as e:
            log.warning("kline %s source %s failed: %s", code, name, e)
    return None, "none"

def get_intraday(code: str) -> tuple[Any | None, str]:
    """分时（5 分钟）"""
    cache_name = f"intraday_{code}"
    cached = _load_cache(cache_name, C.INTRADAY_CACHE_TTL_SEC)
    if cached is not None:
        return cached, "cache"
    for name, fn in [("em_push2", lambda: _em_push2_intraday(code))]:
        try:
            data = fn()
            if data and len(data) > 10:
                _save_cache(cache_name, data)
                return data, name
        except Exception as e:
            log.warning("intraday %s failed: %s", code, e)
    return None, "none"

def get_zt_pool(date_str: str) -> tuple[list[dict] | None, str]:
    """涨停板池"""
    cache_name = f"zt_pool_{date_str}"
    cached = _load_cache(cache_name, C.DAILY_CACHE_TTL_DAYS * 86400)
    if cached is not None:
        return cached, "cache"
    for name, fn in [("em_datacenter", lambda: _em_dc_zt_pool(date_str)),
                      ("akshare", lambda: _ak_zt_pool(date_str))]:
        try:
            data = fn()
            if data:
                _save_cache(cache_name, data)
                return data, name
        except Exception as e:
            log.warning("zt_pool %s failed: %s", name, e)
    return None, "none"

def get_sector_rank() -> tuple[list[dict] | None, str]:
    """板块涨幅榜"""
    cache_name = "sector_rank"
    cached = _load_cache(cache_name, C.INTRADAY_CACHE_TTL_SEC)
    if cached is not None:
        return cached, "cache"
    for name, fn in [("em_push2", _em_push2_sector_rank),
                      ("em_datacenter", _em_dc_sector_rank)]:
        try:
            data = fn()
            if data:
                _save_cache(cache_name, data)
                return data, name
        except Exception as e:
            log.warning("sector_rank %s failed: %s", name, e)
    return None, "none"

def get_sector_constituents(sector_code: str) -> list[str]:
    """板块成分股"""
    cache_name = f"sector_constituents_{sector_code}"
    cached = _load_cache(cache_name, C.DAILY_CACHE_TTL_DAYS * 86400)
    if cached is not None:
        return cached
    for name, fn in [("em_push2", lambda: _em_push2_sector_constituents(sector_code))]:
        try:
            data = fn()
            if data:
                _save_cache(cache_name, data)
                return data
        except Exception as e:
            log.warning("sector_constituents %s failed: %s", name, e)
    return []

def get_announcements(code: str, days: int = 7) -> list[dict] | None:
    """东财公告（舆情核验）"""
    cache_name = f"ann_{code}_{days}"
    cached = _load_cache(cache_name, 6 * 3600)
    if cached is not None:
        return cached
    url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    begin = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")
    params = {"cb": "jQuery", "sr": -1, "page_size": 50, "page_index": 1,
              "ann_type": "A", "client_source": "web", "stock_list": code,
              "f_node": 1, "s_node": 0, "begin_time": begin, "end_time": end}
    data = _http_get(url, params=params, timeout=10)
    if data and isinstance(data, dict):
        anns = data.get("data", {}).get("list", [])
        _save_cache(cache_name, anns)
        return anns
    return None

# ════════════════════════════════════════════════════════════
# 健康检查
# ════════════════════════════════════════════════════════════
def health_check() -> dict:
    """检查所有数据源是否可用"""
    return {
        "em_push2_spot": _em_push2_spot() is not None,
        "em_dc_zt_pool": _em_dc_zt_pool(datetime.now().strftime("%Y-%m-%d")) is not None,
        "akshare_spot": _ak_spot() is not None,
        "akshare_zt": _ak_zt_pool(datetime.now().strftime("%Y-%m-%d")) is not None,
    }

# ════════════════════════════════════════════════════════════
# CLI 测试
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=== 健康检查 ===")
    print(json.dumps(health_check(), indent=2, ensure_ascii=False))
    print("\n=== 行情快照（前 3 条）===")
    spot, src = get_spot()
    print(f"来源: {src}, 总数: {len(spot) if spot else 0}")
    if spot:
        for row in spot[:3]:
            print(row)
    print("\n=== K 线 600519 (10 条) ===")
    kl, src = get_kline("600519", 60)
    print(f"来源: {src}, 总数: {len(kl) if kl else 0}")
    if kl:
        for k in kl[:5]:
            print(k)