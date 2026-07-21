#!/usr/bin/env python3
"""
multi_source_fetchers.py
共用数据接口糅合层（多源逃生 + 本地缓存）。

为 stock 目录下各脚本（emotion / fund_flow_chart / dragon_scanner /
dragon_backtest / position_monitor 等）提供统一的兜底拉取函数，
避免单点依赖 akshare / 东方财富 / 同花顺 任一接口断连时整体挂死。

约定：
  - 所有 fetch_* 函数优先本地缓存（7 天），再尝试主源，再 1-2 个备源，最后 None / []
  - 主源优先选稳定官方 / 接口稳的；备源选 akshare
  - 不抛异常给上层；调用方只需 if df is None / if not rows
  - 内置 _http_get 自带指数退避 + gzip 解压
"""
from __future__ import annotations

import gzip
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

try:
    import requests
except ImportError:
    requests = None  # 调用方需自检

try:
    import pandas as pd
except ImportError:
    pd = None

LOG = logging.getLogger("multi_source")
if not LOG.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ═══════════════════════════════════════════════════════
# 通用：HTTP GET + 缓存路径
# ═══════════════════════════════════════════════════════
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://quote.eastmoney.com/",
}

CACHE_DIR = Path.home() / ".hermes" / "cache" / "multi_source"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL = timedelta(days=7)


def _http_get(url: str, params: dict | None = None, retries: int = 2,
              timeout: int = 6, sleep_base: float = 1.0,
              max_sleep: float = 6.0) -> Any:
    """带指数退避 + gzip 解压的 HTTP GET。失败返 None。
    2026-07-11: 默认 retries 3→2,timeout 10→6,sleep 收紧,
    避免单源挂 30s+ 拖死端点。已显式传参的调用方不受影响。"""
    if requests is None:
        return None
    last_err = ""
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=timeout)
            r.raise_for_status()
            content = r.content
            if r.headers.get("Content-Encoding") == "gzip" or content[:2] == b"\x1f\x8b":
                try:
                    content = gzip.decompress(content)
                except Exception:
                    pass
            return json.loads(content.decode("utf-8", errors="ignore"))
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:80]}"
            if i < retries - 1:
                wait = min(sleep_base * (2 ** i), max_sleep)
                time.sleep(wait)
    LOG.debug("HTTP GET %s 失败: %s", url, last_err)
    return None


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.json"


def _load_json_cache(name: str, ttl: timedelta = CACHE_TTL) -> Any:
    p = _cache_path(name)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        ts = d.get("ts")
        if ts and datetime.now() - datetime.fromisoformat(ts) < ttl:
            return d.get("data")
    except Exception:
        return None
    return None


def _save_json_cache(name: str, data: Any) -> None:
    try:
        _cache_path(name).write_text(json.dumps(
            {"ts": datetime.now().isoformat(), "data": data},
            ensure_ascii=False, default=str))
    except Exception as e:
        LOG.debug("cache save %s 失败: %s", name, e)


# ═══════════════════════════════════════════════════════
# 1. 交易日历：YYYY-MM-DD 集合
# ═══════════════════════════════════════════════════════
def fetch_trade_dates(force_refresh: bool = False) -> set[str] | None:
    """
    A股交易日历。
    主源：akshare.tool_trade_date_hist_sina
    备源：东方财富 datacenter 交易日历（mrsj）
    兜底：陈旧缓存 / 工作日近似（None 让调用方自己用 weekday）
    """
    cache_name = "trade_dates"
    if not force_refresh:
        cached = _load_json_cache(cache_name)
        if cached:
            return set(cached)

    # 主源：akshare
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        if df is not None and not df.empty:
            norm = set()
            for d in df["trade_date"].tolist():
                s = str(d)
                if len(s) == 8 and s.isdigit():
                    s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
                norm.add(s[:10])
            if norm:
                _save_json_cache(cache_name, sorted(norm))
                return norm
    except Exception as e:
        LOG.warning("akshare 交易日历失败: %s", e)

    # 备源：东财 datacenter
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPT_TRADE_DATE_CAL",
            "columns": "TRADE_DATE,TRADE_DATE_INT",
            "pageNumber": 1, "pageSize": 5000,
        }
        d = _http_get(url, params)
        rows = (d.get("result") or {}).get("data") if d else None
        if rows:
            norm = set()
            for r in rows:
                tdi = str(r.get("TRADE_DATE_INT", ""))
                if len(tdi) == 8 and tdi.isdigit():
                    norm.add(f"{tdi[:4]}-{tdi[4:6]}-{tdi[6:]}")
            if norm:
                _save_json_cache(cache_name, sorted(norm))
                return norm
    except Exception as e:
        LOG.debug("em 交易日历备源失败: %s", e)

    # 兜底：陈旧缓存
    cached = _load_json_cache(cache_name, ttl=timedelta(days=365))
    if cached:
        LOG.info("交易日历回退陈旧缓存（>7天）")
        return set(cached)
    return None


# ═══════════════════════════════════════════════════════
# 2. 涨停股池：每日所有涨停股
# ═══════════════════════════════════════════════════════
def fetch_zt_pool(date_str: str) -> list[dict]:
    """
    单日涨停股池。
    主源：akshare.stock_zt_pool_em(date)              ← 含 封板资金/首次封板时间/炸板次数
    备源：akshare.stock_zt_pool_zbgc_em(date)         ← 炸板股池
    兜底：EM datacenter 9501 RPT_LIMIT_BOARD_INFO     ← 当前接口常 down
    返回 [{code, name, streak, limit_price, limit_order_amount, amount, first_time, ...}]
    """
    out: list[dict] = []

    # 主源
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=date_str)
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                try:
                    out.append({
                        "code": str(r.get("代码", "")).zfill(6),
                        "name": str(r.get("名称", "")),
                        "streak": int(r.get("连板数", 1) or 1),
                        "limit_price": float(r.get("最新价", 0) or 0),
                        "limit_order_amount": float(r.get("封板资金", 0) or 0),
                        "amount": float(r.get("成交额", 0) or 0),
                        "market_cap": float(r.get("流通市值", 0) or 0),
                        "sector": str(r.get("所属行业", "")),
                        "first_time": str(r.get("首次封板时间", "")),
                        "burst_count": int(r.get("炸板次数", 0) or 0),
                        "last_time": str(r.get("最后封板时间", "")),
                        "turnover_pct": float(r.get("换手率", 0) or 0),
                    })
                except Exception:
                    continue
            if out:
                return out
    except Exception as e:
        LOG.warning("akshare_zt_pool %s 失败: %s", date_str, e)

    # 备 0 (2026-07-16 新增): 新浪全 A 列表 + 涨停过滤 (零依赖)
    #    akshare + push2 全挂时启用;字段不完整(无封板资金/连板数),仅给名单
    try:
        import requests as _req
        rows_sina = []
        for page in range(1, 6):  # 最多 5 页 × 80 = 400 只涨停股足够
            url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
            params = {"node": "hs_a", "sort": "changepercent",
                      "ascen": "desc", "page": page, "num": 80}
            r = _req.get(url, params=params, timeout=6,
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://finance.sina.com.cn/"})
            if r.status_code != 200:
                break
            try:
                arr = r.json()
            except Exception:
                break
            if not arr:
                break
            for it in arr:
                try:
                    cp = float(it.get("changepercent", 0) or 0)
                    code = str(it.get("code", "")).zfill(6)
                    # 中证 10%, 创板 (300/301)/科创板 (688/689) 20%
                    is_chinext = code.startswith(("300", "301"))
                    is_star = code.startswith(("688", "689"))
                    threshold = 19.5 if (is_chinext or is_star) else 9.5
                    if cp < threshold:
                        # 排序按涨幅 desc,触发即终止后续遍历
                        if cp < threshold - 1:
                            break
                        continue
                    trade = float(it.get("trade", 0) or 0)
                    rows_sina.append({
                        "code": code,
                        "name": str(it.get("name", "")),
                        "streak": 1,
                        "limit_price": trade,
                        "limit_order_amount": 0.0,
                        "amount": float(it.get("amount", 0) or 0),
                        "market_cap": 0.0,
                        "sector": "",
                        "first_time": "",
                        "burst_count": 0,
                        "last_time": "",
                        "turnover_pct": float(it.get("turnoverratio", 0) or 0),
                    })
                except Exception:
                    continue
            if len(arr) < 80:
                break
        if rows_sina:
            LOG.info("%s 回退到新浪涨停过滤(零依赖): %d 只", date_str, len(rows_sina))
            return rows_sina
    except Exception as e:
        LOG.debug("sina 涨停过滤 %s 失败: %s", date_str, e)

    # 备源：炸板股池（字段不完整，仅含 涨停价/首次封板时间/炸板次数）
    try:
        import akshare as ak
        df = ak.stock_zt_pool_zbgc_em(date=date_str)
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                try:
                    out.append({
                        "code": str(r.get("代码", "")).zfill(6),
                        "name": str(r.get("名称", "")),
                        "streak": 0,
                        "limit_price": float(r.get("涨停价", 0) or 0),
                        "limit_order_amount": 0.0,
                        "amount": 0.0,
                        "market_cap": 0.0,
                        "sector": "",
                        "first_time": str(r.get("首次封板时间", "")),
                        "burst_count": int(r.get("炸板次数", 0) or 0),
                        "last_time": "",
                    })
                except Exception:
                    continue
            if out:
                LOG.info("%s 回退到炸板股池（无封单数据）", date_str)
                return out
    except Exception as e:
        LOG.debug("akshare_zt_zbgc %s 失败: %s", date_str, e)

    # 兜底：EM datacenter 9501
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPT_LIMIT_BOARD_INFO",
            "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,LIMIT_PRICE,FLD_LIMIT_ORDER_VOLUME,LIMIT_ORDER_AMOUNT,FLD_AMOUNT,FLD_MARKET_CAP,BOARD_NAME,OPEN_TIME,FIRST_LIMIT_UP_TIME,LIMIT_UP_TIMES",
            "filter": f'(TRADE_DATE>="{date_str}")',
            "pageNumber": 1, "pageSize": 500,
        }
        d = _http_get(url, params, retries=2)
        rows = (d.get("result") or {}).get("data") if d else None
        if rows:
            for r in rows:
                try:
                    out.append({
                        "code": str(r.get("SECURITY_CODE", "")).zfill(6),
                        "name": str(r.get("SECURITY_NAME_ABBR", "")),
                        "streak": int(r.get("LIMIT_UP_TIMES", 1) or 1),
                        "limit_price": float(r.get("LIMIT_PRICE", 0) or 0),
                        "limit_order_amount": float(r.get("LIMIT_ORDER_AMOUNT", 0) or 0),
                        "amount": float(r.get("FLD_AMOUNT", 0) or 0),
                        "market_cap": float(r.get("FLD_MARKET_CAP", 0) or 0),
                        "sector": "",  # 9501 接口无行业字段,BOARD_NAME 是上市板名(主板/创业板)而非行业
                        "first_time": str(r.get("FIRST_LIMIT_UP_TIME", "")),
                        "burst_count": 0,
                        "last_time": "",
                    })
                except Exception:
                    continue
            if out:
                return out
    except Exception as e:
        LOG.debug("em 9501 涨停池失败: %s", e)

    return out


# ═══════════════════════════════════════════════════════
# 3. 行业板块列表：[{name, code}]
# ═══════════════════════════════════════════════════════
def fetch_sector_industries() -> list[dict]:
    """
    全部行业板块列表（~90 个，code=BKxxxx）。
    主源：akshare.ths（同花顺）  ← 字段齐全
    备源：akshare.em             ← EM 接口
    兜底：push2delay.eastmoney.com clist 多页
    """
    cache_name = "sector_industries"
    cached = _load_json_cache(cache_name)
    if cached:
        return cached

    rows: list[dict] = []

    # 主源：同花顺
    try:
        import akshare as ak
        df = ak.stock_board_industry_name_ths()
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                rows.append({"name": str(r.get("name", "")), "code": str(r.get("code", ""))})
            if rows:
                _save_json_cache(cache_name, rows)
                return rows
    except Exception as e:
        LOG.warning("ths 板块列表失败: %s", e)

    # 备源：东方财富 EM
    try:
        import akshare as ak
        df = ak.stock_board_industry_name_em()
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                rows.append({
                    "name": str(r.get("板块名称", "")),
                    "code": str(r.get("板块代码", "")),
                })
            if rows:
                _save_json_cache(cache_name, rows)
                return rows
    except Exception as e:
        LOG.warning("em 板块列表失败: %s", e)

    # 兜底：push2delay 多页
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        for pn in range(1, 8):
            params = {
                "pn": pn, "pz": 30, "po": 1, "np": 1,
                "fltt": 2, "invt": 2, "fid": "f3",
                "fs": "m:90+t:2",
                "fields": "f12,f14",
            }
            d = _http_get(url, params, retries=2)
            diff = (d.get("data") or {}).get("diff") if d else None
            if not diff:
                break
            for it in diff:
                rows.append({"name": it.get("f14", ""), "code": it.get("f12", "")})
            if len(diff) < 30:
                break
        if rows:
            _save_json_cache(cache_name, rows)
            return rows
    except Exception as e:
        LOG.debug("push2delay 板块列表失败: %s", e)

    return rows


# ═══════════════════════════════════════════════════════
# 4. 板块成分股：[(code, name)]
# ═══════════════════════════════════════════════════════
def fetch_sector_constituents(sector_code: str, sector_name: str = "") -> list[tuple[str, str]]:
    """
    板块成分股。
    主源：akshare.stock_board_industry_cons_em(code)  (2026-07 起东财接口挂死,加 5s 硬超时)
    备源：东财 push2 clist (BK 行业)
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
    out: list[tuple[str, str]] = []

    # 主源 — 5s 硬超时,东财接口 hang 时不拖死整条 screen
    def _ak_main():
        import akshare as ak
        df = ak.stock_board_industry_cons_em(symbol=sector_code)
        if df is None or df.empty:
            return []
        return [(str(r.get("代码", "")).zfill(6), str(r.get("名称", "")))
                for _, r in df.iterrows()]

    with ThreadPoolExecutor(max_workers=1) as ex:
        try:
            rows = ex.submit(_ak_main).result(timeout=5)
            if rows:
                return rows
        except FutTimeout:
            LOG.warning("em cons %s 超时 5s,东财接口挂死,跳过", sector_code)
        except Exception as e:
            LOG.warning("em cons %s 失败: %s", sector_code, e)

    # 备源：push2 clist (BK 行业)
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        for pn in range(1, 6):
            params = {
                "pn": pn, "pz": 50, "po": 1, "np": 1,
                "fltt": 2, "invt": 2, "fid": "f3",
                "fs": f"b:{sector_code}+f:!50",
                "fields": "f12,f14",
            }
            d = _http_get(url, params, retries=2)
            diff = (d.get("data") or {}).get("diff") if d else None
            if not diff:
                break
            for it in diff:
                out.append((str(it.get("f12", "")).zfill(6), str(it.get("f14", ""))))
            if len(diff) < 50:
                break
    except Exception as e:
        LOG.debug("push2 cons %s 失败: %s", sector_code, e)

    return out


# ═══════════════════════════════════════════════════════
# 4b. Prefilter 数据源（2026-07 推荐池重构新增）
# ═══════════════════════════════════════════════════════
def fetch_recent_zt_pool(days: int = 3) -> dict[str, dict]:
    """
    近 N 个交易日涨停池合并。
    返回 {code: {"name", "zt_count", "total_streak", "sector", "last_date"}}，
    zt_count = 该股在近 days 天内涨停过的天数，total_streak = 累计连板数。

    2026-07：原 fetch_trade_dates 返回的日期格式是 "YYYY-MM-DD"，而 fetch_zt_pool
    要 "YYYYMMDD"。这里统一用 datetime 解析 + 转 compact 格式，并只取 <= 今天的日期
    （trade_dates 偶尔会含未来日期，需过滤）。
    """
    import datetime as _dt
    today = _dt.date.today()
    all_dates_raw = sorted(fetch_trade_dates() or [], reverse=True)
    dates: list[str] = []
    for d in all_dates_raw:
        try:
            dt = _dt.datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        if dt > today:
            continue
        dates.append(dt.strftime("%Y%m%d"))
        if len(dates) >= days:
            break
    agg: dict[str, dict] = {}
    for d in dates:
        try:
            pool = fetch_zt_pool(d) or []
        except Exception as e:
            LOG.warning("recent_zt %s 失败: %s", d, e)
            continue
        for row in pool:
            code = row.get("code", "")
            if not code:
                continue
            cur = agg.setdefault(code, {
                "code": code,
                "name": row.get("name", ""),
                "zt_count": 0,
                "total_streak": 0,
                "sector": row.get("sector", ""),
                "last_date": d,
                "last_streak": 0,
            })
            cur["zt_count"] += 1
            cur["total_streak"] += int(row.get("streak", 1) or 1)
            if d >= cur["last_date"]:
                cur["last_date"] = d
                cur["last_streak"] = int(row.get("streak", 1) or 1)
                cur["sector"] = row.get("sector") or cur["sector"]
                cur["name"] = row.get("name") or cur["name"]
    LOG.info("recent_zt_pool days=%s → %d 只", days, len(agg))
    return agg


def fetch_hot_sectors(top_n_flow: int = 15, top_n_pct: int = 10) -> list[dict]:
    """
    今日热门板块 = (主力净流入 Top N) ∪ (涨幅 Top N)。

    2026-07-08: 用户要求优先东财。东财 stock_sector_fund_flow_rank +
    stock_board_industry_name_em 仍 RemoteDisconnected → 5s 硬超时快速失败,
    THS 同花顺接口做兜底（一次拿 90 板块含涨跌幅+净流入+领涨股）。

    返回 [{"code", "name", "rank_flow", "rank_pct", "rank_kind", "rank", "change_pct", "net_inflow"}]
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

    # 1) 优先东财 (5s 硬超时)
    df_em = None
    name_to_code_em: dict[str, str] = {}

    def _em_main():
        import akshare as ak
        return ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")

    def _em_name():
        import akshare as ak
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return {}
        return {str(r.get("板块名称", "")): str(r.get("板块代码", ""))
                for _, r in df.iterrows()}

    # 2026-07-09: 不用 with 块, 手动 shutdown(wait=False) 才能不被 __exit__ 阻塞
    ex = ThreadPoolExecutor(max_workers=2)
    try:
        f_main = ex.submit(_em_main)
        f_name = ex.submit(_em_name)
        try:
            df_em = f_main.result(timeout=5)
        except (FutTimeout, Exception) as e:
            LOG.warning("hot_sector 东财主源 5s 超时/失败: %s", e)
        try:
            name_to_code_em = f_name.result(timeout=5) or {}
        except (FutTimeout, Exception) as e:
            LOG.warning("hot_sector 东财 name 接口 5s 超时/失败: %s", e)
    finally:
        ex.shutdown(wait=False)

    if df_em is not None and not df_em.empty and name_to_code_em:
        LOG.info("hot_sector 使用东财数据源")
        sectors_em: dict[str, dict] = {}
        # 2026-07-20: akshare 行业资金流列名加了 "今日" 前缀 (今日涨跌幅 / 今日主力净流入-净额)
        # 新/旧列名都试, 取存在的那个
        _pct_col = next((c for c in ("今日涨跌幅", "涨跌幅") if c in df_em.columns), None)
        _flow_col = next((c for c in ("今日主力净流入-净额", "主力净流入-净额") if c in df_em.columns), None)
        if not _pct_col or not _flow_col:
            LOG.warning("hot_sector EM 列名不匹配: cols=%s", list(df_em.columns))
            return _fetch_hot_sectors_qq_fallback(top_n_pct)
        pct_ranked = df_em[~df_em["名称"].astype(str).str.contains("ST", na=False)] \
            .sort_values(_pct_col, ascending=False)
        flow_ranked = df_em.sort_values(_flow_col, ascending=False)

        def _ingest_em(df, kind):
            for i, (_, r) in enumerate(df.iterrows(), start=1):
                name = str(r.get("名称", ""))
                code = name_to_code_em.get(name, name)
                cur = sectors_em.setdefault(code, {
                    "code": code, "name": name,
                    "rank_flow": None, "rank_pct": None,
                    "change_pct": float(r.get(_pct_col, 0) or 0),
                    "net_inflow": float(r.get(_flow_col, 0) or 0),
                })
                if kind == "flow":
                    cur["rank_flow"] = i
                    cur["net_inflow"] = float(r.get(_flow_col, 0) or 0)
                else:
                    cur["rank_pct"] = i
                    cur["change_pct"] = float(r.get(_pct_col, 0) or 0)

        _ingest_em(flow_ranked.head(top_n_flow), "flow")
        _ingest_em(pct_ranked.head(top_n_pct), "pct")

        out_em: list[dict] = []
        for code, s in sectors_em.items():
            kinds = []
            if s["rank_flow"] is not None:
                kinds.append("flow")
            if s["rank_pct"] is not None:
                kinds.append("pct")
            out_em.append({
                "code": code,
                "name": s["name"],
                "rank_flow": s["rank_flow"],
                "rank_pct": s["rank_pct"],
                "rank_kind": "+".join(kinds) or "none",
                "rank": min([r for r in (s["rank_flow"], s["rank_pct"]) if r is not None]),
                "change_pct": s["change_pct"],
                "net_inflow": s["net_inflow"],
            })
        out_em.sort(key=lambda x: x["rank"])
        LOG.info("hot_sectors(东财) → %d 个", len(out_em))
        return out_em

    # 2) 兜底: THS 同花顺 (8s 硬超时)
    LOG.info("hot_sector 东财不可用,降级 THS")
    summary_df = None
    name_to_code: dict[str, str] = {}

    def _ths_summary():
        import akshare as ak
        return ak.stock_board_industry_summary_ths()

    def _ths_name():
        import akshare as ak
        df = ak.stock_board_industry_name_ths()
        out = {}
        for _, r in df.iterrows():
            out[str(r.get("name", ""))] = str(r.get("code", ""))
        return out

    # 2026-07-09: 同上, 不用 with, 手动 shutdown
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        try:
            summary_df = ex.submit(_ths_summary).result(timeout=8)
        except (FutTimeout, Exception) as e:
            LOG.warning("hot_sector summary_ths 8s 超时/失败: %s", e)
        try:
            name_to_code = ex.submit(_ths_name).result(timeout=8) or {}
        except (FutTimeout, Exception) as e:
            LOG.warning("hot_sector name_ths 8s 超时/失败: %s", e)
    finally:
        ex.shutdown(wait=False)

    if summary_df is None or summary_df.empty:
        # 兜底 (2026-07-16 新增): 腾讯 qt.gtimg.cn 板块指数快照 (零依赖)
        return _fetch_hot_sectors_qq_fallback(top_n_pct)

    pct_ranked = summary_df[~summary_df["板块"].str.contains("ST", na=False)] \
        .sort_values("涨跌幅", ascending=False)
    pct_top = pct_ranked.head(top_n_pct)
    flow_ranked = summary_df.sort_values("净流入", ascending=False)
    flow_top = flow_ranked.head(top_n_flow)

    sectors: dict[str, dict] = {}

    def _ingest(df, kind):
        for i, (_, r) in enumerate(df.iterrows(), start=1):
            name = str(r.get("板块", ""))
            code = name_to_code.get(name, "")
            if not code:
                continue
            cur = sectors.setdefault(code, {
                "code": code, "name": name,
                "rank_flow": None, "rank_pct": None,
                "change_pct": float(r.get("涨跌幅", 0) or 0),
                "net_inflow": float(r.get("净流入", 0) or 0),
            })
            if kind == "flow":
                cur["rank_flow"] = i
                cur["net_inflow"] = float(r.get("净流入", 0) or 0)
            else:
                cur["rank_pct"] = i
                cur["change_pct"] = float(r.get("涨跌幅", 0) or 0)

    _ingest(flow_top, "flow")
    _ingest(pct_top, "pct")

    out: list[dict] = []
    for code, s in sectors.items():
        kinds = []
        if s["rank_flow"] is not None:
            kinds.append("flow")
        if s["rank_pct"] is not None:
            kinds.append("pct")
        out.append({
            "code": code,
            "name": s["name"],
            "rank_flow": s["rank_flow"],
            "rank_pct": s["rank_pct"],
            "rank_kind": "+".join(kinds) or "none",
            "rank": min([r for r in (s["rank_flow"], s["rank_pct"]) if r is not None]),
            "change_pct": s["change_pct"],
            "net_inflow": s["net_inflow"],
        })
    out.sort(key=lambda x: x["rank"])
    LOG.info("hot_sectors(THS) → %d 个 (flow=%s + pct=%s)",
             len(out), top_n_flow, top_n_pct)
    return out


def _fetch_hot_sectors_qq_fallback(top_n_pct: int) -> list[dict]:
    """
    兜底 (2026-07-16 新增): 腾讯 qt.gtimg.cn 板块指数快照,零依赖。
    限定用 中证系列行业指数 (sz399xxx),这些在 qt.gtimg.cn 上稳定可用,
    与项目里 fetch_sector_industries 返回的 881xxx SW 行业不同(腾讯不支持 SW 代码)。
    只给 change_pct 排名,无 net_inflow(标 source=qq_degraded,前端可识别)。
    """
    # 中证行业指数 (sz399xxx + sz3999xx), 在 qt.gtimg.cn 上验证可用
    # 跨多个批次 1 次拉 ~30 个, 覆盖消费/医药/科技/能源/金融/工业 等核心行业
    ZZ_INDICES = [
        ("399987", "中证酒"),
        ("399971", "中证传媒"),
        ("399986", "中证金融"),
        ("399967", "中证军工"),
        ("399975", "中证有色"),
        ("399976", "中证医药"),
        ("399989", "中证医疗"),
        ("399998", "中证煤炭"),
        ("399995", "中证基建"),
        ("399996", "中证证券"),
        ("399970", "中证环保"),
        ("399993", "中证生科"),
        ("399983", "中证资讯"),
        ("399978", "中证医药100"),
        ("399991", "中证一带一路"),
        ("399992", "中证光伏"),
        ("399997", "中证白酒"),
        ("399966", "中证800医药"),
        ("399979", "中证TMT"),
        ("399982", "中证500"),
        ("399905", "中证500"),
        ("399906", "中证800"),
        ("399001", "深证成指"),
        ("399006", "创业板指"),
        ("399852", "中证1000"),
    ]
    try:
        import requests as _req
        codes_qq = ",".join(f"sz{code}" for code, _ in ZZ_INDICES)
        url = f"http://qt.gtimg.cn/q={codes_qq}"
        r = _req.get(url, timeout=6,
                     headers={"User-Agent": "Mozilla/5.0",
                              "Referer": "https://gu.qq.com/"})
        if r.status_code != 200:
            return []
        raw = r.content.decode("gbk", errors="ignore")
        name_map = dict(ZZ_INDICES)
        parsed: list[dict] = []
        for line in raw.strip().split(";"):
            line = line.strip()
            if "=" not in line or '"' not in line:
                continue
            try:
                body = line.split('"')[1]
                fields = body.split("~")
                if len(fields) < 40:
                    continue
                code = fields[2]
                price = float(fields[3] or 0)
                last_close = float(fields[4] or 0)
                if not price or not last_close:
                    continue
                name = name_map.get(code) or fields[1] or code
                change_pct = float(fields[32] or 0) or (price - last_close) / last_close * 100
                parsed.append({
                    "code": code, "name": name,
                    "rank_flow": None, "rank_pct": None,
                    "change_pct": change_pct, "net_inflow": 0.0,
                })
            except Exception:
                continue
        if not parsed:
            return []
        parsed.sort(key=lambda x: x["change_pct"], reverse=True)
        top = parsed[:top_n_pct]
        for i, s in enumerate(top, start=1):
            s["rank_pct"] = i
            s["rank_kind"] = "pct"
            s["rank"] = i
        LOG.info("hot_sectors(qq_degraded) → %d 个 (仅涨跌幅)", len(top))
        return top
    except Exception as e:
        LOG.debug("hot_sectors qq fallback 失败: %s", e)
        return []


def filter_zt_by_hot_sectors(zt_pool: dict[str, dict],
                             hot_sectors: list[dict]) -> dict[str, dict]:
    """
    过滤涨停池：只保留「所属行业 ∈ 热门板块名集合」的股票。

    2026-07：EM cons 接口对 THS 881xxx code 持续 RemoteDisconnected；改成直接用
    fetch_zt_pool 返回的 sector 字段做匹配，避开成分股拉取。匹配规则：
      - sector 名精确匹配板块名
      - sector 名 ∈ {hot_sector.name 的子串} (防 sector 字段是 "半导体" 而板块是 "半导体" 但前面有"国产"之类的前缀)
      - 反向：板块名 ∈ sector (sector 字段常被截断如 "半导体" vs "半导体材料")

    返回过滤后的 zt_pool 子集 (dict[code, info])，并附加 hot_sector_rank / hot_sector_name 字段。
    """
    if not hot_sectors:
        return {}
    hot_names = [s["name"] for s in hot_sectors]
    out: dict[str, dict] = {}
    for code, info in zt_pool.items():
        sec = info.get("sector", "") or ""
        if not sec:
            continue
        matched = None
        for hs in hot_sectors:
            hn = hs["name"]
            # 双向 substring 匹配
            if hn in sec or sec in hn:
                matched = hs
                break
        if matched is None:
            continue
        new_info = dict(info)
        new_info["hot_sector_rank"] = matched["rank"]
        new_info["hot_sector_kind"] = matched["rank_kind"]
        new_info["hot_sector_name"] = matched["name"]
        out[code] = new_info
    LOG.info("filter_zt_by_hot_sectors: %d → %d (匹配 %d 个热门板块)",
             len(zt_pool), len(out), len(hot_sectors))
    return out


# ═══════════════════════════════════════════════════════
# 5. 龙虎榜（单日明细）
# ═══════════════════════════════════════════════════════
def fetch_lhb_detail(date_str: str) -> Any:
    """
    单日龙虎榜明细。
    主源：akshare.stock_lhb_detail_em(start, end)
    备源：东财 datacenter LHB（待补字段较多，这里先只主源 + 日内缓存）
    """
    cache_name = f"lhb_{date_str}"
    cached = _load_json_cache(cache_name, ttl=timedelta(days=2))
    if cached is not None:
        return cached  # list of dict or []

    try:
        import akshare as ak
        df = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
        if df is None or df.empty:
            _save_json_cache(cache_name, [])
            return pd.DataFrame() if pd else []
        # 过滤退市股
        if "名称" in df.columns:
            df = df[~df["名称"].astype(str).str.contains("退", na=False)]
        result = df.reset_index(drop=True)
        _save_json_cache(cache_name, result.to_dict(orient="records"))
        return result
    except Exception as e:
        LOG.warning("龙虎榜 %s 拉取失败: %s", date_str, e)
        return None


def fetch_lhb_hyyyb(date_str: str) -> list[dict] | None:
    """
    龙虎榜席位明细（营业部 → 买入股票清单）。
    主源：akshare.stock_lhb_hyyyb_em(start, end)
    备源：暂无（EM 9501 已 down）
    返 list[dict]（含 营业部名称/买入股票 等字段），失败返 None。
    """
    cache_name = f"lhb_hyyyb_{date_str}"
    cached = _load_json_cache(cache_name, ttl=timedelta(days=2))
    if cached is not None:
        return cached  # list of dict or []

    try:
        import akshare as ak
        df = ak.stock_lhb_hyyyb_em(start_date=date_str, end_date=date_str)
        if df is None or df.empty:
            _save_json_cache(cache_name, [])
            return []
        result = df.reset_index(drop=True).to_dict(orient="records")
        _save_json_cache(cache_name, result)
        return result
    except Exception as e:
        LOG.warning("akshare lhb_hyyyb %s 失败: %s", date_str, e)
        return None  # 调用方降级


# ═══════════════════════════════════════════════════════
# 6. 北向资金概览
# ═══════════════════════════════════════════════════════
def fetch_north_flow_summary() -> Any:
    """
    沪深港通资金净买入概览（4 行）。
    主源：akshare.stock_hsgt_fund_flow_summary_em
    备源：东财 push2 北向资金
    """
    cache_name = "north_flow_summary"
    cached = _load_json_cache(cache_name, ttl=timedelta(hours=2))
    if cached is not None:
        return pd.DataFrame(cached) if pd else cached

    try:
        import akshare as ak
        df = ak.stock_hsgt_fund_flow_summary_em()
        if df is not None and not df.empty:
            _save_json_cache(cache_name, df.to_dict(orient="records"))
            return df
    except Exception as e:
        LOG.warning("akshare 北向资金失败: %s", e)

    # 备源：push2 北向资金（北向资金页面 API）
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1, "pz": 10, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fs": "m:6+m:7+m:8",  # 沪/深/京
            "fields": "f12,f14,f2,f3,f8",
        }
        d = _http_get(url, params, retries=2)
        diff = (d.get("data") or {}).get("diff") if d else None
        if diff and pd:
            df = pd.DataFrame(diff)
            _save_json_cache(cache_name, df.to_dict(orient="records"))
            return df
    except Exception as e:
        LOG.debug("push2 北向失败: %s", e)

    # 兜底 2 (2026-07-16 新增): EM datacenter 持股汇总 → 估算北向净流入
    #    RPT_MUTUAL_STOCK_HOLDRANKS 返回按持股变动数据(增持市值),聚合可近似"北向今日净流入"
    #    注意:这是兜底近似,精度低于 akshare/push2
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPT_MUTUAL_STOCK_HOLDRANKS",
            "columns": "ALL",
            "filter": '(TRADE_DATE>="2024-01-01")',
            "pageNumber": 1, "pageSize": 200,
        }
        d = _http_get(url, params, retries=2)
        rows = (d.get("result") or {}).get("data") if d else None
        if rows and pd:
            df = pd.DataFrame(rows)
            _save_json_cache(cache_name, df.to_dict(orient="records"))
            return df
    except Exception as e:
        LOG.debug("em datacenter 北向持股兜底失败: %s", e)

    return None


# ═══════════════════════════════════════════════════════
# 7. 全 A 实时行情：[(code, name)]（用于回测/扫描）
# ═══════════════════════════════════════════════════════
def fetch_spot_a(exclude_bjs: bool = True, exclude_st: bool = True) -> list[tuple[str, str]]:
    """
    全 A 实时行情（代码+名称）。
    主源：push2delay 直连
    备源：akshare.stock_zh_a_spot_em
    兜底：返回空
    """
    out: list[tuple[str, str]] = []

    # 主源：push2delay（akshare 经常断）
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get"
        for pn in range(1, 50):
            params = {
                "pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # 沪深京全 A
                "fields": "f12,f14",
            }
            d = _http_get(url, params, retries=2)
            diff = (d.get("data") or {}).get("diff") if d else None
            if not diff:
                break
            for it in diff:
                code = str(it.get("f12", "")).zfill(6)
                name = str(it.get("f14", ""))
                if not code or not name:
                    continue
                if exclude_bjs and (code.startswith(("688", "689", "8", "4", "30"))):
                    continue
                if exclude_st and ("ST" in name or "退" in name):
                    continue
                out.append((code, name))
            if len(diff) < 100:
                break
        if out:
            return out
    except Exception as e:
        LOG.warning("push2delay spot 失败: %s", e)

    # 备源：akshare
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                code = str(row.get("代码", "")).zfill(6)
                name = str(row.get("名称", ""))
                if not code:
                    continue
                if exclude_bjs and code.startswith(("688", "689", "8", "4", "30")):
                    continue
                if exclude_st and ("ST" in name or "退" in name):
                    continue
                out.append((code, name))
    except Exception as e:
        LOG.debug("akshare spot 失败: %s", e)

    return out


# ═══════════════════════════════════════════════════════
# 7b. 全 A 全字段实时快照 (2026-07-21 顶级架构核心)
# ───────────────────────────────────────────────────────
# push2delay clist 一次拉全市场 5540 只 + 全部报价字段。
# 实测: 57 页 × 100, 16 并发 ≈ 1.4s 拉完整个市场。
# 旧架构逐只 fetch_realtime → poller 永远暖不满 5400; 这里一次全拿。
# ═══════════════════════════════════════════════════════
# 东财 clist 字段映射 (fltt=2 → 已是十进制数值)
_SPOT_FIELDS = "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f18,f20,f23"
_SPOT_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"  # 沪深京全 A (主板/中小/创业/科创)
_SPOT_URL = "https://push2delay.eastmoney.com/api/qt/clist/get"


def _spot_num(v) -> float:
    """clist 字段可能是 '-' (停牌/无值), 统一转 float, 失败 0。"""
    if v is None or v == "-" or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _spot_page(pn: int) -> list[dict]:
    params = {
        "pn": pn, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f6",
        "fs": _SPOT_FS, "fields": _SPOT_FIELDS,
    }
    d = _http_get(_SPOT_URL, params, retries=2, timeout=8)
    return (d.get("data") or {}).get("diff") or [] if d else []


def fetch_spot_a_full(
    max_workers: int = 16,
    exclude_st: bool = False,
    overall_timeout: float = 12.0,
) -> dict[str, dict]:
    """全 A 全字段实时快照 → {code: {price, change_pct, change_amt, volume,
       amount, amplitude, turnover, pe_ttm, volume_ratio, prev_close, mcap, pb, name}}.

    主源: push2delay clist 并发全页扫描 (57 页)。
    失败/空时返回 {}, 调用方走 per-code 缓存兜底。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 先探一页拿 total, 决定页数
    first = _spot_page(1)
    if not first:
        LOG.warning("fetch_spot_a_full: 首页空 (push2delay 挂?)")
        return {}
    # total 只能从带 total 的响应拿; 这里直接扫到空页为止 (最多 60 页 = 6000 只)
    out: dict[str, dict] = {}

    def _ingest(diff: list[dict]) -> None:
        for it in diff:
            code = str(it.get("f12", "")).zfill(6)
            name = str(it.get("f14", ""))
            if not code or code == "000000" or not name:
                continue
            if exclude_st and ("ST" in name or "退" in name):
                continue
            out[code] = {
                "name":         name,
                "最新价":        _spot_num(it.get("f2")),
                "涨跌幅":        _spot_num(it.get("f3")),
                "涨跌额":        _spot_num(it.get("f4")),
                "成交量":        _spot_num(it.get("f5")),
                "成交额":        _spot_num(it.get("f6")),
                "振幅":          _spot_num(it.get("f7")),
                "换手率":        _spot_num(it.get("f8")),
                "市盈率":        _spot_num(it.get("f9")),
                "量比":          _spot_num(it.get("f10")),
                "昨收":          _spot_num(it.get("f18")),
                "总市值":        _spot_num(it.get("f20")),
                "市净率":        _spot_num(it.get("f23")),
                "_source":      "push2delay_spot",
            }

    _ingest(first)
    deadline = time.time() + overall_timeout
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="spot_full") as ex:
        futs = {ex.submit(_spot_page, pn): pn for pn in range(2, 61)}
        for f in as_completed(futs):
            if time.time() > deadline:
                break
            try:
                _ingest(f.result() or [])
            except Exception:
                pass
    LOG.info("fetch_spot_a_full: 全市场 %d 只", len(out))
    return out


# ═══════════════════════════════════════════════════════
# 8. 个股当日分钟线
# ═══════════════════════════════════════════════════════
def fetch_intraday_min(code: str, period: str = "1") -> Any:
    """
    个股当日分钟 K 线。
    主源：akshare.stock_zh_a_hist_min_em(symbol, period, adjust='qfq')
    备源：腾讯 qt.gtimg.cn 分钟线（如有）
    """
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist_min_em(symbol=code, period=period, adjust="qfq")
        if df is not None and not df.empty:
            return df
    except Exception as e:
        LOG.warning("akshare 分钟线 %s 失败: %s", code, e)

    # 备源：暂时无（腾讯分钟线鉴权严格）
    LOG.debug("分钟线 %s 全部源失败", code)
    return None


# ═══════════════════════════════════════════════════════
# 9. 大单交易（盘后/盘中）
# ═══════════════════════════════════════════════════════
def fetch_big_deals(limit: int = 20) -> Any:
    """
    实时大单交易（东财大单精灵）。
    主源：akshare.stock_fund_flow_big_deal
    备源：东财 push2 大单流
    """
    try:
        import akshare as ak
        df = ak.stock_fund_flow_big_deal()
        if df is not None and not df.empty:
            return df.head(limit).reset_index(drop=True)
    except Exception as e:
        LOG.warning("akshare 大单流失败: %s", e)

    # 备源：暂时跳过
    return None


# ═══════════════════════════════════════════════════════
# 10. Baostock 历史日线/分钟线 (2026-07-16 新增)
#     免费注册, 5000次/天, 仅回测/历史场景用, 不在实时链
# ═══════════════════════════════════════════════════════
_BS_LOGIN_OK = False
_BS_LOCK = None
_bs_login_lock = None  # 延迟初始化避免 ImportError 时 Lock 未建


def _ensure_bs_login():
    """惰性登录 baostock, 只登一次。失败返 False (调用方走 daily_proxy)。"""
    global _BS_LOGIN_OK, _bs_login_lock
    if _BS_LOGIN_OK:
        return True
    if _bs_login_lock is None:
        import threading as _th
        _bs_login_lock = _th.Lock()
    with _bs_login_lock:
        if _BS_LOGIN_OK:
            return True
        try:
            import baostock as bs
            lg = bs.login()
            if lg.error_code == "0":
                _BS_LOGIN_OK = True
                LOG.info("baostock 登录成功")
                return True
            LOG.warning(f"baostock 登录失败: {lg.error_msg}")
            return False
        except Exception as e:
            LOG.warning(f"baostock 登录异常: {e}")
            return False


def fetch_daily_baostock(code: str, start_date: str, end_date: str,
                         adj: str = "qfq") -> Any:
    """
    历史日线 (Baostock) — 仅用于 backtest / screener 历史场景。
    adj: "qfq" 前复权 / "hfq" 后复权 / "" 不复权
    返回 DataFrame: [日期, 开盘, 最高, 最低, 收盘, 成交量] (注: 日线无成交额字段)
    失败 (akshare 挂 + baostock 没装/没登录/超时) 返 None。
    8s 硬超时 (沙箱网络)。

    日期格式: baostock 要求 YYYY-MM-DD (不是 YYYYMMDD), 调用方负责转换。
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
    if not _ensure_bs_login():
        return None
    bs_freq_map = {"qfq": "2", "hfq": "1", "": "3"}
    adjustflag = bs_freq_map.get(adj, "2")
    def _q():
        import baostock as bs
        mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
        # baostock 日线只支持到 volume, 无 amount 字段
        rs = bs.query_history_k_data_plus(
            f"{mkt}.{code}",
            "date,open,high,low,close,volume",
            start_date=start_date, end_date=end_date,
            frequency="d", adjustflag=adjustflag,
        )
        rows = []
        while (rs.error_code == "0") and rs.next():
            r = rs.get_row_data()
            rows.append(r)
        if not rows:
            return None
        import pandas as pd
        # baostock 返回字段: date, open, high, low, close, volume (小写英文)
        df = pd.DataFrame(rows, columns=["日期", "开盘", "最高", "最低", "收盘", "成交量"])
        for c in ["开盘", "最高", "最低", "收盘"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["成交量"] = pd.to_numeric(df["成交量"], errors="coerce")
        df["成交额"] = None   # baostock 日线无 amount, 留 None 兼容上层
        return df
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        df = ex.submit(_q).result(timeout=8)
        if df is not None and not df.empty:
            LOG.info(f"baostock_daily {code} {start_date}~{end_date} → {len(df)} 行")
        return df
    except FutTimeout:
        LOG.warning(f"baostock_daily {code} 8s 超时")
        return None
    except Exception as e:
        LOG.warning(f"baostock_daily {code} 失败: {e}")
        return None
    finally:
        ex.shutdown(wait=False)


def fetch_min_baostock(code: str, start_date: str, end_date: str,
                       freq: int = 5) -> Any:
    """
    历史分钟 K 线 (Baostock), freq=1/5/15/30/60 分钟。
    仅用于回测场景。8s 硬超时。日期格式 YYYY-MM-DD。
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
    if not _ensure_bs_login():
        return None
    def _q():
        import baostock as bs
        mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
        rs = bs.query_history_k_data_plus(
            f"{mkt}.{code}",
            "date,time,open,high,low,close,volume,amount",
            start_date=start_date, end_date=end_date,
            frequency=f"{freq}", adjustflag="2",
        )
        rows = []
        while (rs.error_code == "0") and rs.next():
            r = rs.get_row_data()
            rows.append(r)
        if not rows:
            return None
        import pandas as pd
        df = pd.DataFrame(rows, columns=["日期", "时间", "开盘", "最高", "最低", "收盘", "成交量", "成交额"])
        for c in ["开盘", "最高", "最低", "收盘", "成交量", "成交额"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(_q).result(timeout=8)
    except FutTimeout:
        LOG.warning(f"baostock_min {code} 8s 超时")
        return None
    except Exception as e:
        LOG.warning(f"baostock_min {code} 失败: {e}")
        return None
    finally:
        ex.shutdown(wait=False)


# ═══════════════════════════════════════════════════════
# CLI 调试
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys

    cmds = {
        "trade_dates": lambda: sorted(fetch_trade_dates() or []),
        "sectors":     lambda: fetch_sector_industries(),
        "north":       lambda: (fetch_north_flow_summary() or pd.DataFrame()).to_string() if pd else "pd not avail",
        "spot":        lambda: f"共 {len(fetch_spot_a())} 只",
        "zt":          lambda: fetch_zt_pool(sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime("%Y%m%d")),
        "min":         lambda: (fetch_intraday_min(sys.argv[2]) if len(sys.argv) > 2 else "need code"),
    }
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(f"用法: python3 multi_source_fetchers.py <{'|'.join(cmds.keys())}> [args...]")
        sys.exit(1)
    print(cmds[sys.argv[1]]())