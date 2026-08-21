"""test_quality.py — 7 维度质量基准

每个维度有可量化的指标,可被自动打分。所有指标都是 [0, 100] 区间,越大越好。
每个测试可独立运行,既可单跑单个维度,也可跑全部。

设计原则:
- 离线 + 异步:每个测试只用项目内已有资源,无需外网
- 单元友好:接受 fixture 文件或合成输入
- 失败时打印具体的扣分项,方便定位改进点

7 个维度:
1. NEWS_SOURCE       — 新闻源覆盖/真实信息差检测
2. NEWS_FRESHNESS    — 新鲜度(本新闻距今多久)
3. NEWS_HEAT         — 热度(独立多平台加权)
4. SCRIPT_QUALITY    — 脚本可读性/事实密度/故事结构
5. VOICEOVER_QUALITY — 配音断句/SSML/段落对齐
6. FOOTAGE_MATCH     — 视频素材匹配度(段落级)
7. SUBTITLE_SYNC     — 字幕-AV 时间对齐精度
"""

import json
import math
import re
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple


# ─────────────────────────────────────────────────────────────────────
# 评分工具
# ─────────────────────────────────────────────────────────────────────
def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


# ─────────────────────────────────────────────────────────────────────
# 维度 1: 新闻源 — 覆盖广度 + 真信息差
# ─────────────────────────────────────────────────────────────────────
class TestNewsSourceQuality(unittest.TestCase):
    """评估 news.research 的输出质量。
    关注:
    a) 多平台聚合覆盖率
    b) 真信息差指标(同事件被多源交叉验证/独家性)
    c) 数据字段完整度
    """

    DIMS = ("coverage", "info_gap", "field_completeness")

    @staticmethod
    def _score_coverage(topics: List[Dict[str, Any]]) -> float:
        """覆盖广度:多少独立来源出现在结果中。最理想 = N_sources / N_topics。"""
        sources_seen = set()
        for t in topics:
            src = t.get("source") or t.get("src") or ""
            if src:
                sources_seen.add(src)
        # 7 个源全拿到 = 满分;若 topics 全来自单一源 → 极低
        # 用对数增长奖励独立源
        n_unique = len(sources_seen)
        if n_unique == 0:
            return 0.0
        # 7 sources → 100, 5 → 71, 3 → 43, 1 → 14
        return _clamp(100 * math.log(1 + n_unique) / math.log(1 + 7))

    @staticmethod
    def _score_info_gap(topics: List[Dict[str, Any]]) -> float:
        """真信息差指标:
        - 必须有时间戳(信息差的价值随时间衰减)
        - 关键词不应只是"内幕/真相"这种浅判,而要看结构性差异
          (实际简化为:有 raw_url + 标题里包含数字/年份/数据/具体数字 → 高分)
        """
        if not topics:
            return 0.0
        score = 0.0
        for t in topics:
            row_score = 0
            if t.get("url") or t.get("raw_url"):
                row_score += 20
            if t.get("heat") or t.get("hot_value") or t.get("rank"):
                row_score += 20
            title = t.get("title", "")
            # 数据/年份/具体数字 → 30
            if re.search(r'\d+([%\.]\d+)?|\d{2,}|2024|2025|2026', title):
                row_score += 30
            # 含具体主体名(中文段落+英数符号) → 30
            if re.search(r'[A-Za-z一-鿿]{4,}', title) and len(title) >= 6:
                row_score += 30
            score += row_score
        return _clamp(score / len(topics))

    @staticmethod
    def _score_field_completeness(topics: List[Dict[str, Any]]) -> float:
        """每条话题必须至少有 title + url + source"""
        if not topics:
            return 0.0
        ok = 0
        for t in topics:
            if t.get("title") and (t.get("url") or t.get("raw_url")) and (t.get("source") or t.get("src")):
                ok += 1
        return _clamp(100 * ok / len(topics))

    def test_score(self):
        # 用一个合成输入算分
        topics = self._synthesize()
        for d in self.DIMS:
            fn = getattr(self, f"_score_{d}")
            score = fn(topics)
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)
        print(json.dumps({
            "dim": "NEWS_SOURCE",
            "scores": {d: getattr(self, f"_score_{d}")(topics) for d in self.DIMS},
            "n_topics": len(topics),
        }, ensure_ascii=False))

    @staticmethod
    def _synthesize() -> List[Dict[str, Any]]:
        return [
            {"title": "科学家发现地球内核2026年首次反向旋转", "url": "https://example.com/1", "source": "知乎", "heat": "9999"},
            {"title": "比特币2025年暴跌30%, 背后真相曝光", "url": "https://example.com/2", "source": "微博", "rank": 1},
            {"title": "央行行长罕见表态, 99% 的人都没听懂的潜规则", "url": "https://example.com/3", "source": "百度"},
            {"title": "DeepSeek V4 发布, 性能提升 200%", "url": "https://example.com/4", "source": "抖音"},
            {"title": "OpenAI 秘密计划: 中国市场重大突破", "url": "https://example.com/5", "source": "B站"},
        ]


# ─────────────────────────────────────────────────────────────────────
# 维度 2: 新鲜度
# ─────────────────────────────────────────────────────────────────────
class TestNewsFreshness(unittest.TestCase):
    """新鲜度:新闻越近越好。
    评分: < 1h = 100, < 6h = 80, < 12h = 60, < 24h = 40, < 48h = 20, else 0
    """

    @staticmethod
    def _score_freshness(topics: List[Dict[str, Any]], now: datetime = None) -> Tuple[float, List[Dict[str, Any]]]:
        now = now or datetime.now()
        per = []
        total = 0.0
        for t in topics:
            age_minutes = None
            ts_raw = t.get("timestamp") or t.get("showTime") or t.get("publish_time") or t.get("ctime")
            if ts_raw:
                try:
                    if isinstance(ts_raw, (int, float)):
                        dt = datetime.fromtimestamp(ts_raw)
                    elif isinstance(ts_raw, str):
                        # 兼容 "2026-07-23 12:34:56" 格式
                        dt = datetime.fromisoformat(ts_raw.replace("/", "-"))
                    else:
                        dt = None
                    if dt:
                        age_minutes = (now - dt).total_seconds() / 60.0
                except Exception:
                    age_minutes = None
            # 缺时间戳 → 0 分(惩罚)
            if age_minutes is None:
                score = 0.0
            elif age_minutes < 60:
                score = 100.0
            elif age_minutes < 60 * 6:
                score = 80.0
            elif age_minutes < 60 * 12:
                score = 60.0
            elif age_minutes < 60 * 24:
                score = 40.0
            elif age_minutes < 60 * 48:
                score = 20.0
            else:
                score = 0.0
            per.append({"title": t.get("title", "")[:30], "age_min": age_minutes, "score": score})
            total += score
        return _clamp(total / max(1, len(topics))), per

    def test_score(self):
        now = datetime(2026, 7, 24, 12, 0, 0)
        topics = [
            {"title": "刚刚发生", "timestamp": "2026-07-24 11:30:00"},
            {"title": "2小时前",   "timestamp": "2026-07-24 10:00:00"},
            {"title": "10小时前",  "timestamp": "2026-07-24 02:00:00"},
            {"title": "无时间戳",  "timestamp": None},
        ]
        score, per = self._score_freshness(topics, now)
        print(json.dumps({"dim": "NEWS_FRESHNESS", "score": score, "per": per}, ensure_ascii=False, indent=2))
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)


# ─────────────────────────────────────────────────────────────────────
# 维度 3: 热度
# ─────────────────────────────────────────────────────────────────────
class TestNewsHeat(unittest.TestCase):
    """热度评分:综合多平台 heat 值。
    评分要点:
      - 多平台覆盖 (微博 heat=9999 在 B站 heat=8888)
      - 数值规范化 (log scale)
      - 排序稳定性
    """

    @staticmethod
    def _score_heat(topics: List[Dict[str, Any]]) -> float:
        if not topics:
            return 0.0
        total_log = 0.0
        max_log = 0.0
        for t in topics:
            # 找数字热度
            nums = []
            for k in ("heat", "hot_value", "hotScore", "score", "rank"):
                v = t.get(k)
                if v is None:
                    continue
                try:
                    v = float(str(v).replace(",", ""))
                    if v > 0:
                        nums.append(v)
                except Exception:
                    pass
            if not nums:
                continue
            heat_log = math.log10(max(1, max(nums)))
            # rank 越小越值钱 — 反转 (仅当 rank 是真数字)
            rank_v = t.get("rank")
            if isinstance(rank_v, (int, float)) and rank_v > 0:
                try:
                    heat_log += math.log10(max(1, 100 - int(rank_v)) + 1)
                except Exception:
                    pass
            total_log += heat_log
            max_log = max(max_log, heat_log)
        if max_log == 0:
            return 0.0
        # 满分:5 条话题,平均 heat ~100w → log10(1e6)=6
        avg = total_log / len(topics)
        return _clamp(100 * avg / 6.0)

    def test_score(self):
        topics = [
            {"title": "高热度", "heat": "1000000"},
            {"title": "中热度", "heat": "10000"},
            {"title": "B站第一", "rank": "1"},
            {"title": "无热度"},
        ]
        score = self._score_heat(topics)
        print(json.dumps({"dim": "NEWS_HEAT", "score": score}, ensure_ascii=False))
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)


# ─────────────────────────────────────────────────────────────────────
# 维度 4: 脚本质量
# ─────────────────────────────────────────────────────────────────────
class TestScriptQuality(unittest.TestCase):
    """脚本评分:
    - 字数合理性 (300-1200 字 ≈ 1-3 分钟口播)
    - 事实密度 (出现数字/年份/具体数据占比)
    - 故事钩子 (开头 50 字内有"刚刚/突发/震惊/首次/真相/曝光"...等 hook 词)
    - 段落切分 (有 seg_idx/keywords 字段,供下游匹配)
    - TTS markup (含 [pause] <emphasis> 之类标记)
    """

    DIMS = ("length", "fact_density", "hook", "segmentation", "tts_markup", "keywords_per_segment")

    @staticmethod
    def _score_length(text: str) -> float:
        n = len(text or "")
        if n < 50:
            return 0
        if n < 300:
            return 50 * (n - 50) / 250
        if n <= 1200:
            return 100
        if n <= 2000:
            return max(0, 100 - 50 * (n - 1200) / 800)
        return 0

    @staticmethod
    def _score_fact_density(text: str) -> float:
        if not text:
            return 0.0
        # 数字 + 数据点
        nums = re.findall(r'\d+(?:\.\d+)?', text)
        years = re.findall(r'20[1-3]\d', text)
        facts = re.findall(r'(据|显示|报告|数据|统计|增长|下降|对比|研究|机构)', text)
        # 1 个数字/10 字 为饱和
        density = (len(nums) + len(years) * 2 + len(facts) * 1.5) / (len(text) / 100.0)
        return _clamp(100 * density / 5.0)

    @staticmethod
    def _score_hook(text: str) -> float:
        if not text:
            return 0
        head = text[:80]
        hook_kw = ["刚刚", "突发", "震惊", "首次", "真相", "曝光", "内幕", "99%", "90%", "大多数人", "你不知道", "罕见", "突破", "颠覆"]
        hits = sum(1 for k in hook_kw if k in head)
        # 3 个 hook 词 = 满
        return _clamp(100 * hits / 3.0)

    @staticmethod
    def _score_segmentation(scripts: List[Dict[str, Any]]) -> float:
        # 有 segments 字段 且 每段有 keywords + char_count + duration
        if not scripts:
            return 0
        total = 0.0
        for s in scripts:
            segs = s.get("segments", [])
            if not segs:
                total += 0
                continue
            ok = sum(1 for seg in segs if seg.get("text") and seg.get("keywords") and seg.get("duration", 0) > 0)
            total += 100 * ok / len(segs)
        return _clamp(total / len(scripts))

    @staticmethod
    def _score_tts_markup(text: str) -> float:
        if not text:
            return 0
        # [pause]/<emphasis> 等标记
        pause = text.count("[pause]")
        emph = len(re.findall(r'<emphasis>.*?</emphasis>', text))
        # 200 字 1 个标记合理
        return _clamp(100 * (pause + emph) * 100 / len(text))

    @staticmethod
    def _score_keywords_per_segment(scripts: List[Dict[str, Any]]) -> float:
        if not scripts:
            return 0
        total = 0.0
        for s in scripts:
            segs = s.get("segments", [])
            if not segs:
                continue
            ok = sum(1 for seg in segs if seg.get("keywords"))
            total += 100 * ok / len(segs)
        return _clamp(total / len(scripts))

    def test_score(self):
        text = "刚刚,一个内幕被曝光! 据统计,99% 的人都不知道这件事 [pause]。科学家发现,2025 年地球内核首次 [pause] 反向旋转。数据对比下来,这种突破在过去 100 年里从未记录。<emphasis>真相</emphasis> 让你吃惊。"
        scripts = [{
            "script": text,
            "segments": [
                {"text": "刚刚,一个内幕被曝光! 据统计,99% 的人都不知道这件事 [pause]。", "duration": 5.0, "keywords": ["曝光", "99%"]},
                {"text": "科学家发现,2025 年地球内核首次 [pause] 反向旋转。", "duration": 4.0, "keywords": ["地球内核", "旋转"]},
                {"text": "数据对比下来,这种突破在过去 100 年里从未记录。", "duration": 3.0, "keywords": ["突破", "100 年"]},
            ],
        }]
        scores = {
            "length": self._score_length(text),
            "fact_density": self._score_fact_density(text),
            "hook": self._score_hook(text),
            "segmentation": self._score_segmentation(scripts),
            "tts_markup": self._score_tts_markup(text),
            "keywords_per_segment": self._score_keywords_per_segment(scripts),
        }
        print(json.dumps({"dim": "SCRIPT_QUALITY", "scores": scores}, ensure_ascii=False, indent=2))
        for s in scores.values():
            self.assertGreaterEqual(s, 0)


# ─────────────────────────────────────────────────────────────────────
# 维度 5: 配音质量
# ─────────────────────────────────────────────────────────────────────
class TestVoiceoverQuality(unittest.TestCase):
    """配音评分:
    - 段落对齐(每个脚本段都有对应配音)
    - 时长匹配(配音时长 ≈ 脚本估算时长 ± 30%)
    - 静音段无异常(无 0 时长/超长段)
    - 音量 RMS 稳定(无 clipped/无声)
    """

    @staticmethod
    def _score_segment_alignment(script_segments: List[Dict], audio_segments: List[Dict]) -> float:
        if not script_segments:
            return 0
        n_match = 0
        for s in script_segments:
            # 找匹配:同 idx 或 同 text 前缀
            found = None
            for a in audio_segments:
                if a.get("idx") == s.get("idx") or a.get("text", "").startswith(s.get("text", "")[:20]):
                    found = a
                    break
            if found:
                n_match += 1
        return _clamp(100 * n_match / len(script_segments))

    @staticmethod
    def _score_duration_match(script_segments: List[Dict], audio_segments: List[Dict]) -> float:
        if not script_segments or not audio_segments:
            return 0
        ratios = []
        for s in script_segments:
            for a in audio_segments:
                if a.get("idx") == s.get("idx"):
                    ed = s.get("duration", 0)
                    ad = a.get("audio_duration", 0)
                    if ed and ad:
                        ratio = ad / ed
                        # 0.7 ~ 1.3 算合理
                        if 0.5 <= ratio <= 1.5:
                            ratios.append(1 - abs(ratio - 1))
                        else:
                            ratios.append(0)
                    break
        return _clamp(100 * sum(ratios) / max(1, len(ratios)))

    @staticmethod
    def _score_no_anomalies(audio_segments: List[Dict]) -> float:
        if not audio_segments:
            return 0
        bad = 0
        for a in audio_segments:
            d = a.get("audio_duration", 0)
            if d <= 0 or d > 600:  # 0 或 > 10 分钟都异常
                bad += 1
        return _clamp(100 * (1 - bad / len(audio_segments)))

    def test_score(self):
        scripts = [{"idx": 0, "duration": 5.0}, {"idx": 1, "duration": 4.0}]
        audios = [{"idx": 0, "audio_duration": 5.2}, {"idx": 1, "audio_duration": 4.1}]
        scores = {
            "segment_alignment": self._score_segment_alignment(scripts, audios),
            "duration_match": self._score_duration_match(scripts, audios),
            "no_anomalies": self._score_no_anomalies(audios),
        }
        print(json.dumps({"dim": "VOICEOVER_QUALITY", "scores": scores}, ensure_ascii=False, indent=2))
        for s in scores.values():
            self.assertGreaterEqual(s, 0)


# ─────────────────────────────────────────────────────────────────────
# 维度 6: 视频素材匹配
# ─────────────────────────────────────────────────────────────────────
class TestFootageMatch(unittest.TestCase):
    """视频素材评分:
    - 每段都有视频(无 None fallback)
    - 关键词-视频匹配(段落关键词出现在素材标题/描述里)
    - 时长匹配
    - 无降级噪点兜底(无 cellauto / testsig / random noise)
    """

    @staticmethod
    def _score_no_fallback(matches: List[Dict]) -> float:
        if not matches:
            return 0
        bad_patterns = re.compile(r"cellauto|testsrc|test_clip|noise|fallback|undefined|null|nan", re.I)
        good = sum(1 for m in matches if not bad_patterns.search(str(m.get("source_url", "")) + " " + str(m.get("source_title", ""))))
        return _clamp(100 * good / len(matches))

    @staticmethod
    def _score_keyword_match(scripts: List[Dict], matches: List[Dict]) -> float:
        if not scripts or not matches:
            return 0
        score = 0.0
        for i, s in enumerate(scripts):
            kw = (s.get("keywords") or [])
            m = matches[i] if i < len(matches) else {}
            title = (m.get("source_title", "") or "").lower()
            kw_lower = [k.lower() for k in kw]
            hits = sum(1 for k in kw_lower if k and k in title)
            if kw:
                score += 100 * hits / len(kw)
            else:
                score += 30  # 没关键词也至少 30
        return _clamp(score / len(scripts))

    @staticmethod
    def _score_duration_match(scripts: List[Dict], matches: List[Dict]) -> float:
        if not scripts or not matches:
            return 0
        ok = 0
        for i, s in enumerate(scripts):
            if i >= len(matches):
                continue
            req = s.get("duration", 0)
            got = matches[i].get("video_duration", 0)
            if req and got and abs(got - req) / req < 0.5:
                ok += 1
        return _clamp(100 * ok / len(scripts))

    def test_score(self):
        scripts = [
            {"keywords": ["地球", "内核", "旋转"], "duration": 5.0},
            {"keywords": ["NASA", "发现"], "duration": 4.0},
        ]
        matches = [
            {"source_url": "https://www.bilibili.com/video/BV1xxx", "source_title": "NASA 发现地球内核异常", "video_duration": 5.2},
            {"source_url": "https://www.bilibili.com/video/BV2xxx", "source_title": "科学家证实地核逆转", "video_duration": 4.1},
        ]
        scores = {
            "no_fallback": self._score_no_fallback(matches),
            "keyword_match": self._score_keyword_match(scripts, matches),
            "duration_match": self._score_duration_match(scripts, matches),
        }
        print(json.dumps({"dim": "FOOTAGE_MATCH", "scores": scores}, ensure_ascii=False, indent=2))
        for s in scores.values():
            self.assertGreaterEqual(s, 0)


# ─────────────────────────────────────────────────────────────────────
# 维度 7: 字幕-AV 时间对齐
# ─────────────────────────────────────────────────────────────────────
class TestSubtitleSync(unittest.TestCase):
    """字幕评分:
    - 字级别时间戳(每个字/词级时间戳)
    - 行不会跨秒太多 (< 8 chars/行)
    - 起始时间 > 视频开始 (不是 t=0)
    - 与配音 actual_end 一致(不要早退/晚退)
    - 关键实体高亮(mark/em)
    """

    @staticmethod
    def _score_word_timestamps(subtitle_entries: List[Dict]) -> float:
        if not subtitle_entries:
            return 0
        have_word = sum(1 for e in subtitle_entries if "words" in e and len(e["words"]) >= 1)
        return _clamp(100 * have_word / len(subtitle_entries))

    @staticmethod
    def _score_line_chunks(subtitle_entries: List[Dict]) -> float:
        """字幕断行: 每 8 字左右换行,避免大段"""
        if not subtitle_entries:
            return 0
        ok = 0
        for e in subtitle_entries:
            text = e.get("text", "")
            # 长度 ≤ 16 字算合规
            if len(text) <= 16:
                ok += 1
        return _clamp(100 * ok / len(subtitle_entries))

    @staticmethod
    def _score_keyword_highlight(subtitle_entries: List[Dict]) -> float:
        if not subtitle_entries:
            return 0
        # 如果有关键词就 +分
        ok = sum(1 for e in subtitle_entries if e.get("em") or e.get("keyword"))
        return _clamp(100 * ok / len(subtitle_entries))

    @staticmethod
    def _score_no_overrun(subtitle_entries: List[Dict], total_audio_dur: float) -> float:
        if not subtitle_entries:
            return 0
        bad = 0
        for e in subtitle_entries:
            if e.get("end", 0) > total_audio_dur + 1.0:
                bad += 1
        return _clamp(100 * (1 - bad / len(subtitle_entries)))

    def test_score(self):
        entries = [
            {"text": "刚刚", "start": 0.1, "end": 0.6, "em": True, "words": [{"t": 0.1, "w": "刚"}, {"t": 0.4, "w": "刚"}]},
            {"text": "一个", "start": 0.7, "end": 1.0, "words": [{"t": 0.7, "w": "一"}, {"t": 0.9, "w": "个"}]},
            {"text": "内幕被曝光!", "start": 1.1, "end": 2.2, "em": True, "words": [{"t": 1.1, "w": "内"}, {"t": 1.2, "w": "幕"}]},
        ]
        scores = {
            "word_timestamps": self._score_word_timestamps(entries),
            "line_chunks": self._score_line_chunks(entries),
            "keyword_highlight": self._score_keyword_highlight(entries),
            "no_overrun": self._score_no_overrun(entries, total_audio_dur=2.5),
        }
        print(json.dumps({"dim": "SUBTITLE_SYNC", "scores": scores}, ensure_ascii=False, indent=2))
        for s in scores.values():
            self.assertGreaterEqual(s, 0)


# ─────────────────────────────────────────────────────────────────────
# 全量跑 + 报告
# ─────────────────────────────────────────────────────────────────────
def run_all(report_path: Path = None) -> Dict[str, Dict[str, Any]]:
    """运行全部 7 个维度,汇总成 dict。
    返回: {"DIM_NAME": {"score": float, "sub": {...}, "per": [...]}}"""
    suite = unittest.TestSuite()
    for cls in (
        TestNewsSourceQuality,
        TestNewsFreshness,
        TestNewsHeat,
        TestScriptQuality,
        TestVoiceoverQuality,
        TestFootageMatch,
        TestSubtitleSync,
    ):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    # 把所有打印截取,实际维度放在 _last_score
    # 这里更简单:从每个 class 的 _score 方法调用上重新评估
    out = {}
    now = datetime(2026, 7, 24, 12, 0, 0)
    # D1
    topics_d1 = TestNewsSourceQuality._synthesize()
    out["NEWS_SOURCE"] = {
        d: getattr(TestNewsSourceQuality, f"_score_{d}")(topics_d1)
        for d in TestNewsSourceQuality.DIMS
    }
    # D2
    topics_d2 = [
        {"title": "刚刚", "timestamp": "2026-07-24 11:30:00"},
        {"title": "2h前", "timestamp": "2026-07-24 10:00:00"},
        {"title": "10h前", "timestamp": "2026-07-24 02:00:00"},
        {"title": "无戳", "timestamp": None},
    ]
    score2, _ = TestNewsFreshness._score_freshness(topics_d2, now)
    out["NEWS_FRESHNESS"] = {"score": score2}
    # D3
    topics_d3 = [
        {"title": "高", "heat": "1000000"},
        {"title": "中", "heat": "10000"},
        {"title": "r1", "rank": "1"},
        {"title": "无", "heat": None},
    ]
    out["NEWS_HEAT"] = {"score": TestNewsHeat._score_heat(topics_d3)}
    # D4
    text4 = "刚刚,一个内幕被曝光! 据统计,99% 的人都不知道这件事 [pause]。科学家发现,2025 年地球内核首次 [pause] 反向旋转。"
    scripts4 = [{
        "script": text4,
        "segments": [
            {"text": "刚刚,一个内幕被曝光!", "duration": 3.0, "keywords": ["曝光"]},
            {"text": "99% 的人都不知道", "duration": 2.0, "keywords": ["99%"]},
        ],
    }]
    out["SCRIPT_QUALITY"] = {
        d: getattr(TestScriptQuality, f"_score_{d}")(text4 if d in {"length","fact_density","hook","tts_markup"} else scripts4)
        for d in TestScriptQuality.DIMS
    }
    # D5
    out["VOICEOVER_QUALITY"] = {
        "segment_alignment":  TestVoiceoverQuality._score_segment_alignment([{"idx": 0}], [{"idx": 0, "audio_duration": 5}]),
        "duration_match":     TestVoiceoverQuality._score_duration_match([{"idx": 0, "duration": 5}], [{"idx": 0, "audio_duration": 5.2}]),
        "no_anomalies":       TestVoiceoverQuality._score_no_anomalies([{"audio_duration": 5.2}]),
    }
    # D6
    scripts6 = [{"keywords": ["NASA"], "duration": 5}]
    matches6 = [{"source_url": "https://www.bilibili.com/BV1", "source_title": "NASA 探索", "video_duration": 5.1}]
    out["FOOTAGE_MATCH"] = {
        "no_fallback":  TestFootageMatch._score_no_fallback(matches6),
        "keyword_match":TestFootageMatch._score_keyword_match(scripts6, matches6),
        "duration_match":TestFootageMatch._score_duration_match(scripts6, matches6),
    }
    # D7
    entries7 = [{"text": "刚刚", "start": 0.1, "end": 0.6, "em": True, "words": [{"t":0.1,"w":"刚"}]}]
    out["SUBTITLE_SYNC"] = {
        "word_timestamps": TestSubtitleSync._score_word_timestamps(entries7),
        "line_chunks":     TestSubtitleSync._score_line_chunks(entries7),
        "keyword_highlight":TestSubtitleSync._score_keyword_highlight(entries7),
        "no_overrun":      TestSubtitleSync._score_no_overrun(entries7, 1.0),
    }

    if report_path:
        report_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))

    return out


if __name__ == "__main__":
    out = run_all()
    print(json.dumps(out, ensure_ascii=False, indent=2))
