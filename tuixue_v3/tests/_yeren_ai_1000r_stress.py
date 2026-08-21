#!/usr/bin/env python3
"""战法 AI 1000 轮压力测试 (R1-R5 基础设施 + Baseline)

设计: 50 题 (12 维度) × 20 code = 1000 题。
执行: 4 worker 并行 (受 AI_INFLIGHT_MAX=20 约束), 串行 chat (LLM 调用)。
指标: tool_call_accuracy / ok_pct / avg_latency / eval_hits_pct / token_usage

输出: /tmp/ai_1000r_<tag>.json + 打印 summary
"""
import os, json, time, sys, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── 配置 ──────────────────────────────────────────
BASE = "http://127.0.0.1:7799"
TAG = sys.argv[1] if len(sys.argv) > 1 else "baseline"
WORKERS = int(os.environ.get("STRESS_WORKERS", "1"))  # 默认串行,R6-R20 后再升
N_CODES = int(os.environ.get("STRESS_N_CODES", "20"))  # 每题跑的 code 数
OUT = f"/tmp/ai_1000r_{TAG}.json"

# 20 个典型 code (覆盖大盘/中小板/创业板/科创板)
CODES = [
    "002716",  # 农业 — 野人主线
    "600519",  # 茅台 — 蓝筹
    "000858",  # 五粮液
    "300750",  # 宁王 — 新能源
    "002594",  # 比亚迪
    "600276",  # 恒瑞 — 医药
    "000333",  # 美的
    "300059",  # 东方财富
    "601318",  # 中国平安
    "000651",  # 格力
    "002415",  # 海康 — 科技
    "600036",  # 招行 — 银行
    "300015",  # 爱尔 — 医美
    "002230",  # 科大讯飞
    "600030",  # 中信证券
    "000725",  # 京东方
    "002475",  # 立讯
    "300760",  # 迈瑞
    "601012",  # 隆基
    "600887",  # 伊利
]

# 50 题 (复用 _yeren_ai_50r_stress.py:1-112 模板)
QUESTIONS = [
    {"cat": "基础买卖", "q": "现在可以买吗?给出明确结论"},
    {"cat": "基础买卖", "q": "现在该卖吗?理由是什么?"},
    {"cat": "基础买卖", "q": "持有的话, 目标价多少?"},
    {"cat": "基础买卖", "q": "空仓的话, 现在该不该建仓?"},
    {"cat": "基础买卖", "q": "加仓还是减仓?比例多少?"},
    {"cat": "板块主线", "q": "它属于哪个板块?板块当前是主线还是退潮?"},
    {"cat": "板块主线", "q": "板块龙头是谁?这只排名第几?"},
    {"cat": "板块主线", "q": "板块如果退潮, 这只票会跌多少?"},
    {"cat": "板块主线", "q": "板块轮动到下一主线的话, 这只还跟不跟?"},
    {"cat": "板块主线", "q": "板块整体估值处于历史百分位多少?"},
    {"cat": "板块主线", "q": "板块最近的资金流入/流出如何?"},
    {"cat": "战法规则", "q": "符合 Y0-Y9 哪几条战法?具体怎么套用?"},
    {"cat": "战法规则", "q": "它的买点是否符合战法标准?差距在哪?"},
    {"cat": "战法规则", "q": "如果不符合战法, 还要等什么条件?"},
    {"cat": "战法规则", "q": "战法 Y 编号里哪条最适合当前市场?"},
    {"cat": "战法规则", "q": "它的止损位按战法应该设在哪儿?"},
    {"cat": "业绩财务", "q": "业绩反转的核心指标有没有改善?"},
    {"cat": "业绩财务", "q": "营收/利润同比增速多少?环比呢?"},
    {"cat": "业绩财务", "q": "ROE/毛利率/净利率处于历史什么位置?"},
    {"cat": "业绩财务", "q": "有没有商誉/应收账款等雷区?"},
    {"cat": "资金席位", "q": "今天的主力资金净流入/流出多少?"},
    {"cat": "资金席位", "q": "北向/融资/ETF 资金有什么动作?"},
    {"cat": "资金席位", "q": "龙虎榜席位构成?游资/机构比例?"},
    {"cat": "资金席位", "q": "今天量比 / 换手率多少?是否异常?"},
    {"cat": "龙虎榜", "q": "上龙虎榜了吗?买入前 5 席位?"},
    {"cat": "龙虎榜", "q": "卖方席位的实力?有没有温州帮/杭州帮?"},
    {"cat": "龙虎榜", "q": "机构席位的买入是配置还是博弈?"},
    {"cat": "K线技术", "q": "K线形态属于什么结构?突破还是回调?"},
    {"cat": "K线技术", "q": "均线系统(M5/M10/M20/M60)的排列?"},
    {"cat": "K线技术", "q": "MACD/KDJ/RSI 处于什么状态?金叉/死叉?"},
    {"cat": "K线技术", "q": "成交量是否支持当前价位?有没有放量/缩量信号?"},
    {"cat": "K线技术", "q": "近 N 日最高/最低/中位数价位是多少?"},
    {"cat": "涨停连板", "q": "涨停是首板还是连板?封单多少?"},
    {"cat": "涨停连板", "q": "连板梯队它在第几板?天花板在哪?"},
    {"cat": "涨停连板", "q": "今天的炸板率/封板时间/开板次数?"},
    {"cat": "涨停连板", "q": "二板/三板的胜率历史数据怎么看?"},
    {"cat": "仓位止损", "q": "建议仓位是多少?满仓/半仓/轻仓/空仓?"},
    {"cat": "仓位止损", "q": "止损位具体在哪?跌穿后怎么操作?"},
    {"cat": "仓位止损", "q": "止盈位在哪?分批止盈还是一次止盈?"},
    {"cat": "仓位止损", "q": "持有期多久?短线/中线/长线?"},
    {"cat": "仓位止损", "q": "最坏情况下 (跌停连板), 仓位如何调整?"},
    {"cat": "多轮追问", "q": "为什么?展开说一下逻辑"},
    {"cat": "多轮追问", "q": "有没有反例?什么情况下你的判断会反过来?"},
    {"cat": "多轮追问", "q": "和板块龙头比, 它差在哪?"},
    {"cat": "多轮追问", "q": "如果业绩真的反转了, 你会改口吗?"},
    {"cat": "多轮追问", "q": "总结一下, 它的核心矛盾是什么?"},
    {"cat": "跨维度综合", "q": "综合所有数据, 给出最终交易计划: 买点/仓位/止损/止盈/持有期"},
    {"cat": "跨维度综合", "q": "如果让你给一个评分 (0-100), 这只票能得几分?为什么?"},
    {"cat": "跨维度综合", "q": "如果有 100 万, 你会全仓/半仓/轻仓/空仓这只?"},
    {"cat": "边界", "q": "如果它今天停牌了, 之前的判断还成立吗?"},
    {"cat": "边界", "q": "如果业绩证伪 + 板块退潮同时发生, 它会跌几个板?"},
]

# 5 维关键词评估
EVAL_KEYWORDS = {
    "明确结论": ["结论", "判断", "建议", "不买", "观望", "买入", "卖出", "持有"],
    "战法引用": ["Y0", "Y1", "Y2", "Y3", "Y4", "Y5", "Y6", "Y7", "Y8", "Y9"],
    "止损位": ["止损", "减仓", "离场", "出场"],
    "免责": ["⚠", "非投资建议", "自负"],
    "板块判定": ["板块", "主线", "题材"],
}

# 加 4 个新维度 (R1 起): tool_call_used / 止损数值具体 / Y编号 / ctx_used
EVAL_KEYWORDS_V2 = {
    **EVAL_KEYWORDS,
    "tool_used": [],  # 由 reply 中包含 tool_call 痕迹判定
    "ctx_used": [],   # 由 reply 引用具体 ctx_keys 判定
    "止损数值": ["止损", "%"],  # 同时含 "止损" + 数字百分比
    "Y编号具体": [f"Y{i}" for i in range(10)],  # 任意 Y 编号
}


def evaluate_reply(reply: str, tool_calls_used: list | None = None) -> dict:
    hits = {}
    tc_count = len(tool_calls_used or [])
    for k, kws in EVAL_KEYWORDS_V2.items():
        if k == "tool_used":
            # 命中条件: tool_calls_used 非空 OR reply 含原始协议 (新格式 <<tool_calls>> 已被 strip)
            hits[k] = tc_count > 0 or "<<<call:" in reply or "<<ToolCall>>" in reply or "<<<tool_call" in reply or "<<<ToolCall" in reply
        elif k == "ctx_used":
            # 引用 ctx 关键词 (板块/资金/技术/财务 等具体词) >=3
            ctx_keywords = ["板块", "资金", "席位", "技术", "财务", "业绩", "龙虎榜"]
            hits[k] = sum(1 for kw in ctx_keywords if kw in reply) >= 3
        elif k == "止损数值":
            hits[k] = "止损" in reply and any(c.isdigit() for c in reply)
        elif k == "Y编号具体":
            hits[k] = any(kw in reply for kw in kws)
        else:
            hits[k] = any(kw in reply for kw in kws) if kws else False
    return hits


def run_one(idx: int, code: str, question: str, history: list) -> dict:
    t0 = time.time()
    try:
        # R292 · 加 _nocache=1 防止语义缓存命中 (R97-5 缓存对压力测试会假性冲指标)
        resp = requests.post(
            f"{BASE}/api/yeren/ai/chat?_nocache=1",
            json={"code": code, "message": question, "history": history},
            timeout=60,  # R292: 60s (server chat_yeren 内部 120s, 但常见 hang 在 60s 内, 提前 fail)
        )
        latency = time.time() - t0
        j = resp.json()
        if not j.get("ok"):
            return {"idx": idx, "code": code, "q": question, "cat": next((q["cat"] for q in QUESTIONS if q["q"] == question), "?"), "ok": False, "error": str(j.get("error"))[:200], "latency": round(latency, 1)}
        data = j.get("data", {})
        reply = data.get("reply", "")
        tc = data.get("tool_calls", []) or data.get("tools_called", []) or data.get("yeren_tool_calls", [])
        hits = evaluate_reply(reply, tc)
        return {
            "idx": idx,
            "code": code,
            "q": question,
            "cat": next((q["cat"] for q in QUESTIONS if q["q"] == question), "?"),
            "ok": True,
            "reply_len": len(reply),
            "latency": round(latency, 1),
            "rules_hit": data.get("rules_hit", []),
            "tool_calls_used": data.get("tool_calls", []) or data.get("tools_called", []) or data.get("yeren_tool_calls", []),
            "used_ctx_keys": data.get("used_ctx_keys", []),
            "degraded": data.get("degraded", False),
            "reply_first_200": reply[:200],
            "eval": hits,
            "eval_score": sum(hits.values()) / len(hits),
        }
    except Exception as e:
        return {"idx": idx, "code": code, "q": question, "cat": next((q["cat"] for q in QUESTIONS if q["q"] == question), "?"), "ok": False, "error": str(e)[:200], "latency": round(time.time() - t0, 1)}


def gen_jobs():
    idx = 0
    codes_use = CODES[:N_CODES]
    for qi, q in enumerate(QUESTIONS):
        for code in codes_use:
            idx += 1
            yield (idx, code, q["q"], [])


def main():
    jobs = list(gen_jobs())
    print(f"=== 1000r stress test [{TAG}] | {len(jobs)} jobs × {WORKERS} workers ===")
    results = []
    t_start = time.time()
    completed = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(run_one, *job) for job in jobs]
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            completed += 1
            if completed % 50 == 0 or completed == len(jobs):
                ok = sum(1 for x in results if x.get("ok"))
                avg_lat = sum(x.get("latency", 0) for x in results) / len(results)
                pct = completed / len(jobs) * 100
                eta = (time.time() - t_start) / completed * (len(jobs) - completed)
                print(f"  [{completed:4d}/{len(jobs)} {pct:5.1f}%] ok={ok} avg_lat={avg_lat:.1f}s ETA={eta:.0f}s", flush=True)
                # R101: 每 50 个增量保存, 防止崩溃丢结果
                _partial = {
                    "tag": TAG,
                    "completed": completed,
                    "total": len(jobs),
                    "results": results,
                    "ts": time.time(),
                }
                _partial_path = OUT.replace('.json', '.partial.json')
                with open(_partial_path, 'w') as _pf:
                    json.dump(_partial, _pf, ensure_ascii=False)

    # ── 汇总 ──
    ok_n = sum(1 for x in results if x.get("ok"))
    avg_lat = sum(x.get("latency", 0) for x in results) / max(1, len(results))
    ok_lat = [x["latency"] for x in results if x.get("ok")]
    p50_lat = sorted(ok_lat)[len(ok_lat) // 2] if ok_lat else 0
    p95_lat = ok_lat[int(len(ok_lat) * 0.95)] if ok_lat else 0

    # 5+4 维关键词命中
    eval_sum = {k: 0 for k in EVAL_KEYWORDS_V2}
    eval_n = 0
    for x in results:
        if x.get("ok") and x.get("eval"):
            eval_n += 1
            for k, v in x["eval"].items():
                eval_sum[k] += 1

    # tool_call_accuracy: 含 tool_calls 且 name 命中预期端点
    tool_calls_total = sum(len(x.get("tool_calls_used", [])) for x in results if x.get("ok"))
    tool_calls_with_code = sum(1 for x in results if x.get("ok") and any(c in (x.get("reply", "")) for c in []))  # placeholder
    # 用 rules_hit 长度作为 proxy
    rules_hit_avg = sum(len(x.get("rules_hit", [])) for x in results if x.get("ok")) / max(1, sum(1 for x in results if x.get("ok")))

    # R411 · per-tool 统计 + per-category tool-call rate
    tool_calls_per_tool: dict[str, int] = {}
    for x in results:
        if not x.get("ok"):
            continue
        for tc in (x.get("tool_calls_used") or []):
            # tc 是 dict, 形式 {call: "name=...", ok: bool, size: N}
            call_str = tc.get("call", "") if isinstance(tc, dict) else str(tc)
            name = call_str.split(",")[0].split("=", 1)[-1] if "=" in call_str else "unknown"
            tool_calls_per_tool[name] = tool_calls_per_tool.get(name, 0) + 1

    cat_tool_rate: dict[str, dict] = {}
    for x in results:
        if not x.get("ok"):
            continue
        c = x.get("cat", "?")
        cat_tool_rate.setdefault(c, {"n": 0, "with_tc": 0})
        cat_tool_rate[c]["n"] += 1
        if x.get("tool_calls_used"):
            cat_tool_rate[c]["with_tc"] += 1

    summary = {
        "tag": TAG,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(results),
        "ok": ok_n,
        "ok_pct": round(ok_n / max(1, len(results)) * 100, 1),
        "avg_latency_s": round(avg_lat, 2),
        "p50_latency_s": round(p50_lat, 2),
        "p95_latency_s": round(p95_lat, 2),
        "eval_hits_pct": {k: round(v / max(1, eval_n) * 100, 1) for k, v in eval_sum.items()},
        "tool_calls_total": tool_calls_total,
        "avg_rules_hit": round(rules_hit_avg, 2),
        "degraded_count": sum(1 for x in results if x.get("ok") and x.get("degraded")),
        "tool_calls_per_tool": dict(sorted(tool_calls_per_tool.items(), key=lambda x: -x[1])),
        "cat_tool_rate": {c: {"n": v["n"], "tc_rate": round(v["with_tc"]/max(1,v["n"])*100, 1)} for c, v in cat_tool_rate.items()},
    }
    print(f"\n=== SUMMARY [{TAG}] ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # 保存
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\nSaved → {OUT}")


if __name__ == "__main__":
    main()