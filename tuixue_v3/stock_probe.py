#!/usr/bin/env python3
"""
stock_probe.py - 快速诊断股票数据源存活状态
用法: python3 stock_probe.py
用途: pipeline 数据全挂时 30s 定位是哪个源挂了
2026-08-05 创建: 腾讯/东财/akshare 三源同时被封后,需要快速识别
"""
from __future__ import annotations
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

import requests

# 测试代码: sh600519 (贵州茅台)
TEST_CODE = "600519"
TEST_DAYS = 5
TIMEOUT = 4.0


@dataclass
class Probe:
    name: str
    fn: Callable[[], tuple[bool, str, float]]  # (ok, info, elapsed)


def probe_tencent_web() -> tuple[bool, str, float]:
    """主源: 腾讯 K 线 (web.ifzq.gtimg.cn)"""
    t0 = time.time()
    try:
        r = requests.get(
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh{TEST_CODE},day,,,{TEST_DAYS},qfq",
            timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
        )
        elapsed = time.time() - t0
        if r.status_code == 200 and r.json().get("code") == 0:
            return True, f"200 OK ({len(r.text)} bytes)", elapsed
        return False, f"HTTP {r.status_code} body[:50]={r.text[:50]!r}", elapsed
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", time.time() - t0


def probe_tencent_qt() -> tuple[bool, str, float]:
    """备用: 腾讯简易接口 (qt.gtimg.cn) - 实时报价"""
    t0 = time.time()
    try:
        r = requests.get(f"https://qt.gtimg.cn/q=sh{TEST_CODE}", timeout=TIMEOUT)
        elapsed = time.time() - t0
        if r.status_code == 200 and "v_sh" in r.text:
            return True, "200 OK (real-time quote)", elapsed
        return False, f"HTTP {r.status_code}", elapsed
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", time.time() - t0


def probe_em_push2() -> tuple[bool, str, float]:
    """主源: 东财 push2 K 线"""
    t0 = time.time()
    try:
        r = requests.get(
            f"https://push2.eastmoney.com/api/qt/stock/kline/get?secid=1.{TEST_CODE}&fields1=f1,f2&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&end=20500101&lmt={TEST_DAYS}",
            timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
        )
        elapsed = time.time() - t0
        if r.status_code == 200:
            return True, f"200 OK ({len(r.text)} bytes)", elapsed
        return False, f"HTTP {r.status_code}", elapsed
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", time.time() - t0


def probe_sina_hq() -> tuple[bool, str, float]:
    """实时: sina hq"""
    t0 = time.time()
    try:
        r = requests.get(
            f"https://hq.sinajs.cn/list=sh{TEST_CODE}",
            timeout=TIMEOUT, headers={"Referer": "https://finance.sina.com.cn/"}
        )
        elapsed = time.time() - t0
        if r.status_code == 200 and len(r.text) > 50:
            return True, "200 OK (real-time)", elapsed
        return False, f"HTTP {r.status_code}", elapsed
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", time.time() - t0


def probe_sina_history() -> tuple[bool, str, float]:
    """历史 K: sina money.finance"""
    t0 = time.time()
    try:
        r = requests.get(
            f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sh{TEST_CODE}&scale=240&ma=no&datalen={TEST_DAYS}",
            timeout=TIMEOUT
        )
        elapsed = time.time() - t0
        if r.status_code == 200 and r.text.startswith("["):
            return True, f"200 OK ({len(r.text)} bytes, JSON array)", elapsed
        return False, f"HTTP {r.status_code} body[:80]={r.text[:80]!r}", elapsed
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", time.time() - t0


def probe_akshare() -> tuple[bool, str, float]:
    """akshare 间接调用 (依赖 datacenter-web)"""
    t0 = time.time()
    try:
        r = requests.get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_DMSK_TS_STOCKTYPE&columns=SECUCODE%2CSECURITY_TYPE_CODE%2CSECURITY_TYPE_NAME&pageNumber=1&pageSize=10",
            timeout=TIMEOUT
        )
        elapsed = time.time() - t0
        if r.status_code == 200:
            return True, f"200 OK (datacenter)", elapsed
        return False, f"HTTP {r.status_code}", elapsed
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", time.time() - t0


def probe_baostock() -> tuple[bool, str, float]:
    """baostock (合规兜底)"""
    t0 = time.time()
    try:
        r = requests.get("https://baostock.com/baostock/index.html", timeout=TIMEOUT)
        elapsed = time.time() - t0
        if r.status_code == 200:
            return True, f"200 OK", elapsed
        return False, f"HTTP {r.status_code}", elapsed
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", time.time() - t0


def probe_xueqiu() -> tuple[bool, str, float]:
    """雪球 (经常 403)"""
    t0 = time.time()
    try:
        r = requests.get(
            f"https://stock.xueqiu.com/v5/stock/chart/kline.json?symbol=SH{TEST_CODE}&begin={int(time.time())-86400*TEST_DAYS}&period=day&type=before&count=-{TEST_DAYS}&indicator=kline",
            timeout=TIMEOUT
        )
        elapsed = time.time() - t0
        if r.status_code == 200:
            return True, f"200 OK", elapsed
        return False, f"HTTP {r.status_code}", elapsed
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", time.time() - t0


def probe_yahoo() -> tuple[bool, str, float]:
    """yahoo finance (境外)"""
    t0 = time.time()
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v7/finance/download/{TEST_CODE}.SS",
            timeout=TIMEOUT
        )
        elapsed = time.time() - t0
        if r.status_code == 200:
            return True, f"200 OK", elapsed
        return False, f"HTTP {r.status_code}", elapsed
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", time.time() - t0


PROBES = [
    Probe("tencent_web", probe_tencent_web),       # pipeline 主源
    Probe("tencent_qt", probe_tencent_qt),         # 实时报价
    Probe("em_push2", probe_em_push2),             # pipeline 备 1
    Probe("sina_hq", probe_sina_hq),               # 实时
    Probe("sina_history", probe_sina_history),     # 历史 K
    Probe("akshare", probe_akshare),               # pipeline 备 2
    Probe("baostock", probe_baostock),             # 合规兜底
    Probe("xueqiu", probe_xueqiu),
    Probe("yahoo", probe_yahoo),
]


def main() -> int:
    print(f"=== 股票数据源存活诊断 (测试代码 sh{TEST_CODE}, {TEST_DAYS} 天) ===\n")
    with ThreadPoolExecutor(max_workers=len(PROBES)) as ex:
        futures = {ex.submit(p.fn): p for p in PROBES}
        results = []
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                ok, info, elapsed = fut.result()
            except Exception as e:
                ok, info, elapsed = False, f"probe exception: {e}", 0.0
            results.append((p.name, ok, info, elapsed))
    # 排序: ok 在前
    results.sort(key=lambda x: (not x[1], x[0]))
    n_alive = sum(1 for r in results if r[1])
    for name, ok, info, elapsed in results:
        flag = "✅" if ok else "❌"
        print(f"{flag}  {name:15s}  {elapsed:5.2f}s  {info}")
    print(f"\n存活: {n_alive}/{len(results)}")
    return 0 if n_alive >= 2 else 1  # 至少要有 2 个源


if __name__ == "__main__":
    sys.exit(main())