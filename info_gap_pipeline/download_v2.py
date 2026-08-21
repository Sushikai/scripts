"""download_v2.py — Round 4: 视频素材语义匹配工具集

(注意: 这是 download 目录外的同级 _v2 模块, 不污染 v1 下载逻辑)

工具:
1. score_keyword_match — 段落关键词 vs 素材标题/描述 (0-100)
2. pick_best_material — 多候选中选 score 最高
3. duration_match_score — 时长匹配 (≥80 = ok)
4. ensure_footage — 包装 downloader,禁用 cellauto fallback

设计原则:
  - 不能用 cellauto / testsig / noise 兜底 — 返回 None 让流水线真正失败
  - 段落级 keyword vs 素材标题/desc 命中数评分
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def score_keyword_match(segment: Dict[str, Any], material: Dict[str, Any]) -> float:
    """段落 keywords vs material title+desc 的命中率 (0-100)。

    每个关键词 ≥2 字才计入,防止 "的" 这种 1 字 token 污染。
    """
    kws = (segment.get("keywords") or [])
    kws = [k for k in kws if k and len(k) >= 2]
    if not kws:
        return 0

    title = (material.get("title") or "").lower()
    desc = (material.get("desc") or "").lower()
    haystack = f"{title} {desc}"

    hits = 0
    for k in kws:
        k_lower = k.lower()
        if k_lower in haystack:
            hits += 1
    return round(100 * hits / len(kws), 1)


def pick_best_material(candidates: List[Dict[str, Any]], segment_keywords: List[str]) -> Optional[Dict[str, Any]]:
    """多个候选选 score 最高的。无候选→ None。"""
    if not candidates:
        return None
    seg = {"keywords": segment_keywords}
    scored = []
    for c in candidates:
        s = score_keyword_match(seg, c)
        scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    if best_score == 0:
        return None
    return best


def duration_match_score(required: float, actual: float) -> float:
    """required 秒 video,actual 秒可用素材 → 0-100 评分。

    0 ≤ diff ≤ 30% → 100-scaled
    30-50% → 50
    50-100% → 25
    > 100% → 0
    """
    if not required or not actual:
        return 0
    diff = abs(actual - required) / required
    if diff <= 0.30:
        return 100
    if diff <= 0.50:
        return 50
    if diff <= 1.0:
        return 25
    return 0


def has_cellauto_fallback(path: Optional[Path]) -> bool:
    """判断路径来源是否 cellauto / testsig / 降级噪声视频。"""
    if path is None:
        return False
    name = path.name.lower()
    patterns = ("cellauto", "testsrc", "test_clip", "fallback", "noise", "rand_")
    return any(p in name for p in patterns)


def ensure_footage(bvid: Optional[str], segment_idx: int, output_dir: Path) -> Optional[Path]:
    """Round 4 接口: 拒绝 noise fallback, 真材实料。

    Args:
        bvid: B 站 bvid (可为 None)
        segment_idx: 段落 index
        output_dir: 输出目录

    Returns:
        Path to real downloaded mp4; or None if can't get real material
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    if not bvid:
        log.warning("段 %d 没有 bvid, 拒绝降级 noise — 返回 None" % segment_idx)
        return None
    try:
        from info_gap_pipeline.download import VideoDownloader
        dl = VideoDownloader()
        path = dl.download_bilibili(bvid, segment_idx)
    except Exception as e:
        log.warning(f"段 {segment_idx} 下载失败: {e}")
        return None
    if has_cellauto_fallback(path):
        log.warning(f"段 {segment_idx} 命中 noise 兜底, 拒绝使用")
        return None
    return path
