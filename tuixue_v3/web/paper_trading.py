"""
paper_trading.py — 涨停策略模拟盘引擎 (无真实资金)

初始 2W, 每天收盘后按涨停策略 (OPTIMAL_PARAMS) 推进:
  - 信号: 当日涨停池 → 过滤 (连板/封板时间/市值/换手/封单/非一字板) → top_n
  - 买入: T+1 开盘价 (open_t1), A股 100 股一手
  - 退出: stop_loss (-3%) / trail (0.5% 激活 + 1.5% 回撤), T+2 起可卖 (T+1 锁仓)
  - 数据源: cache_db 直读 (与 zt_backtest.build_zt_cache 同源), miss 才回源 fetch_daily
  - 杠杆: 1x (贴 A 股现实, 跟回测 2x 杠杆不同; 2026-08-03 用户确认不加杠杆)
sqlite 持久化, 跨进程安全 (safe_write 重试)。
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time as systime
from datetime import datetime

log = logging.getLogger("tuixue_v3.paper")

PAPER_INIT_CASH = 20_000.0
PAPER_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_trading.db")

# 交易参数 (对齐 zt_config.OPTIMAL_PARAMS — 10K 优化结果)
PAPER_TOP_N = 4                 # 2026-08-04: 每只 1/4 仓 — 每日最多 4 只候选同时持仓
PAPER_TRAIL_ACTIVATE = 0.3      # % 激活 (10K 优化: 0.3% 极早锁利)
PAPER_TRAIL_PULLBACK = 2.0      # % 回撤 (10K 优化: 2.0% 宽回撤让利润跑)
PAPER_STOP_LOSS = -3.0          # % 硬止损
PAPER_MAX_HOLD_DAYS = 3         # 最长持仓天数 (含买入日), 强制平仓
PAPER_SLIP_OUT = 0.005          # 止损挂单滑点
PAPER_FEE_RATE = 0.0003         # 佣金
PAPER_STAMP_RATE = 0.001        # 印花税 (卖出)
PAPER_LOT = 100                 # 一手 100 股

_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS paper_state (
            k TEXT PRIMARY KEY,
            v TEXT
        );
        CREATE TABLE IF NOT EXISTS paper_positions (
            code TEXT PRIMARY KEY,
            name TEXT, qty INTEGER, buy_price REAL, buy_date TEXT,
            trail_peak REAL, activate_level REAL, stop_price REAL,
            status TEXT DEFAULT 'holding'
        );
        CREATE TABLE IF NOT EXISTS paper_pending (
            code TEXT PRIMARY KEY,
            name TEXT, signal_date TEXT, streak INTEGER,
            reason TEXT
        );
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT, name TEXT,
            buy_date TEXT, buy_price REAL, qty INTEGER,
            sell_date TEXT, sell_price REAL,
            return_pct REAL, pnl REAL, trigger TEXT, hold_days INTEGER
        );
    """)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(PAPER_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    return conn


def _safe_write(fn):
    """写保护: retry + rollback, 防 database is locked。"""
    for attempt in range(5):
        conn = _connect()
        try:
            conn.execute("BEGIN")
            fn(conn)
            conn.commit()
            conn.close()
            return
        except sqlite3.OperationalError as e:
            try:
                conn.rollback()
            except Exception:
                pass
            conn.close()
            if "locked" in str(e).lower() and attempt < 4:
                systime.sleep(0.2 * (attempt + 1))
                continue
            raise
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            conn.close()
            raise


def _get_state() -> dict:
    conn = _connect()
    try:
        rows = conn.execute("SELECT k, v FROM paper_state").fetchall()
        return {r["k"]: r["v"] for r in rows}
    finally:
        conn.close()


def _set_state(key: str, value) -> None:
    def _fn(conn):
        conn.execute(
            "INSERT INTO paper_state(k, v) VALUES(?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, str(value)))
    _safe_write(_fn)


def get_account() -> dict:
    """账户总览: 现金/持仓/总资产/收益率 (2026-08-03: 总资产按实时价算)。"""
    state = _get_state()
    cash = float(state.get("cash", PAPER_INIT_CASH))
    positions = get_positions()
    pos_value = sum(p["value"] for p in positions)
    pos_cost = sum(p.get("cost", p["qty"] * p["buy_price"]) for p in positions)
    float_pnl = round(pos_value - pos_cost, 2)
    total = cash + pos_value
    init = float(state.get("init_cash", PAPER_INIT_CASH))
    ret_pct = (total / init - 1) * 100 if init > 0 else 0
    return {
        "init_cash": init,
        "cash": round(cash, 2),
        "positions_value": round(pos_value, 2),
        "positions_cost": round(pos_cost, 2),
        "float_pnl": float_pnl,
        "total": round(total, 2),
        "return_pct": round(ret_pct, 2),
        "position_count": len(positions),
        "last_run": state.get("last_run", ""),
        "started": state.get("started", ""),
    }


def get_positions() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM paper_positions WHERE status='holding'").fetchall()
        out = []
        for r in rows:
            qty = r["qty"]
            buy_price = r["buy_price"]
            # 2026-08-03: 持仓市值按最新价算 (实时浮盈浮亏), 没有最新价时回退买价
            current_price = _latest_price(r["code"]) or buy_price
            cost = qty * buy_price
            market_value = qty * current_price
            float_pnl = round(market_value - cost, 2)
            float_pct = round(float_pnl / cost * 100, 2) if cost > 0 else 0
            out.append({
                "code": r["code"], "name": r["name"],
                "qty": qty, "buy_price": buy_price,
                "buy_date": r["buy_date"],
                "current_price": round(current_price, 3),
                "value": round(market_value, 2),
                "cost": round(cost, 2),
                "float_pnl": float_pnl,
                "float_pct": float_pct,
                "activate_level": r["activate_level"],
                "stop_price": r["stop_price"],
            })
        return out
    finally:
        conn.close()


def get_trades(limit: int = 200) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM paper_trades ORDER BY buy_date DESC, id DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_pending() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM paper_pending ORDER BY signal_date DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _row_ohlc(code: str, date_ymd: str) -> dict | None:
    """取某股票在指定交易日的 OHLC (真实行情)。date_ymd: YYYYMMDD。

    2026-08-03: 优先 cache_db (回测同源, 零网络), miss 才回源 fetch_daily。
    """
    # 1) cache_db 直读 (与 zt_backtest.build_zt_cache 一致, cache_db 日期是无横杠 YYYYMMDD)
    try:
        from .. import cache_db as _cdb
        df = _cdb.daily().get(code, 200)
        if df is not None and not df.empty and "收盘" in df.columns:
            for _, row in df.iterrows():
                d = str(row.get("日期", ""))[:10].replace("-", "")
                if d == date_ymd:
                    return {
                        "open": float(row.get("开盘", 0) or 0),
                        "high": float(row.get("最高", 0) or 0),
                        "low": float(row.get("最低", 0) or 0),
                        "close": float(row.get("收盘", 0) or 0),
                    }
    except Exception as e:
        log.debug("paper cache_db miss %s: %s", code, e)
    # 2) fallback: 多源 fetch_daily
    try:
        from .. import lib_common as lc
        df = lc.fetch_daily(code, days=120)
        if df is None or df.empty:
            return None
        for _, row in df.iterrows():
            d = str(row.get("日期", ""))[:10].replace("-", "")
            if d == date_ymd:
                return {
                    "open": float(row.get("开盘", 0) or 0),
                    "high": float(row.get("最高", 0) or 0),
                    "low": float(row.get("最低", 0) or 0),
                    "close": float(row.get("收盘", 0) or 0),
                }
    except Exception as e:
        log.warning("row_ohlc fallback %s/%s err: %s", code, date_ymd, e)
    return None


def _latest_price(code: str) -> float | None:
    """2026-08-03: 取某股票最新价 (实时浮盈浮亏用)。

    优先 cache_db 直读 (与回测同源), miss 才回源。
    """
    try:
        from .. import cache_db as _cdb
        df = _cdb.daily().get(code, 5)
        if df is not None and not df.empty and "收盘" in df.columns:
            last = df.iloc[-1]
            c = float(last.get("收盘", 0) or 0)
            if c > 0:
                return c
    except Exception:
        pass
    try:
        from .. import lib_common as lc
        df = lc.fetch_daily(code, days=5)
        if df is None or df.empty:
            return None
        last = df.iloc[-1]
        c = float(last.get("收盘", 0) or 0)
        return c if c > 0 else None
    except Exception:
        return None


def _stock_name(code: str) -> str:
    try:
        from .all_stocks import _build_universe
        _, nm = _build_universe()
        return nm.get(code, nm.get(code[:6], "")) or ""
    except Exception:
        return ""


def _cost_in(price: float) -> float:
    return price * PAPER_FEE_RATE


def _cost_out(price: float) -> float:
    return price * (PAPER_FEE_RATE + PAPER_STAMP_RATE)


def _ret(buy_price: float, sell_price: float) -> float:
    if buy_price <= 0 or sell_price <= 0:
        return 0.0
    return (sell_price / buy_price - 1) * 100 - (_cost_in(buy_price) + _cost_out(sell_price)) / buy_price * 100


def _fetch_zt_signals(date_ymd: str) -> list[dict]:
    """当日涨停池 → OPTIMAL_PARAMS 过滤 → 按涨停强度排序。

    返回候选 [{code, name, streak, sector, market_cap_yi, turnover_pct,
               limit_order_yi, first_time, burst_count, is_yiziban, score}]
    """
    try:
        from .limit_up_context import _fetch_zt_pool
        from .. import zt_config as cfg
        from ..zt_backtest import _score_zt_candidate
    except Exception as e:
        log.warning("zt signal import err: %s", e)
        return []

    pool = _fetch_zt_pool(date_ymd)
    if not pool:
        return []

    p = cfg.OPTIMAL_PARAMS
    cands = []
    for row in pool:
        code = str(row.get("代码", "")).strip().zfill(6)
        streak = int(row.get("连板数", 1) or 1)
        if streak < p["min_streak"] or streak > p["max_streak"]:
            continue
        burst = int(row.get("炸板次数", 0) or 0)
        if burst > p["burst_max"]:
            continue
        ft = str(row.get("首次封板时间", "") or "")
        if ft and p["sealed_before"] != "11:30":
            try:
                h, m = ft.strip().split(":")
                if int(h) * 60 + int(m) > int(p["sealed_before"].split(":")[0]) * 60 + int(p["sealed_before"].split(":")[1]):
                    continue
            except Exception:
                pass
        mcap = float(row.get("总市值", 0) or 0)
        if mcap > 1e6:
            mcap /= 1e8
        if mcap > 0 and (mcap < p["mcap_min_yi"] or mcap > p["mcap_max_yi"]):
            continue
        turn = float(row.get("换手率", 0) or 0)
        if turn > 0 and (turn < p["turnover_min_pct"] or turn > p["turnover_max_pct"]):
            continue
        lamt = float(row.get("封板资金", 0) or 0)
        if lamt > 1e6:
            lamt /= 1e8
        if lamt > 0 and lamt < p["limit_order_min_yi"]:
            continue
        if p.get("exclude_yiziban") and row.get("一字涨停", False):
            continue
        # 2026-08-03: 板块联动 — 板块当日涨停数下限 (与回测 min_sector_zt_count 一致)
        min_sec_cnt = int(p.get("min_sector_zt_count", 0))
        if min_sec_cnt > 0:
            sec_cnt = int(row.get("sector_zt_count", 0) or 0)
            if sec_cnt < min_sec_cnt:
                continue
        # 归一化字段名 → zt_backtest._score_zt_candidate 需要的
        norm = {
            "code": code,
            "name": str(row.get("名称", "") or ""),
            "streak": streak,
            "burst_count": burst,
            "market_cap": mcap * 1e8,
            "turnover_pct": turn,
            "limit_order_amount": lamt * 1e8,
            "first_time": ft,
            "is_yiziban": bool(row.get("一字涨停", False)),
            "sector": str(row.get("所属行业", "") or ""),
        }
        cands.append((norm, _score_zt_candidate(norm)))

    cands.sort(key=lambda x: -x[1])
    out = []
    for norm, score in cands[:PAPER_TOP_N]:
        out.append({**norm, "score": round(score, 1)})
    return out


def daily_run(date_ymd: str | None = None) -> dict:
    """模拟盘每日推进。date_ymd: YYYYMMDD (默认今天)。"""
    if date_ymd is None:
        date_ymd = datetime.now().strftime("%Y%m%d")

    if not _get_state().get("started"):
        _set_state("started", _now())
        _set_state("init_cash", PAPER_INIT_CASH)
        _set_state("cash", PAPER_INIT_CASH)

    cash = float(_get_state().get("cash", PAPER_INIT_CASH))
    new_trades: list[dict] = []
    fills: list[dict] = []
    skipped = []

    # ── 0) 批量预取 OHLC: 持仓 + pending 并行 fetch_daily,避免单只 12s 超时阻断 ──
    pos_list = get_positions()
    pendings = get_pending()
    need_codes = sorted({p["code"] for p in pos_list if p["buy_date"] < date_ymd} |
                        {p["code"] for p in pendings})
    ohlc_cache: dict[str, dict | None] = {}
    if need_codes:
        # 顺序 fetch — 8 路并发撞上游限频全熔断, 实测单线程 2-3s/只 反而最稳
        for c in need_codes:
            ohlc_cache[c] = _row_ohlc(c, date_ymd)

    def _sell(pos: dict, sell_price: float, trigger: str, date_ymd: str):
        nonlocal cash
        sell_price = round(sell_price, 3)
        pnl = round(pos["qty"] * (sell_price - pos["buy_price"])
                     - pos["qty"] * _cost_out(sell_price) - pos["qty"] * _cost_in(pos["buy_price"]), 2)
        ret = round(_ret(pos["buy_price"], sell_price), 2)
        hold_days = 0
        try:
            d0 = datetime.strptime(pos["buy_date"], "%Y%m%d")
            d1 = datetime.strptime(date_ymd, "%Y%m%d")
            hold_days = max(0, (d1 - d0).days)
        except Exception:
            pass
        cash += pos["qty"] * sell_price
        new_trades.append({
            "code": pos["code"], "name": pos["name"],
            "buy_date": pos["buy_date"], "buy_price": pos["buy_price"],
            "qty": pos["qty"], "sell_date": date_ymd, "sell_price": sell_price,
            "return_pct": ret, "pnl": pnl, "trigger": trigger, "hold_days": hold_days,
        })

    # ── 1) 持仓退出检查 (T+2 起可卖, 买入日当天锁仓) ──
    for pos in pos_list:
        if pos["buy_date"] >= date_ymd:
            continue  # 当天买入, T+1 锁仓
        # 2026-08-03: 最长持仓天数检查, 强制平仓 (按当日 open 价)
        hold_days = 0
        try:
            d0 = datetime.strptime(pos["buy_date"], "%Y%m%d")
            d1 = datetime.strptime(date_ymd, "%Y%m%d")
            hold_days = max(0, (d1 - d0).days)
        except Exception:
            pass
        ohlc = ohlc_cache.get(pos["code"])
        if ohlc is None:
            skipped.append(f"{pos['code']} 无当日行情, 继续持有")
            continue
        low, high, open_ = ohlc["low"], ohlc["high"], ohlc["open"]
        stop_price = pos["stop_price"]
        activate = pos["activate_level"]
        # stop_loss 优先 (跳空低开按开盘价出)
        if low <= stop_price:
            actual = open_ if open_ <= stop_price else stop_price * (1 - PAPER_SLIP_OUT)
            _sell(pos, actual, "stop_loss", date_ymd)
        elif high >= activate:
            pullback_price = high * (1 - PAPER_TRAIL_PULLBACK / 100)
            actual = max(pullback_price, low)
            _sell(pos, actual, "trail", date_ymd)
        elif hold_days >= PAPER_MAX_HOLD_DAYS:
            # 超过最长持仓天数, 强制按 open 平仓
            _sell(pos, open_, "max_hold", date_ymd)
        # 都没触发 → 继续持有

    # ── 2) pending 买入: 昨日信号 → 今开成交 ──
    # 2026-08-04: 每只 1/4 仓 — 单只预算 = 初始资金 5000 ÷ 当前候选数
    # 但已持仓部分不参与, 所以预算 = 剩余现金 ÷ (候选数 - 已持仓数)
    pending_budget_count = len(pendings)
    held_count = sum(1 for pos in pos_list if pos.get("buy_date", "") < date_ymd)
    if pending_budget_count > held_count:
        budget_per = cash / (pending_budget_count - held_count)
    else:
        budget_per = cash
    for p in pendings:
        ohlc = ohlc_cache.get(p["code"])
        if ohlc is None or ohlc["open"] <= 0:
            skipped.append(f"{p['code']} {p['name']} 今日无开盘价, 跳过买入")
            continue
        open_price = ohlc["open"]
        # 单只仓位 = 1/4 初始资金 (5000 元) — 用户指定
        alloc = min(budget_per, 5000.0)
        if alloc < open_price * PAPER_LOT:
            skipped.append(f"{p['code']} {p['name']} 资金不足一手 (预算 {alloc:.0f} < {open_price*100:.0f})")
            continue
        qty = int(alloc / (open_price * PAPER_LOT)) * PAPER_LOT
        if qty < PAPER_LOT:
            qty = PAPER_LOT  # 至少买一手
        cost = qty * open_price * (1 + PAPER_FEE_RATE)
        if cost > cash:
            qty = int(cash / (open_price * PAPER_LOT * (1 + PAPER_FEE_RATE))) * PAPER_LOT
            if qty < PAPER_LOT:
                skipped.append(f"{p['code']} {p['name']} 资金不足一手")
                continue
            cost = qty * open_price * (1 + PAPER_FEE_RATE)
        cash -= cost
        fills.append({
            "code": p["code"], "name": p["name"],
            "buy_date": date_ymd, "buy_price": round(open_price, 3), "qty": qty,
            "activate_level": round(open_price * (1 + PAPER_TRAIL_ACTIVATE / 100), 3),
            "stop_price": round(open_price * (1 + PAPER_STOP_LOSS / 100), 3),
        })

    # ── 3) 新信号: 当日涨停池 → 明日开盘买入 ──
    signals = _fetch_zt_signals(date_ymd)
    new_pending = 0
    for s in signals:
        def _add(conn):
            conn.execute(
                "INSERT INTO paper_pending(code, name, signal_date, streak, reason) "
                "VALUES(?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET "
                "name=excluded.name, signal_date=excluded.signal_date, "
                "streak=excluded.streak, reason=excluded.reason",
                (s["code"], s["name"], date_ymd, s["streak"],
                 f"连板{s['streak']} 分数{s['score']}"))
        _safe_write(_add)
        new_pending += 1

    # ── 落库 ──
    def _commit(conn):
        if new_trades:
            conn.executemany(
                "INSERT INTO paper_trades(code, name, buy_date, buy_price, qty, "
                "sell_date, sell_price, return_pct, pnl, trigger, hold_days) "
                "VALUES(:code, :name, :buy_date, :buy_price, :qty, :sell_date, "
                ":sell_price, :return_pct, :pnl, :trigger, :hold_days)",
                new_trades)
            # 2026-08-03: 已平仓的持仓从 holding 改为 closed,避免下次 daily_run 重复评估
            for nt in new_trades:
                conn.execute(
                    "UPDATE paper_positions SET status='closed' WHERE code=?",
                    (nt["code"],))
        if fills:
            conn.executemany(
                "INSERT INTO paper_positions(code, name, qty, buy_price, buy_date, "
                "trail_peak, activate_level, stop_price, status) "
                "VALUES(:code, :name, :qty, :buy_price, :buy_date, "
                ":buy_price, :activate_level, :stop_price, 'holding') "
                "ON CONFLICT(code) DO UPDATE SET name=excluded.name, qty=excluded.qty, "
                "buy_price=excluded.buy_price, buy_date=excluded.buy_date, "
                "activate_level=excluded.activate_level, stop_price=excluded.stop_price, "
                "status='holding'",
                fills)
        # 清掉已处理/已成交的 pending (成交 + 跳过资金不足的 → 都清, 信号过期不重试)
        for f in fills:
            conn.execute("DELETE FROM paper_pending WHERE code=?", (f["code"],))
        for p in pendings:
            conn.execute("DELETE FROM paper_pending WHERE code=?", (p["code"],))
        conn.execute(
            "INSERT INTO paper_state(k, v) VALUES('cash', ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (str(round(cash, 2)),))
        conn.execute(
            "INSERT INTO paper_state(k, v) VALUES('last_run', ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (f"{date_ymd} {_now()}",))
    _safe_write(_commit)

    acc = get_account()
    return {
        "date": date_ymd,
        "new_trades": new_trades,
        "fills": fills,
        "new_signals": signals,
        "skipped": skipped,
        "account": acc,
    }


def reset_account(init_cash: float = PAPER_INIT_CASH) -> dict:
    """清空账户重置。"""
    def _fn(conn):
        conn.execute("DELETE FROM paper_positions")
        conn.execute("DELETE FROM paper_pending")
        conn.execute("DELETE FROM paper_trades")
        conn.execute("DELETE FROM paper_state")
        conn.execute("INSERT INTO paper_state(k, v) VALUES('init_cash', ?)", (str(init_cash),))
        conn.execute("INSERT INTO paper_state(k, v) VALUES('cash', ?)", (str(init_cash),))
        conn.execute("INSERT INTO paper_state(k, v) VALUES('started', ?)", (_now(),))
    _safe_write(_fn)
    return get_account()
