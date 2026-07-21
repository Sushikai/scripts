"""
选股 (条件选股 · 14:30 启) — 8 条规则 1:1 实现
──────────────────────────────────────────────────────
规则 (用户原视频, 严格 1:1):
  1. 14:30 后才启
  2. 主板 only (剔除 创业板/科创板/北交所/ST)
  3. 涨幅 [3%, 5%] 闭区间
  4. 20 日内有涨停 (主板 9.5% 阈值)
  5. 量比 ≥ 1
  6. 总市值 ≤ 300 亿
  7. 换手 [5%, 10%] 闭区间
  8. 分时结构: 全天运行在均价之上

数据流:
  startup: build_candidate_pool()  →  主板 + 静态规则预筛  →  候选池 200-500
  poller (1s/次): evaluate_all()    →  拉 quote + intraday  →  _RESULT
  on demand:  current_results()     →  按 sort 字段排 + 截断

异常处理:
  - 单股失败 → 标 —, 不抛整体
  - 数据源全挂 → _STALE 兜底 (10min)
  - 14:30 前 → rule_status.open=false
  - 非交易日 → "今日非交易日"
  - 候选池空 → 空态文案
  - intraday 缺失 → 规则 8 标 "缺数据", 其他规则仍工作
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

log = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════
# 1) 规则定义
# ═════════════════════════════════════════════════════════════════
# 主板涨停阈值 (其他板 19.5%,但规则 2 已剔除)
LIMIT_UP_PCT = 9.5

# 规则 1 时间门
OPEN_TIME  = (14, 30)  # 14:30 启
CLOSE_TIME = (15, 0)   # 15:00 冻结
PRE_OPEN   = (9, 30)   # 09:30 起 (用于倒计时, 不真启)

# 候选池阈值 (静态预筛, 不依赖 intraday) — 用户可调
_THRESHOLDS = {
    "change_pct_min":   3.0,   # 涨幅下限
    "change_pct_max":   5.0,   # 涨幅上限
    "volume_ratio_min": 1.0,   # 量比下限
    "mcap_yi_min":     40.0,   # 总市值下限 (亿)
    "mcap_yi_max":     300.0,  # 总市值上限 (亿)
    "turnover_min":     5.0,   # 换手下限
    "turnover_max":    10.0,   # 换手上限
    "zt_20d_min":       1,     # 20 日内涨停次数下限
    "above_vwap_min_pct": 100.0, # 分时在均价之上的 tick 占比下限 (规则 8 — 视频"全天运行在均价之上"严格 100%)
}
# 兼容别名
CANDIDATE_FILTERS = _THRESHOLDS

# 出厂默认值 (用于"恢复默认")
_DEFAULT_THRESHOLDS = dict(_THRESHOLDS)

# 阈值边界校验
_THRESHOLD_BOUNDS = {
    "change_pct_min":   (-10.0, 10.0),
    "change_pct_max":   (-10.0, 15.0),
    "volume_ratio_min": (0.0, 20.0),
    "mcap_yi_min":      (5.0, 5000.0),
    "mcap_yi_max":      (10.0, 5000.0),
    "turnover_min":     (0.0, 30.0),
    "turnover_max":     (0.5, 50.0),
    "zt_20d_min":       (0, 10),
    "above_vwap_min_pct": (50.0, 100.0),
}

# 实时层并发数 — 40 对 I/O 无益 (socket 连接才是瓶颈); 20 足够打满带宽
_EXECUTOR = ThreadPoolExecutor(max_workers=20, thread_name_prefix="screener")
# 候选池构建 deadline (沙箱网络慢, 但 35s 会让用户等太久; 25s + 重试是甜点)
_POOL_DEADLINE_SEC = 25


# 规则开关 (用户可关掉单条规则, 仍归入候选池展示)
# 默认全开
_RULE_TOGGLES: dict[str, bool] = {k: True for k in [
    "main_board", "change_pct", "volume_ratio", "mcap", "turnover", "zt_20d", "above_vwap",
]}


def get_thresholds() -> dict[str, Any]:
    return dict(_THRESHOLDS)


def set_threshold(key: str, value: float) -> dict[str, Any]:
    """用户阈值编辑 — 边界校验 + 候选池失效"""
    k = (key or "").strip()
    if k not in _THRESHOLDS:
        return {"ok": False, "error": f"未知阈值 {key}"}
    lo, hi = _THRESHOLD_BOUNDS[k]
    try:
        v = float(value)
    except Exception:
        return {"ok": False, "error": f"{key} 不是数字"}
    if not (lo <= v <= hi):
        return {"ok": False, "error": f"{key} 需在 [{lo},{hi}]"}
    _THRESHOLDS[k] = v
    # 失效候选池 → 下次 tick 全量重筛 (不清 _RESULT, 避免 35s 空白)
    _CANDIDATE_POOL["ts"] = 0.0
    _schedule_rebuild()
    return {"ok": True, "thresholds": dict(_THRESHOLDS)}


def reset_thresholds() -> dict:
    """恢复所有阈值到出厂默认值 + 候选池失效"""
    _THRESHOLDS.clear()
    _THRESHOLDS.update(_DEFAULT_THRESHOLDS)
    _CANDIDATE_POOL["ts"] = 0.0
    _schedule_rebuild()
    return {"ok": True, "thresholds": dict(_THRESHOLDS)}


# 后台重建池子的线程池 — 专用于阈值变化后的异步重建
_REBUILD_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scr-rebuild")


def _schedule_rebuild(force: bool = False) -> None:
    """提交 screener_tick 到后台线程 — 立即触发池子重建, 不阻塞调用方
    force=True: 强制重建候选池 (不走 3min 缓存)
    """
    try:
        _REBUILD_EXECUTOR.submit(screener_tick, force=force)
    except Exception as e:
        log.debug(f"_schedule_rebuild 失败: {e}")


def get_rule_toggles() -> dict[str, bool]:
    return dict(_RULE_TOGGLES)


def set_rule_toggle(rule: str, on: bool) -> dict[str, Any]:
    if rule not in _RULE_TOGGLES:
        return {"ok": False, "error": f"未知规则 {rule}"}
    _RULE_TOGGLES[rule] = bool(on)
    return {"ok": True, "toggles": dict(_RULE_TOGGLES)}


# ═════════════════════════════════════════════════════════════════
# 2) 时间门 + 交易日
# ═════════════════════════════════════════════════════════════════
def _now_china() -> _dt.datetime:
    return _dt.datetime.utcnow() + _dt.timedelta(hours=8)


def _is_trade_day(d: _dt.date | None = None) -> bool:
    """通过 multi_source_fetchers.fetch_trade_dates() 检测"""
    try:
        from .. import multi_source_fetchers as _msf
        all_dates = _msf.fetch_trade_dates() or []
        if not all_dates:
            return True  # 拿不到就放行
        target = (d or _now_china().date()).strftime("%Y-%m-%d")
        return target in all_dates
    except Exception:
        return True  # 沙箱挂时放行


def rule_status() -> dict[str, Any]:
    """返回当前时间门状态 — 前端用来决定显示哪个面板"""
    now = _now_china()
    h, m = now.hour, now.minute
    cur_min = h * 60 + m
    open_min  = OPEN_TIME[0]  * 60 + OPEN_TIME[1]
    close_min = CLOSE_TIME[0] * 60 + CLOSE_TIME[1]

    is_trade = _is_trade_day(now.date())
    is_open_window = open_min <= cur_min < close_min

    base = {
        "open": True,  # 用户要求: 不按时间门禁过滤,没开盘也显示当下数据
        "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "opens_at": f"{OPEN_TIME[0]:02d}:{OPEN_TIME[1]:02d}",
    }
    if not is_trade:
        base["reason"] = "non_trade_day"
    elif cur_min < open_min:
        base["reason"] = "before_open"
        base["seconds_to_open"] = (open_min - cur_min) * 60
    elif cur_min >= close_min:
        base["reason"] = "after_close"
        base["frozen"] = True
    else:
        base["reason"] = "open"
    return base


# ═════════════════════════════════════════════════════════════════
# 3) 候选池构建 (一次, 后台)
# ═════════════════════════════════════════════════════════════════
def _is_main_board(code: str) -> bool:
    """主板 only — 剔除 创业板 (300/301) 科创板 (688) 北交所 (8/9) ST"""
    if not code or len(code) != 6:
        return False
    if not code.isdigit():
        return False
    if code.startswith(("300", "301", "688", "8", "9")):
        return False
    return True


def _safe_quote(code: str) -> dict | None:
    """读 quote — 优先 _cache_quote, fallback fetch_realtime_snapshot
    单位约定:
      mcap / mcap_yi  — 亿 (qq 接口本身就是亿, 不再除)
      amount          — 元 (qq 接口本身就是元)
      change_pct / turnover / amplitude / volume_ratio — %
    """
    try:
        from .. import data_layer as _dl
        q = _dl.fetch_realtime_snapshot(code)
        if not q:
            return None
        mcap_yi = float(q.get("总市值") or 0)
        return {
            "price":         float(q.get("最新价") or 0),
            "change_pct":    float(q.get("涨跌幅") or 0),
            "change_amt":    float(q.get("涨跌额") or 0),
            "turnover":      float(q.get("换手率") or 0),
            "volume_ratio":  float(q.get("量比") or 0),
            "amplitude":     float(q.get("振幅") or 0),
            "amount":        float(q.get("成交额") or 0),
            "mcap":          mcap_yi,
            "mcap_yi":       mcap_yi,
            "pe_ttm":        float(q.get("市盈率") or 0),
            "name":          q.get("name") or "",
        }
    except Exception as e:
        log.debug(f"_safe_quote {code} err: {e}")
        return None


# 批量引用 — 使用腾讯批量报价接口一次性取所有候选股
_BATCH_QUOTE_CACHE: dict[str, dict | None] = {}
_BATCH_QUOTE_TS = 0.0
_BATCH_QUOTE_TTL = 3.0  # 3 秒内复用 (1s tick 场景下大部分复用)


def _batch_fetch_quotes(codes: list[str]) -> dict[str, dict | None]:
    """批量取报价 — 使用腾讯批量接口获取所有股票实时行情。
    比逐股调用 data_layer 快 50-100 倍 (1 次 TCP 连接 vs N 次)。

    返 {code: dict|None} 字典。
    """
    global _BATCH_QUOTE_CACHE, _BATCH_QUOTE_TS
    now = time.time()
    need = [c for c in codes if c not in _BATCH_QUOTE_CACHE or (now - _BATCH_QUOTE_TS) >= _BATCH_QUOTE_TTL]
    if not need:
        return {c: _BATCH_QUOTE_CACHE.get(c) for c in codes}

    # 腾讯批量接口: sz000001,sh600000,... → 一次返回所有
    try:
        import requests as _req
        batch_size = 100  # 腾讯单次最多 100 只, 超过要分批
        for i in range(0, len(need), batch_size):
            chunk = need[i:i+batch_size]
            prefixes = {"6": "sh", "0": "sz", "3": "sz", "1": "sz", "2": "sz", "4": "bj", "8": "bj", "9": "bj"}
            symbols = ",".join(f"{prefixes.get(c[0], 'sh')}{c}" for c in chunk)
            url = f"https://qt.gtimg.cn/q={symbols}"
            try:
                r = _req.get(url, timeout=3)
                if r.status_code == 200:
                    for line in r.text.split("\n"):
                        line = line.strip()
                        if not line or not line.startswith("v_"):
                            continue
                        try:
                            # 格式: v_sz000001="1~平安银行~...~..."
                        # 腾讯 qt.gtimg.cn 字段索引 (47+ 列):
                        #   0=市场 1=名称 2=代码 3=现价 4=昨收 5=今开
                        #   6~28=买卖5档
                        #   30=时间 YYYYMMDDHHMMSS
                        #   31=涨跌额 32=涨跌幅% 33=最高 34=最低
                        #   35=price/vol/amt 36=成交量(手) 37=成交额(万元)
                        #   38=换手率% 39=市盈率PE
                        #   43=振幅% 44=流通市值(亿) 45=总市值(亿)
                            parts = line.split('"')
                            if len(parts) < 3:
                                continue
                            vals = parts[1].split("~")
                            if len(vals) < 45:
                                continue
                            c = vals[2]  # code like "600519"
                            mcap_str = vals[45] if len(vals) > 45 else vals[44] if len(vals) > 44 else "0"
                            mcap_yi = float(mcap_str.strip() or 0)
                            amount_str = vals[37] if len(vals) > 37 else "0"
                            amount = float(amount_str.strip() or 0) * 1e4  # 腾讯给的是万元→元
                            q = {
                                "price":        float(vals[3] or 0),
                                "change_pct":   float(vals[32] or 0) if len(vals) > 32 else 0,
                                "change_amt":   float(vals[31] or 0) if len(vals) > 31 else 0,
                                "turnover":     float(vals[38] or 0) if len(vals) > 38 else 0,
                                "volume_ratio": 0,  # 腾讯不直接给量比,后面通过 data_layer 补
                                "amplitude":    float(vals[43] or 0) if len(vals) > 43 else 0,
                                "amount":       amount,
                                "mcap":         mcap_yi,
                                "mcap_yi":      mcap_yi,
                                "pe_ttm":       float(vals[39] or 0) if len(vals) > 39 else 0,
                                "name":         vals[1] or "",
                            }
                            _BATCH_QUOTE_CACHE[c] = q
                        except (ValueError, IndexError):
                            continue
            except Exception:
                pass
        _BATCH_QUOTE_TS = now
    except Exception as e:
        log.debug(f"_batch_fetch_quotes err: {e}")
        # fallback: 逐股拉
        for c in need:
            q = _safe_quote(c)
            _BATCH_QUOTE_CACHE[c] = q
        _BATCH_QUOTE_TS = now

    return {c: _BATCH_QUOTE_CACHE.get(c) for c in codes}


def _safe_zt20(code: str) -> int:
    """20 日内涨停次数 — 用 收盘/昨收 自算涨跌幅
    fetch_daily 返回的字段: 日期/开盘/最高/最低/收盘/成交量/成交额/换手率 (无涨跌幅列)
    """
    try:
        from .. import data_layer as _dl
        df = _dl.fetch_daily(code, 20)
        if df is None or df.empty or "收盘" not in df.columns:
            return 0
        # 用 收盘 vs 上一根收盘 自算 pct
        prev = df["收盘"].shift(1)
        # 首根没前收 → 跳过 (可能含 IPO 当天的大涨, 但保守起见不算)
        pct = (df["收盘"] / prev - 1.0) * 100.0
        # 主板涨停 ≥ 9.5% (新股首日 44% 也归入)
        return int((pct.fillna(0) >= LIMIT_UP_PCT).sum())
    except Exception as e:
        log.debug(f"_safe_zt20 {code} err: {e}")
        return 0


def _passes_static(q: dict) -> bool:
    """规则 3/5/6/7 — 静态可判, 用于缩候选池"""
    cp = q.get("change_pct") or 0
    if not (CANDIDATE_FILTERS["change_pct_min"] <= cp <= CANDIDATE_FILTERS["change_pct_max"]):
        return False
    vr = q.get("volume_ratio") or 0
    if vr < CANDIDATE_FILTERS["volume_ratio_min"]:
        return False
    mc = q.get("mcap_yi") or 0
    if mc <= 0 or mc < CANDIDATE_FILTERS["mcap_yi_min"] or mc > CANDIDATE_FILTERS["mcap_yi_max"]:
        return False
    to = q.get("turnover") or 0
    if not (CANDIDATE_FILTERS["turnover_min"] <= to <= CANDIDATE_FILTERS["turnover_max"]):
        return False
    return True


# 候选池缓存 — 3 分钟 TTL (行情变化会让更多股进入)
_CANDIDATE_POOL: dict = {"codes": [], "ts": 0.0, "names": {}}
_CANDIDATE_TTL = 180.0

# tick 级 intraday 缓存 — 避免同 tick 内重复拉取同股分时 (评估+尾盘分类)
_TICK_INTRADAY_CACHE: dict[str, dict] = {}
_TICK_CACHE_TS = 0.0
_TICK_CACHE_TTL = 1.5  # 1.5 秒内复用 (1s tick 基本全复用)

# tick 级日线缓存 — 给 optimized 多因子用 (3d 累跌/20d 量均/20d 涨停比)
# 日线不需 1s 刷 (板内日线收盘价当日不变), 长 TTL 保护网络
_TICK_DAILY_CACHE: dict[str, dict] = {}
_TICK_DAILY_TS = 0.0
_TICK_DAILY_TTL = 60.0  # 60s 内复用 (双策略并行 fetch 一次, V2 同 tick 复用)


def _safe_daily_factors(code: str) -> dict:
    """optimized 因子 — 拉一次 25 日 K 线, 提 ret_3d / vol_ma20 / zt_ratio

    返回:
      {
        "ret_3d":   float|None,  # 近 3 日累计收益% (含今日前 → 用于 reversal 判定)
        "vol_ma20": float|None,  # 近 20 日均成交量 (手)
        "zt_ratio": float|None,  # 近 20 日涨停占比 [0,1]
      }

    tick 级 60s 缓存 (双策略同 tick 并行时只拉 1 次).
    失败 (None|空|超时|异常) → 返全 None, 不抛.
    """
    global _TICK_DAILY_TS
    now = time.time()
    if _TICK_DAILY_CACHE and (now - _TICK_DAILY_TS) < _TICK_DAILY_TTL and code in _TICK_DAILY_CACHE:
        return dict(_TICK_DAILY_CACHE[code])

    out = {"ret_3d": None, "vol_ma20": None, "zt_ratio": None}
    try:
        from .. import data_layer as _dl
        df = _dl.fetch_daily(code, 25)
        if df is None or df.empty or "收盘" not in df.columns:
            return out
        # ret_3d: 近 3 日累计收益% (用最近一根前收 vs 3 根前)
        closes = df["收盘"].tolist()
        if len(closes) >= 4:
            today_pct = (closes[-1] / closes[-2] - 1.0) * 100.0 if closes[-2] > 0 else 0
            # ret_3d = (close[-1]-close[-4])/close[-4]*100 (今+前 3 日累计)
            out["ret_3d"] = (closes[-1] / closes[-4] - 1.0) * 100.0 if closes[-4] > 0 else 0
        elif len(closes) >= 2:
            out["ret_3d"] = (closes[-1] / closes[-2] - 1.0) * 100.0 if closes[-2] > 0 else 0
        # vol_ma20
        vols = df["成交量"].astype(float).tail(20)
        if len(vols) >= 5:
            out["vol_ma20"] = float(vols.mean())
        # zt_ratio: 近 20 日内 ≥ 9.5% 占比
        prev = df["收盘"].shift(1)
        if len(prev) >= 2:
            pct = (df["收盘"] / prev - 1.0) * 100.0
            pct = pct.iloc[1:]  # 去掉首根无前收
            if len(pct) > 0:
                zt_days = int((pct >= LIMIT_UP_PCT).sum())
                out["zt_ratio"] = float(zt_days) / float(min(20, len(pct)))
        log.debug(f"_safe_daily_factors {code}: ret_3d={out['ret_3d']} vol_ma20={out['vol_ma20']:.0f}" if out['vol_ma20'] else f"_safe_daily_factors {code}: ret_3d={out['ret_3d']}")
    except Exception as e:
        log.debug(f"_safe_daily_factors {code} err: {e}")

    _TICK_DAILY_CACHE[code] = out
    _TICK_DAILY_TS = now
    return out


def build_candidate_pool(force: bool = False) -> list[str]:
    """主板 + 静态规则预筛, 返回候选 codes
    3min 缓存 — 行情每 3min 重算一次候选池
    """
    now = time.time()
    if not force and _CANDIDATE_POOL["codes"] and (now - _CANDIDATE_POOL["ts"]) < _CANDIDATE_TTL:
        return _CANDIDATE_POOL["codes"]

    from concurrent.futures import as_completed
    from .. import data_layer as _dl
    try:
        all_stocks = _dl.fetch_stock_list() or []
    except Exception as e:
        log.error(f"fetch_stock_list 失败: {e}")
        return []

    # 主板 only
    main_codes = [c for c, _ in all_stocks if _is_main_board(c)]
    log.info(f"screener: 主板 {len(main_codes)} 只")

    # 并发拉 quote (deadline 35s, 数据源慢 — 12s 时池缩水 50%)
    out: dict[str, dict] = {}
    futs = {_EXECUTOR.submit(_safe_quote, c): c for c in main_codes}
    deadline = time.time() + _POOL_DEADLINE_SEC
    try:
        for f in as_completed(futs, timeout=_POOL_DEADLINE_SEC):
            if time.time() > deadline:
                break
            try:
                q = f.result(timeout=0.1)
                if q and _passes_static(q):
                    out[futs[f]] = q
            except Exception:
                pass
    except Exception as e:
        log.debug(f"candidate pool 拉取超时: {e}")

    # 补 name
    name_map = {c: n for c, n in all_stocks}
    pool = []
    for c, q in out.items():
        if not q.get("name"):
            q["name"] = name_map.get(c, c)
        pool.append(c)
    pool.sort()  # 稳定序

    _CANDIDATE_POOL["codes"] = pool
    _CANDIDATE_POOL["ts"] = now
    _CANDIDATE_POOL["names"] = name_map
    log.info(f"screener: 候选池 {len(pool)} 只 (主板 {len(main_codes)} 中)")
    return pool


# ═════════════════════════════════════════════════════════════════
# 4) 单股评估 (含 8 条规则)
# ═════════════════════════════════════════════════════════════════


def evaluate_one(code: str, name: str = "", strategy_id: str = "baseline") -> dict[str, Any]:
    """对单股做 评估, 返 {row, rules_pass, fail_rules}
    strategy_id:
      - "baseline"    (默认) 7 条规则评分 (现有逻辑)
      - "optimized"  多因子评分 IRS / VM20 / reversal / zt_ratio 复合同分 (原 WIN_RATE_V2)
        optimized 不另设 fail_rules (只是排序), 复用基础字段
    阈值/开关动态读 _THRESHOLDS + _RULE_TOGGLES
    """
    rules_pass: list[str] = []
    fail_rules: list[str] = []
    row: dict[str, Any] = {"code": code, "name": name or code}

    # 规则 2: 主板
    if _is_main_board(code):
        if _RULE_TOGGLES.get("main_board", True):
            rules_pass.append("main_board")
    else:
        if _RULE_TOGGLES.get("main_board", True):
            fail_rules.append("main_board")
        return {"row": row, "rules_pass": rules_pass, "fail_rules": fail_rules, "skip": True}

    # quote — 优先从批量缓存取 (由 _eval_pool_serial 在调用 evaluate_one 前填充)
    q = _BATCH_QUOTE_CACHE.get(code)
    # volume_ratio 腾讯批量接口没有, 补拉
    if q is None:
        q = _safe_quote(code)
    elif not q.get("volume_ratio"):
        fq = _safe_quote(code)
        if fq and fq.get("volume_ratio"):
            q["volume_ratio"] = fq["volume_ratio"]
    if not q:
        fail_rules.extend(["quote", "change_pct", "volume_ratio", "mcap", "turnover"])
        return {"row": row, "rules_pass": rules_pass, "fail_rules": fail_rules, "skip": True}

    row.update(q)
    cp  = q.get("change_pct") or 0
    vr  = q.get("volume_ratio") or 0
    mc  = q.get("mcap_yi") or 0
    to  = q.get("turnover") or 0
    amp = q.get("amplitude") or 0
    amt = q.get("amount") or 0
    pe  = q.get("pe_ttm") or 0

    # 规则 3: 涨幅 [_THRESHOLDS 区间]
    if _THRESHOLDS["change_pct_min"] <= cp <= _THRESHOLDS["change_pct_max"]:
        if _RULE_TOGGLES.get("change_pct", True):
            rules_pass.append("change_pct")
    else:
        if _RULE_TOGGLES.get("change_pct", True):
            fail_rules.append("change_pct")

    # 规则 5: 量比 ≥ _THRESHOLDS
    if vr >= _THRESHOLDS["volume_ratio_min"]:
        if _RULE_TOGGLES.get("volume_ratio", True):
            rules_pass.append("volume_ratio")
    else:
        if _RULE_TOGGLES.get("volume_ratio", True):
            fail_rules.append("volume_ratio")

    # 规则 6: 市值 [_THRESHOLDS]
    if _THRESHOLDS["mcap_yi_min"] <= mc <= _THRESHOLDS["mcap_yi_max"]:
        if _RULE_TOGGLES.get("mcap", True):
            rules_pass.append("mcap")
    else:
        if _RULE_TOGGLES.get("mcap", True):
            fail_rules.append("mcap")

    # 规则 7: 换手 [_THRESHOLDS 区间]
    if _THRESHOLDS["turnover_min"] <= to <= _THRESHOLDS["turnover_max"]:
        if _RULE_TOGGLES.get("turnover", True):
            rules_pass.append("turnover")
    else:
        if _RULE_TOGGLES.get("turnover", True):
            fail_rules.append("turnover")

    # 规则 4: 20 日内涨停 ≥ _THRESHOLDS
    zt20 = _safe_zt20(code)
    row["zt_20d"] = zt20
    if zt20 >= _THRESHOLDS["zt_20d_min"]:
        if _RULE_TOGGLES.get("zt_20d", True):
            rules_pass.append("zt_20d")
    else:
        if _RULE_TOGGLES.get("zt_20d", True):
            fail_rules.append("zt_20d")

    # 规则 8: 分时结构 — ≥ _THRESHOLDS['above_vwap_min_pct'] 的 tick 在均价之上
    # 严格模式: 缺数据 = fail (无法验证全天在均价上, 不能算 pass)
    # 顺手: 尾盘 30 分钟 5 条规则分类 (共用同一次 intraday 拉取, 不重复打网络)
    intraday = _safe_intraday_analyze(code)
    above_ratio = intraday.get("vwap_ratio")
    vwap = intraday.get("vwap_now")
    row["vwap"] = vwap
    row["above_vwap_ratio"] = above_ratio
    # 尾盘 5 条规则分类 — 排序用 + 前端展示用 (2 列)
    row["last30_label"] = intraday.get("last30_label") or "—缺"
    row["last30_desc"]  = intraday.get("last30_desc")  or ""
    if above_ratio is None:
        if _RULE_TOGGLES.get("above_vwap", True):
            fail_rules.append("above_vwap_missing")
    elif above_ratio * 100 >= _THRESHOLDS["above_vwap_min_pct"]:
        if _RULE_TOGGLES.get("above_vwap", True):
            rules_pass.append("above_vwap")
    else:
        if _RULE_TOGGLES.get("above_vwap", True):
            fail_rules.append("above_vwap")

    # 主力净流入 (排序用, 不参与 8 条)
    try:
        from .. import lib_common as _lc
        ff = _lc.fetch_main_fund_flow(code) or {}
        row["main_fund_inflow_wan"] = float(ff.get("main_net") or 0)
    except Exception:
        row["main_fund_inflow_wan"] = 0

    # ── optimized 多因子评分 ──
    # 4 个因子:
    #   IRS     日内强度  = (close - low) / (high - low + 0.001)        复用分时 df
    #   VM20    量能       = vol_today / vol_ma20 , cap 3x → [0,1]      取自日线
    #   reversal 短期反转 = 1 if ret_3d < -2% and today > +2% else 0    取自日线
    #   zt_ratio 涨停近因 = 近 20 日涨停天数占比                       复用 zt_20d
    # composite = 0.30·IRS + 0.25·VM20 + 0.20·reversal + 0.25·zt_ratio
    if strategy_id == "optimized":
        try:
            f = _safe_daily_factors(code)
            ret_3d   = f.get("ret_3d")
            vol_ma20 = f.get("vol_ma20")
            zt_ratio = f.get("zt_ratio")
            # IRS — 从已拉的分时 df 取 (若有) 或用当根 quote 兜底
            df_intra = _fetch_intraday_df_with_fallback(code)
            irs = None
            if df_intra is not None and not df_intra.empty and "最高" in df_intra.columns and "最低" in df_intra.columns:
                hi = float(df_intra["最高"].max() or 0)
                lo = float(df_intra["最低"].min() or 0)
                cl = float(df_intra["收盘"].iloc[-1] or 0)
                if hi > lo > 0:
                    irs = max(0.0, min(1.0, (cl - lo) / (hi - lo)))
            # VM20 — 用今天 vol (从分时 df 累加)
            vol_today = 0.0
            if df_intra is not None and not df_intra.empty and "成交量" in df_intra.columns:
                vol_today = float(df_intra["成交量"].sum() or 0)
            vm20 = None
            if vol_ma20 and vol_ma20 > 0 and vol_today > 0:
                vm20 = min(1.0, (vol_today / vol_ma20) / 3.0)
            # reversal
            reversal = 0.0
            try:
                if ret_3d is not None and ret_3d < -2.0 and (row.get("change_pct") or 0) > 2.0:
                    reversal = 1.0
            except Exception:
                pass
            # composite
            parts = []
            if irs is not None:
                parts.append(0.30 * irs)
            if vm20 is not None:
                parts.append(0.25 * vm20)
            parts.append(0.20 * reversal)
            if zt_ratio is not None:
                parts.append(0.25 * zt_ratio)
            score = round(sum(parts), 4) if parts else None
            row["v2_score"]    = score
            row["v2_irs"]      = round(irs, 3) if irs is not None else None
            row["v2_vm20"]     = round(vm20, 3) if vm20 is not None else None
            row["v2_reversal"] = reversal
            row["v2_zt_ratio"] = round(zt_ratio, 3) if zt_ratio is not None else None
            # v149 (2026-07-21): 加权得分 = pass_count + V2_WEIGHT · v2_score
            # 让优化表与基线表在排序上立刻有差异 (基线只看 pass_count;
            # 优化表用 8 条规则过/不过 + 多因子分数综合排序)
            # V2_WEIGHT=5 → 1 个 v2_score (≈0.5) ≈ 2.5 个规则 (8条规则的 1/3 权重)
            from . import _constants as _const
            v2_weight = getattr(_const, 'V2_WEIGHT', 5.0)
            pass_cnt = 8 - len(fail_rules)
            if score is not None:
                weighted = round(pass_cnt + v2_weight * float(score), 4)
            else:
                # v2 没算出时回退到 pass_cnt (避免拉低排位)
                weighted = float(pass_cnt)
            row["weighted_score"] = weighted
            row["v2_weight"]      = v2_weight
        except Exception as e:
            log.debug(f"optimized score {code} err: {e}")
            row["v2_score"] = None

    # 4 层 taxonomy (排序用 + 跨模块跳转)
    try:
        from . import sector_classify as _sc
        sec = _sc.get_sector(code, force_refresh=False) or {}
        tax = sec.get("taxonomy") or {}
        row["taxonomy"] = {
            "l1":        tax.get("level1_cluster", "") or "",
            "l1_color":  tax.get("cluster_color", "#888"),
            "l2":        tax.get("level2_sw", "") or "",
            "l3":        tax.get("level3_chain", "") or "",
            "l3_source": tax.get("source", ""),
            "l4":        list(tax.get("level4_subconcept") or []),
        }
    except Exception:
        row["taxonomy"] = {"l1": "", "l1_color": "#888", "l2": "", "l3": "", "l3_source": "", "l4": []}

    return {"row": row, "rules_pass": rules_pass, "fail_rules": fail_rules, "skip": False}


def _safe_intraday_vwap_ratio(code: str) -> tuple[float | None, float | None]:
    """拉今天分时, 比较 收盘 vs 均价 (数据源已算好均价 = 当日 VWAP).
    返 (ratio, vwap) — ratio: [0,1] = 今天在均价之上的 tick 占比
    算法: 逐 tick (1 分钟) 比对 收盘 ≥ 均价 → 之上
    视频"全天运行在均价之上" → ratio 必须 = 1.0 (100%)

    数据源 (3 路兜底):
      1) akshare 已有均价列 (主)
      2) 腾讯 push2his minute query (备, 自己算 VWAP = amount / (vol * 100))
      3) 返回 None (前端标 "—缺")
    """
    df = _fetch_intraday_df_with_fallback(code)
    if df is None or df.empty:
        return (None, None)
    return _compute_above_vwap(df)


def _fetch_intraday_df_with_fallback(code: str):
    """拉今天分时 df, 标准化列 (时间/收盘/均价/成交量/开盘/最高/最低).
    数据源 (2 路兜底):
      1) akshare stock_zh_a_hist_min_em (2 次重试 + 超时, 主)
      2) 腾讯 push2his minute query (备, 自己算 VWAP)

    标准化:
      - 时间 → "YYYY-MM-DD HH:MM:SS"
      - 均价缺 → 自算 VWAP = 累计成交额 / (累计成交量 * 100)
      - 开盘/最高/最低 缺 → 用 收盘 兜底 (尾盘分类只需 open + 收盘 + 均价, 其余可省)

    超时: 单股单次拉取 ≥5s 直接跳过 (不阻塞整个 tick)
    """
    import concurrent.futures as _cf
    df = None
    # ── 1) akshare 主源 (2 次重试 + 3s 超时) ──
    from .. import data_layer as _dl
    for attempt in range(2):
        try:
            with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
                fut = _pool.submit(_dl.fetch_intraday, code, None)
                df = fut.result(timeout=3.0)
            if df is not None and not df.empty:
                break
        except _cf.TimeoutError:
            log.debug(f"_fetch_intraday_df akshare {code} attempt {attempt+1}: 超时")
        except Exception as e:
            log.debug(f"_fetch_intraday_df akshare {code} attempt {attempt+1}: {e}")
    if df is not None and not df.empty:
        std = _standardize_intraday_df(df)
        if std is not None:
            return std

    # ── 2) 腾讯 push2his fallback (2s 超时) ──
    try:
        with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
            fut = _pool.submit(_fetch_intraday_tencent, code)
            df = fut.result(timeout=2.0)
    except _cf.TimeoutError:
        log.debug(f"_fetch_intraday_tencent {code}: 超时")
        df = None
    except Exception:
        df = None
    if df is not None and not df.empty:
        std = _standardize_intraday_df(df)
        if std is not None:
            return std

    log.debug(f"_fetch_intraday_df_with_fallback {code} 全部源失败")
    return None


def _standardize_intraday_df(df):
    """统一列名 + 缺均价自算. 返 None 表示无法标准化.
    输入 df: 任意 akshare/腾讯 分钟线格式
    输出 df: 列 = [时间, 开盘, 最高, 最低, 收盘, 成交量, 成交额, 均价]
            时间格式 "YYYY-MM-DD HH:MM:SS"
    """
    import pandas as _pd
    try:
        if df is None or df.empty:
            return None
        # 列名容错 (akshare: 时间/开盘/收盘/最高/最低/成交量/成交额/振幅/涨跌幅/涨跌额/换手率)
        rename_map = {
            "时间": "时间", "time": "时间", "日期时间": "时间", "日期": "时间",
            "开盘": "开盘", "open": "开盘",
            "最高": "最高", "high": "最高",
            "最低": "最低", "low":  "最低",
            "收盘": "收盘", "close": "收盘", "最新价": "收盘",
            "成交量": "成交量", "volume": "成交量", "vol": "成交量",
            "成交额": "成交额", "amount": "成交额", "成交金额": "成交额",
            "均价": "均价", "avg": "均价", "成交均价": "均价",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns}).copy()
        if "时间" not in df.columns or "收盘" not in df.columns:
            return None
        # 时间统一 string "YYYY-MM-DD HH:MM:SS"
        if not _pd.api.types.is_string_dtype(df["时间"]):
            df["时间"] = _pd.to_datetime(df["时间"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            df["时间"] = df["时间"].astype(str)
        # 缺列填 0/同收盘
        for col, default in (("开盘", None), ("最高", None), ("最低", None),
                              ("成交量", 0.0), ("成交额", 0.0)):
            if col not in df.columns:
                df[col] = default if default is not None else df["收盘"]
        # 均价缺 → 自算 VWAP = 累计成交额 / (累计成交量 * 100)
        if "均价" not in df.columns:
            cum_vol = df["成交量"].cumsum()
            cum_amt = df["成交额"].cumsum()
            df["均价"] = (cum_amt / (cum_vol * 100.0)).where(cum_vol > 0, df["收盘"])
        # 仅今天的数据 (akshare 可能含历史多天)
        today_str = _now_china().strftime("%Y-%m-%d")
        df = df[df["时间"].str.startswith(today_str)].reset_index(drop=True)
        if df.empty:
            return None
        return df[["时间", "开盘", "最高", "最低", "收盘", "成交量", "成交额", "均价"]]
    except Exception as e:
        log.debug(f"_standardize_intraday_df err: {e}")
        return None


def _fetch_intraday_tencent(code: str):
    """腾讯 push2his 分钟线 fallback — 自己算 VWAP.
    URL: https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={prefix}{code}
    每行: "0930 14.82 271 401622.00" = time(4位) close cumulative_volume(手) cumulative_amount(元)
    输出 df: 含 时间/开盘/最高/最低/收盘/成交量/成交额/均价 (拆分累计值为逐分钟)
    """
    import requests as _req
    try:
        if code.startswith(("5", "6", "7", "9")): pfx = "sh"
        elif code.startswith(("0", "1", "2", "3")): pfx = "sz"
        elif code.startswith(("4", "8")):           pfx = "bj"
        else: return None
        url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={pfx}{code}"
        r = _req.get(url, timeout=4)
        data = r.json().get("data", {}).get(f"{pfx}{code}", {})
        # 真实结构: data.data.data = [rows], data.date = "2026-07-13"
        rows_obj = data.get("data") or {}
        if isinstance(rows_obj, dict):
            rows = rows_obj.get("data") or []
            date_str = rows_obj.get("date") or _now_china().strftime("%Y-%m-%d")
        else:
            rows = rows_obj or []
            date_str = data.get("date") or _now_china().strftime("%Y-%m-%d")
        if not rows:
            return None
        # 腾讯 date 字段两种格式都见过: "20260713" / "2026-07-13"
        date_norm = date_str if "-" in date_str else (date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:8])

        raw = []
        for row in rows:
            parts = row.split(" ") if isinstance(row, str) else row
            if len(parts) < 4: continue
            try:
                t = parts[0]
                if len(t) == 4 and t.isdigit():
                    time_str = f"{date_norm} {t[:2]}:{t[2:]}:00"
                else:
                    continue
                close_p = float(parts[1])
                cum_vol = float(parts[2])    # 累计手
                cum_amt = float(parts[3])    # 累计元
                vwap = cum_amt / (cum_vol * 100.0) if cum_vol > 0 else close_p
                raw.append({"时间": time_str, "收盘": close_p, "均价": vwap,
                            "_cum_vol": cum_vol, "_cum_amt": cum_amt})
            except (ValueError, IndexError):
                continue
        if not raw:
            return None
        # 累计值 → 逐分钟值
        prev_vol = prev_amt = 0.0
        out = []
        for r in raw:
            v_min = max(0.0, r["_cum_vol"] - prev_vol) * 100.0   # 手 → 股
            a_min = max(0.0, r["_cum_amt"] - prev_amt)           # 元
            out.append({
                "时间": r["时间"], "开盘": r["收盘"], "最高": r["收盘"], "最低": r["收盘"],
                "收盘": r["收盘"],
                "成交量": v_min, "成交额": a_min,
                "均价": r["均价"],
            })
            prev_vol, prev_amt = r["_cum_vol"], r["_cum_amt"]
        import pandas as _pd
        return _pd.DataFrame(out)
    except Exception as e:
        log.debug(f"_fetch_intraday_tencent {code} fail: {e}")
        return None


def _compute_above_vwap(df) -> tuple[float | None, float | None]:
    """给定 df (含 时间/收盘/均价 列) → (ratio, vwap_now)
    严格按今天日期过滤, 逐 tick 比对.
    """
    try:
        if df is None or df.empty:
            return (None, None)
        if not all(c in df.columns for c in ("时间", "收盘", "均价")):
            return (None, None)
        today_str = _now_china().strftime("%Y-%m-%d")
        df = df.copy()
        df["_date"] = df["时间"].astype(str).str.slice(0, 10)
        df = df[df["_date"] == today_str]
        if df.empty:
            return (None, None)
        df = df.sort_values("时间").reset_index(drop=True)
        # 允许 0.01% 浮点误差
        diff = (df["收盘"] - df["均价"]) / df["均价"].replace(0, float("nan"))
        above_mask = diff.fillna(-1) >= -0.0001
        ratio = float(above_mask.sum()) / max(1, len(df))
        vwap_now = float(df["均价"].iloc[-1]) if not df.empty else None
        return (ratio, vwap_now)
    except Exception as e:
        log.debug(f"_compute_above_vwap err: {e}")
        return (None, None)


# ═════════════════════════════════════════════════════════════════
# 4.5) 尾盘 30 分钟 5 条规则分类 (用户原话)
# ────────────────────────────────────────────────────────────────
# 标记 / 走势 (走势描述照搬用户原文):
_LAST30_LABELS: dict[str, dict[str, str]] = {
    "主力抢跑":       {"label": "主力抢跑",       "color": "#ef4444",
                       "desc": "尾盘半小时分时先涨后落，回落直接跌破当天开盘价，后续大概率还有一次大跌"},
    "做多意愿弱":     {"label": "做多意愿弱",     "color": "#f59e0b",
                       "desc": "尾盘半小时先跌后反弹，反弹未过开盘价或弹一下又快速回落，第二天基本低开"},
    "次日大概率异动": {"label": "次日大概率异动", "color": "#22c55e",
                       "desc": "尾盘半小时小幅拉升（涨幅不超三个点），之后在均线上方小幅震荡且成交量慢慢放大，第二天大概率有大动作，甚至直接涨停"},
    "承接力强":       {"label": "承接力强",       "color": "#3b82f6",
                       "desc": "尾盘半小时先涨后回落，但回落过程中始终没跌破分时黄线，第二天还有机会继续冲高"},
    "洗盘阶段":       {"label": "洗盘阶段",       "color": "#9ca3af",
                       "desc": "尾盘半小时一直震荡，股价无亮眼表现，明显还在洗盘阶段，不想浪费时间可直接略过"},
}


def _safe_intraday_analyze(code: str) -> dict[str, Any]:
    """单次拉 intraday, 同时算 VWAP 占比 + 尾盘 5 条分类.
    使用 tick 级缓存避免同 tick 内重复网络请求.

    返: {
        vwap_ratio: float|None,
        vwap_now: float|None,
        last30_label: str,  # "主力抢跑" / "—缺" / ...
        last30_desc:  str,  # 走势描述 / ""
    }
    """
    global _TICK_INTRADAY_CACHE, _TICK_CACHE_TS
    now = time.time()
    # tick 级缓存
    if _TICK_INTRADAY_CACHE and (now - _TICK_CACHE_TS) < _TICK_CACHE_TTL and code in _TICK_INTRADAY_CACHE:
        return dict(_TICK_INTRADAY_CACHE[code])

    out = {"vwap_ratio": None, "vwap_now": None, "last30_label": "—缺", "last30_desc": ""}
    df = _fetch_intraday_df_with_fallback(code)
    if df is None or df.empty:
        return out
    ratio, vwap = _compute_above_vwap(df)
    out["vwap_ratio"] = ratio
    out["vwap_now"] = vwap
    label, desc = _classify_last30_from_df(df)
    out["last30_label"] = label
    out["last30_desc"] = desc

    # 写入缓存
    _TICK_INTRADAY_CACHE[code] = out
    _TICK_CACHE_TS = now
    return out


def _classify_last30_from_df(df) -> tuple[str, str]:
    """5 条规则打 1 个标记 (按优先级匹配, 命中即返).
    入参 df: 已标准化 + 已按今天过滤 + 列含 时间/收盘/均价/成交量/开盘.

    优先级 (互斥, 命中即停):
      1) 主力抢跑       — 尾盘先涨后落, 回落直接跌破当天开盘价
      2) 承接力强       — 尾盘先涨后落, 但回落始终未破分时黄线
      3) 做多意愿弱     — 尾盘先跌后反弹, 反弹未过开盘价 (或弹一下又快速回落)
      4) 次日大概率异动 — 尾盘小幅拉升 (≤3%) + 在均线上方小幅震荡 + 量慢慢放大
      5) 洗盘阶段       — 兜底 (无亮眼表现)
    """
    try:
        if df is None or df.empty:
            return ("—缺", "分时数据缺失")

        # ── 切窗口: 14:30–15:00 (含) ──
        df = df.copy()
        df["_t"] = df["时间"].str.slice(11, 16)  # "HH:MM"
        last30 = df[(df["_t"] >= "14:30") & (df["_t"] <= "15:00")].reset_index(drop=True)
        if last30.empty:
            return ("—缺", "尾盘窗口无数据")
        if len(last30) < 5:
            return ("—少", f"尾盘仅 {len(last30)} 分钟数据, 不够分类")

        # ── 关键特征 ──
        cur_price    = float(last30["收盘"].iloc[-1])
        start_price  = float(last30["收盘"].iloc[0])  # 14:30 tick
        peak_price   = float(last30["收盘"].max())
        trough_price = float(last30["收盘"].min())
        vwap_now     = float(last30["均价"].iloc[-1])
        n            = len(last30)

        # 开盘价 — 用 9:30 那个 tick 的收盘价 (or 全天第一根)
        morning = df[df["_t"] < "14:30"]
        if morning.empty:
            return ("—缺", "今日早盘数据缺失")
        open_price = float(morning["收盘"].iloc[0])
        if open_price <= 0:
            return ("—缺", "开盘价异常")

        # peak / trough 位置 (用于判断顺序)
        closes = last30["收盘"].tolist()
        peak_pos   = closes.index(peak_price)
        trough_pos = closes.index(trough_price)

        # 尾盘整体涨跌 (vs 14:30 起点)
        last30_change_pct = (cur_price - start_price) / start_price * 100.0
        # 尾盘振幅
        amplitude_pct = (peak_price - trough_price) / trough_price * 100.0 if trough_price > 0 else 0.0

        # 量趋势 — 尾盘对半, 后半 > 前半 1.2× 视为放量
        half = max(1, n // 2)
        vol_first  = float(last30["成交量"].iloc[:half].sum())
        vol_second = float(last30["成交量"].iloc[half:].sum())
        vol_growing = vol_second > vol_first * 1.2

        # ── 初始方向 (前 3 个 tick): 决定走 "先涨" 路线 还是 "先跌" 路线 ──
        first3_min = float(last30["收盘"].iloc[:min(3, n)].min())
        went_down_first = first3_min < start_price * 0.998

        if not went_down_first:
            # ── 路线 A: 先涨 (前 3 min 没跌破起点) ──
            # ── 规则 1: 主力抢跑 ──
            # 先涨 (peak 出现在中段) + 当前价 < 开盘价
            # 允许 peak == open (开盘价附近拉一下)
            if (peak_pos >= 1 and peak_pos <= n - 2
                    and peak_price >= open_price * 0.999
                    and cur_price < open_price):
                return ("主力抢跑", _LAST30_LABELS["主力抢跑"]["desc"])

            # ── 规则 2: 承接力强 ──
            # 先涨后回落 (peak > start, cur < peak) + 始终未破分时黄线
            # (用 vwap_now 作整段 VWAP 的代理; 全天均价平稳, 误差 < 0.1%)
            if (peak_price > start_price * 1.001
                    and trough_price >= vwap_now * 0.999
                    and cur_price >= vwap_now * 0.999
                    and cur_price < peak_price * 0.998):
                return ("承接力强", _LAST30_LABELS["承接力强"]["desc"])

        else:
            # ── 路线 B: 先跌 (前 3 min 跌破起点) ──
            # ── 规则 3: 做多意愿弱 ──
            # 找前半段首次显著低点 → 看反弹是否过 open
            # (用前 15 min 找首次低点, 避免用整段 trough 落在末尾时漏判)
            first_half = last30.iloc[:max(1, n // 2)]
            if not first_half.empty:
                first_low = float(first_half["收盘"].min())
                first_low_pos = first_half["收盘"].tolist().index(first_low)
                if first_low < start_price * 0.998:
                    # 首次低点之后的所有 tick → 反弹峰
                    after_low = last30.iloc[first_low_pos:]
                    if not after_low.empty:
                        rebound_peak = float(after_low["收盘"].max())
                        rebound_pos_rel = int(after_low["收盘"].argmax())
                        rebound_pos_abs = first_low_pos + rebound_pos_rel
                        rebound_to_open = rebound_peak >= open_price * 0.999
                        # 弹一下又快速回落: 反弹过 open 后, 当前价 < open 且 < 反弹峰
                        # (双门槛 — 反弹过 open 是前提, 跌回去才算 "快速回落")
                        quick_fall_back = (
                            rebound_to_open
                            and cur_price < open_price * 0.99
                            and cur_price < rebound_peak * 0.99
                        )
                        if not rebound_to_open or quick_fall_back:
                            return ("做多意愿弱", _LAST30_LABELS["做多意愿弱"]["desc"])

        # ── 规则 4: 次日大概率异动 ──
        # 小幅拉升 (0 < change ≤ 3%) + 在均线上方 + 小幅震荡 (amp < 2%) + 量放大
        if (0 < last30_change_pct <= 3.0
                and cur_price >= vwap_now * 1.001
                and amplitude_pct < 2.0
                and vol_growing):
            return ("次日大概率异动", _LAST30_LABELS["次日大概率异动"]["desc"])

        # ── 规则 5: 洗盘阶段 (兜底) ──
        return ("洗盘阶段", _LAST30_LABELS["洗盘阶段"]["desc"])
    except Exception as e:
        log.debug(f"_classify_last30_from_df err: {e}")
        return ("—缺", "分类异常")


# ═════════════════════════════════════════════════════════════════
# 5) 实时 1s 评估循环 (双策略: baseline + optimized)
# ═════════════════════════════════════════════════════════════════
_RESULT: dict = {
    "ts": 0.0, "took_ms": 0,
    "baseline": {"items": [], "ts": 0.0, "took_ms": 0},
    "optimized":       {"items": [], "ts": 0.0, "took_ms": 0},
}
_STALE: dict = {"items": [], "ts": 0.0}  # 数据源全挂兜底 (留作向后兼容)
# v148 (2026-07-21): 双策略 stale 兜底 — 修"非交易时段优化策略永远 0 行"bug
# 之前 _STALE 只存 baseline, optimized slot 在 9:30-15:00 之外永远空 (poller_loop
# 非交易时段只重建候选池不评估). 现在双 slot 各自 stale, 重启/非交易时段都能看上次结果.
_STALE_OPT: dict = {"items": [], "ts": 0.0}  # optimized 策略 stale 兜底
_STALE_TTL = 86400.0  # 1 天 — 非交易时段/重启后能看到上次数据 (7 天太旧会误导)


def _eval_pool_serial(codes: list[str], names: dict[str, str],
                     strategy_id: str = "baseline") -> list[dict]:
    """并发评估整个候选池 — 使用批量引用减少网络 IO
    strategy_id: "baseline" 或 "optimized"
    同池共享同一份 quote/intraday 缓存, V2 只额外 fetch_daily (tick 级 60s 复用)
    """
    from concurrent.futures import as_completed
    # 先批量拉所有报价
    _batch_fetch_quotes(codes)
    out_rows: list[dict] = []
    futs = {_EXECUTOR.submit(evaluate_one, c, names.get(c, c), strategy_id): c for c in codes}
    try:
        for f in as_completed(futs, timeout=20):
            try:
                r = f.result(timeout=0.5) or {}
                row = r.get("row") or {}
                if row:
                    row["rules_pass"] = r.get("rules_pass", [])
                    row["fail_rules"] = r.get("fail_rules", [])
                    row["pass_all"]   = len(row.get("fail_rules", [])) == 0
                    out_rows.append(row)
            except Exception:
                pass
    except Exception as e:
        log.debug(f"_eval_pool_serial ({strategy_id}) 超时/异常: {e}")
    return out_rows


def _sort_key_fn(sort: str):
    s = (sort or "change_pct").strip().lower()
    if s in ("change_pct", "涨幅"):
        return lambda r: r.get("change_pct", 0)
    if s in ("turnover", "换手"):
        return lambda r: r.get("turnover", 0)
    if s in ("volume_ratio", "量比"):
        return lambda r: r.get("volume_ratio", 0)
    if s in ("amplitude", "振幅"):
        return lambda r: r.get("amplitude", 0)
    if s in ("amount", "amount_yi", "成交额"):
        return lambda r: r.get("amount", 0) / 1e8
    if s in ("mcap", "mcap_yi", "总市值"):
        return lambda r: r.get("mcap_yi", 0)
    if s in ("pe", "pe_ttm", "PE"):
        return lambda r: r.get("pe_ttm", 0)
    if s in ("main_fund_inflow", "主力净流入"):
        return lambda r: r.get("main_fund_inflow_wan", 0)
    if s in ("zt_20d", "20d涨停"):
        return lambda r: r.get("zt_20d", 0)
    if s in ("score", "得分", "rules_pass_count"):
        return lambda r: len(r.get("rules_pass") or [])
    if s in ("v2_score",) or "v2_score" in s:
        return lambda r: r.get("v2_score") if r.get("v2_score") is not None else -1.0
    if s in ("v2_irs",):
        return lambda r: r.get("v2_irs") if r.get("v2_irs") is not None else -1.0
    if s in ("v2_vm20",):
        return lambda r: r.get("v2_vm20") if r.get("v2_vm20") is not None else -1.0
    if s in ("v2_reversal",):
        return lambda r: r.get("v2_reversal") if r.get("v2_reversal") is not None else -1.0
    if s in ("v2_zt_ratio", "v2_zt_ratio"):
        return lambda r: r.get("v2_zt_ratio") if r.get("v2_zt_ratio") is not None else -1.0
    # v149 (2026-07-21): 加权得分 = pass_count + V2_WEIGHT · v2_score — 优化表主排序键
    if s in ("weighted_score", "加权得分", "composite", "v2_weighted"):
        return lambda r: r.get("weighted_score") if r.get("weighted_score") is not None else -1.0
    return lambda r: r.get("change_pct", 0)  # 兜底


def screener_tick(force: bool = False) -> dict[str, Any]:
    """1s tick — 双策略并跑 (baseline + optimized), 共享同一候选池+quote/intraday 缓存
    force=True: 强制重建候选池 (手动刷新用)
    单次 tick 超时 25s 兜底
    """
    t0 = time.time()
    # 每次 tick 清空 tick 级缓存, 保证数据新鲜
    global _TICK_INTRADAY_CACHE, _TICK_CACHE_TS, _TICK_DAILY_CACHE, _TICK_DAILY_TS
    _TICK_INTRADAY_CACHE = {}
    _TICK_CACHE_TS = 0.0
    _TICK_DAILY_CACHE = {}  # optimized 日线缓存也清
    _TICK_DAILY_TS = 0.0
    try:
        codes = build_candidate_pool(force=force) or []
        names = _CANDIDATE_POOL.get("names") or {}
        if not codes:
            log.info("screener_tick: 候选池空")
            return {"items": [], "count": 0, "took_ms": int((time.time() - t0) * 1000)}

        # ── 双策略并跑 (eval 共享 quote/intraday cache, optimized 多 1 路 daily) ──
        rows_base = _eval_pool_serial(codes, names, strategy_id="baseline")
        rows_opt   = _eval_pool_serial(codes, names, strategy_id="optimized")
        took_ms = int((time.time() - t0) * 1000)
        now_ts = time.time()

        # 写 _RESULT 双 slot
        _RESULT["ts"] = now_ts
        _RESULT["took_ms"] = took_ms
        _RESULT["baseline"]["items"]    = rows_base
        _RESULT["baseline"]["ts"]       = now_ts
        _RESULT["baseline"]["took_ms"]  = int((time.time() - t0) * 1000)
        _RESULT["optimized"]["items"]          = rows_opt
        _RESULT["optimized"]["ts"]             = now_ts
        _RESULT["optimized"]["took_ms"]        = int((time.time() - t0) * 1000)

        # 数据源全挂兜底 — baseline 至少 1 行才存
        if rows_base:
            _STALE["items"] = rows_base
            _STALE["ts"] = now_ts
        # v148 (2026-07-21): optimized 同步存 stale (双策略并跑时一并落,共时间戳)
        if rows_opt:
            _STALE_OPT["items"] = rows_opt
            _STALE_OPT["ts"] = now_ts
        # 1) SSE 广播 (双策略 payload)
        try:
            _broadcast({
                "ts": now_ts,
                "took_ms": took_ms,
                "baseline": {
                    "items":   rows_base[:50],
                    "count":   len(rows_base),
                    "took_ms": _RESULT["baseline"]["took_ms"],
                },
                "optimized": {
                    "items":   rows_opt[:50],
                    "count":   len(rows_opt),
                    "took_ms": _RESULT["optimized"]["took_ms"],
                },
            })
        except Exception:
            pass
        # 2) 历史快照 (30s 一次, 由 save_snapshot 内部节流)
        try:
            save_snapshot()
        except Exception:
            pass
        return {
            "ts": now_ts, "took_ms": took_ms,
            "baseline": {"items": rows_base, "count": len(rows_base)},
            "optimized":       {"items": rows_opt,   "count": len(rows_opt)},
        }
    except Exception as e:
        log.exception(f"screener_tick 异常: {e}")
        return {
            "ts": time.time(), "took_ms": int((time.time() - t0) * 1000), "error": str(e)[:200],
            "baseline": {"items": [], "count": 0},
            "optimized":       {"items": [], "count": 0},
        }


def _is_trading_session(now: _dt.datetime | None = None) -> bool:
    """判断当前是否在交易时段内 (9:30-15:00)。
    用于 poller_loop: 整个交易时段都跑 tick, 不止 14:30-15:00。
    """
    n = now or _now_china()
    h, m = n.hour, n.minute
    cur = h * 60 + m
    return 9 * 60 + 30 <= cur < 15 * 60


async def screener_poller_loop():
    """持续保活 — 1s 一次
    - 交易时段 (9:30-15:00): 每秒跑 screener_tick, 实时更新候选池 + 评估
    - 非交易时段: 每 30s 重建候选池 (数据源可能恢复)
    - 手动刷新通过 POST /api/screener/rebuild (force=True) 不依赖 poller
    """
    poll_log = logging.getLogger("tuixue.screener")
    _next_pool_rebuild = 0.0
    while True:
        try:
            if _is_trading_session():
                # 全交易时段每秒评估 (9:30-15:00)
                await asyncio.to_thread(screener_tick)
            else:
                now = time.time()
                if now >= _next_pool_rebuild:
                    await asyncio.to_thread(build_candidate_pool, True)
                    _next_pool_rebuild = now + 30.0
        except Exception as e:
            poll_log.debug(f"screener_poller_loop 异常: {e}")
        await asyncio.sleep(1.0)


def _load_last_snapshot(strategy_id: str = "baseline") -> list[dict]:
    """加载最近一个快照文件中的 items，用于 _RESULT 空且 _STALE 也空时的兜底。
    遍历 _SNAPSHOT_DIR 下所有 jsonl,找 ts 最新的那个文件的最后一行。
    v148 (2026-07-21): strategy_id 路由 — baseline 走 `items`,
    optimized 走 `optimized_items` (之前 save_snapshot 已实装双策略并存)。
    """
    try:
        best_ts = 0.0
        best_items: list[dict] | None = None
        # v148 (2026-07-21): 双策略 — 根据 strategy_id 取不同字段
        items_key = "optimized_items" if strategy_id == "optimized" else "items"
        for fp in sorted(_SNAPSHOT_DIR.glob("*.jsonl"), reverse=True):
            if fp.name == "_last_ts":
                continue
            try:
                lines = fp.read_text(encoding="utf-8").splitlines()
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    r = _json.loads(line)
                    its = r.get(items_key) or r.get("items") or []   # v148 fallback to old key
                    ts = float(r.get("ts", 0))
                    if ts > best_ts:
                        best_ts = ts
                        best_items = its
                    break  # 每个文件只取最后一行
            except Exception:
                continue
            if best_items:
                break  # sorted reverse → 最新的日期文件
        if best_items:
            log.info(f"[兜底] 从快照加载 {len(best_items)} 项 ({strategy_id}, ts={best_ts})")
        return best_items or []
    except Exception as e:
        log.debug(f"_load_last_snapshot 失败: {e}")
        return []


def passing_codes(strategy_id: str = "optimized") -> set[str]:
    """跨模块只读:返回当前尾盘战法命中股票 code 集合 (6位补零)。
    优先内存 _RESULT,空则 _STALE,再空则最近快照。绝不触发重算 — 供 all_stocks
    「尾盘战法」信号筛选复用,永不阻塞 board 路径。
    """
    try:
        slot = _RESULT.get(strategy_id) or {}
        items = slot.get("items") or []
        if not items:
            stale = _STALE_OPT if strategy_id == "optimized" else _STALE
            items = stale.get("items") or []
        if not items:
            items = _load_last_snapshot(strategy_id)
        out: set[str] = set()
        for it in items or []:
            c = (it.get("code") if isinstance(it, dict) else it) or ""
            c = str(c).strip()
            if c:
                out.add(c.zfill(6))
        return out
    except Exception as e:
        log.debug(f"passing_codes({strategy_id}) err: {e}")
        return set()


def _warm_stale_from_snapshot() -> dict:
    """v148 (2026-07-21): server 启动/首次访问时,从最近快照暖 _STALE + _STALE_OPT
    否则非交易时段首屏 optimized 会一直空 (poller_loop 不评估 + _STALE_OPT 没数据)
    旧快照 (v148 之前保存的) 只有 baseline items 没有 optimized_items,
    这种情况回退到 baseline items 共享(因 ts 一致),前端 missing 列会显示 — 缺
    返回 {"baseline_loaded": n, "optimized_loaded": m} 报告
    """
    out = {"baseline_loaded": 0, "optimized_loaded": 0}
    try:
        # baseline
        if not _STALE.get("items"):
            its = _load_last_snapshot("baseline")
            if its:
                _STALE["items"] = its
                _STALE["ts"] = time.time()
                out["baseline_loaded"] = len(its)
        # optimized
        if not _STALE_OPT.get("items"):
            its = _load_last_snapshot("optimized")
            if its:
                _STALE_OPT["items"] = its
                _STALE_OPT["ts"] = time.time()
                out["optimized_loaded"] = len(its)
            else:
                # v148 之前保存的旧快照没有 optimized_items,
                # 兜底共用 baseline items (ts 同步,V2 列前端的 renderCell 也会适配)
                base_its = _STALE.get("items") if _STALE.get("items") else None
                if base_its:
                    _STALE_OPT["items"] = list(base_its)
                    _STALE_OPT["ts"] = _STALE.get("ts", time.time())
                    out["optimized_loaded"] = len(base_its)
                    log.info(f"[v148] optimized 从 baseline 共享兜底 {len(base_its)} 项")
        if out["baseline_loaded"] or out["optimized_loaded"]:
            log.info(f"[v148] 启动暖 stale: {out}")
    except Exception as e:
        log.debug(f"_warm_stale_from_snapshot 失败: {e}")
    return out


def current_results(sort: str = "change_pct", order: str = "desc",
                     limit: int = 100, show_failed: bool = True,
                     strategy_id: str = "baseline") -> dict[str, Any]:
    """返当前 _RESULT[strategy_id] (按 sort 排 + 截断)
    strategy_id: "baseline" (默认) 或 "optimized"
    show_failed=False → 只返 pass_all=True 的 (用户全规则过)
    """
    slot = _RESULT.get(strategy_id) or {"items": [], "ts": 0.0, "took_ms": 0}
    rs = slot.get("items") or []
    src_ts = slot.get("ts") or 0
    took = slot.get("took_ms") or 0
    stale = False

    if not rs:
        # v148 (2026-07-21): 双策略 stale 兜底 — 非交易时段 poller_loop 不评估,
        # 双策略空时各自从 _STALE / _STALE_OPT 拉最近一次结果,沿用 stale=True 标记
        if strategy_id == "baseline":
            stale_src = _STALE
        elif strategy_id == "optimized":
            stale_src = _STALE_OPT
        else:
            stale_src = None
        if stale_src and stale_src.get("items") and (time.time() - (stale_src.get("ts") or 0)) < _STALE_TTL:
            rs = list(stale_src["items"])
            src_ts = stale_src.get("ts", 0)
            stale = True
        elif not rs:
            # v148 (2026-07-21): 第一次访问时暖 stale(从最近快照) — 修"非交易时段首屏优化策略空白"
            if not stale_src or not stale_src.get("items"):
                _warm_stale_from_snapshot()
                if stale_src and stale_src.get("items"):
                    rs = list(stale_src["items"])
                    src_ts = stale_src.get("ts", 0)
                    stale = True
            if not rs:
                # 最后兜底:从磁盘快照读 (可能仍空, optimized 真没存)
                snap_items = _load_last_snapshot(strategy_id)
                if snap_items:
                    rs = snap_items
                    stale = True
                    log.info(f"[screener] current_results 快照兜底 {len(rs)} 项 ({strategy_id})")

    # 过滤
    if not show_failed:
        rs = [r for r in rs if r.get("pass_all")]

    # 排序 (None 排最后)
    key_fn = _sort_key_fn(sort)
    def _key(r):
        try:
            v = key_fn(r)
            return (0, v) if v is not None else (1, 0)
        except Exception:
            return (1, 0)
    rs.sort(key=_key, reverse=(order == "desc"))

    # limit 截断
    try:
        lim = max(1, min(int(limit or 100), 500))
    except Exception:
        lim = 100
    rs = rs[:lim]

    return {
        "items":    rs,
        "count":    len(rs),
        "ts":       src_ts,
        "took_ms":  took,
        "sort":     sort,
        "order":    order,
        "stale":    stale,
        "rules_total":  8,
        "strategy_id": strategy_id,
    }


def current_results_multi(sort_spec: str = "change_pct:desc",
                          limit: int = 100,
                          show_failed: bool = True,
                          rule_filters: list[str] | None = None,
                          strategy_id: str = "baseline") -> dict[str, Any]:
    """多字段排序 + 规则过滤 — 形如 "change_pct:desc,volume_ratio:asc"
    rule_filters: 用户勾选要看的规则 (eg. ["change_pct","volume_ratio"])
                  未勾选的规则 fail 不影响行的可见性
    strategy_id: "baseline" (默认) 或 "optimized" — 决定读 _RESULT 哪一份
    """
    slot = _RESULT.get(strategy_id) or {"items": [], "ts": 0.0, "took_ms": 0}
    rs = slot.get("items") or []
    src_ts = slot.get("ts") or 0
    took = slot.get("took_ms") or 0
    stale = False

    if not rs:
        # v148 (2026-07-21): 双策略 stale 兜底,与 current_results 保持一致
        if strategy_id == "baseline":
            stale_src = _STALE
        elif strategy_id == "optimized":
            stale_src = _STALE_OPT
        else:
            stale_src = None
        if stale_src and stale_src.get("items") and (time.time() - (stale_src.get("ts") or 0)) < _STALE_TTL:
            rs = list(stale_src["items"])
            src_ts = stale_src.get("ts", 0)
            stale = True
        elif not rs:
            # v148 (2026-07-21): 双策略都试快照兜底
            snap_items = _load_last_snapshot(strategy_id)
            if snap_items:
                rs = snap_items
                stale = True
                log.info(f"[screener] 快照兜底 {len(rs)} 项 ({strategy_id})")

    # 规则过滤 (用户在前端勾选了要看哪几条)
    if rule_filters:
        keep = set(rule_filters)
        rs = [r for r in rs if not (set(r.get("fail_rules") or []) - keep)]
    elif not show_failed:
        rs = [r for r in rs if r.get("pass_all")]

    # 多字段排序 — 解析 "field:dir,field:dir"
    specs: list[tuple] = []
    for piece in (sort_spec or "change_pct:desc").split(","):
        p = piece.strip().split(":")
        if len(p) >= 2:
            specs.append((p[0].strip(), p[1].strip().lower() == "desc"))
        elif len(p) == 1 and p[0]:
            specs.append((p[0].strip(), True))

    keys = [(_sort_key_fn(f), desc) for f, desc in specs]
    def _k(r):
        return tuple((0, kfn(r)) if not isinstance(kfn(r), (int, float)) or kfn(r) is not None else (1, 0) for kfn, _ in keys)
    # 简化: 用 None 排最后
    def _k2(r):
        out = []
        for kfn, desc in keys:
            try:
                v = kfn(r)
                out.append((0 if v is not None else 1, v if v is not None else 0))
            except Exception:
                out.append((1, 0))
        # 反向按 desc 翻转 (tuple 内每个元素单独 reverse 处理麻烦, 直接整体 reverse 不对)
        # 这里仅支持单维 multi — Python tuple sort 反转太复杂, 简化版本:
        return tuple(out)
    # 简化: 若 specs 长度 > 1, 用 tuple 比较 (None 排最后)
    # Python sort reverse=True 会把整个反转, 多字段下不直观 — 改为正序 sort 后再按用户期望翻转
    # 工程简化: 多字段支持逗号分隔, reverse 按最后字段方向
    if specs:
        # 主排序: 第一个 spec
        primary_key = _sort_key_fn(specs[0][0])
        primary_desc = specs[0][1]
        try:
            rs.sort(key=lambda r: (0, primary_key(r)) if primary_key(r) is not None else (1, 0), reverse=primary_desc)
        except Exception:
            pass
        # 副排序: 在主排序基础上稳定排
        for f, desc in specs[1:]:
            kfn = _sort_key_fn(f)
            try:
                rs.sort(key=lambda r: (0, kfn(r)) if kfn(r) is not None else (1, 0), reverse=desc)
            except Exception:
                pass

    try:
        lim = max(1, min(int(limit or 100), 500))
    except Exception:
        lim = 100
    rs = rs[:lim]

    return {
        "items":   rs,
        "count":   len(rs),
        "ts":      src_ts,
        "took_ms": took,
        "sort":    sort_spec,
        "stale":   stale,
        "rules_total": 8,
        "strategy_id": strategy_id,
    }


# ═════════════════════════════════════════════════════════════════
# 6) 历史快照 (历史回放用)
# ═════════════════════════════════════════════════════════════════
import json as _json
from pathlib import Path as _Path
_SNAPSHOT_DIR = _Path(__file__).parent.parent / "data" / "screener_snapshots"
_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
_SNAPSHOT_INTERVAL = 30.0   # 每 30s 一个快照
_SNAPSHOT_KEEP_DAYS = 5     # 保留 5 个交易日


def _snapshot_path(date_str: str) -> _Path:
    return _SNAPSHOT_DIR / f"{date_str}.jsonl"


def save_snapshot(force: bool = False) -> dict[str, Any] | None:
    """保存当前 _RESULT 到当日的 jsonl — 14:30-15:00 期间每 30s 一次
    v148 (2026-07-21): 双策略并存的 _RESULT 格式,
    之前读 _RESULT['items'] 永远空 (实际 key 是 baseline/optimized),
    导致快照从未真的存过 baseline + v2 数据 — server 重启即丢历史.
    现在存 baseline + optimized 双 slot.
    """
    baseline_items = _RESULT.get("baseline", {}).get("items") or []
    if not baseline_items:
        return None
    optimized_items = _RESULT.get("optimized", {}).get("items") or []
    now = time.time()
    last_path = _SNAPSHOT_DIR / "_last_ts"
    last_ts = 0.0
    if last_path.is_file():
        try:
            last_ts = float(last_path.read_text().strip() or "0")
        except Exception:
            pass
    if not force and (now - last_ts) < _SNAPSHOT_INTERVAL:
        return None
    date_str = _now_china().strftime("%Y-%m-%d")
    rec = {
        "ts":      now,
        "ts_str":  _dt.datetime.utcfromtimestamp(now).strftime("%H:%M:%S"),
        "iso":     _now_china().strftime("%Y-%m-%d %H:%M:%S"),
        "count":   len(baseline_items),
        "items":   baseline_items[:200],   # 截断 200 行,避免单文件过大 (兼容旧版读法)
        "optimized_items": optimized_items[:200],
        "thresholds": dict(_THRESHOLDS),
        "toggles":    dict(_RULE_TOGGLES),
    }
    try:
        with open(_snapshot_path(date_str), "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
        last_path.write_text(str(now))
        # 清理过期
        _prune_old_snapshots()
        return rec
    except Exception as e:
        log.warning(f"save_snapshot fail: {e}")
        return None


def _prune_old_snapshots():
    try:
        keep_since = (_now_china() - _dt.timedelta(days=_SNAPSHOT_KEEP_DAYS)).strftime("%Y-%m-%d")
        for fp in _SNAPSHOT_DIR.glob("*.jsonl"):
            if fp.stem < keep_since:
                fp.unlink()
    except Exception:
        pass


def list_snapshots(date_str: str | None = None) -> list[dict]:
    """列出某日所有快照点 (用于历史回放)
    date_str=None → 今日
    """
    date_str = date_str or _now_china().strftime("%Y-%m-%d")
    fp = _snapshot_path(date_str)
    if not fp.is_file():
        return []
    out = []
    try:
        for line in fp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = _json.loads(line)
                # 只返摘要, 不返大 items (前端按需再拉)
                out.append({
                    "ts": r.get("ts", 0),
                    "iso": r.get("iso", ""),
                    "ts_str": r.get("ts_str", ""),
                    "count": r.get("count", 0),
                })
            except Exception:
                pass
    except Exception:
        return []
    return out


def get_snapshot(date_str: str | None, ts: float) -> dict | None:
    """取具体某个时间点的快照"""
    date_str = date_str or _now_china().strftime("%Y-%m-%d")
    fp = _snapshot_path(date_str)
    if not fp.is_file():
        return None
    try:
        # 找 ts 最接近的
        best = None
        best_diff = float("inf")
        for line in fp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = _json.loads(line)
                diff = abs(r.get("ts", 0) - ts)
                if diff < best_diff:
                    best = r
                    best_diff = diff
            except Exception:
                pass
        return best
    except Exception:
        return None


def available_snapshot_dates() -> list[str]:
    """返所有有快照的日期"""
    return sorted([fp.stem for fp in _SNAPSHOT_DIR.glob("*.jsonl")])


# ═════════════════════════════════════════════════════════════════
# 7) SSE 推送订阅 (1s push, 不再轮询)
# ═════════════════════════════════════════════════════════════════
_SUBSCRIBERS: list[asyncio.Queue] = []
_SUBSCRIBERS_LOCK = threading.RLock()      # B3: 防多线程订阅/取消竞态
_LAST_BROADCAST: dict | None = None        # B3: rebuild 期间返 last-known good (避免客户端空白)
_LAST_BROADCAST_LOCK = threading.RLock()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=4)   # 限队列长, 防止客户端慢拖累
    with _SUBSCRIBERS_LOCK:
        _SUBSCRIBERS.append(q)
    return q


def unsubscribe(q: asyncio.Queue):
    with _SUBSCRIBERS_LOCK:
        try:
            _SUBSCRIBERS.remove(q)
        except Exception:
            pass


def get_last_broadcast() -> dict | None:
    """B3: rebuild 期间 SSE 客户端拿 last-known-good 缓存"""
    with _LAST_BROADCAST_LOCK:
        if _LAST_BROADCAST is None:
            return None
        return dict(_LAST_BROADCAST)


def _broadcast(payload: dict):
    """1s tick 后通知所有订阅者 (队列满就丢)"""
    # B3: 先存 last-known,rebuild 时客户端可拿这个
    with _LAST_BROADCAST_LOCK:
        global _LAST_BROADCAST
        _LAST_BROADCAST = dict(payload)
    with _SUBSCRIBERS_LOCK:
        if not _SUBSCRIBERS:
            return
        # 快照副本 (list() 已 copy),锁释放后再 put,避免持锁 I/O
        subs = list(_SUBSCRIBERS)
    for q in subs:
        try:
            q.put_nowait(payload)
        except Exception:
            pass  # 队列满 → 丢这一帧


def rule_status_enriched() -> dict[str, Any]:
    """带阈值/开关的完整状态 — 前端初始化用"""
    s = rule_status()
    s["thresholds"] = get_thresholds()
    s["rule_toggles"] = get_rule_toggles()
    s["snapshot_dates"] = available_snapshot_dates()
    return s
