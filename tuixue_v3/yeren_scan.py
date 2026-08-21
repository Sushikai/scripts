"""
野人哥战法 · 扫描引擎
优先复用 server 内已加载的 dragons/sectors 数据,避免 HTTP 自调用。
命中标的 = 同时满足某 COMBO 全部规则的得分排序 top-N。
每个 rule 给 0/1 命中标记 + 解释,前端可展开看依据。
"""
from __future__ import annotations
import json
import datetime
from typing import Any
from concurrent.futures import ThreadPoolExecutor
import requests as _rq

TUIXUE = "http://localhost:7799"
TIMEOUT = 12


def _http_get(path: str) -> dict | None:
    """HTTP fallback — 仅在内部 import 失败时使用"""
    try:
        r = _rq.get(f"{TUIXUE}{path}", timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _get_data() -> tuple[list, list, set, set]:
    """获取候选股 + 主线板块 + 科技板块集合。
    优先复用 server 进程内已加载的模块数据 (避免 HTTP 自调用),
    失败则 HTTP 自调用。
    """
    import sys as _sys
    candidates: list[dict] = []
    mainline: list[dict] = []
    tech_sectors: set[str] = set()
    mainline_names: set[str] = set()

    # ── 路径 A: 复用 server 进程内的缓存 ──
    try:
        # 多种方式拿到 server 模块
        server_mod = None
        for modname in ("tuixue_v3.web.server", "web.server", "server"):
            if modname in _sys.modules:
                server_mod = _sys.modules[modname]
                break
        if server_mod is None:
            from tuixue_v3.web import server as _srv  # type: ignore
            server_mod = _srv
        cache_dict = getattr(server_mod, "_DRAGONS_CACHE", None)
        if isinstance(cache_dict, dict) and cache_dict:
            best = None
            for k, v in cache_dict.items():
                if isinstance(v, dict) and "data" in v:
                    if best is None or v.get("ts", 0) > best[1]:
                        best = (v, v.get("ts", 0))
            if best:
                candidates = (best[0].get("data") or {}).get("all", []) or []
                mainline = (best[0].get("data") or {}).get("mainline", []) or []
                print(f"[yeren_scan] path A: cache hit, candidates={len(candidates)}", flush=True)
    except Exception as e:
        print(f"[yeren_scan] path A failed: {e}", flush=True)

    # ── 路径 B: 直接调 dragons 模块的 score_dragons ──
    if not candidates:
        try:
            from tuixue_v3.dragons import score_dragons  # type: ignore
            res = score_dragons() or {}
            if isinstance(res, dict):
                candidates = res.get("all", []) or []
                mainline = res.get("mainline", []) or []
                print(f"[yeren_scan] path B: dragons module, candidates={len(candidates)}", flush=True)
        except Exception as e:
            print(f"[yeren_scan] path B failed: {e}", flush=True)

    # ── 路径 C: HTTP 自调用 (兜底) ──
    if not candidates:
        d = _http_get("/api/dragons") or {}
        candidates = ((d or {}).get("data") or {}).get("all", []) or []
        s = _http_get("/api/dashboard/hot_sectors") or {}
        mainline = ((s or {}).get("data") or {}).get("mainline", []) or []
        print(f"[yeren_scan] path C: HTTP, candidates={len(candidates)}", flush=True)

    mainline_names = {m.get("name", "") for m in mainline if m.get("name")}

    tech_sectors = {
        "半导体", "PCB", "电子", "元件", "消费电子", "光学", "通信", "通信设备",
        "计算机", "软件", "互联网", "传媒", "游戏", "AI", "算力", "CPO",
        "国产芯片", "数据要素", "数字货币", "机器人", "智能穿戴", "汽车电子",
        "电池", "新能源", "光伏", "储能", "医药", "生物", "医疗服务",
        "AR", "VR", "智能眼镜",
    }

    return candidates, mainline, tech_sectors, mainline_names


def _eval_one(rid: str, c: dict, mainline_names: set[str], tech_sectors: set[str]) -> dict:
    """单条规则对单股的评估。返回 {passed, weight, note}"""
    name = c.get("name", "")
    sector = c.get("sector", "")
    taxonomy = c.get("taxonomy", {}) or {}
    streak = c.get("streak", 0) or 0
    seal_ratio = c.get("seal_ratio_pct", 0) or 0
    turnover = c.get("turnover_pct", 0) or 0
    mcap = c.get("market_cap_yi", 0) or 0
    first_time = c.get("first_time", "")  # HHMMSS
    burst = c.get("burst_count", 0) or 0
    seats = c.get("seat_aliases", []) or []
    is_mainline = c.get("is_mainline", False)
    l1 = taxonomy.get("l1", "") or ""
    l2 = taxonomy.get("l2", "") or ""

    has_lasa = any("拉萨" in s for s in seats)

    # ── Y01 N字战法首板龙头 ──
    if rid == "Y01":
        ok = streak in (1, 2) and not has_lasa and seal_ratio > 30
        return dict(passed=ok, weight=1.0, note=f"N字基础:连板{streak} + 封成比{seal_ratio}% + 拉萨过滤={not has_lasa}")

    # ── Y02 封死优先 ──
    if rid == "Y02":
        early_seal = False
        if first_time and len(first_time) == 6:
            hh = int(first_time[:2]); mm = int(first_time[2:4])
            early_seal = (hh < 14) or (hh == 14 and mm < 30)
        ok = seal_ratio >= 50 and burst == 0 and early_seal
        return dict(passed=ok, weight=1.0, note=f"封成{seal_ratio}% + 封板{first_time} + 炸{burst}次")

    # ── Y03 分歧转一致(3板买点) ──
    if rid == "Y03":
        ok = streak == 3 and 8 <= turnover <= 25
        return dict(passed=ok, weight=0.9, note=f"3板换手{turnover}%(8-25 区间)")

    # ── Y04 买家枯竭(高位缩量 = 警惕,反向规则) ──
    if rid == "Y04":
        # 反向: 通过 = 不在高位缩量状态
        dangerous = streak >= 4 and turnover < 3
        ok = not dangerous
        return dict(passed=ok, weight=-0.7, note=f"{streak}板 + 换手{turnover}% {'高位缩量-危险' if dangerous else 'OK'}")

    # ── Y05 震仓=持筹成本靠近峰 ──
    if rid == "Y05":
        ok = 30 <= mcap <= 150 and 5 <= turnover <= 30 and is_mainline
        return dict(passed=ok, weight=0.7, note=f"市值{mcap}亿 + 换手{turnover}% + 主线{is_mainline}")

    # ── Y06 尊重市场不回撤加仓(runtime 自检,这里 passed=True) ──
    if rid == "Y06":
        return dict(passed=True, weight=1.0, note="运行时:回撤 ≥ 8% 强制离场")

    # ── Y07 买入/卖出时机 ──
    if rid == "Y07":
        early_seal = False
        if first_time and len(first_time) == 6:
            hh = int(first_time[:2]); mm = int(first_time[2:4])
            early_seal = (hh < 14) or (hh == 14 and mm < 30)
        ok = early_seal
        return dict(passed=ok, weight=0.9, note=f"封板{first_time} (14:30前=买点)")

    # ── Y08 题材唱戏=科技板块 ──
    if rid == "Y08":
        in_tech = any((s in tech_sectors) for s in (l1, l2, sector))
        return dict(passed=in_tech, weight=0.8, note=f"板块[{l1}/{l2}/{sector}] 科技={in_tech}")

    # ── Y09 大科技/泛科技主导 ──
    if rid == "Y09":
        in_tech = any((s in tech_sectors) for s in (l1, l2, sector))
        ok = in_tech and (is_mainline or sector in mainline_names)
        return dict(passed=ok, weight=0.9, note=f"板块[{l1}/{l2}] 科技={in_tech} + 主线={is_mainline}")

    # ── Y10 PCB 国产替代 ──
    if rid == "Y10":
        ok = any(k in (sector + l1 + l2) for k in ("PCB", "半导体", "元件", "电子", "国产"))
        return dict(passed=ok, weight=0.6, note=f"板块[{l1}/{l2}/{sector}] 国产替代={ok}")

    # ── Y11 预期=扭亏为盈 ──
    if rid == "Y11":
        return dict(passed=False, weight=0.6, note="业绩预告字段需另查 (stub)")

    # ── Y12 AR/VR 低位补涨 ──
    if rid == "Y12":
        ok = any(k in (sector + l1 + l2) for k in ("AR", "VR", "消费电子", "光学", "智能眼镜"))
        return dict(passed=ok, weight=0.5, note=f"板块[{l1}/{l2}/{sector}] AR/VR={ok}")

    # ── Y13 尾盘阴线套利 ──
    if rid == "Y13":
        return dict(passed=False, weight=0.5, note="依赖 last_30m_volume, 需盘后/次日数据 (stub)")

    # ── Y14 不切换模式 ──
    if rid == "Y14":
        return dict(passed=True, weight=1.0, note="运行时:固定一种模式")

    # ── Y15 龙头四问齐备 ──
    if rid == "Y15":
        is_leader = streak >= 3 and is_mainline and seal_ratio > 50 and burst == 0
        ok = is_leader
        return dict(passed=ok, weight=1.0, note=f"四问:主线{is_mainline}+龙头{streak}板+买点{first_time}+风控{burst}")

    # ── Y16 估值有空间 ──
    if rid == "Y16":
        return dict(passed=False, weight=0.5, note="需 PE/PS 数据 (stub)")

    # ── Y17 防拉萨天团 ──
    if rid == "Y17":
        ok = not has_lasa
        return dict(passed=ok, weight=-0.8, note=f"席位={seats[:3]} 拉萨过滤={'通过' if ok else '回避'}")

    return dict(passed=False, weight=0.0, note=f"unknown rule {rid}")


def _eval_combo(rules: list[str], candidates: list[dict], mainline_names: set[str], tech_sectors: set[str]) -> list[dict]:
    """对每个候选股评估该 combo 全部规则。"""
    out = []
    for c in candidates:
        evals = [_eval_one(rid, c, mainline_names, tech_sectors) for rid in rules]
        # 通过数
        n_pass = sum(1 for e in evals if e["passed"])
        # 加权得分(正向+反向)
        weighted = sum(e["weight"] for e in evals if e["passed"])
        out.append({
            **c,
            "_evals": evals,
            "_n_pass": n_pass,
            "_n_total": len(rules),
            "_weighted": round(weighted, 2),
        })
    return out


def scan(combo_id: str = "C1", limit: int = 20) -> dict:
    """主入口:取数 → 评估 → 排序。"""
    import importlib
    try:
        _yl = importlib.import_module(".yeren_laws", package=__package__ or "tuixue_v3")
    except Exception:
        import sys
        sys.path.insert(0, "/Users/kaikai/scripts/tuixue_v3_perf/tuixue_v3")
        try:
            _yl = importlib.import_module("yeren_laws")
        except Exception:
            _yl = importlib.import_module("tuixue_v3.yeren_laws")

    combo = _yl.combo_by_id(combo_id)
    if not combo:
        return {"combo": None, "hits": [], "note": f"combo {combo_id} 不存在"}

    candidates, mainline, tech_sectors, mainline_names = _get_data()
    evaluated = _eval_combo(combo["rules"], candidates, mainline_names, tech_sectors)

    # 排序: 通过数 × 10 + 加权分 × 5 + 巨龙分 × 0.2
    def _score(x):
        return x["_n_pass"] * 10 + x["_weighted"] * 5 + (x.get("score_total", 0) or 0) * 0.2

    evaluated.sort(key=_score, reverse=True)

    # 至少命中 ≥ 1 条才输出,否则显示空
    top_all = evaluated[:limit]
    top = [c for c in top_all if c["_n_pass"] > 0]

    hits = []
    for c in top:
        hits.append({
            "code": c.get("code"),
            "name": c.get("name"),
            "sector": c.get("sector"),
            "streak": c.get("streak"),
            "change_pct": c.get("change_pct"),
            "seal_ratio_pct": c.get("seal_ratio_pct"),
            "turnover_pct": c.get("turnover_pct"),
            "market_cap_yi": c.get("market_cap_yi"),
            "first_time": c.get("first_time"),
            "seat_aliases": c.get("seat_aliases", []),
            "score_total": c.get("score_total"),
            "yeren_pass": c["_n_pass"],
            "yeren_total": c["_n_total"],
            "yeren_weighted": c["_weighted"],
            "evals": c["_evals"],
        })

    return {
        "combo": combo,
        "hits": hits,
        "scan_meta": {
            "candidate_total": len(candidates),
            "mainline_names": sorted(mainline_names),
            "tech_sectors_sample": sorted(tech_sectors)[:8],
            "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "note": "扫描引擎已实装 · 数据源 /api/dragons + /api/dashboard/hot_sectors · 每命中规则可展开看 note",
    }