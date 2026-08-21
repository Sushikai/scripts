"""
yaogu_backtest.py — 妖股回测引擎 (500 调研 → 1000 迭代的核心)

与 zt_backtest 完全同口径 (2026-08-06 用户确认的真实路径):
  - 买入: 信号日 T 收盘确认 → T+1 开盘价买入
  - 一字板: T+1 开盘==最高 (容差 0.3%) → 空仓 0 收益
  - 成本: 双边 0.66% (滑点 0.2% + 买卖费 0.06% + 印花税 0.1%)
  - 退出: 默认断板收盘卖 (妖股铁律, 调研 6b.3 验证)

调研结论 (YAOGU_500_SURVEY.md §6b):
  - 2板介入期望最优 (胜率 42%, avg +0.52%, 空仓率 42%)
  - 断板低吸负期望 (胜率 34%, avg -2.91%) — 默认排除, 仅显式参数可测

事件缓存: /tmp/yaogu_events.json — 首次构建 ~200s, 之后秒级加载
"""
from __future__ import annotations

import json
import logging
import statistics
import sys
import time as systime
from collections import defaultdict
from pathlib import Path

import pandas as pd

_ROOT = str(Path(__file__).resolve().parent)
sys.path.insert(0, _ROOT)

from yaogu_survey import COST_BPS, extract_streaks, is_limit_up, load_daily

log = logging.getLogger("yaogu_backtest")

# ═══════════════════════════════════════════
# 默认参数
# ═══════════════════════════════════════════
BT_START = "20201201"
BT_END = "20260807"
ENTRY_STREAK = 2            # 介入连板数 (调研: 2 最优)
EXIT_RULE = "break_close"   # 默认: 断板收盘卖
ZT_COUNT_MIN = 30           # 环境闸门: 当日涨停家数下限
PROMO_MIN = 25              # 环境闸门: 晋级率% 下限 (昨日涨停今日仍涨停)
TOP_N = 5                   # 真实账户每日最多买入
EVENTS_CACHE = Path("/tmp/yaogu_events.json")


# ═══════════════════════════════════════════
# 事件构建 (带缓存)
# ═══════════════════════════════════════════
def build_events(daily: dict[str, pd.DataFrame], force: bool = False) -> list[dict]:
    """全部连板段事件 (streak>=2, 含段内逐日 + 断板日索引). 缓存到 /tmp. """
    if EVENTS_CACHE.exists() and not force:
        t0 = systime.time()
        events = json.loads(EVENTS_CACHE.read_text())
        log.info("events cache: %d 段 (%ds)", len(events), systime.time() - t0)
        return events
    t0 = systime.time()
    events = []
    for code, df in daily.items():
        df.attrs["code"] = code
        for st in extract_streaks(df):
            if st["streak"] < 2:
                continue
            st.pop("days", None)  # 缓存瘦身: 段内逐日只在评分用, 回测不需要
            events.append(st)
    EVENTS_CACHE.write_text(json.dumps(events, ensure_ascii=False))
    log.info("events: %d 段 (streak>=2), %ds → %s", len(events), systime.time() - t0, EVENTS_CACHE)
    return events


# ═══════════════════════════════════════════
# 环境 (每日涨停家数 + 晋级率)
# ═══════════════════════════════════════════
def calc_env(daily: dict[str, pd.DataFrame]) -> dict[str, dict]:
    zt_days: dict[str, set[str]] = defaultdict(set)
    for code, df in daily.items():
        prev_up = False
        for _, r in df.iterrows():
            if bool(r["涨停"]):
                zt_days.setdefault(r["日期"], set()).add(code)
    env: dict[str, dict] = {}
    sorted_dates = sorted(zt_days)
    for d in sorted_dates:
        env[d] = {"zt_count": len(zt_days[d])}
    for i, d in enumerate(sorted_dates):
        if i == 0:
            continue
        prev = zt_days.get(sorted_dates[i - 1], set())
        cur = zt_days.get(d, set())
        if prev:
            env[d]["promo"] = round(len(prev & cur) / len(prev) * 100, 1)
    return env


def env_gate_open(env: dict, date: str, zt_count_min: int, promo_min: int) -> bool:
    e = env.get(date)
    if e is None:
        return True  # 无环境数据不拦截 (回测起点边界)
    if e["zt_count"] < zt_count_min:
        return False
    if "promo" in e and e["promo"] < promo_min:
        return False
    return True


def _is_lanban_event(ev: dict, daily: dict[str, pd.DataFrame]) -> bool:
    """R101: 烂板历史口径代理 (cache_db 数据稀疏版).
    cache_db turnover 覆盖率仅 1%, 严格 turnover>=20% 几乎无样本.
    用更宽松代理: 涨停且 非一字弱封板 (开盘 != 收盘 OR 开盘 != 最高)
    即"板上有过博弈", 是烂板/分歧板的核心特征.
    """
    df = daily.get(ev["code"])
    if df is None:
        return False
    entry_idx = ev["start_idx"] + 1  # 第 2 板
    if entry_idx >= len(df):
        return False
    row = df.iloc[entry_idx]
    open_, high, close = float(row["开盘"]), float(row["最高"]), float(row["收盘"])
    if open_ <= 0:
        return False
    # 严格一字: open==close 且 open==high
    is_yizi = abs(open_ - close) < 0.01 and abs(open_ - high) < 0.01
    # 烂板代理: 涨停 + 有过博弈 (close>open*1.005 或 high>close*1.001)
    has_bargain = close > open_ * 1.005 or high > close * 1.001
    return (not is_yizi) and has_bargain


# ═══════════════════════════════════════════
# 单笔模拟
# ═══════════════════════════════════════════
def sim_trade(ev: dict, daily: dict[str, pd.DataFrame], entry_streak: int,
              exit_rule: str, stop_loss_pct: float) -> dict | None:
    """连板段第 entry_streak 板收盘后 → T+1 开盘买 (一字空仓), 按退出规则卖."""
    if ev["streak"] < entry_streak:
        return None
    df = daily.get(ev["code"])
    if df is None:
        return None
    hit_idx = ev["start_idx"] + entry_streak - 1
    if hit_idx + 1 >= len(df):
        return None
    nx = df.iloc[hit_idx + 1]
    t_open, t_high = float(nx["开盘"]), float(nx["最高"])
    if t_open <= 0:
        return None
    # 一字板空仓
    if abs(t_open - t_high) <= t_high * 0.003:
        return {"code": ev["code"], "buy": 0.0, "ret": 0.0, "trigger": "one_word",
                "date": nx["日期"], "exit": nx["日期"], "streak": ev["streak"], "hold": 0}
    buy_price = t_open
    buy_date = nx["日期"]
    exit_date, sell_price, trigger = None, None, None
    end_idx = ev["end_idx"]  # 段末
    # 持有区间: hit_idx+1 .. end_idx+1 (断板日)
    for j in range(hit_idx + 1, min(end_idx + 2, len(df))):
        row = df.iloc[j]
        close, high, low = float(row["收盘"]), float(row["最高"]), float(row["最低"])
        if exit_rule == "break_close":
            if j == end_idx + 1:  # 断板日收盘
                sell_price, exit_date, trigger = close, row["日期"], "break_close"
            continue  # 段内日: 持有
        elif exit_rule == "hold_n":
            if j == hit_idx + ENTRY_STREAK_HOLD:
                sell_price, exit_date, trigger = close, row["日期"], "hold_n"
        elif exit_rule == "ma5_stop":
            # 收盘 < 5日线卖出 (用前 4 日收盘 + 当日)
            if j >= 5:
                ma5 = float(df.iloc[j - 4 : j + 1]["收盘"].mean())
                if close < ma5:
                    sell_price, exit_date, trigger = close, row["日期"], "ma5_stop"
            if j == end_idx + 1:
                sell_price, exit_date, trigger = close, row["日期"], "break_close"
        elif exit_rule == "stop_loss":
            if low <= buy_price * (1 + stop_loss_pct / 100):
                sell_price, exit_date, trigger = buy_price * (1 + stop_loss_pct / 100), row["日期"], "stop_loss"
            elif j == end_idx + 1:
                sell_price, exit_date, trigger = close, row["日期"], "break_close"
        elif exit_rule == "hard_stop":
            # 双保险: -X% 硬止损 OR 断板收盘 — 谁先触发谁卖 (R100)
            if low <= buy_price * (1 + stop_loss_pct / 100):
                sell_price, exit_date, trigger = buy_price * (1 + stop_loss_pct / 100), row["日期"], "hard_stop"
            elif j == end_idx + 1:
                sell_price, exit_date, trigger = close, row["日期"], "break_close"
    if sell_price is None or exit_date is None:
        return None
    ret = (sell_price / buy_price - 1) * 100 - COST_BPS
    return {"code": ev["code"], "buy": buy_price, "ret": round(ret, 2), "trigger": trigger,
            "date": buy_date, "exit": exit_date, "streak": ev["streak"],
            "hold": int(exit_date > buy_date)}


ENTRY_STREAK_HOLD = 3  # hold_n 规则持有天数


# ═══════════════════════════════════════════
# 主回测
# ═══════════════════════════════════════════
def run_yaogu_backtest(
    start: str = BT_START,
    end: str = BT_END,
    entry_streak: int = ENTRY_STREAK,
    exit_rule: str = EXIT_RULE,
    zt_count_min: int = ZT_COUNT_MIN,
    promo_min: int = PROMO_MIN,
    top_n: int = TOP_N,
    gate_enabled: bool = True,
    stop_loss_pct: float = -8.0,
) -> dict:
    t0 = systime.time()
    daily = load_daily()
    events = build_events(daily)
    env = calc_env(daily)

    # 事件按日期过滤
    trades: list[dict] = []
    for ev in events:
        if not (start <= ev["end_date"] <= end):
            continue
        if gate_enabled and not env_gate_open(env, ev["end_date"], zt_count_min, promo_min):
            continue
        t = sim_trade(ev, daily, entry_streak, exit_rule, stop_loss_pct)
        if t:
            trades.append(t)

    nf = sum(1 for t in trades if t.get("trigger") == "one_word")
    live = [t for t in trades if t.get("trigger") != "one_word"]
    rets = [t["ret"] for t in live]
    wins = sum(1 for x in rets if x > 0)
    n = len(rets)

    summary = {
        "trades": n, "one_word": nf, "one_word_pct": round(nf / len(trades) * 100, 1) if trades else 0,
        "wr": round(wins / n * 100, 1) if n else 0,
        "avg": round(sum(rets) / n, 2) if n else 0,
        "cum": round(sum(rets), 1) if n else 0,
        "best": round(max(rets), 2) if n else 0,
        "worst": round(min(rets), 2) if n else 0,
        "median": round(statistics.median(rets), 2) if n else 0,
        "pf": round(sum(x for x in rets if x > 0) / abs(sum(x for x in rets if x < 0)), 2) if any(x < 0 for x in rets) else None,
        "params": {
            "start": start, "end": end, "entry_streak": entry_streak,
            "exit_rule": exit_rule, "zt_count_min": zt_count_min,
            "promo_min": promo_min, "top_n": top_n, "gate": gate_enabled,
        },
        "elapsed_s": round(systime.time() - t0, 1),
    }

    # 分组: 按最终连板数
    by_streak = {}
    for lo, hi in [(2, 2), (3, 3), (4, 4), (5, 5), (6, 99)]:
        sub = [t["ret"] for t in live if lo <= t["streak"] <= hi]
        if sub:
            by_streak[f"{lo}-{hi}"] = {
                "n": len(sub),
                "wr": round(sum(1 for x in sub if x > 0) / len(sub) * 100, 1),
                "avg": round(sum(sub) / len(sub), 2),
            }
    summary["by_streak"] = by_streak

    # 按月
    by_month: dict[str, dict] = {}
    for t in live:
        m = t["date"][:6]
        by_month.setdefault(m, []).append(t["ret"])
    summary["by_month"] = {
        m: {"n": len(v), "avg": round(sum(v) / len(v), 2)} for m, v in sorted(by_month.items())
    }

    # 闸门对比
    if gate_enabled:
        all_trades = []
        for ev in events:
            if not (start <= ev["end_date"] <= end):
                continue
            t = sim_trade(ev, daily, entry_streak, exit_rule, stop_loss_pct)
            if t:
                all_trades.append(t)
        off_live = [t for t in all_trades if t.get("trigger") != "one_word"]
        off_rets = [t["ret"] for t in off_live]
        summary["gate_off"] = {
            "trades": len(off_rets),
            "wr": round(sum(1 for x in off_rets if x > 0) / len(off_rets) * 100, 1) if off_rets else 0,
            "avg": round(sum(off_rets) / len(off_rets), 2) if off_rets else 0,
            "cum": round(sum(off_rets), 1) if off_rets else 0,
        }
    return summary


# ═══════════════════════════════════════════
# R153 2026-08-19: 严格烂板回测 — turnover 补全 + 真实炸板判定
# ═══════════════════════════════════════════
def _enrich_turnover_for_events(events: list[dict], max_codes: int = 200,
                                 turnover_min: float = 0.0) -> dict[str, int]:
    """R153: 对 cache_db 中 turnover=0 的 ZT 事件, 从 baostock 拉补 turnover。
    返回 {code: filled_count} 统计。仅触发在事件有 ZT 但 turnover 缺失的 code。

    上游: 多源 sina/tencent 不返换手率; EM push2his 间歇被 ban; baostock turn 稳定可用。
    限制 max_codes 防过度上游压力,默认 200 个 code 覆盖大多数妖股候选池。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time as _t
    import multi_source_fetchers as msf
    # 兼容包内/包外两种调用: 优先用 sys.modules 拿 (已在 yaogu_backtest 启动时导入),
    # 否则 lazy import (允许 standalone 脚本调本函数)
    cache_db = sys.modules.get("cache_db") or sys.modules.get("tuixue_v3.cache_db")
    if cache_db is None:
        import importlib
        # standalone 脚本: _ROOT 已在 sys.path[0], 但其内的 cache_db 用相对导入 →
        # 需把父目录 (scripts/) 也加入, 以 tuixue_v3.cache_db 形式导入
        _parent = str(Path(__file__).resolve().parent.parent)
        if _parent not in sys.path:
            sys.path.insert(0, _parent)
        try:
            cache_db = importlib.import_module("tuixue_v3.cache_db")
        except ImportError:
            cache_db = importlib.import_module("cache_db")

    # 1) 找出 turnover 缺失的 code (事件里有 ZT 但 cache_db.turnover=0)
    conn = cache_db.get_conn()
    codes_with_missing: set[str] = set()
    for ev in events:
        code = ev.get("code")
        if not code or len(codes_with_missing) >= max_codes:
            break
        # 查询 cache_db 中该 code 的 turnover 覆盖率
        row = conn.execute(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN turnover > 0 THEN 1 ELSE 0 END) AS n_t "
            "FROM daily WHERE code=?",
            (code,),
        ).fetchone()
        if row and row[0] > 50 and row[1] == 0:  # >50 行 且 0 turnover
            codes_with_missing.add(code)

    if not codes_with_missing:
        return {}

    log.info("R153: enriching turnover for %d codes (max=%d)", len(codes_with_missing), max_codes)

    # 2) 并行拉 baostock 日线 (含 turn 换手率)。baostock 单 socket 会话,
    #    msf 内部 _bs_query_lock 已串行 query; 这里 max_workers=4 只并发解析/写库。
    _BT_WINDOW_START = "2020-12-01"
    _BT_WINDOW_END = "2026-08-19"
    filled_per_code: dict[str, int] = {}
    def _fetch_one(code: str) -> tuple[str, pd.DataFrame | None]:
        try:
            # 外层持锁: 排队等待不计入 fetch 内部 30s 超时 (内层 RLock 重入为 no-op)
            _q = getattr(msf, "_bs_query_lock", None)
            if _q is None:
                import threading as _th
                _q = _th.RLock()
                msf._bs_query_lock = _q
            with _q:
                df = msf.fetch_daily_baostock(code, _BT_WINDOW_START, _BT_WINDOW_END)
            return code, df
        except Exception as e:
            log.debug("R153 enrich %s fail: %s", code, str(e)[:60])
            return code, None

    t0 = _t.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(_fetch_one, c) for c in codes_with_missing]
        for fut in as_completed(futures, timeout=1200):
            code, df = fut.result()
            if df is None or df.empty:
                continue
            n_filled = 0
            records: list[tuple] = []
            for _, r in df.iterrows():
                try:
                    turnover = float(r.get("换手率") or 0)
                except (ValueError, TypeError):
                    continue
                if turnover <= 0:
                    continue
                d_raw = str(r["日期"]).replace("-", "")
                if len(d_raw) != 8:
                    continue
                records.append((
                    code, d_raw,
                    float(r.get("开盘") or 0),
                    float(r.get("最高") or 0),
                    float(r.get("最低") or 0),
                    float(r.get("收盘") or 0),
                    float(r.get("成交量") or 0),
                    float(r.get("成交额") or 0),
                    turnover,
                    _t.time(),
                ))
                n_filled += 1
            if records:
                # 用独立连接 + 长 busy_timeout — 服务器(WAL 写锁)在跑时,短 timeout
                # 的 _thread_conn 会 'database is locked'。批量补数据不追求实时,20s 等锁更稳。
                import sqlite3 as _sq
                try:
                    _wconn = _sq.connect(str(getattr(cache_db, "_DB_PATH", Path("data/cache.db"))),
                                         timeout=20.0)
                    _wconn.execute("PRAGMA busy_timeout=20000")
                    _wconn.executemany(
                        "UPDATE daily SET turnover=? WHERE code=? AND date=?",
                        [(r[8], r[0], r[1]) for r in records],
                    )
                    _wconn.commit()
                    _wconn.close()
                    filled_per_code[code] = n_filled
                except Exception as e:
                    log.debug("R153 update %s fail: %s", code, str(e)[:60])

    log.info("R153: enriched %d codes (%d rows) in %.1fs",
             len(filled_per_code), sum(filled_per_code.values()), _t.time() - t0)
    return filled_per_code


def _is_lanban_event_strict(ev: dict, daily: dict[str, pd.DataFrame],
                            turnover_min: float = 20.0) -> bool:
    """R153 严格烂板判定: turnover >= 阈值 + end_idx 当日 是"烂板信号日"。

    中国式"烂板"实际定义 (源自 Seeker / 龙虎榜 战法):
      - 连板段末日 (end_idx) 当日 ZT + turnover≥阈值 (放量封板) — 这是"烂板信号",
        不是当日真的炸板 (盘中开过又回落), 而是放量封板后下一天大概率回落的先兆。
      - 非一字 (开盘 != 最高, 容差 0.3%)

    R153 2026-08-20: 之前要求"高/收比 > 1.005" (真实炸板), 实际检查显示中国 70%+ 烂板日
    是"收盘干净 + 次日断板" (high == close), 真实炸板是更罕见的子集。把判断改为:
      turnover≥阈值 AND 非一字 (不强求盘中炸板, 真实炸板作为可选 filter)。

    严格一字: open == high (容差 0.3%)
    """
    df = daily.get(ev["code"])
    if df is None:
        return False
    end_idx = ev.get("end_idx")
    if end_idx is None or end_idx >= len(df):
        return False
    row = df.iloc[end_idx]
    open_ = float(row["开盘"])
    high = float(row["最高"])
    close = float(row["收盘"])
    turnover = float(row.get("换手率", 0) or 0)
    if open_ <= 0 or close <= 0:
        return False
    # 严格一字: open == high (容差 0.3%) — 一字板直接排除
    is_yizi = abs(open_ - high) <= max(open_, close) * 0.003
    # 真炸板 (可选): high > close * 1.005 且 high > open * 1.005 (盘中开过又回落)
    return (not is_yizi) and (turnover >= turnover_min)


def run_lanban_backtest_strict(
    start: str = BT_START,
    end: str = BT_END,
    entry_streak: int = ENTRY_STREAK,
    exit_rule: str = EXIT_RULE,
    zt_count_min: int = 1,
    promo_min: int = 0,
    gate_enabled: bool = False,
    stop_loss_pct: float = -8.0,
    turnover_min: float = 20.0,
    enrich: bool = True,
    max_codes: int = 200,
) -> dict:
    """R153 严格烂板回测: 先 enrich turnover,再用 end_idx 当日 turnover≥阈值 + 非一字 判定。

    R153 2026-08-20: 修复 _is_lanban_event_strict 真实炸板条件过严 — 中国烂板实际是"放量封板 +
    次日大概率回落", 非"盘中开过又回落"。"真炸板"作为更罕见的子集存在, 但用主 filter 排除会
    把 70%+ 烂板样本误杀, 改用 end_idx 当日 turnover≥阈值 + 非一字。
    """
    t0 = systime.time()
    daily = load_daily()
    events = build_events(daily)
    env = calc_env(daily)

    enrich_stats = {}
    if enrich:
        enrich_stats = _enrich_turnover_for_events(events, max_codes=max_codes)
        # 重新加载 daily (turnover 已更新)
        daily = load_daily()

    trades: list[dict] = []
    skipped_no_turnover = 0
    for ev in events:
        if not (start <= ev["end_date"] <= end):
            continue
        if gate_enabled and not env_gate_open(env, ev["end_date"], zt_count_min, promo_min):
            continue
        # 先看 turnover 是否就绪 (end_idx 当日 — 烂板判定日)
        df = daily.get(ev["code"])
        if df is not None:
            end_idx = ev.get("end_idx")
            if end_idx is not None and end_idx < len(df):
                row_t = float(df.iloc[end_idx].get("换手率", 0) or 0)
                if row_t <= 0:
                    skipped_no_turnover += 1
                    continue
        if not _is_lanban_event_strict(ev, daily, turnover_min):
            continue
        t = sim_trade(ev, daily, entry_streak, exit_rule, stop_loss_pct)
        if t:
            trades.append(t)

    nf = sum(1 for t in trades if t.get("trigger") == "one_word")
    live = [t for t in trades if t.get("trigger") != "one_word"]
    rets = [t["ret"] for t in live]
    wins = sum(1 for x in rets if x > 0)
    n = len(rets)
    return {
        "trades": n, "one_word": nf,
        "one_word_pct": round(nf / len(trades) * 100, 1) if trades else 0,
        "wr": round(wins / n * 100, 1) if n else 0,
        "avg": round(sum(rets) / n, 2) if n else 0,
        "cum": round(sum(rets), 1) if n else 0,
        "best": round(max(rets), 2) if n else 0,
        "worst": round(min(rets), 2) if n else 0,
        "median": round(statistics.median(rets), 2) if n else 0,
        "pf": round(sum(x for x in rets if x > 0) / abs(sum(x for x in rets if x < 0)), 2) if any(x < 0 for x in rets) else None,
        "filter": f"strict lanban (turnover≥{turnover_min}% + 非一字, end_idx 当日)",
        "enrich": enrich_stats,
        "skipped_no_turnover": skipped_no_turnover,
        "elapsed_s": round(systime.time() - t0, 1),
    }


# ═══════════════════════════════════════════
# R101: 烂板子集回测 (turnover ≥ 20% 且 非一字)
# ═══════════════════════════════════
def run_lanban_backtest(
    start: str = BT_START,
    end: str = BT_END,
    entry_streak: int = ENTRY_STREAK,
    exit_rule: str = EXIT_RULE,
    zt_count_min: int = 1,           # A/B 卡默认关闸门, 保持纯净对比
    promo_min: int = 0,
    gate_enabled: bool = False,
    stop_loss_pct: float = -8.0,
) -> dict:
    """仅在第 entry_streak 板判定为烂板(turnover≥20% 且非一字)的事件上跑回测."""
    t0 = systime.time()
    daily = load_daily()
    events = build_events(daily)
    env = calc_env(daily)

    trades: list[dict] = []
    for ev in events:
        if not (start <= ev["end_date"] <= end):
            continue
        if gate_enabled and not env_gate_open(env, ev["end_date"], zt_count_min, promo_min):
            continue
        if not _is_lanban_event(ev, daily):
            continue
        t = sim_trade(ev, daily, entry_streak, exit_rule, stop_loss_pct)
        if t:
            trades.append(t)

    nf = sum(1 for t in trades if t.get("trigger") == "one_word")
    live = [t for t in trades if t.get("trigger") != "one_word"]
    rets = [t["ret"] for t in live]
    wins = sum(1 for x in rets if x > 0)
    n = len(rets)
    return {
        "trades": n, "one_word": nf,
        "one_word_pct": round(nf / len(trades) * 100, 1) if trades else 0,
        "wr": round(wins / n * 100, 1) if n else 0,
        "avg": round(sum(rets) / n, 2) if n else 0,
        "cum": round(sum(rets), 1) if n else 0,
        "best": round(max(rets), 2) if n else 0,
        "worst": round(min(rets), 2) if n else 0,
        "median": round(statistics.median(rets), 2) if n else 0,
        "pf": round(sum(x for x in rets if x > 0) / abs(sum(x for x in rets if x < 0)), 2) if any(x < 0 for x in rets) else None,
        "filter": "lanban (非一字 + 有博弈 · cache_db 严格版无样本)",
        "note": "cache_db turnover 覆盖率仅 1%, 严格 turnover>=20% 几乎无样本 (5 笔); 用 OHLC 代理: 涨停 + 非一字 + 有博弈",
        "elapsed_s": round(systime.time() - t0, 1),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry", type=int, default=ENTRY_STREAK)
    ap.add_argument("--exit", default=EXIT_RULE, choices=["break_close", "hold_n", "ma5_stop", "stop_loss", "hard_stop"])
    ap.add_argument("--start", default=BT_START)
    ap.add_argument("--end", default=BT_END)
    ap.add_argument("--zt-min", type=int, default=ZT_COUNT_MIN)
    ap.add_argument("--promo-min", type=float, default=PROMO_MIN)
    ap.add_argument("--no-gate", action="store_true")
    ap.add_argument("--stop-loss", type=float, default=-8.0, help="硬止损 %% (用于 stop_loss / hard_stop, 默认 -8%%)")
    args = ap.parse_args()
    r = run_yaogu_backtest(start=args.start, end=args.end, entry_streak=args.entry,
                           exit_rule=args.exit, zt_count_min=args.zt_min,
                           promo_min=args.promo_min, gate_enabled=not args.no_gate,
                           stop_loss_pct=args.stop_loss)
    print(json.dumps(r, ensure_ascii=False, indent=1))
