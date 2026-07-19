"""
tuixue_v3/data_layer.py
三级热备数据层（akshare / 东财 / 同花顺 / 腾讯）+ Redis 统一缓存
- 单源超时 5s 自动切下一级
- 单源重试 3 次
- 缓存:Redis (cache_store) — 日线 4h / 分时 30min / 基本面 24h
- 降级:Redis 挂了自动走 SQLite (cache_store 内置 fallback)

v3.0 (2026-07-11): 所有 _cache_* 调用统一走 cache_store
"""
from __future__ import annotations

import json
import logging
import time as systime
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from . import config as cfg
from . import lib_common as lc
from . import multi_source_fetchers as msf
from . import cache_store as cs
from .cache_store import get_store, K

log = logging.getLogger("tuixue_v3.data")

# ═══════════════════════════════════════════════════
# 缓存工具 — 统一走 cache_store (Redis 主用 + SQLite fallback)
# ═══════════════════════════════════════════════════
_store = get_store()  # 模块级单例


def _cache_load(name: str, ttl_sec: int) -> Any | None:
    """通用 KV 缓存读取 (TTL 校验在 cache_store 内做,这里 ttl_sec 仅用于 store.set 时回填)。"""
    return _store.get(name)


def _cache_save(name: str, data: Any, ttl: int | None = None) -> None:
    """通用 KV 缓存写入。ttl=None 时用 24h 默认。"""
    _store.set(name, data, ttl=ttl if ttl is not None else 24 * 3600)


def _cache_save_df(name: str, df: pd.DataFrame, ttl: int = 4 * 3600) -> None:
    """DataFrame 缓存: list-of-dict 形式写入 Redis。"""
    if df is None or df.empty:
        return
    try:
        d = df.copy()
        if "日期" in d.columns:
            d["日期"] = d["日期"].astype(str)
        records = d.to_dict(orient="records")
        _store.set(name, records, ttl=ttl)
    except Exception as e:
        log.warning(f"DataFrame 缓存失败 {name}: {e}")


def _cache_load_df(name: str, ttl_sec: int) -> pd.DataFrame | None:
    """DataFrame 缓存读取。"""
    raw = _store.get(name)
    if raw is None:
        return None
    try:
        df = pd.DataFrame(raw)
        if "日期" in df.columns:
            df["日期"] = pd.to_datetime(df["日期"])
            df = df.sort_values("日期").reset_index(drop=True)
        return df
    except Exception:
        return None


# ═══════════════════════════════════════════════════
# 全市场股票列表（带缓存）
# ═══════════════════════════════════════════════════
def fetch_stock_list() -> list[tuple[str, str]]:
    """返回 [(code, name), ...]，剔除创业板/科创板/北交所/ST
    2026-07-09: 改用 akshare.stock_info_a_code_name() 全量（稳定 5500+ 只）
    然后本地过滤，避开 msf.fetch_spot_a 实时接口被 ban / 数据不全的问题
    2026-07-11: 走 Redis K.STOCKLIST_FILTERED
    """
    cached = _store.get(K.STOCKLIST_FILTERED)
    if cached:
        return [(c, n) for c, n in cached]

    raw = []
    # 主：akshare 全量（稳定 5500+ 只）
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        if df is not None and not df.empty and "code" in df.columns and "name" in df.columns:
            for _, r in df.iterrows():
                c = str(r["code"]).zfill(6)
                n = str(r["name"])
                if c and n:
                    raw.append((c, n))
    except Exception as e:
        log.warning(f"fetch_stock_list akshare 失败,降级 msf: {e}")
        try:
            raw = msf.fetch_spot_a(exclude_bjs=True, exclude_st=True) or []
        except Exception as e2:
            log.warning(f"msf.fetch_spot_a 失败: {e2}")

    filtered = []
    for code, name in raw:
        if not code:
            continue
        # 沪市主板 60xxxx / 601xxx / 603xxx / 605xxx
        # 深市主板 000xxx / 001xxx / 002xxx（002 是中小板，已并入主板）
        # 排除：创业板 300xxx / 科创板 688xxx / 北交所 8xx / 4xx
        if code.startswith(("300", "301", "688", "689", "8", "43", "83", "87")):
            continue
        if not (code.startswith(("60", "601", "603", "605", "000", "001", "002"))):
            continue
        if "ST" in (name or "") or "*ST" in (name or ""):
            continue
        filtered.append((code, name))

    _store.set(K.STOCKLIST_FILTERED, filtered, ttl=24 * 3600)
    log.info(f"股票池过滤后: {len(filtered)} 只")
    return filtered


def fetch_stock_list_all() -> list[tuple[str, str]]:
    """返回全 A 股 [(code, name), ...]，不过滤 (用于个股名称查询)
    2026-07-11: 走 Redis K.STOCKLIST_ALL
    """
    cached = _store.get(K.STOCKLIST_ALL)
    if cached:
        return [(c, n) for c, n in cached]

    out: list[tuple[str, str]] = []
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        if df is not None and not df.empty and "code" in df.columns and "name" in df.columns:
            for _, r in df.iterrows():
                code = str(r["code"]).zfill(6)
                name = str(r["name"])
                if code and name:
                    out.append((code, name))
    except Exception as e:
        log.warning(f"fetch_stock_list_all akshare 失败: {e}")
    if not out:
        raw = msf.fetch_spot_a(exclude_bjs=False, exclude_st=True) or []
        out = [(c, n) for c, n in raw if c]

    _store.set(K.STOCKLIST_ALL, out, ttl=24 * 3600)
    log.info(f"全 A 股票池: {len(out)} 只")
    return out


# ═══════════════════════════════════════════════════
# 日 K 线（带三级热备 + 缓存）
# ═══════════════════════════════════════════════════
def fetch_daily(code: str, days: int = 120, force: bool = False) -> pd.DataFrame | None:
    """优先 Redis (cache_store) → 降级 SQLite (cache_db.daily) → 拉三级热备 → 双写
    v3.0 (2026-07-11): 不再写散文件 JSON
    """
    if not force:
        try:
            from . import cache_db
            cached = cache_db.daily().get(code, days)
            if cached is not None and len(cached) >= days * 0.7:
                return cached
        except Exception as e:
            log.debug(f"cache_db.daily 读失败 {code}: {e}")

    t0 = systime.time()
    df = lc.fetch_daily(code, days=days)
    elapsed = systime.time() - t0

    if df is None or df.empty:
        log.warning(f"[{code}] 日线三级热备全部失败(耗时 {elapsed:.1f}s)")
        return None

    if elapsed > cfg.DATA_TIMEOUT_SEC:
        log.warning(f"[{code}] 日线耗时 {elapsed:.1f}s 超阈值")

    try:
        from . import cache_db
        cache_db.daily().set(code, df)
    except Exception as e:
        log.debug(f"cache_db.daily 写失败 {code}: {e}")
    return df


# ═══════════════════════════════════════════════════
# 分时 K 线（带缓存）
# ═══════════════════════════════════════════════════
def fetch_intraday(code: str, date_str: str | None = None, force: bool = False) -> pd.DataFrame | None:
    """分时 1 分钟 K。date_str: YYYYMMDD；None 为最近一个交易日。
    2026-07-11: 走 Redis K.INTRADAY (TTL 30min)
    2026-07-19: 历史日 TTL 7d (回测准确性关键 — 历史分时不再变), 今日仍 30min
    """
    cache_key = f"intraday:{code}:{date_str or 'latest'}"
    # 2026-07-19: 历史日长 TTL (7 天),保证回测拿到一致数据
    from datetime import datetime as _dt
    _is_today = (date_str is None) or (date_str == _dt.now().strftime("%Y%m%d"))
    ttl = cfg.CACHE_TTL_INTRADAY if _is_today else 7 * 24 * 3600
    if not force:
        cached = _cache_load_df(cache_key, ttl)
        if cached is not None:
            return cached

    # 主
    t0 = systime.time()
    df = None
    try:
        if hasattr(lc, "fetch_intraday_min"):
            df = lc.fetch_intraday_min(code, period="1")
    except Exception as e:
        log.debug(f"fetch_intraday_min {code} 失败: {e}")

    # 备 1: akshare
    if df is None or df.empty:
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist_min_em(symbol=code, period="1", adjust="qfq")
        except Exception as e:
            log.debug(f"akshare hist_min {code} 失败: {e}")

    elapsed = systime.time() - t0
    if df is None or (hasattr(df, "empty") and df.empty):
        log.warning(f"[{code}] 分时获取失败（{elapsed:.1f}s）")
        return None

    _cache_save_df(cache_key, df, ttl=ttl)
    return df


# ═══════════════════════════════════════════════════
# 实时行情（用于当日实时选股）
# ═══════════════════════════════════════════════════
def fetch_realtime_snapshot(code: str) -> dict | None:
    """单只实时：最新价 / 涨跌幅 / 换手 / 成交额 / 总市值 / 流通市值
    2026-07-11: 走 Redis K.QUOTE (TTL 5s)
    """
    cache_key = f"quote:{code}"
    cached = _store.get(cache_key)
    if cached:
        return cached
    rt = lc.fetch_realtime(code)
    if not rt:
        return None
    _store.set(cache_key, rt, ttl=5)
    return rt


def fetch_spot_realtime() -> list[dict]:
    """全市场实时行情快照（仅在手动选股时调用，避免频繁抓取）"""
    raw = msf.fetch_spot_a(exclude_bjs=True, exclude_st=False) or []
    # raw 是 [(code, name)]，需要补全字段
    return [{"code": c, "name": n} for c, n in raw]


# ═══════════════════════════════════════════════════
# 板块数据
# ═══════════════════════════════════════════════════
def fetch_sector_industries() -> list[dict]:
    """全市场行业板块列表"""
    cache_key = "sector_industries"
    cached = _cache_load(cache_key, cfg.CACHE_TTL_FUNDAMENTAL)
    if cached:
        return cached
    data = msf.fetch_sector_industries() or []
    _cache_save(cache_key, data)
    return data


def fetch_sector_constituents(sector_code: str, sector_name: str = "") -> list[tuple[str, str]]:
    return lc.fetch_sector_constituents(sector_code, sector_name) or []


# ═══════════════════════════════════════════════════
# 涨停 / 炸板 / 连板 / 封单
# ═══════════════════════════════════════════════════
def fetch_limit_up_pool(date_str: str | None = None) -> list[dict]:
    """涨停池（含涨停价、连板数、封单金额、炸板次数等）

    3 种逃生:
      1) 同包 multi_source_fetchers.fetch_zt_pool       — 主
      2) akshare.stock_zt_pool_em(date)               — 备 1
      3) 返回空列表                                    — 兜底
    """
    today = date_str or datetime.now().strftime("%Y%m%d")
    # 主
    try:
        from . import multi_source_fetchers as msf
        return msf.fetch_zt_pool(today) or []
    except Exception:
        pass
    # 备 1
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=today)
        if df is not None and not df.empty:
            out = []
            for _, r in df.iterrows():
                out.append({
                    "code": str(r.get("代码", "")).zfill(6),
                    "name": str(r.get("名称", "")),
                    "streak": int(r.get("连板数", 1) or 1),
                    "limit_price": float(r.get("最新价", 0) or 0),
                })
            return out
    except Exception:
        pass
    return []


# ═══════════════════════════════════════════════════
# 交易日历
# ═══════════════════════════════════════════════════
def fetch_trade_dates(start: str, end: str) -> list[str]:
    """返回 [YYYYMMDD, ...] 升序（过滤 start/end 范围内 + ≤今日）

    2 种逃生:
      1) 同包 multi_source_fetchers.fetch_trade_dates
      2) akshare 工具交易日工具

    注：msf 返回 YYYY-MM-DD 格式，本函数统一转 YYYYMMDD 输出。
    """
    try:
        from . import multi_source_fetchers as msf
        dates = msf.fetch_trade_dates()
    except Exception:
        try:
            import akshare as ak
            dates = ak.tool_trade_date_hist_sina()["date"].dt.strftime("%Y%m%d").tolist()
        except Exception:
            dates = []
    if isinstance(dates, set):
        dates = sorted(dates)
    elif isinstance(dates, list):
        dates = sorted(set(dates))

    norm = []
    for d in dates:
        s = str(d).replace("-", "")[:8]
        if len(s) == 8 and s.isdigit():
            norm.append(s)
    norm = sorted(set(norm))

    s_start = start.replace("-", "")[:8]
    s_end = end.replace("-", "")[:8]
    today = datetime.now().strftime("%Y%m%d")
    upper = min(s_end, today)
    return [d for d in norm if s_start <= d <= upper]


# ═══════════════════════════════════════════════════
# 批量拉取（日线）— 回测用
# ═══════════════════════════════════════════════════
def batch_fetch_daily(codes: list[str], days: int = 250, progress_every: int = 50) -> dict[str, pd.DataFrame]:
    """批量拉日线，结果以 {code: df} 返回。回测预热用。"""
    out: dict[str, pd.DataFrame] = {}
    for i, c in enumerate(codes):
        df = fetch_daily(c, days=days, force=False)
        if df is not None and not df.empty:
            out[c] = df
        if (i + 1) % progress_every == 0:
            log.info(f"  batch 日线进度 {i+1}/{len(codes)} 命中 {len(out)}")
    log.info(f"batch_fetch_daily 完成：请求 {len(codes)} 命中 {len(out)}")
    return out


# ═══════════════════════════════════════════════════
# 健康检查
# ═══════════════════════════════════════════════════
def data_health_check() -> dict:
    """对外暴露：用于 run_stock_screen 前的故障预判"""
    health = {"daily_ok": False, "spot_ok": False, "sector_ok": False, "ts": datetime.now().isoformat()}
    try:
        df = fetch_daily("000001", days=30, force=True)
        health["daily_ok"] = df is not None and len(df) > 10
    except Exception as e:
        health["daily_err"] = str(e)
    try:
        sp = msf.fetch_spot_a(exclude_bjs=True, exclude_st=True)
        health["spot_ok"] = bool(sp) and len(sp) > 100
    except Exception as e:
        health["spot_err"] = str(e)
    try:
        si = msf.fetch_sector_industries()
        health["sector_ok"] = bool(si) and len(si) > 30
    except Exception as e:
        health["sector_err"] = str(e)
    log.info(f"健康检查: {health}")
    return health