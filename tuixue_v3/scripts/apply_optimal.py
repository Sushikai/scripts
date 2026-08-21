#!/usr/bin/env python3
"""2026-08-04: 应用 /tmp/zt_opt_top.json 替换 zt_config.OPTIMAL_PARAMS。

用法: python3 scripts/apply_optimal.py [--dry-run]
"""
import json
import re
import sys
import argparse
from pathlib import Path

CFG = Path(__file__).resolve().parent.parent / "zt_config.py"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印不写")
    parser.add_argument("--source", default="/tmp/zt_opt_top.json")
    args = parser.parse_args()

    src = Path(args.source)
    if not src.exists():
        print(f"❌ {src} 不存在, 优化器还没跑完或失败了")
        sys.exit(1)

    with open(src) as f:
        data = json.load(f)

    best = data.get("best_params") or (data.get("in_sample") or {}).get("best_params") or {}
    best_score = data.get("best_score")
    summary = data.get("best_summary") or {}
    if not best:
        print("❌ best_params 为空")
        sys.exit(1)

    print(f"最佳 score={best_score} 月复利={summary.get('monthly_compound_pct',0):.1f}% "
          f"笔={summary.get('trades',0)} WR={summary.get('win_rate_pct',0):.1f}% "
          f"DD={summary.get('max_drawdown_pct',0):.1f}%")
    print("\n推荐参数:")
    for k, v in best.items():
        print(f"  {k!r}: {v!r}")

    if args.dry_run:
        print("\n--dry-run 模式, 不写入")
        return

    # 解析现有 OPTIMAL_PARAMS, 替换值
    text = CFG.read_text(encoding="utf-8")
    # 找 OPTIMAL_PARAMS = { ... } 块
    m = re.search(r"OPTIMAL_PARAMS = \{[^}]*\}", text, re.DOTALL)
    if not m:
        print("❌ zt_config.py 没找到 OPTIMAL_PARAMS 块")
        sys.exit(1)
    old_block = m.group(0)

    new_lines = ["OPTIMAL_PARAMS = {"]
    for k, v in best.items():
        if isinstance(v, str):
            new_lines.append(f'    "{k}": "{v}",')
        elif isinstance(v, bool):
            new_lines.append(f'    "{k}": {v},')
        else:
            new_lines.append(f'    "{k}": {v},')
    new_lines.append("}")
    new_block = "\n".join(new_lines)

    text2 = text.replace(old_block, new_block, 1)
    if text == text2:
        print("❌ 替换失败, 内容未变化")
        sys.exit(1)

    CFG.write_text(text2, encoding="utf-8")
    print(f"\n✅ 已更新 {CFG}")


if __name__ == "__main__":
    main()