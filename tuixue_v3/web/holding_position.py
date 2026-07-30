"""
web/holding_position.py — 用户持仓盈亏聚合 (2026-07-30)

为 AI 深度判断 (适不适合卖) 提供持仓维度
- 接 cache_db.trades 表
- FIFO 计算平均持仓成本 + 当前持仓股数
- 提供浮盈率 / 持仓天数 / 最近加仓价
- 若无持仓返 has_position=False,前端显示 "无持仓 · 按策略建议"
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _safe_float(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_code(code: str) -> str:
    """统一 6 位股票代码 — 兼容 "600519" / "sh600519" / 6 多种格式"""
    s = str(code or "").strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if s.startswith(prefix):
            s = s[2:]
            break
    return s.zfill(6)


def get_holding_view(code: str, current_price: float | None = None) -> dict:
    """返回该股票的持仓视图。

    Args:
        code: 股票代码 (6 位,可带 sh/sz/bj 前缀)
        current_price: 当前最新价;若未传则 attempt 从 quote fetch (略,本期 nil)

    Returns:
        {
          has_position:           bool
          symbol:                 str — 6 位标准代码
          shares:                 int — 当前持仓股数
          avg_cost:               float — 加权平均成本价
          total_cost:             float — 总成本 (元)
          market_value:           float — 当前市值 (元)
          last_price:             float — 当前价格
          unrealized_pnl_yuan:    float — 浮盈 (元)
          unrealized_pnl_pct:     float — 浮盈率 (%)
          first_buy_date:         str — 首笔买入 YYYYMMDD
          last_buy_date:          str — 最近加仓 YYYYMMDD
          last_buy_price:         float
          days_held:              int — 持有天数 (首笔→今日)
          buy_count / sell_count: int
          realized_pnl_yuan:      float — 历史已实现盈亏 (FIFO 卖出已扣)
        }
    """
    symbol = _normalize_code(code)
    today_yyyymmdd = datetime.now().strftime("%Y%m%d")

    out = {
        "has_position": False,
        "symbol": symbol,
        "shares": 0,
        "avg_cost": 0.0,
        "total_cost": 0.0,
        "market_value": 0.0,
        "last_price": _safe_float(current_price),
        "unrealized_pnl_yuan": 0.0,
        "unrealized_pnl_pct": 0.0,
        "first_buy_date": "",
        "last_buy_date": "",
        "last_buy_price": 0.0,
        "days_held": 0,
        "buy_count": 0,
        "sell_count": 0,
        "realized_pnl_yuan": 0.0,
    }

    try:
        from . import review as _review
        # review.list_trades(limit, code, since_days) → list[dict]
        trades = _review.list_trades(limit=99999, code=symbol, since_days=365 * 5) or []
    except Exception as e:
        import logging
        log = logging.getLogger("tuixue_v3.web.holding")
        log.debug(f"holding {symbol} trades list fail: {e}")
        return out

    if not trades:
        return out

    # FIFO 算法
    # 按 trade_date 升序处理
    lots: list[dict] = []  # [{date, shares, price}, ...]  未平仓的买 batch
    realized = 0.0
    first_buy = ""
    last_buy = ""
    last_buy_price = 0.0
    buy_n = 0
    sell_n = 0

    # trades 可能是 dict 列表,按时间升序
    t_sorted = sorted(trades, key=lambda t: (t.get("trade_date") or "", t.get("id") or 0))

    for t in t_sorted:
        d = str(t.get("trade_date") or "")
        direction = str(t.get("direction") or "").lower()
        shares = int(t.get("shares") or 0)
        price = _safe_float(t.get("price"))
        if not d or shares <= 0 or price <= 0:
            continue

        if direction == "buy":
            lots.append({"date": d, "shares": shares, "price": price})
            buy_n += 1
            if not first_buy:
                first_buy = d
            last_buy = d
            last_buy_price = price
        elif direction == "sell":
            sell_n += 1
            remaining = shares
            cost_basis = 0.0
            while remaining > 0 and lots:
                lot = lots[0]
                take = min(remaining, lot["shares"])
                cost_per_share = lot["price"]
                cost_basis += take * cost_per_share
                # 已实现盈亏 = (sell_price - cost_basis) * take
                realized += (price - cost_per_share) * take
                lot["shares"] -= take
                remaining -= take
                if lot["shares"] <= 0:
                    lots.pop(0)
            # remaining > 0 说明卖空 (用户没持仓),忽略超卖部分

    # 计算未平仓
    pos_shares = sum(int(lot["shares"]) for lot in lots)
    if pos_shares > 0 and lots:
        total_cost = sum(lot["shares"] * lot["price"] for lot in lots)
        avg_cost = total_cost / pos_shares
    else:
        total_cost = 0.0
        avg_cost = 0.0

    cp = _safe_float(current_price)
    market_value = pos_shares * cp if pos_shares > 0 and cp > 0 else 0.0
    pnl_yuan = (cp - avg_cost) * pos_shares if pos_shares > 0 and cp > 0 and avg_cost > 0 else 0.0
    pnl_pct = ((cp - avg_cost) / avg_cost * 100) if pos_shares > 0 and cp > 0 and avg_cost > 0 else 0.0

    # 持有天数 (首笔买入 → 今日)
    days_held = 0
    if first_buy:
        try:
            d1 = datetime.strptime(first_buy, "%Y%m%d")
            d2 = datetime.strptime(today_yyyymmdd, "%Y%m%d")
            days_held = max(0, (d2 - d1).days)
        except ValueError:
            pass

    out.update({
        "has_position": pos_shares > 0,
        "shares": pos_shares,
        "avg_cost": round(avg_cost, 3),
        "total_cost": round(total_cost, 2),
        "market_value": round(market_value, 2),
        "last_price": cp,
        "unrealized_pnl_yuan": round(pnl_yuan, 2),
        "unrealized_pnl_pct": round(pnl_pct, 2),
        "first_buy_date": first_buy,
        "last_buy_date": last_buy,
        "last_buy_price": round(last_buy_price, 3),
        "days_held": days_held,
        "buy_count": buy_n,
        "sell_count": sell_n,
        "realized_pnl_yuan": round(realized, 2),
    })

    return out


def summarize_for_prompt(view: dict) -> str:
    """LLM 用: ≤200 字符摘要,无持仓返 \"无持仓\"。"""
    v = view or {}
    if not v.get("has_position"):
        return "用户无持仓 · 按策略/技术面建议"
    sym = v.get("symbol", "")
    sh = v.get("shares", 0)
    cost = v.get("avg_cost", 0)
    cur = v.get("last_price", 0)
    pnl_pct = v.get("unrealized_pnl_pct", 0)
    days = v.get("days_held", 0)
    last_buy = v.get("last_buy_date", "")
    pct_str = f"{pnl_pct:+.1f}%"
    return (
        f"{sym} 持仓 {sh}股 成本 ¥{cost:.2f} 现价 ¥{cur:.2f} "
        f"浮盈 {pct_str} 持仓 {days}日 末次加仓 {last_buy}"
    )
