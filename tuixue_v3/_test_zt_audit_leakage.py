"""
泄漏审计 — 验证 /tmp/zt_winrate_best.json 中 best params 都过 _verify_executable()
(2026-08-12 R69)

用法: python _test_zt_audit_leakage.py [best.json 路径]
默认读 /tmp/zt_winrate_best.json
"""
import sys, json
sys.path.insert(0, '/Users/kaikai/scripts')

from tuixue_v3.zt_optimizer import (
    _verify_executable,
    split_weights,
    _NON_EXECUTABLE_EXITS,
    _NON_EXECUTABLE_FIELDS,
    WEIGHT_KEYS,
)

best_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/zt_winrate_best.json"

print(f"读取 best params: {best_path}")
try:
    with open(best_path) as f:
        best = json.load(f)
except FileNotFoundError:
    print(f"❌ 文件不存在: {best_path}")
    sys.exit(1)

print(f"  score: {best.get('score')}")
s = best.get('result_summary') or {}
print(f"  WR: {s.get('win_rate_pct', 0):.1f}%  trades: {s.get('trades', 0)}  "
      f"total_ret: {s.get('total_return_pct', 0):+.2f}%  max_dd: {s.get('max_drawdown_pct', 0):+.2f}%")

# 拆分 fp (filter params) 和 weights (打分权重)
params = best.get("params", {})
fp, weights = split_weights(params)
print(f"\n  拆分: {len(fp)} filter params + {len(weights)} weights")

# 1. 退场审计 (用 fp)
exit_strat = fp.get("exit_strategy", "?")
print(f"\n{'='*70}")
print(f"1. 退场审计")
print(f"{'='*70}")
if exit_strat in _NON_EXECUTABLE_EXITS:
    print(f"  ❌ 退场 {exit_strat} 是 post-hoc cheating/越窗, 不可实盘")
    print(f"     _NON_EXECUTABLE_EXITS = {sorted(_NON_EXECUTABLE_EXITS)}")
    exit_ok = False
else:
    print(f"  ✅ 退场 {exit_strat} 通过审计")
    exit_ok = True

# 2. 字段审计 (用 fp, 排除 weights)
print(f"\n{'='*70}")
print(f"2. 字段审计 (是否含未来数据字段 — 只查 filter params)")
print(f"{'='*70}")
leak_fields = [k for k in fp if k in _NON_EXECUTABLE_FIELDS]
if leak_fields:
    print(f"  ❌ 发现泄漏字段: {leak_fields}")
    print(f"     _NON_EXECUTABLE_FIELDS = {sorted(_NON_EXECUTABLE_FIELDS)}")
    fields_ok = False
else:
    print(f"  ✅ 所有 {len(fp)} 个 filter params 都通过审计 (无泄漏字段)")
    fields_ok = True

# 3. 全量 _verify_executable (用 fp)
print(f"\n{'='*70}")
print(f"3. 全量 _verify_executable() 检查 (filter params only)")
print(f"{'='*70}")
ok, reason = _verify_executable(fp)
if ok:
    print(f"  ✅ _verify_executable PASS: filter params 100% 可实盘复现")
else:
    print(f"  ❌ _verify_executable FAIL: {reason}")
    fields_ok = False

# 4. entry_rule (用 fp)
print(f"\n{'='*70}")
print(f"4. entry_rule 检查")
print(f"{'='*70}")
entry = fp.get("entry_rule", "?")
if entry == "open_t1":
    print(f"  ✅ entry_rule={entry} (T+1 集合竞价, 可实盘)")
else:
    print(f"  ⚠ entry_rule={entry}")

# 5. market_filter_mode (用 fp)
print(f"\n{'='*70}")
print(f"5. market_filter_mode 检查")
print(f"{'='*70}")
mfm = fp.get("market_filter_mode", "?")
if mfm == "breadth_bullish":
    print(f"  ❌ market_filter_mode={mfm} (用了未来指数)")
elif mfm in ("off", "breadth_panic", "zt_panic", "combined"):
    print(f"  ✅ market_filter_mode={mfm} (只用 T 日已知数据)")

# 6. weights 字段合法性 (weights 是打分系数, 字段名只要在 PARAM_GRID 即可, 不会进 _verify_executable)
print(f"\n{'='*70}")
print(f"6. Weights 检查 (打分系数, 不参与可执行性审计)")
print(f"{'='*70}")
print(f"  共 {len(weights)} 个 weights, 全部为数值, OK")
for k in sorted(weights.keys()):
    if isinstance(weights[k], (int, float)):
        marker = "✅" if -50 <= weights[k] <= 50 else "⚠"
    else:
        marker = "❌"
    print(f"  {marker} {k}: {weights[k]}")

# 7. 总结
print(f"\n{'='*70}")
print(f"7. 总结")
print(f"{'='*70}")
if exit_ok and fields_ok and ok:
    print(f"  🎯 best params 全部通过可执行性审计, 实盘可复现")
    sys.exit(0)
else:
    print(f"  ❌ best params 含不可实盘成分, 不可直接采用")
    sys.exit(1)
