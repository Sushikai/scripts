"""
野人战法 AI · 50 轮压力测试 (R73 闭环验收)

目的: 把系统有的功能都问到, 验证 AI 在多轮对话 + 多维度 context 下
      - 召回是否合理 (是否引用了正确数据)
      - 风格是否专业 (野人哥术语是否准确)
      - 一致性 (后续追问是否跟前文一致)
      - 决策明确性 (是否给了明确买卖/止损位)

覆盖维度 (50 题):
  - 基础买卖 (5 题)
  - 板块/主线 (6 题)
  - 战法规则 (5 题)
  - 业绩/财务 (4 题)
  - 资金/席位 (4 题)
  - 龙虎榜 (3 题)
  - K线/技术 (5 题)
  - 涨停历史/连板 (4 题)
  - 仓位/止损/止盈 (5 题)
  - 多轮追问 (5 题 — 验证历史引用)
  - 跨维度 (3 题 — 综合判断)
  - 边界 (3 题 — 已持仓/空仓/停牌)

运行: python3 tests/_yeren_ai_50r_stress.py [--code 002716] [--out /tmp/yeren_ai_50r.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# 测试问题集 (50 题)
QUESTIONS: list[dict] = [
    # 基础买卖 (5)
    {"cat": "基础买卖", "q": "这只股票现在能买吗?给个明确建议"},
    {"cat": "基础买卖", "q": "开盘价挂多少?买入价区间?"},
    {"cat": "基础买卖", "q": "持有期多久?T+1 还是 T+5 出?"},
    {"cat": "基础买卖", "q": "仓位多少?轻仓还是重仓?"},
    {"cat": "基础买卖", "q": "如果今天没涨停,我还应该关注吗?"},

    # 板块/主线 (6)
    {"cat": "板块主线", "q": "它在板块里算什么地位?是中军还是跟风?"},
    {"cat": "板块主线", "q": "所属板块是不是当期主线?有没可能切换?"},
    {"cat": "板块主线", "q": "板块退潮的话, 这只票会跟着跌多少?"},
    {"cat": "板块主线", "q": "有没有替代标的?同板块其他可以打?"},
    {"cat": "板块主线", "q": "板块龙头是谁?它算老二还是老三?"},
    {"cat": "板块主线", "q": "板块今天整体爆发的话, 它能不能追?"},

    # 战法规则 (5)
    {"cat": "战法规则", "q": "命中了哪些野人规则?具体讲讲"},
    {"cat": "战法规则", "q": "17 条规则里, 哪几条是它最关键的?"},
    {"cat": "战法规则", "q": "为什么 Y11 (扭亏) 命中了但还是不建议买?"},
    {"cat": "战法规则", "q": "如果它涨停了, 该按哪个套餐买?C1/C2/C3/C4/C5?"},
    {"cat": "战法规则", "q": "野人哥的四问自检 (Y15), 它过几关?"},

    # 业绩/财务 (4)
    {"cat": "业绩财务", "q": "业绩同比多少?扣非呢?算扭亏吗?"},
    {"cat": "业绩财务", "q": "业绩趋势是 DOWN 还是 UP?有反转迹象吗?"},
    {"cat": "业绩财务", "q": "如果业绩证伪, 这只票会跌多少?"},
    {"cat": "业绩财务", "q": "三季度业绩预告方向是什么?"},

    # 资金/席位 (4)
    {"cat": "资金席位", "q": "今天主力资金是流入还是流出?净额多少?"},
    {"cat": "资金席位", "q": "有没有拉萨系席位?如果有要警惕什么?"},
    {"cat": "资金席位", "q": "龙虎榜买入前 5 是谁?有没有机构?"},
    {"cat": "资金席位", "q": "北向资金今天参与了吗?"},

    # 龙虎榜 (3)
    {"cat": "龙虎榜", "q": "近 30 日上榜几次?上榜原因是什么?"},
    {"cat": "龙虎榜", "q": "上一次上榜是哪天?次日表现如何?"},
    {"cat": "龙虎榜", "q": "龙虎榜买入席位是否锁仓?有没有砸盘风险?"},

    # K线/技术 (5)
    {"cat": "K线技术", "q": "均线状态怎么样?5/10/20/60 日线排列?"},
    {"cat": "K线技术", "q": "MACD 是金叉还是死叉?周线月线呢?"},
    {"cat": "K线技术", "q": "近 60 日有多少次涨停?跌停?"},
    {"cat": "K线技术", "q": "当前股价在布林带什么位置?是否超买?"},
    {"cat": "K线技术", "q": "N 字形态成立吗?左低右低有没有?"},

    # 涨停历史/连板 (4)
    {"cat": "涨停连板", "q": "现在是几连板?最高连板数是多少?"},
    {"cat": "涨停连板", "q": "封成比多少?开板过几次?"},
    {"cat": "涨停连板", "q": "首次封板时间?14:30 前还是后?"},
    {"cat": "涨停连板", "q": "昨天是不是涨停?今天二板概率多大?"},

    # 仓位/止损/止盈 (5)
    {"cat": "仓位止损", "q": "止损位设在哪?跌破就走?"},
    {"cat": "仓位止损", "q": "止盈位呢?赚多少该卖?"},
    {"cat": "仓位止损", "q": "如果已经持仓, 现在该怎么操作?"},
    {"cat": "仓位止损", "q": "如果今天低开 5%, 我要不要止损?"},
    {"cat": "仓位止损", "q": "补仓策略?什么价位可以加?"},

    # 多轮追问 (5) — 这些依赖历史
    {"cat": "多轮追问", "q": "那白银板块整体爆发呢?能不能追?止损位?"},
    {"cat": "多轮追问", "q": "如果银价明天大涨 10%, 这只票能跟多少?"},
    {"cat": "多轮追问", "q": "假设它今天封板了, 明天怎么操作?"},
    {"cat": "多轮追问", "q": "如果业绩真的反转了, 你会改口吗?"},
    {"cat": "多轮追问", "q": "总结一下, 它的核心矛盾是什么?"},

    # 跨维度综合 (3)
    {"cat": "跨维度综合", "q": "综合所有数据, 给出最终交易计划: 买点/仓位/止损/止盈/持有期"},
    {"cat": "跨维度综合", "q": "如果让你给一个评分 (0-100), 这只票能得几分?为什么?"},
    {"cat": "跨维度综合", "q": "如果有 100 万, 你会全仓/半仓/轻仓/空仓这只?"},

    # 边界 (3)
    {"cat": "边界", "q": "如果它今天停牌了, 之前的判断还成立吗?"},
    {"cat": "边界", "q": "如果我是空仓, 现在该不该建仓?"},
    {"cat": "边界", "q": "如果业绩证伪 + 板块退潮同时发生, 它会跌几个板?"},
]

# 评估指标: 检查 AI 回复是否包含某些关键词
EVAL_KEYWORDS = {
    "明确结论": ["结论", "判断", "建议", "不买", "观望", "买入", "卖出", "持有"],
    "战法引用": ["Y0", "Y1", "Y2", "Y3", "Y4", "Y5", "Y6", "Y7", "Y8", "Y9"],
    "止损位": ["止损", "减仓", "离场", "出场"],
    "免责": ["⚠", "非投资建议", "自负"],
    "板块判定": ["板块", "主线", "题材"],
}


def evaluate_reply(reply: str) -> dict:
    """评估单条 AI 回复, 返回每项命中情况"""
    hits = {}
    for k, kws in EVAL_KEYWORDS.items():
        hits[k] = any(kw in reply for kw in kws)
    return hits


def run_one_round(idx: int, code: str, question: str, history: list[dict]) -> dict:
    """调一次 /api/yeren/ai/chat, 评估回复, 返回结果 dict。"""
    import requests
    t0 = time.time()
    try:
        resp = requests.post(
            "http://127.0.0.1:7799/api/yeren/ai/chat",
            json={"code": code, "message": question, "history": history},
            timeout=120,
        )
        latency = time.time() - t0
        j = resp.json()
        if not j.get("ok"):
            return {"idx": idx, "q": question, "ok": False, "error": j.get("error"), "latency": latency}
        data = j.get("data", {})
        reply = data.get("reply", "")
        hits = evaluate_reply(reply)
        return {
            "idx": idx,
            "cat": next((q["cat"] for q in QUESTIONS if q["q"] == question), "?"),
            "q": question,
            "ok": True,
            "reply_len": len(reply),
            "latency": round(latency, 1),
            "rules_hit": data.get("rules_hit", []),
            "suggestions": data.get("suggestions", [])[:3],
            "used_ctx_keys": data.get("used_ctx_keys", []),
            "degraded": data.get("degraded", False),
            "hits": hits,
            "reply_head": reply[:120],
        }
    except Exception as e:
        return {"idx": idx, "q": question, "ok": False, "error": str(e), "latency": time.time() - t0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="002716", help="测试用股票代码")
    ap.add_argument("--out", default="/tmp/yeren_ai_50r.json")
    ap.add_argument("--limit", type=int, default=None, help="限制题目数 (默认全部 50)")
    args = ap.parse_args()

    qs = QUESTIONS[:args.limit] if args.limit else QUESTIONS
    print(f"R73 · 野人战法 AI 50 轮压力测试 | code={args.code} | 共 {len(qs)} 题\n", flush=True)

    history: list[dict] = []
    results: list[dict] = []
    summary = {
        "total": len(qs),
        "ok": 0,
        "degraded": 0,
        "err": 0,
        "avg_latency": 0.0,
        "eval_hits_pct": {},
    }

    for i, q in enumerate(qs):
        r = run_one_round(i, args.code, q["q"], history)
        r["cat"] = q["cat"]
        results.append(r)
        if r.get("ok"):
            summary["ok"] += 1
            if r.get("degraded"):
                summary["degraded"] += 1
            summary["avg_latency"] += r["latency"]
            # 把 AI 回复加入 history (用于下一轮)
            history.append({"role": "user", "content": q["q"]})
            history.append({"role": "assistant", "content": r.get("reply_head", "")[:200]})
        else:
            summary["err"] += 1

        # 实时输出
        status = "✓" if r.get("ok") and not r.get("degraded") else "✗"
        latency = r.get("latency", 0)
        print(f"  [{i+1:02d}/{len(qs):02d}] {status} {latency:5.1f}s | {q['cat']:8s} | {q['q'][:40]}", flush=True)

    # 汇总评估指标
    if summary["ok"]:
        summary["avg_latency"] = round(summary["avg_latency"] / summary["ok"], 1)
    for k in EVAL_KEYWORDS:
        hits = sum(1 for r in results if r.get("ok") and r.get("hits", {}).get(k))
        summary["eval_hits_pct"][k] = round(hits * 100 / max(summary["ok"], 1), 1)

    out_path = Path(args.out)
    out_path.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 汇总 ===")
    print(f"  题目数: {summary['total']}")
    print(f"  成功: {summary['ok']} | 降级: {summary['degraded']} | 失败: {summary['err']}")
    print(f"  平均延迟: {summary['avg_latency']}s")
    print(f"  评估命中:")
    for k, pct in summary["eval_hits_pct"].items():
        print(f"    {k}: {pct}%")
    print(f"\n详细: {args.out}")


if __name__ == "__main__":
    main()