"""baseline_compare.py — 对比 v0 baseline 与当前管线现状,产出 v1 报告"""

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from tests.baseline_capture import capture_baseline
from tests.test_quality import (
    TestNewsSourceQuality,
    TestNewsFreshness,
    TestNewsHeat,
)


def dim_summary(label: str, scores: dict) -> str:
    if "scores" in scores and isinstance(scores["scores"], dict):
        items = scores["scores"]
        bits = ", ".join(f"{k}={v:.1f}" for k, v in items.items())
    elif "score" in scores:
        bits = f"score={scores['score']:.1f}"
    else:
        bits = json.dumps(scores, ensure_ascii=False)
    return f"{label:20s}  {bits}"


def compare(version: str = "v1") -> dict:
    new = capture_baseline(version)
    v0_path = BASE_DIR / "outputs" / "quality_baseline_v0.json"
    if not v0_path.exists():
        print("⚠️ 无 v0 baseline")
        return new
    old = json.loads(v0_path.read_text())

    print(f"\n====== {version.upper()} vs v0 (freshness/heat/news_source 等) ======")
    print()

    rows = []
    for dim in ("NEWS_SOURCE", "NEWS_FRESHNESS", "NEWS_HEAT"):
        new_d = new.get(dim, {})
        old_d = old.get(dim, {})
        # 数值化
        def _to_score(d):
            if "score" in d:
                return d["score"]
            if "scores" in d:
                return sum(d["scores"].values()) / max(1, len(d["scores"]))
            return 0

        ns, os = _to_score(new_d), _to_score(old_d)
        ratio = ns / max(0.01, os) if os > 0 else float("inf")
        rows.append((dim, os, ns, ratio))

    for dim, o, n, r in rows:
        bar_old = "█" * max(0, int(o / 5))
        bar_new = "█" * max(0, int(n / 5))
        improvement = "✓ ≥1.5x" if r >= 1.5 else ("—" if r == 1 else "↑")
        print(f"{dim:18s} v0 {o:6.1f}  v1 {n:6.1f}  × {r:5.2f}  {improvement}")
        print(f"  v0: {bar_old}")
        print(f"  v1: {bar_new}")

    out_path = BASE_DIR / "outputs" / f"quality_baseline_{version}.json"
    out_path.write_text(json.dumps(new, ensure_ascii=False, indent=2))
    print(f"\n✅ {version} baseline saved → {out_path}")
    return new


if __name__ == "__main__":
    compare("v1")
