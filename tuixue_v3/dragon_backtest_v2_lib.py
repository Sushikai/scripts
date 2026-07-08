#!/usr/bin/env python3
"""
stock/dragon_backtest_v2_lib.py
v2 回测基础设施库（与 dragon_scanner_v2.py 解耦）。

提供：
  - 缓存 I/O（~/.hermes/cache/dragon_backtest_v2/）
  - 多源糅合（涨停池/龙虎榜/席位）— 含 try/except + 重试 + 兜底
  - 退学骨架预加载（情绪/主线/新闻/每日数据）
  - 首次运行迁移 scripts/ 下旧缓存

不依赖 dragon_backtest_v2.py。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from . import multi_source_fetchers as msf

log = logging.getLogger("dragon_backtest_v2_lib")

# ═══════════════════════════════════════════════════════
# 缓存路径统一管理（~/.hermes/cache/<component>/ 约定）
# ═══════════════════════════════════════════════════════
CACHE_DIR = Path.home() / ".hermes" / "cache" / "dragon_backtest_v2"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DAILY_CACHE_FILE     = CACHE_DIR / "dragon_backtest_v2_daily_cache.json"
EMOTION_CACHE_FILE   = CACHE_DIR / "dragon_backtest_v2_emotion_calendar.json"
MAINLINE_CACHE_FILE  = CACHE_DIR / "dragon_backtest_v2_mainline_calendar.json"
NEWS_CACHE_FILE      = CACHE_DIR / "dragon_backtest_v2_news_cache.json"
HISTORY_CACHE_FILE   = CACHE_DIR / "dragon_backtest_v2_history_cache.json"
FAILED_CACHE_FILE    = CACHE_DIR / "dragon_backtest_v2_failed_cache.json"
NAME_CODE_CACHE_FILE = CACHE_DIR / "dragon_backtest_v2_name_code_cache.json"
SECTOR_CODES_CACHE_FILE = CACHE_DIR / "dragon_backtest_v2_sector_codes.json"


# ═══════════════════════════════════════════════════════
# 旧缓存迁移（一次性）
# ═══════════════════════════════════════════════════════
_LEGACY_FILES = [
    "dragon_backtest_v2_daily_cache.json",
    "dragon_backtest_v2_emotion_calendar.json",
    "dragon_backtest_v2_mainline_calendar.json",
    "dragon_backtest_v2_news_cache.json",
    "dragon_backtest_v2_history_cache.json",
    "dragon_backtest_v2_failed_cache.json",
    "dragon_backtest_v2_name_code_cache.json",
    "dragon_backtest_v2_sector_codes.json",
]

_MIGRATION_MARKER = CACHE_DIR / ".migrated_from_scripts"


def migrate_legacy_caches() -> None:
    """首次运行：把 scripts/ 下旧缓存 JSON 移到 ~/.hermes/cache/dragon_backtest_v2/。"""
    if _MIGRATION_MARKER.exists():
        return
    legacy_dir = Path(__file__).parent
    moved = 0
    for fn in _LEGACY_FILES:
        src = legacy_dir / fn
        if src.exists():
            try:
                dst = CACHE_DIR / fn
                dst.write_bytes(src.read_bytes())
                src.unlink()
                moved += 1
                log.info("迁移 %s → ~/.hermes/cache/dragon_backtest_v2/%s", fn, fn)
            except Exception as e:
                log.warning("迁移 %s 失败: %s", fn, e)
    if moved or not _MIGRATION_MARKER.exists():
        try:
            _MIGRATION_MARKER.write_text(json.dumps({
                "ts": datetime.now().isoformat(),
                "moved": moved,
            }, ensure_ascii=False))
        except Exception:
            pass


# 模块导入时自动迁移（幂等）
migrate_legacy_caches()


# ═══════════════════════════════════════════════════════
# 缓存 I/O 通用 helper
# ═══════════════════════════════════════════════════════
def _load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _save_json(path: Path, data) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, default=str))
    except Exception as e:
        log.warning(f"写 {path.name} 失败: {e}")


def _load_daily_cache() -> dict:
    return _load_json(DAILY_CACHE_FILE) or {}


def _save_daily_cache(cache: dict) -> None:
    _save_json(DAILY_CACHE_FILE, cache)


def _load_emotion_cache() -> dict:
    return _load_json(EMOTION_CACHE_FILE) or {}


def _save_emotion_cache(cache: dict) -> None:
    _save_json(EMOTION_CACHE_FILE, cache)


def _load_mainline_cache() -> dict:
    return _load_json(MAINLINE_CACHE_FILE) or {}


def _save_mainline_cache(cache: dict) -> None:
    _save_json(MAINLINE_CACHE_FILE, cache)


def _load_news_cache() -> dict:
    return _load_json(NEWS_CACHE_FILE) or {}


def load_news_cache() -> dict:
    """公开：供报告统计用。"""
    return _load_news_cache()


# ═══════════════════════════════════════════════════════
# K 线衍生指标（streak / lhb_net 估算）
# ═══════════════════════════════════════════════════════
def limit_threshold(code: str, name: str) -> float:
    """涨停阈值（普通股 9.8%/ST 4.8%/科创+创业 19.5%）"""
    if "ST" in (name or "") or "st" in (name or "").lower():
        return 4.8
    if (code or "").startswith(("688", "300", "301")):
        return 19.5
    return 9.8


def derive_streak_from_df(df: pd.DataFrame) -> int:
    """
    从 K 线计算连板数（截至最后一日）。
    A 股主板涨停近似：当日涨跌幅 ≥ 9.5% 视为涨停。
    返回最后一日的连续涨停天数（含当日，1 = 首板，2 = 2 连板）。
    """
    if df is None or len(df) < 2:
        return 0
    streak = 0
    for change in reversed(df["涨跌幅"].tolist()):
        try:
            ch = float(change)
        except Exception:
            break
        if ch >= 9.5:
            streak += 1
        else:
            break
    return streak


def derive_lhb_net_from_df(df: pd.DataFrame) -> float:
    """
    粗略估算龙虎榜净买入（大单净额近似）。
    涨停日且量能放大 → 视为净流入；否则视为 0 或负。
    """
    if df is None or len(df) < 10 or "成交额" not in df.columns:
        return 0.0
    last = df.iloc[-1]
    if float(last["涨跌幅"]) < 9.5:
        return 0.0
    avg_amount_20 = float(df["成交额"].tail(20).mean())
    today_amount = float(last["成交额"])
    if avg_amount_20 <= 0:
        return 0.0
    amplification = today_amount / avg_amount_20
    if amplification >= 2.0:
        return 8_000_000.0
    if amplification >= 1.5:
        return 4_000_000.0
    if amplification >= 1.2:
        return 1_500_000.0
    return 500_000.0


def reconstruct_zt_from_kline(all_history: dict, target_date: pd.Timestamp) -> dict[str, dict]:
    """
    从 K 线重建 target_date 的合成涨停池。
    返回 {code: {streak, change_pct, amount, name, board_name=""}}。
    """
    out = {}
    target_date_norm = pd.Timestamp(target_date).normalize()
    for code, (name, df) in all_history.items():
        if df is None or len(df) < 2:
            continue
        sub = df[df["日期"] <= target_date_norm]
        if len(sub) < 1:
            continue
        thr = limit_threshold(code, name)
        last = sub.iloc[-1]
        try:
            change_today = float(last["涨跌幅"])
        except Exception:
            continue
        if change_today < thr:
            continue
        streak = 0
        for ch in sub["涨跌幅"].tolist()[::-1]:
            try:
                v = float(ch)
            except Exception:
                break
            if v >= thr * 0.95:
                streak += 1
            else:
                break
        if streak < 1:
            continue
        amount = float(last.get("成交额", 0) or 0)
        out[code] = {
            "streak": streak,
            "change_pct": round(change_today, 2),
            "amount": amount,
            "limit_order_amount": 0,
            "market_cap": 0,
            "board_name": "",
            "name": str(name),
            "source": "kline_reconstructed",
        }
    return out


# ═══════════════════════════════════════════════════════
# 龙虎榜多源糅合
# ═══════════════════════════════════════════════════════
def _try_em_lhb_seats(date_str: str) -> dict[str, list[str]] | None:
    """
    EM datacenter 龙虎榜席位明细（9501 不可用 → 走兜底 → 几乎总是空）。
    返回 {code: [seat_name, ...]}；失败返 None。
    """
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    for report in ["RPT_LHB_GG_DETAIL", "RPT_LHB_DETAIL"]:
        params = {
            "reportName": report,
            "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,OPERATE_DEPT_NAME,DIRECTION,ORDER_VOLUME,AMOUNT",
            "filter": f'(TRADE_DATE="{date_str}")',
            "pageNumber": 1, "pageSize": 1000,
        }
        try:
            d = msf._http_get(url, params, retries=1)
        except Exception as e:
            log.debug(f"em_lhb_seats {date_str} {report}: {e}")
            continue
        rows = (d.get("result") or {}).get("data") if d else None
        if rows:
            out = {}
            for r in rows:
                code = str(r.get("SECURITY_CODE", "")).zfill(6)
                dept = str(r.get("OPERATE_DEPT_NAME") or r.get("BRANCH_NAME") or "")
                if code and dept:
                    out.setdefault(code, []).append(dept)
            if out:
                return out
    return None


def _try_akshare_lhb_seats(date_str: str,
                           name_to_code: dict[str, str]) -> dict[str, list[str]] | None:
    """
    akshare stock_lhb_hyyyb_em — 返回 {code: [营业部, ...]}
    字段"买入股票"是中文名，用 name_to_code 反查。
    """
    try:
        import akshare as ak
    except ImportError:
        return None
    for attempt in range(2):
        try:
            df = ak.stock_lhb_hyyyb_em(start_date=date_str, end_date=date_str)
            if df is None or df.empty:
                return {}
            out = {}
            for _, r in df.iterrows():
                seat = str(r.get("营业部名称") or "").strip()
                stocks_field = str(r.get("买入股票") or "")
                if not seat or not stocks_field:
                    continue
                for nm in stocks_field.split():
                    code = name_to_code.get(nm)
                    if code:
                        out.setdefault(code, []).append(seat)
            return out
        except Exception as e:
            log.debug(f"akshare_lhb_seats {date_str} attempt {attempt+1}: "
                      f"{type(e).__name__}: {str(e)[:80]}")
            if attempt < 1:
                time.sleep(0.5)
    return None


NAME_CODE_TTL = timedelta(days=7)


def _load_name_to_code() -> dict[str, str]:
    if NAME_CODE_CACHE_FILE.exists():
        try:
            d = json.loads(NAME_CODE_CACHE_FILE.read_text())
            ts = d.get("_ts")
            if ts and datetime.now() - datetime.fromisoformat(ts) < NAME_CODE_TTL:
                return d.get("map", {})
        except Exception:
            pass
    return {}


def _save_name_to_code(m: dict[str, str]) -> None:
    try:
        NAME_CODE_CACHE_FILE.write_text(json.dumps(
            {"_ts": datetime.now().isoformat(), "map": m},
            ensure_ascii=False,
        ))
    except Exception as e:
        log.warning(f"写 name_code 缓存失败: {e}")


def build_name_to_code() -> dict[str, str]:
    """SH + SZ 名称→代码映射，缓存 7 天。"""
    cached = _load_name_to_code()
    if cached:
        return cached
    try:
        import akshare as ak
    except ImportError:
        return {}
    out = {}
    try:
        sh = ak.stock_info_sh_name_code()
        for _, r in sh.iterrows():
            code = str(r.get("证券代码", "")).zfill(6)
            nm = str(r.get("证券简称", "")).strip()
            if code and nm:
                out[nm] = code
    except Exception as e:
        log.warning(f"SH name-code 拉取失败: {e}")
    try:
        sz = ak.stock_info_sz_name_code()
        for _, r in sz.iterrows():
            code = str(r.get("A股代码", "")).zfill(6)
            nm = str(r.get("A股简称", "")).strip()
            if code and nm:
                out[nm] = code
    except Exception as e:
        log.warning(f"SZ name-code 拉取失败: {e}")
    if out:
        _save_name_to_code(out)
    log.info(f"  name→code 映射 {len(out)} 条（缓存 7 天）")
    return out


def fetch_lhb_detail_multi(date_str: str) -> dict[str, dict]:
    """多源糅合：取某日的龙虎榜详情（净买额/换手率/流通市值）。"""
    try:
        import akshare as ak
    except ImportError:
        return {}
    for attempt in range(2):
        try:
            df = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
            if df is None or len(df) == 0:
                return {}
            out = {}
            for _, r in df.iterrows():
                code = str(r.get("代码", "")).zfill(6)
                if not code:
                    continue
                out[code] = {
                    "龙虎榜净买额": float(r.get("龙虎榜净买额") or 0),
                    "换手率":       float(r.get("换手率") or 0),
                    "流通市值":     float(r.get("流通市值") or 0),
                    "上榜原因":     str(r.get("上榜原因") or ""),
                    "龙虎榜成交额": float(r.get("龙虎榜成交额") or 0),
                    "龙虎榜买入额": float(r.get("龙虎榜买入额") or 0),
                    "龙虎榜卖出额": float(r.get("龙虎榜卖出额") or 0),
                    "上榜日":       str(r.get("上榜日") or ""),
                    "source":       "akshare_lhb_detail",
                }
            return out
        except Exception as e:
            log.debug(f"akshare_lhb_detail {date_str} attempt {attempt+1}: "
                      f"{type(e).__name__}: {str(e)[:80]}")
            if attempt < 1:
                time.sleep(0.5)
    return {}


def fetch_lhb_seats_multi(date_str: str,
                          name_to_code: dict[str, str]) -> dict[str, list[str]]:
    """
    多源糅合：取某日的龙虎榜席位（code→seats）。
    EM 9501 优先（精确），akshare hyyyb 兜底（需 name→code 反查）。
    """
    em_seats = _try_em_lhb_seats(date_str)
    if em_seats:
        return em_seats
    ak_seats = _try_akshare_lhb_seats(date_str, name_to_code)
    if ak_seats is not None:
        return ak_seats
    return {}


# ═══════════════════════════════════════════════════════
# 退学骨架 1：情绪闸门（冰点/退潮空仓）
# ═══════════════════════════════════════════════════════
def safe_compute_emotion(date_str: str) -> dict | None:
    """
    emotion_mod.compute_daily_emotion 加 try/except + 重试兜底。
    失败 → None（调用方降级到默认中性 phase=启动、open_allowed=True、position_pct=0.5）。
    """
    try:
        import emotion as emotion_mod
    except ImportError as e:
        log.warning(f"import emotion 失败: {e}")
        return None
    for attempt in range(2):
        try:
            return emotion_mod.compute_daily_emotion(date_str)
        except Exception as e:
            log.warning(f"compute_daily_emotion {date_str} attempt {attempt+1}: "
                        f"{type(e).__name__}: {str(e)[:80]}")
            if attempt < 1:
                time.sleep(0.5)
    return None


def preload_emotion_calendar(trade_dates: list[str], use_cache: bool = True) -> dict[str, dict]:
    """
    {date_str: {phase, score, open_allowed, blocked, position_pct, source, ts}}
    失败日期 → 默认 phase=启动、不阻断、position_pct=0.5（保守降级）。
    """
    cache = _load_emotion_cache() if use_cache else {}
    out = {}
    n_loaded = n_fallback = 0
    for i, d in enumerate(trade_dates):
        rec = cache.get(d)
        if use_cache and isinstance(rec, dict) and "phase" in rec:
            out[d] = rec
            n_loaded += 1
            continue
        emo = safe_compute_emotion(d)
        if emo is None:
            rec = {
                "phase": "启动",
                "score": 50.0,
                "open_allowed": True,
                "blocked": False,
                "position_pct": 0.5,
                "source": "fallback_neutral",
                "ts": datetime.now().isoformat(),
            }
            n_fallback += 1
        else:
            blocked = (not emo.get("open_allowed", True))
            rec = {
                "phase": emo.get("phase", "?"),
                "score": float(emo.get("emotion_score", 0)),
                "open_allowed": bool(emo.get("open_allowed", True)),
                "blocked": blocked,
                "position_pct": float(emo.get("position_pct", 0.5)),
                "source": "compute_daily_emotion",
                "ts": datetime.now().isoformat(),
            }
            n_loaded += 1
        out[d] = rec
        cache[d] = rec
        if (i + 1) % 5 == 0:
            print(f"  情绪预加载 {i+1}/{len(trade_dates)} "
                  f"(loaded {n_loaded}, fallback {n_fallback})")
            _save_emotion_cache(cache)
    _save_emotion_cache(cache)
    print(f"  情绪汇总: loaded {n_loaded}/{len(trade_dates)}, fallback {n_fallback}")
    return out


# ═══════════════════════════════════════════════════════
# 退学骨架 2：主线识别（top2 板块）
# ═══════════════════════════════════════════════════════
def safe_fetch_sectors_history(sector_codes: list[str], days: int):
    """fund_flow_chart.fetch_all_sectors_history 加 try/except。失败 → None。"""
    try:
        import fund_flow_chart as ffc
    except ImportError as e:
        log.warning(f"import fund_flow_chart: {e}")
        return None
    for attempt in range(2):
        try:
            return ffc.fetch_all_sectors_history(
                sector_codes, history_days=days,
                max_workers=6, deadline_sec=120,
            )
        except Exception as e:
            log.warning(f"fetch_all_sectors_history attempt {attempt+1}: "
                        f"{type(e).__name__}: {str(e)[:80]}")
            if attempt < 1:
                time.sleep(1.0)
    return None


def get_sector_codes() -> list[str]:
    """akshare stock_board_industry_name_em 一次拉全板块代码（带缓存）。失败 → []。"""
    if SECTOR_CODES_CACHE_FILE.exists():
        try:
            d = json.loads(SECTOR_CODES_CACHE_FILE.read_text())
            if d.get("_ts") and (datetime.now() - datetime.fromisoformat(d["_ts"])).days < 7:
                return d.get("codes", [])
        except Exception:
            pass
    try:
        import akshare as ak
    except ImportError as e:
        log.warning(f"import akshare: {e}")
        return []
    for attempt in range(2):
        try:
            df = ak.stock_board_industry_name_em()
            if df is None or len(df) == 0:
                return []
            codes = df["板块代码"].astype(str).tolist()
            try:
                SECTOR_CODES_CACHE_FILE.write_text(json.dumps(
                    {"_ts": datetime.now().isoformat(), "codes": codes},
                    ensure_ascii=False,
                ))
            except Exception:
                pass
            log.info(f"  板块代码 {len(codes)} 个（缓存 7 天）")
            return codes
        except Exception as e:
            log.warning(f"stock_board_industry_name_em attempt {attempt+1}: {e}")
            if attempt < 1:
                time.sleep(0.5)
    return []


def preload_mainline_calendar(trade_dates: list[str], use_cache: bool = True) -> dict[str, set]:
    """
    {date_str: set_of_sector_names}。
    全源失败 → 所有日期返空 set（is_mainline=False，不致命）。
    """
    cache = _load_mainline_cache() if use_cache else {}
    if all(d in cache for d in trade_dates):
        out = {}
        for d in trade_dates:
            out[d] = set(cache[d].get("names", []))
        n_with = sum(1 for d in trade_dates if out[d])
        print(f"  主线缓存命中: {n_with}/{len(trade_dates)} 天有主线")
        return out
    codes = get_sector_codes()
    if not codes:
        return {d: set() for d in trade_dates}
    df_hist = safe_fetch_sectors_history(codes, days=65)
    if df_hist is None or df_hist.empty:
        return {d: set() for d in trade_dates}
    out: dict[str, set] = {}
    n_with = 0
    try:
        # import lazily to avoid circular import
        import dragon_scanner_v2 as dsv2
        for d, grp in df_hist.groupby("date"):
            sectors_today = []
            for _, r in grp.iterrows():
                sectors_today.append({
                    "code": str(r["code"]),
                    "name": str(r["name"]),
                    "change_pct": float(r.get("change", 0)),
                    "main_net": float(r.get("main", 0)),
                })
            main = dsv2.identify_mainline(sectors_today, top_n=2)
            # 强制统一为 YYYYMMDD 紧凑格式（无论 df_hist 的 date 是 Timestamp / "2026-04-01" / 其它）
            d_str = pd.Timestamp(d).strftime("%Y%m%d")
            names = {s["name"] for s in main}
            out[d_str] = names
            if names:
                n_with += 1
    except Exception as e:
        log.warning(f"mainline build: {e}")
        return {d: set() for d in trade_dates}
    persist = {d: {"names": sorted(list(s)), "source": "fetch_all_sectors_history"}
               for d, s in out.items()}
    _save_mainline_cache(persist)
    print(f"  主线汇总: {n_with}/{len(out)} 天识别出主线（{len(codes)} 板块）")
    for d in trade_dates:
        if d not in out:
            out[d] = set()
    return out


# ═══════════════════════════════════════════════════════
# 退学骨架 4：新闻 bonus（历史化 + 兜底）
# ═══════════════════════════════════════════════════════
def get_news_bonus_for_hist(code: str, date_str: str) -> tuple[int, dict]:
    """
    backtest 专用：查 NEWS_CACHE_FILE（schema: {"YYYY-MM-DD": {code: {score, ann_count}}}）。
    缓存缺失 / 接口失败 → (0, {src: 'miss'})。永不抛异常。
    """
    if not date_str or len(date_str) != 8:
        return 0, {"src": "bad_date"}
    d_key = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    try:
        cache = _load_news_cache()
        rec = cache.get(d_key, {}).get(code)
        if not rec:
            return 0, {"src": "miss"}
        try:
            import mainline_news as mn
            bonus = mn.score_dragon_news_bonus(code)
            return bonus, {
                "src": "hist_cache",
                "score": rec.get("score", 0),
                "ann_count": rec.get("ann_count", 0),
            }
        except Exception:
            score = int(rec.get("score", 0))
            if score >= 70:
                return 15, {"src": "hist_cache_fallback", "score": score}
            if score >= 50:
                return 10, {"src": "hist_cache_fallback", "score": score}
            if score >= 30:
                return 5, {"src": "hist_cache_fallback", "score": score}
            return 0, {"src": "hist_cache_fallback", "score": score}
    except Exception as e:
        log.warning(f"news_bonus_for_hist {code} {date_str}: {e}")
        return 0, {"src": "err"}


# ═══════════════════════════════════════════════════════
# 退学骨架 3 + 数据源糅合：每日真实数据预加载
# ═══════════════════════════════════════════════════════
def preload_daily_context(trade_dates: list[str], all_history: dict,
                          use_cache: bool = True) -> dict[str, dict]:
    """
    多源糅合：为每个交易日构建 {zt_map, lhb_detail_map, lhb_seats_map}。
    """
    cache = _load_daily_cache() if use_cache else {}
    out = {}
    n_zt = n_lhb_d = n_lhb_s = 0

    name_to_code = build_name_to_code()
    log.info(f"  name→code 映射: {len(name_to_code)} 条")

    for i, d in enumerate(trade_dates):
        rec = cache.get(d)
        if (use_cache and isinstance(rec, dict)
                and "zt_map" in rec
                and "lhb_detail_map" in rec
                and "lhb_seats_map" in rec):
            out[d] = rec
            n_zt += int(bool(rec.get("zt_map")))
            n_lhb_d += int(bool(rec.get("lhb_detail_map")))
            n_lhb_s += int(bool(rec.get("lhb_seats_map")))
            continue
        d_ts = pd.Timestamp(datetime.strptime(d, "%Y%m%d").date())
        zt_map = reconstruct_zt_from_kline(all_history, d_ts)
        lhb_detail = fetch_lhb_detail_multi(d)
        lhb_seats = fetch_lhb_seats_multi(d, name_to_code)
        srcs = []
        if zt_map:
            srcs.append("kline_zt")
        if lhb_detail:
            srcs.append("akshare_lhb_detail")
        if lhb_seats:
            srcs.append("akshare_lhb_seats")
        rec = {
            "zt_map": zt_map,
            "lhb_detail_map": lhb_detail,
            "lhb_seats_map": lhb_seats,
            "source": "+".join(srcs) if srcs else "none",
            "ts": datetime.now().isoformat(),
        }
        out[d] = rec
        cache[d] = rec
        if zt_map:
            n_zt += 1
        if lhb_detail:
            n_lhb_d += 1
        if lhb_seats:
            n_lhb_s += 1
        if (i + 1) % 10 == 0:
            print(f"  多源预加载 {i+1}/{len(trade_dates)} "
                  f"(zt {n_zt}, lhb_detail {n_lhb_d}, lhb_seats {n_lhb_s})")
            _save_daily_cache(cache)
    _save_daily_cache(cache)
    print(f"  数据汇总: zt_pool {n_zt}/{len(trade_dates)}, "
          f"lhb_detail {n_lhb_d}/{len(trade_dates)}, "
          f"lhb_seats {n_lhb_s}/{len(trade_dates)}")
    return out


# ═══════════════════════════════════════════════════════
# 暴露给主脚本的缓存文件路径常量（兼容旧调用方）
# ═══════════════════════════════════════════════════════
HISTORY_CACHE = HISTORY_CACHE_FILE
FAILED_CACHE = FAILED_CACHE_FILE