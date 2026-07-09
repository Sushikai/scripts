#!/usr/bin/env python3
"""
tuixue_screener/sector_history.py
历史板块数据接口（绕过 akshare 限流）。

方案：
- 主：东方财富 push2 BK 板块 K 线（web.ifzq 或 push2delay）
- 备：用涨停池 + 涨幅榜反推当日"强势板块"
- 兜底：用全市场 K 线反推（间接法）
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("sector_history")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ROOT = Path(__file__).parent
CACHE = ROOT / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


def _http_get(url, params=None, timeout=10, retries=2):
    last_err = ""
    for i in range(retries):
        try:
            r = requests.get(url, params=params or {}, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:60]}"
            if i < retries - 1:
                time.sleep(2 ** i)
    log.debug("HTTP 失败 %s: %s", url[:60], last_err)
    return None


def _cache_path(name: str) -> Path:
    return CACHE / f"{name}.json"


def _load_cache(name: str, ttl_sec: int) -> Any:
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


def _save_cache(name: str, data: Any):
    try:
        _cache_path(name).write_text(json.dumps(
            {"ts": time.time(), "data": data},
            ensure_ascii=False, default=str))
    except Exception as e:
        log.warning("缓存写入失败 %s: %s", name, e)


# ════════════════════════════════════════════════════════════
# 1. 板块列表（沪深行业板块）
# ════════════════════════════════════════════════════════════
def fetch_sector_list() -> list[dict]:
    """获取所有行业板块列表（带 sector_code）"""
    cache_name = "sector_list_v2"
    cached = _load_cache(cache_name, 7 * 86400)
    if cached is not None:
        return cached

    url = "https://push2delay.eastmoney.com/api/qt/clist/get"
    all_rows = []
    for pn in range(1, 10):
        params = {
            "pn": pn, "pz": 50, "po": 1, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2, "invt": 2, "fid": "f3",
            "fs": "m:90+t:2",  # 行业板块
            "fields": "f1,f2,f3,f4,f12,f14,f20,f128",
        }
        data = _http_get(url, params=params, retries=3)
        if not data or "data" not in data:
            break
        diff = data["data"].get("diff", []) or []
        if not diff:
            break
        all_rows.extend(diff)
        if len(diff) < 50:
            break

    if all_rows:
        # 标准化：每个板块的代码 + 名称 + 当前涨幅
        sectors = []
        for r in all_rows:
            code = str(r.get("f12", "")).strip()
            if not code.startswith("BK"):
                continue
            sectors.append({
                "sector_code": code,
                "sector_name": r.get("f14", ""),
                "change_pct": r.get("f3", 0) or 0,
                "fund_flow": r.get("f20", 0) or 0,
            })
        if sectors:
            _save_cache(cache_name, sectors)
            return sectors
    return []


# ════════════════════════════════════════════════════════════
# 2. 板块日 K 线（push2 BK 一次性拉完整历史）
# ════════════════════════════════════════════════════════════
def fetch_sector_kline(sector_code: str, days: int = 365) -> list[dict] | None:
    """获取某板块日 K 线（带缓存）"""
    cache_name = f"sector_kline_{sector_code}_{days}"
    cached = _load_cache(cache_name, 7 * 86400)
    if cached is not None:
        return cached

    # push2 BK 板块 K 线
    # secid 格式：BK + 数字
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    secid = f"90.{sector_code.replace('BK', '')}"  # push2 BK 用 90.BKxxxx 形式

    params = {
        "secid": secid,
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": 101, "fqt": 1, "end": "20500101",
        "lmt": days,
    }
    data = _http_get(url, params=params, retries=3)
    if not data or "data" not in data:
        return None

    klines_raw = data["data"].get("klines", []) or []
    if not klines_raw:
        return None

    out = []
    for line in klines_raw:
        parts = line.split(",")
        if len(parts) < 11:
            continue
        try:
            out.append({
                "date": parts[0],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": float(parts[5]),
                "amount": float(parts[6]),
                "amplitude": float(parts[7]),
                "change_pct": float(parts[8]),
            })
        except (ValueError, IndexError):
            continue

    if out:
        _save_cache(cache_name, out)
        return out
    return None


# ════════════════════════════════════════════════════════════
# 3. 板块成分股（一次性拉全）
# ════════════════════════════════════════════════════════════
def fetch_sector_constituents(sector_code: str) -> list[str]:
    """板块成分股代码列表"""
    cache_name = f"sector_cons_{sector_code}"
    cached = _load_cache(cache_name, 7 * 86400)
    if cached is not None:
        return cached

    url = "https://push2delay.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1, "pz": 200, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2, "fid": "f3",
        "fs": f"b:{sector_code}+f:!50",
        "fields": "f12,f14",
    }
    data = _http_get(url, params=params, retries=3)
    if not data or "data" not in data:
        return []

    diff = data["data"].get("diff", []) or []
    codes = [str(r.get("f12", "")).zfill(6) for r in diff if r.get("f12")]
    if codes:
        _save_cache(cache_name, codes)
    return codes


# ════════════════════════════════════════════════════════════
# 4. 批量拉取所有板块的 K 线（用于回测）
# ════════════════════════════════════════════════════════════
def fetch_all_sector_klines(days: int = 365, max_workers: int = 4) -> dict[str, list[dict]]:
    """
    批量拉取所有行业板块的日 K 线。
    返回 {sector_code: [klines]}
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cache_file = CACHE / f"all_sector_klines_{days}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            log.info(f"从缓存加载 {len(data)} 个板块 K 线")
            return data
        except Exception:
            pass

    sectors = fetch_sector_list()
    log.info(f"行业板块总数: {len(sectors)}")

    out = {}
    completed = [0]
    failed = [0]

    def _fetch_one(s):
        code = s["sector_code"]
        klines = fetch_sector_kline(code, days)
        return code, klines

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, s): s["sector_code"] for s in sectors}
        for fut in as_completed(futures):
            code, klines = fut.result()
            completed[0] += 1
            if klines:
                out[code] = klines
            else:
                failed[0] += 1
            if completed[0] % 20 == 0:
                log.info(f"  进度: {completed[0]}/{len(sectors)} (成功 {len(out)})")

    log.info(f"板块 K 线拉取完成: 成功 {len(out)}, 失败 {failed[0]}")
    cache_file.write_text(json.dumps(out, ensure_ascii=False, default=str))
    return out


# ════════════════════════════════════════════════════════════
# 5. 给定日期，识别当日主线（涨幅 ≥ N 只 / 涨幅前 X）
# ════════════════════════════════════════════════════════════
def identify_mainline_at_date(all_sectors_kline: dict[str, list[dict]],
                              target_date: str,
                              min_change_pct: float = 1.0,
                              top_n: int = 3) -> list[dict]:
    """
    在给定日期识别主线板块：
    1. 当日涨幅 ≥ 1% 的板块
    2. 按涨幅排序，取前 N
    返回 [{sector_code, sector_name, change_pct}, ...]
    """
    mainlines = []
    for code, klines in all_sectors_kline.items():
        # 找 target_date 当天或之前的最后一根 K 线
        match = None
        for k in klines:
            if k["date"] <= target_date:
                match = k
            else:
                break
        if not match:
            continue
        chg = match.get("change_pct", 0)
        if chg >= min_change_pct:
            mainlines.append({
                "sector_code": code,
                "sector_name": "",  # 暂时缺，加载时回填
                "change_pct": chg,
                "date": match["date"],
            })

    mainlines.sort(key=lambda x: x["change_pct"], reverse=True)
    return mainlines[:top_n]


if __name__ == "__main__":
    print("=== 板块列表 ===")
    sectors = fetch_sector_list()
    print(f"板块数: {len(sectors)}")
    print(f"前 5: {sectors[:5]}")

    print("\n=== 单板块 K 线测试 (BK0420) ===")
    klines = fetch_sector_kline("BK0420", 30)
    print(f"BK0420: {len(klines) if klines else 0} 条")
    if klines:
        print(f"  首条: {klines[0]}")
        print(f"  末条: {klines[-1]}")

    print("\n=== 板块成分股测试 (BK0420) ===")
    cons = fetch_sector_constituents("BK0420")
    print(f"BK0420 成分股: {len(cons)} 只, 示例: {cons[:5]}")