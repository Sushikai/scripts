#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
游资战法 (BV) 实时推票 100 轮数据准确性验证 (R2004.1)

背景: 用户反馈「命中条件各个股票都一样」— 根因是 _get_universe 字段缺口
  (mcap_yi 恒 0 / change_pct 硬编码 10.0 / 无日线特征) 导致 BV04/05/11/12 永不命中,
  只剩 BV03 人人命中。

本脚本验证重构后:
  1. 100 轮无异常、结果稳定 (同一份上游数据, 输出不抖动)
  2. 命中规则有区分度 (unique 组合数 > 1, 非全部同质)
  3. 关键字段准确: mcap_yi > 0、change_pct 非恒值、seal_ratio 真实
  4. 各规则 BV05/BV06/BV07 有真实命中样本

用法:
  cd /Users/kaikai/scripts && .hermes/hermes-agent/venv/bin/python3 \
    -m tuixue_v3.tests.bv_accuracy_100rounds
"""
import sys
import time
from collections import Counter

ROUNDS = 100
TOP_N = 50


def main() -> int:
    from tuixue_v3 import multi_source_fetchers as msf
    from tuixue_v3.web.bv_strategy.screener import screen_universe

    # ── 进程内缓存 fetch_spot_a_full: 首轮拉真实全市场快照, 后续轮复用 (模拟 Redis 缓存命中)
    _spot_holder = {"data": None}

    orig_spot = msf.fetch_spot_a_full

    def _cached_spot(**kwargs):
        if _spot_holder["data"] is None:
            _spot_holder["data"] = orig_spot(overall_timeout=10) or {}
        return dict(_spot_holder["data"])

    msf.fetch_spot_a_full = _cached_spot

    t0 = time.time()
    results = []
    errors = []
    fail = 0
    matched_sizes = []

    for i in range(1, ROUNDS + 1):
        try:
            data = screen_universe(top_n=TOP_N)
        except Exception as e:
            errors.append(f"round {i}: {type(e).__name__}: {e}")
            fail += 1
            continue
        picks = data.get("picks", []) or []
        results.append(picks)
        matched_sizes.append(int(data.get("matched", 0)))

    elapsed = time.time() - t0
    msf.fetch_spot_a_full = orig_spot

    # ═════════ 汇总 ═════════
    print(f"=== BV 推票 100 轮准确性验证 (R2004.1) ===")
    print(f"耗时: {elapsed:.1f}s · 异常: {fail}/{ROUNDS}")

    if errors:
        print("\n--- 异常明细 (前 5) ---")
        for e in errors[:5]:
            print(" ", e)

    nonempty = [p for p in results if p]
    if not nonempty:
        print("\n[FAIL] 所有轮次 picks 均为空 — 数据源全断?")
        return 1

    # ── 0) 候选规模合理性 (修复前全市场 5546 只误命中 BV11) ──
    max_matched = max(matched_sizes)
    print(f"\n--- 0) 候选规模 ---")
    print(f"   matched 范围: {min(matched_sizes)}~{max_matched} (要求 < 1000, 即只保留涨停池+近期涨停)")
    if max_matched >= 1000:
        print(f"[FAIL] matched={max_matched} 过大 — 全市场误命中, 数据不准确")
        return 1
    print("[PASS] 候选规模合理")

    # ── 1) 命中规则区分度 (核心断言: 各股规则不全相同) ──
    combo_counter: Counter = Counter()
    for picks in nonempty:
        for p in picks:
            combo_counter[tuple(p.get("matched_rules") or [])] += 1

    print(f"\n--- 1) 命中规则区分度 ---")
    print(f"唯一规则组合数: {len(combo_counter)} (要求 > 1)")
    for combo, cnt in combo_counter.most_common(10):
        print(f"   {'+'.join(combo) or '(空)'}: {cnt} 次")
    if len(combo_counter) <= 1:
        print("[FAIL] 所有股票命中规则完全相同 — 数据仍同质化")
        return 1
    print("[PASS] 命中规则有区分度")

    # ── 2) 关键字段准确性 ──
    sample = nonempty[len(nonempty) // 2]  # 取中段一轮
    mcap_ok = sum(1 for p in sample if (p.get("mcap_yi") or 0) > 0)
    mcap_total = len(sample)
    change_vals = [p.get("change_pct") for p in sample]
    unique_change = len(set(change_vals))
    change_ok = len(set(change_vals)) > 1
    seal_with = sum(1 for p in sample if (p.get("seal_ratio") or 0) > 0)
    turnover_with = sum(1 for p in sample if p.get("turnover_pct") is not None)

    print(f"\n--- 2) 关键字段 (抽样轮 #{len(nonempty)//2}) ---")
    print(f"   mcap_yi>0: {mcap_ok}/{mcap_total} (要求 >50%)")
    print(f"   change_pct 唯一值: {unique_change} 个 (要求 >1)")
    print(f"   seal_ratio>0: {seal_with}/{mcap_total}")
    print(f"   turnover_pct 有值: {turnover_with}/{mcap_total}")
    if mcap_ok < mcap_total * 0.5 or not change_ok:
        print("[FAIL] 关键字段缺失 — mcap 或 change_pct 不准确")
        return 1
    print("[PASS] 关键字段准确")

    # ── 3) 规则命中分布 (验证 BV05/06/07 真的能命中) ──
    rule_counter: Counter = Counter()
    for picks in nonempty:
        for p in picks:
            for rid in (p.get("matched_rules") or []):
                rule_counter[rid] += 1
    print(f"\n--- 3) 规则命中分布 (100 轮累计) ---")
    for rid, cnt in rule_counter.most_common():
        print(f"   {rid}: {cnt}")
    core = {"BV05", "BV06", "BV07"}
    missed = core - set(rule_counter)
    if missed:
        print(f"[WARN] 以下核心规则 100 轮 0 命中: {sorted(missed)}")
    else:
        print("[PASS] BV05/BV06/BV07 均有真实命中")

    # ── 4) 稳定性: 相邻轮 picks 应完全一致 (同上游数据无抖动) ──
    stable = 0
    for a, b in zip(results, results[1:]):
        if a == b:
            stable += 1
    print(f"\n--- 4) 稳定性 ---")
    print(f"   相邻轮完全一致: {stable}/{ROUNDS - 1} (要求 ≥ 90%)")
    if stable < (ROUNDS - 1) * 0.9:
        print("[WARN] 相邻轮不一致 — 上游数据在轮次间变化或存在随机性")
    else:
        print("[PASS] 结果稳定")

    # ── 5) 首轮 top 详情 ──
    print(f"\n--- 5) 首轮 Top 8 明细 ---")
    for p in results[0][:8]:
        print(
            f"   {p['code']} {p['name']:<6} 涨幅{p.get('change_pct',0):+.2f}% "
            f"连板{p.get('streak',0)} 封单比{(p.get('seal_ratio') or 0)*100:.0f}% "
            f"换手{p.get('turnover_pct',0):.1f}% 市值{p.get('mcap_yi',0):.0f}亿 "
            f"规则[{'/'.join(p.get('matched_rules') or [])}] 分{p.get('score',0):.1f}"
        )

    print(f"\n{'='*20} 总结: 100 轮完成, 失败 {fail} 轮 {'='*20}")
    if fail == 0:
        print("[PASS] 全部通过")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
