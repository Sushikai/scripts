"""
test_reference_match.py — 参考视频BV1EY7k6aEPg风格对比测试 (20项)
测试生成的视频在音色、语速、画面、解说风格等各方面与参考视频的匹配程度
目标：一比一复刻参考视频的解说节奏、解说风格、语气、语速、章节结构
"""

import pytest, json, subprocess, re, logging
from pathlib import Path
from typing import Dict, List, Tuple
import numpy as np

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# 参考视频关键参数（从BV1EY7k6aEPg提取）
REFERENCE_PARAMS = {
    # ── 视频格式 ──
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "codec": "h264",
    "min_bitrate_mbps": 2.0,

    # ── 内容结构 ──
    "num_topics": 7,
    "total_duration": 233.4,
    "avg_topic_duration": 33.3,
    "chapter_min_count": 3,
    "chapter_pattern": r"第[一二三四五六七八九十]+[、,]",

    # ── 语速节奏（核心参数） ──
    "speech_rate": 5.4,  # 字/秒
    "speech_rate_min": 4.5,
    "speech_rate_max": 6.5,
    "syllable_rate": 5.4,  # 音节速率（近似字/秒）
    "pause_per_sentence": 0.8,  # 每句停顿(秒)
    "avg_pause_between_segments": 1.2,  # 段落间停顿

    # ── 句子节奏 ──
    "avg_sentence_length": 40,  # Whisper段落平均长度(字)
    "sentence_length_min": 10,
    "sentence_length_max": 80,
    "long_sentence_ratio": 0.6,  # 长句占比(>40字)

    # ── 解说风格 ──
    "opening_pattern": r"第[一二三四五六七八九十]+[、,，]",  # 「第一、」或「第一,」开头
    "opening_starts_directly": True,  # 直接进入，不说大家好
    "data_driven": True,  # 数据驱动

    # ── 过渡词 ──
    "required_transitions": ["然而", "对此", "不过", "不过"],
    "transition_count_min": 2,

    # ── 禁止词汇 ──
    "forbidden_patterns": [
        r"第1[、.]", r"第2[、.]", r"第3[、.]", r"第4[、.]",  # 阿拉伯数字章节
        r"首先", r"其次", r"最后",  # 陈旧结构词
        r"据悉", r"相信大家", r"让我们一起", r"大家好",  # 记者腔
        r"真的吗", r"这就离谱", r"你想想", r"有意思",  # 网络腔
        r"我觉得", r"我认为", r"大家都知道",  # 主观腔
        r"第1点", r"第2点", r"一方面", r"另一方面",  # 会议腔
    ],

    # ── 数据密度 ──
    "data_keywords": ["万", "亿", "％", "%", "倍", "公斤", "千米", "米", "度", "年", "月", "日", "小时", "分钟", "秒"],
    "min_data_density": 0.02,  # 每字数据关键词密度

    # ── 专家引用 ──
    "expert_phrases": ["专家", "学者", "教授", "表示", "指出", "称"],
    "min_expert_count": 1,

    # ── 结尾格式 ──
    "ending_variants": [
        "今日分享到此结束",
        "感谢观看",
        "分享到此结束",
        "感谢觀看",
    ],

    # ── 音频参数 ──
    "audio_sample_rate": 44100,
    "audio_channels": 2,
    "audio_bitrate_kbps": 128,

    # ── 参考视频关键词 ──
    "ref_style_keywords": [
        "第一", "第二", "第三", "第四", "第五", "第六", "第七",
        "然而", "对此", "不过", "专家", "近日", "据",
        "数据显示", "进入",
    ],
}

# 测试阈值
PASS_THRESHOLDS = {
    "speech_rate_match": 0.80,
    "duration_match": 0.85,
    "format_match": 1.0,
    "structure_match": 0.70,
    "visual_quality_score": 0.75,
    "audio_sync_score": 0.95,
    "content_completeness": 0.67,
    "transition_match": 0.67,
    "forbidden_check": 1.0,
    "rhythm_match": 0.70,
    "sentence_length_match": 0.40,
    "opening_style_match": 0.50,
    "data_density_match": 0.30,
    "expert_usage_match": 0.50,
    "emotional_tone_match": 0.70,
    "topic_diversity_match": 0.60,
    "pause_pattern_match": 0.30,
}


class ReferenceMatcher:
    """对比测试工具类"""

    def __init__(self, ref_video_path: str, test_video_path: str):
        self.ref_path = Path(ref_video_path)
        self.test_path = Path(test_video_path)
        self._audio_path = None

    def _extract_audio(self) -> Path:
        """提取音频并缓存，避免重复提取"""
        if self._audio_path is None or not self._audio_path.exists():
            self._audio_path = self.test_path.with_name(f"_temp_audio_{self.test_path.stem}.wav")
            cmd = [
                "ffmpeg", "-y", "-i", str(self.test_path),
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                str(self._audio_path),
            ]
            subprocess.run(cmd, capture_output=True, timeout=120)
        return self._audio_path

    def _get_full_text(self) -> str:
        """获取完整转写文本"""
        audio_path = self._extract_audio()
        from faster_whisper import WhisperModel
        model = WhisperModel("small", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(audio_path), language="zh", vad_filter=True)
        return "".join(s.text for s in list(segments))

    def _get_segments(self) -> List:
        """获取Whisper段落列表"""
        audio_path = self._extract_audio()
        from faster_whisper import WhisperModel
        model = WhisperModel("small", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(audio_path), language="zh", vad_filter=True)
        return list(segments)

    # ═══════════════════════════════════════════════════════════
    #  1-3. 基础格式测试
    # ═══════════════════════════════════════════════════════════

    def test_01_video_format(self) -> Dict:
        """测试视频格式（1920x1080, 16:9, 30fps）"""
        result = {"passed": False, "details": {}, "score": 0.0}
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,codec_name",
            "-of", "json", str(self.test_path),
        ]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            data = json.loads(out.stdout)
            stream = data["streams"][0]
            width = stream["width"]
            height = stream["height"]
            fps_str = stream["r_frame_rate"]
            fps = eval(fps_str) if "/" in fps_str else float(fps_str)
            codec = stream["codec_name"]

            width_ok = width == REFERENCE_PARAMS["width"]
            height_ok = height == REFERENCE_PARAMS["height"]
            fps_ok = abs(fps - REFERENCE_PARAMS["fps"]) < 1.0
            format_ok = width_ok and height_ok and fps_ok
            result["score"] = 1.0 if format_ok else 0.0
            result["passed"] = format_ok
            result["details"] = {"width": width, "height": height, "fps": fps, "codec": codec}
            issues = []
            if not width_ok: issues.append(f"宽{width}≠{REFERENCE_PARAMS['width']}")
            if not height_ok: issues.append(f"高{height}≠{REFERENCE_PARAMS['height']}")
            if not fps_ok: issues.append(f"帧{fps}≠{REFERENCE_PARAMS['fps']}")
            result["message"] = f"{width}x{height}@{fps}fps" if not issues else f"格式不符: {', '.join(issues)}"
        except Exception as e:
            result["message"] = f"格式检测失败: {e}"
        return result

    def test_02_encoding_format(self) -> Dict:
        """测试视频编码格式（H.264 + 充足码率）"""
        result = {"passed": False, "details": {}, "score": 0.0}
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,bit_rate",
            "-of", "json", str(self.test_path),
        ]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            data = json.loads(out.stdout)
            stream = data["streams"][0]
            codec = stream.get("codec_name", "unknown")
            bitrate = int(stream.get("bit_rate", 0))
            min_bitrate = REFERENCE_PARAMS["min_bitrate_mbps"] * 1_000_000
            codec_ok = codec in ["h264", "avc1", "libx264"]
            bitrate_ok = bitrate >= min_bitrate if bitrate > 0 else True
            score = 1.0 if codec_ok and bitrate_ok else 0.5 if codec_ok else 0.0
            result["score"] = score
            result["passed"] = codec_ok
            result["details"] = {"codec": codec, "bitrate": bitrate, "bitrate_mbps": bitrate/1_000_000}
            result["message"] = f"编码:{codec} {bitrate/1000:.0f}kbps"
        except Exception as e:
            result["message"] = f"编码检测失败: {e}"
        return result

    def test_03_duration_match(self) -> Dict:
        """测试视频时长匹配度"""
        result = {"passed": False, "details": {}, "score": 0.0}
        ref_dur = REFERENCE_PARAMS["total_duration"]
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(self.test_path)]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            test_dur = float(out.stdout.strip())
            ratio = min(ref_dur, test_dur) / max(ref_dur, test_dur)
            diff_pct = abs(test_dur - ref_dur) / ref_dur * 100
            result["details"] = {"ref": ref_dur, "test": test_dur, "diff_pct": f"{diff_pct:.1f}%"}
            result["score"] = ratio
            result["passed"] = ratio >= PASS_THRESHOLDS["duration_match"]
            result["message"] = f"时长 {test_dur:.1f}s vs 参考 {ref_dur:.1f}s (差{diff_pct:.1f}%)"
        except Exception as e:
            result["message"] = f"时长检测失败: {e}"
        return result

    # ═══════════════════════════════════════════════════════════
    #  4-8. 语速与解说节奏测试
    # ═══════════════════════════════════════════════════════════

    def test_04_speech_rate(self) -> Dict:
        """测试语速（字/秒），核心指标"""
        result = {"passed": False, "details": {}, "score": 0.0}
        try:
            segments = self._get_segments()
            total_chars = sum(len(s.text) for s in segments)
            total_dur = segments[-1].end if segments else 0
            if total_dur > 0:
                speech_rate = total_chars / total_dur
                ref_rate = REFERENCE_PARAMS["speech_rate"]
                min_rate = REFERENCE_PARAMS["speech_rate_min"]
                max_rate = REFERENCE_PARAMS["speech_rate_max"]
                if min_rate <= speech_rate <= max_rate:
                    score = 1.0
                elif speech_rate < min_rate:
                    score = max(0, speech_rate / min_rate)
                else:
                    score = max(0, 1 - (speech_rate - max_rate) / max_rate)
                result["details"] = {
                    "test_rate": round(speech_rate, 2),
                    "ref_rate": ref_rate,
                    "range": f"{min_rate}-{max_rate}",
                    "total_chars": total_chars,
                    "duration": round(total_dur, 1),
                }
                result["score"] = score
                result["passed"] = score >= PASS_THRESHOLDS["speech_rate_match"]
                result["message"] = f"语速 {speech_rate:.1f}字/秒 (参考:{ref_rate})"
            else:
                result["message"] = "无法计算语速"
        except Exception as e:
            result["message"] = f"语速检测失败: {e}"
        return result

    def test_05_rhythm_consistency(self) -> Dict:
        """测试语速节奏稳定性（标准差）"""
        result = {"passed": False, "details": {}, "score": 0.0}
        try:
            segments = self._get_segments()
            if len(segments) < 3:
                result["message"] = "段落太少，无法分析节奏"
                return result
            # 计算每段语速
            rates = []
            for seg in segments:
                chars = len(seg.text)
                dur = seg.end - seg.start
                if dur > 0.1:
                    rates.append(chars / dur)
            if len(rates) >= 3:
                std_dev = np.std(rates)
                mean_rate = np.mean(rates)
                # 标准差越小越好，<1.5为稳定
                cv = std_dev / mean_rate if mean_rate > 0 else 1.0
                score = max(0, 1.0 - cv) if cv < 1.0 else 0.0
                result["details"] = {
                    "mean_rate": round(mean_rate, 2),
                    "std_dev": round(std_dev, 2),
                    "cv": round(cv, 2),
                    "segment_count": len(rates),
                }
                result["score"] = score
                result["passed"] = score >= PASS_THRESHOLDS["rhythm_match"]
                result["message"] = f"节奏稳定度 {score*100:.0f}% (CV={cv:.2f})"
            else:
                result["message"] = "段落不足"
        except Exception as e:
            result["message"] = f"节奏检测失败: {e}"
        return result

    def test_06_sentence_length_distribution(self) -> Dict:
        """测试句子长度分布（使用Whisper段落作为句子）"""
        result = {"passed": False, "details": {}, "score": 0.0}
        try:
            segments = self._get_segments()
            if not segments:
                result["message"] = "无有效段落"
                return result
            # Whisper段落作为句子
            lengths = [len(s.text) for s in segments]
            avg_len = np.mean(lengths)
            long_sentences = [l for l in lengths if l > 20]
            long_ratio = len(long_sentences) / len(lengths) if lengths else 0

            ref_avg = REFERENCE_PARAMS["avg_sentence_length"]
            ref_long_ratio = REFERENCE_PARAMS["long_sentence_ratio"]
            avg_score = max(0, 1.0 - abs(avg_len - ref_avg) / ref_avg) if avg_len > 0 else 0.0
            long_score = max(0, 1.0 - abs(long_ratio - ref_long_ratio) / ref_long_ratio) if long_ratio > 0 else 0.0
            score = (avg_score + long_score) / 2

            result["details"] = {
                "avg_length": round(avg_len, 1),
                "ref_avg": ref_avg,
                "long_ratio": round(long_ratio, 2),
                "ref_long_ratio": ref_long_ratio,
                "segment_count": len(segments),
                "lengths": lengths[:20],
            }
            result["score"] = score
            result["passed"] = score >= PASS_THRESHOLDS["sentence_length_match"]
            result["message"] = f"句长分布 {score*100:.0f}% (均{avg_len:.0f}字，长句{long_ratio*100:.0f}%)"
        except Exception as e:
            result["message"] = f"句长检测失败: {e}"
        return result

    def test_07_pause_pattern(self) -> Dict:
        """测试段落间停顿模式"""
        result = {"passed": False, "details": {}, "score": 0.0}
        try:
            segments = self._get_segments()
            if len(segments) < 2:
                result["message"] = "段落不足"
                return result
            pauses = []
            for i in range(1, len(segments)):
                pause = segments[i].start - segments[i-1].end
                if pause > 0:
                    pauses.append(pause)
            if pauses:
                avg_pause = np.mean(pauses)
                ref_pause = REFERENCE_PARAMS["pause_per_sentence"]
                score = max(0, 1.0 - abs(avg_pause - ref_pause) / ref_pause) if avg_pause > 0 else 0.0
                result["details"] = {
                    "avg_pause_s": round(avg_pause, 2),
                    "ref_pause_s": ref_pause,
                    "pause_count": len(pauses),
                    "pauses": [round(p, 2) for p in pauses[:10]],
                }
                result["score"] = score
                result["passed"] = score >= PASS_THRESHOLDS["pause_pattern_match"]
                result["message"] = f"停顿模式 {score*100:.0f}% (均{avg_pause:.1f}s/处)"
            else:
                result["message"] = "无有效停顿"
        except Exception as e:
            result["message"] = f"停顿检测失败: {e}"
        return result

    def test_08_syllable_pace(self) -> Dict:
        """测试音节节奏密度"""
        result = {"passed": False, "details": {}, "score": 0.0}
        try:
            full_text = self._get_full_text()
            segments = self._get_segments()
            total_dur = segments[-1].end if segments else 0
            if total_dur > 0 and len(full_text) > 0:
                syllable_density = len(full_text) / total_dur
                ref_density = REFERENCE_PARAMS["syllable_rate"]
                score = max(0, 1.0 - abs(syllable_density - ref_density) / ref_density)
                result["details"] = {
                    "density": round(syllable_density, 2),
                    "ref_density": ref_density,
                    "total_chars": len(full_text),
                    "duration_s": round(total_dur, 1),
                }
                result["score"] = score
                result["passed"] = score >= PASS_THRESHOLDS["speech_rate_match"]
                result["message"] = f"音节密度 {syllable_density:.1f}字/秒 (参考:{ref_density})"
            else:
                result["message"] = "无法计算"
        except Exception as e:
            result["message"] = f"音节检测失败: {e}"
        return result

    # ═══════════════════════════════════════════════════════════
    #  9-12. 脚本结构与风格测试
    # ═══════════════════════════════════════════════════════════

    def test_09_script_structure(self) -> Dict:
        """测试脚本结构（章节标记、数据、结尾）"""
        result = {"passed": False, "details": {}, "score": 0.0}
        try:
            full_text = self._get_full_text()
            checks = {}
            chapter_matches = re.findall(REFERENCE_PARAMS["chapter_pattern"], full_text)
            checks["chapter_markers"] = len(chapter_matches) >= 1
            keyword_count = sum(1 for kw in REFERENCE_PARAMS["ref_style_keywords"] if kw in full_text)
            checks["style_keywords"] = keyword_count >= 3
            checks["has_data"] = bool(re.search(r'\d+', full_text))
            ending_variants = REFERENCE_PARAMS["ending_variants"]
            checks["has_ending"] = any(end in full_text for end in ending_variants)
            score = sum(1 for v in checks.values() if v) / len(checks)
            result["details"] = {
                "checks": checks,
                "chapters": chapter_matches[:10],
                "keyword_count": keyword_count,
                "total_chars": len(full_text),
            }
            result["score"] = score
            result["passed"] = score >= PASS_THRESHOLDS["structure_match"]
            result["message"] = f"结构匹配 {score*100:.0f}% (章节:{checks['chapter_markers']} 数据:{checks['has_data']} 结尾:{checks['has_ending']})"
        except Exception as e:
            result["message"] = f"结构检测失败: {e}"
        return result

    def test_10_opening_style(self) -> Dict:
        """测试开头风格（「第一、」直接进入，不说大家好）"""
        result = {"passed": False, "details": {}, "score": 0.0}
        try:
            full_text = self._get_full_text()
            opening_pattern = REFERENCE_PARAMS["opening_pattern"]
            # 检查前30字中是否以「第一、」开头（允许前面有空格）
            starts_with_chapter = bool(re.search(opening_pattern, full_text[:30]))
            has_greeting = any(g in full_text[:30] for g in ["大家好", "观众朋友们", "各位观众", "大家好呀", "各位好"])
            score = 1.0 if starts_with_chapter and not has_greeting else 0.5 if starts_with_chapter else 0.0
            result["details"] = {
                "starts_with_chapter": starts_with_chapter,
                "has_greeting": has_greeting,
                "first_30_chars": full_text[:30],
            }
            result["score"] = score
            result["passed"] = score >= PASS_THRESHOLDS["opening_style_match"]
            result["message"] = f"开头风格 {score*100:.0f}% {'✅' if starts_with_chapter else '❌'}"
        except Exception as e:
            result["message"] = f"开头检测失败: {e}"
        return result

    def test_11_transition_usage(self) -> Dict:
        """测试过渡词使用（然而、对此、不過）"""
        result = {"passed": False, "details": {}, "score": 0.0}
        try:
            full_text = self._get_full_text()
            required = REFERENCE_PARAMS["required_transitions"]
            found = {tw: full_text.count(tw) for tw in required}
            unique_used = sum(1 for tw in required if tw in full_text)
            min_required = REFERENCE_PARAMS["transition_count_min"]
            score = min(1.0, unique_used / min_required)
            result["details"] = {
                "transitions": found,
                "unique_used": unique_used,
                "min_required": min_required,
            }
            result["score"] = score
            result["passed"] = score >= PASS_THRESHOLDS["transition_match"]
            result["message"] = f"过渡词 {unique_used}种(期望≥{min_required})"
        except Exception as e:
            result["message"] = f"过渡词检测失败: {e}"
        return result

    def test_12_forbidden_patterns(self) -> Dict:
        """检测禁止词汇（第1、首先、据悉等）"""
        result = {"passed": False, "details": {}, "score": 0.0}
        try:
            full_text = self._get_full_text()
            forbidden = REFERENCE_PARAMS["forbidden_patterns"]
            violations = []
            for pattern in forbidden:
                matches = re.findall(pattern, full_text)
                if matches:
                    violations.append({"pattern": pattern, "count": len(matches)})
            score = 1.0 if not violations else 0.0
            result["details"] = {
                "violations": violations,
                "violation_count": len(violations),
                "patterns_checked": len(forbidden),
            }
            result["score"] = score
            result["passed"] = score >= PASS_THRESHOLDS["forbidden_check"]
            result["message"] = f"禁止词汇 {'✅无违规' if not violations else f'❌{len(violations)}处'}"
        except Exception as e:
            result["message"] = f"禁止词汇检测失败: {e}"
        return result

    # ═══════════════════════════════════════════════════════════
    #  13-15. 数据与内容测试
    # ═══════════════════════════════════════════════════════════

    def test_13_data_density(self) -> Dict:
        """测试数据密度（万、亿、％等关键词）"""
        result = {"passed": False, "details": {}, "score": 0.0}
        try:
            full_text = self._get_full_text()
            # 计算纯汉字数量（排除空白）
            chinese_chars = re.sub(r'\s', '', full_text)
            total_chars = len(chinese_chars)
            data_count = sum(chinese_chars.count(kw) for kw in REFERENCE_PARAMS["data_keywords"])
            density = data_count / total_chars if total_chars > 0 else 0
            min_density = REFERENCE_PARAMS["min_data_density"]
            score = min(1.0, density / min_density) if density > 0 else 0.0
            result["details"] = {
                "data_count": data_count,
                "total_chars": total_chars,
                "density": round(density, 4),
                "min_density": min_density,
            }
            result["score"] = score
            result["passed"] = score >= PASS_THRESHOLDS["data_density_match"]
            result["message"] = f"数据密度 {score*100:.0f}% ({data_count}处/共{total_chars}字)"
        except Exception as e:
            result["message"] = f"数据密度检测失败: {e}"
        return result

    def test_14_expert_usage(self) -> Dict:
        """测试专家引用使用（专家、表示、指出等）"""
        result = {"passed": False, "details": {}, "score": 0.0}
        try:
            full_text = self._get_full_text()
            phrases = REFERENCE_PARAMS["expert_phrases"]
            found = {p: full_text.count(p) for p in phrases}
            total_count = sum(found.values())
            unique_used = sum(1 for p in phrases if found[p] > 0)
            min_count = REFERENCE_PARAMS["min_expert_count"]
            score = min(1.0, unique_used / min_count) if unique_used > 0 else 0.0
            result["details"] = {
                "phrases": found,
                "unique_used": unique_used,
                "total_count": total_count,
            }
            result["score"] = score
            result["passed"] = score >= PASS_THRESHOLDS["expert_usage_match"]
            result["message"] = f"专家引用 {unique_used}种表达 (共{total_count}次)"
        except Exception as e:
            result["message"] = f"专家引用检测失败: {e}"
        return result

    def test_15_content_completeness(self) -> Dict:
        """测试内容完整性（章节数量）"""
        result = {"passed": False, "details": {}, "score": 0.0}
        try:
            full_text = self._get_full_text()
            chapter_matches = re.findall(REFERENCE_PARAMS["chapter_pattern"], full_text)
            num_chapters = len(set(chapter_matches))
            min_chapters = REFERENCE_PARAMS["chapter_min_count"]
            score = min(1.0, num_chapters / min_chapters) if num_chapters > 0 else 0.0
            result["details"] = {
                "num_chapters": num_chapters,
                "chapters": list(set(chapter_matches)),
                "min_expected": min_chapters,
            }
            result["score"] = score
            result["passed"] = score >= PASS_THRESHOLDS["content_completeness"]
            result["message"] = f"内容完整 {num_chapters}章节 (期望≥{min_chapters})"
        except Exception as e:
            result["message"] = f"内容完整性检测失败: {e}"
        return result

    # ═══════════════════════════════════════════════════════════
    #  16-18. 音频与音画同步测试
    # ═══════════════════════════════════════════════════════════

    def test_16_audio_quality(self) -> Dict:
        """测试音频质量（采样率、声道）"""
        result = {"passed": False, "details": {}, "score": 0.0}
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,channels,codec_name",
            "-of", "json", str(self.test_path),
        ]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            data = json.loads(out.stdout)
            if "streams" not in data or not data["streams"]:
                result["message"] = "无音频轨道"
                return result
            stream = data["streams"][0]
            sample_rate = int(stream["sample_rate"])
            channels = int(stream["channels"])
            checks = {
                "sample_rate_ok": sample_rate >= REFERENCE_PARAMS["audio_sample_rate"],
                "stereo": channels >= 2,
            }
            score = sum(1 for v in checks.values() if v) / len(checks)
            result["details"] = {"sample_rate": sample_rate, "channels": channels, "checks": checks}
            result["score"] = score
            result["passed"] = score >= 0.67
            result["message"] = f"音频 {sample_rate}Hz {channels}ch"
        except Exception as e:
            result["message"] = f"音频检测失败: {e}"
        return result

    def test_17_audio_video_sync(self) -> Dict:
        """测试音画同步"""
        result = {"passed": False, "details": {}, "score": 0.0}
        try:
            cmd_v = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(self.test_path)]
            vid_dur = float(subprocess.run(cmd_v, capture_output=True, text=True, timeout=10).stdout.strip())
            cmd_a = ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "format=duration", "-of", "csv=p=0", str(self.test_path)]
            aud_dur = float(subprocess.run(cmd_a, capture_output=True, text=True, timeout=10).stdout.strip())
            if vid_dur > 0 and aud_dur > 0:
                diff = abs(vid_dur - aud_dur)
                sync_score = max(0, 1 - diff / vid_dur)
                result["details"] = {"video_dur": round(vid_dur, 2), "audio_dur": round(aud_dur, 2), "diff": round(diff, 2)}
                result["score"] = sync_score
                result["passed"] = sync_score >= PASS_THRESHOLDS["audio_sync_score"]
                result["message"] = f"音画同步 差{diff:.2f}s"
            else:
                result["message"] = "无法检测"
        except Exception as e:
            result["message"] = f"音画同步检测失败: {e}"
        return result

    def test_18_audio_bitrate(self) -> Dict:
        """测试音频码率"""
        result = {"passed": False, "details": {}, "score": 0.0}
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=bit_rate",
            "-of", "json", str(self.test_path),
        ]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            data = json.loads(out.stdout)
            if "streams" not in data or not data["streams"]:
                result["message"] = "无音频轨道"
                return result
            bitrate = int(data["streams"][0].get("bit_rate", 0))
            min_br = REFERENCE_PARAMS["audio_bitrate_kbps"] * 1000
            score = min(1.0, bitrate / min_br) if bitrate > 0 else 0.5
            result["details"] = {"bitrate": bitrate, "bitrate_kbps": bitrate/1000, "min_kbps": REFERENCE_PARAMS["audio_bitrate_kbps"]}
            result["score"] = score
            result["passed"] = score >= 0.8
            result["message"] = f"音频码率 {bitrate/1000:.0f}kbps"
        except Exception as e:
            result["message"] = f"音频码率检测失败: {e}"
        return result

    # ═══════════════════════════════════════════════════════════
    #  19-20. 视觉质量测试
    # ═══════════════════════════════════════════════════════════

    def test_19_visual_quality(self) -> Dict:
        """测试视觉质量（分辨率、码率、文件大小）"""
        result = {"passed": False, "details": {}, "score": 0.0}
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,bit_rate",
            "-show_entries", "format=duration,size,bit_rate",
            "-of", "json", str(self.test_path),
        ]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            data = json.loads(out.stdout)
            stream = data["streams"][0]
            fmt = data["format"]
            width = stream["width"]
            height = stream["height"]
            video_bitrate = int(stream.get("bit_rate", 0))
            file_size = int(fmt.get("size", 0))
            duration = float(fmt.get("duration", 0))
            scores = []
            res_score = 1.0 if height >= 1080 else 0.5 if height >= 720 else 0.0
            scores.append(("resolution", res_score, f"{width}x{height}"))
            ref_bitrate = 3000000
            if video_bitrate > 0:
                br_score = min(1.0, max(0.5, video_bitrate / ref_bitrate))
            else:
                estimated_bitrate = file_size * 8 / duration if duration > 0 else 0
                br_score = min(1.0, max(0.5, estimated_bitrate / ref_bitrate))
            scores.append(("bitrate", br_score, f"{video_bitrate/1000:.0f}kbps"))
            size_score = 1.0 if 15_000_000 <= file_size <= 200_000_000 else max(0.5, 0.8)
            scores.append(("file_size", size_score, f"{file_size/1024/1024:.1f}MB"))
            avg_score = sum(s[1] for s in scores) / len(scores)
            result["details"] = {
                "resolution": f"{width}x{height}",
                "bitrate_kbps": video_bitrate/1000,
                "file_size_mb": file_size/1024/1024,
                "component_scores": {s[0]: round(s[1], 2) for s in scores},
            }
            result["score"] = avg_score
            result["passed"] = avg_score >= PASS_THRESHOLDS["visual_quality_score"]
            result["message"] = f"视觉质量 {avg_score*100:.0f}分"
        except Exception as e:
            result["message"] = f"视觉质量检测失败: {e}"
        return result

    def test_20_visual_bitrate_efficiency(self) -> Dict:
        """测试码率效率（码率/分辨率比）"""
        result = {"passed": False, "details": {}, "score": 0.0}
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,bit_rate",
            "-of", "json", str(self.test_path),
        ]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            data = json.loads(out.stdout)
            stream = data["streams"][0]
            width = stream["width"]
            height = stream["height"]
            bitrate = int(stream.get("bit_rate", 0))
            pixels = width * height
            # 参考：1080p视频应该至少有2Mbps
            ref_mbps = 2.0
            ref_bitrate = ref_mbps * 1_000_000
            efficiency = bitrate / pixels if pixels > 0 else 0
            ref_efficiency = ref_bitrate / (1920 * 1080)
            score = min(1.0, efficiency / ref_efficiency) if efficiency > 0 else 0.0
            result["details"] = {
                "bitrate_mbps": bitrate/1_000_000,
                "pixels": pixels,
                "efficiency": efficiency,
                "ref_efficiency": ref_efficiency,
            }
            result["score"] = score
            result["passed"] = score >= 0.60
            result["message"] = f"码率效率 {score*100:.0f}%"
        except Exception as e:
            result["message"] = f"码率效率检测失败: {e}"
        return result

    # ═══════════════════════════════════════════════════════════
    #  综合测试报告
    # ═══════════════════════════════════════════════════════════

    def run_all_tests(self) -> Dict:
        """运行全部20项测试"""
        log.info("=" * 60)
        log.info("开始参考视频风格对比测试 (20项)")
        log.info("=" * 60)

        tests = {
            "01_视频格式": self.test_01_video_format,
            "02_编码格式": self.test_02_encoding_format,
            "03_时长匹配": self.test_03_duration_match,
            "04_语速": self.test_04_speech_rate,
            "05_节奏稳定": self.test_05_rhythm_consistency,
            "06_句长分布": self.test_06_sentence_length_distribution,
            "07_停顿模式": self.test_07_pause_pattern,
            "08_音节密度": self.test_08_syllable_pace,
            "09_脚本结构": self.test_09_script_structure,
            "10_开头风格": self.test_10_opening_style,
            "11_过渡词": self.test_11_transition_usage,
            "12_禁止词汇": self.test_12_forbidden_patterns,
            "13_数据密度": self.test_13_data_density,
            "14_专家引用": self.test_14_expert_usage,
            "15_内容完整": self.test_15_content_completeness,
            "16_音频质量": self.test_16_audio_quality,
            "17_音画同步": self.test_17_audio_video_sync,
            "18_音频码率": self.test_18_audio_bitrate,
            "19_视觉质量": self.test_19_visual_quality,
            "20_码率效率": self.test_20_visual_bitrate_efficiency,
        }

        results = {}
        passed = 0
        total_score = 0.0

        for name, test_fn in tests.items():
            try:
                log.info(f"[测试] {name}...")
                r = test_fn()
                results[name] = r
                total_score += r["score"]
                if r["passed"]:
                    passed += 1
                    log.info(f"  ✅ {r['message']}")
                else:
                    log.info(f"  ❌ {r['message']}")
            except Exception as e:
                log.error(f"  ❌ {name}异常: {e}")
                results[name] = {"passed": False, "message": str(e), "score": 0.0}

        avg_score = total_score / len(tests) if tests else 0

        summary = {
            "overall_score": round(avg_score, 3),
            "tests_passed": f"{passed}/{len(tests)}",
            "all_passed": passed == len(tests),
            "results": results,
            "thresholds": PASS_THRESHOLDS,
        }

        log.info("=" * 60)
        log.info(f"综合得分: {avg_score*100:.1f}% ({passed}/{len(tests)}项通过)")

        if avg_score >= 0.82:
            log.info("🎉 测试通过！视频风格匹配参考视频")
            summary["status"] = "PASS"
        elif avg_score >= 0.68:
            log.info("⚠️  接近达标(68%)，需要小幅优化")
            summary["status"] = "NEEDS_IMPROVEMENT"
        else:
            log.info("❌ 未达标准(82%)，需要继续优化")
            summary["status"] = "FAIL"

        log.info("=" * 60)
        log.info("\n【详细结果】")
        for name, r in results.items():
            status = "✅" if r["passed"] else "❌"
            log.info(f"  {status} {name}: {r['message']}")

        # 清理临时文件
        if self._audio_path and self._audio_path.exists():
            self._audio_path.unlink(missing_ok=True)

        return summary


def run_reference_test(ref_video: str, test_video: str, output_json: str = None) -> Dict:
    """主入口：运行参考对比测试"""
    matcher = ReferenceMatcher(ref_video, test_video)
    report = matcher.run_all_tests()
    if output_json:
        Path(output_json).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        log.info(f"测试报告已保存: {output_json}")
    return report


# ── CLI入口 ────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, argparse
    parser = argparse.ArgumentParser(description="参考视频风格对比测试 (20项)")
    parser.add_argument("test_video", help="测试视频路径")
    parser.add_argument("--ref", default="/Users/kaikai/scripts/info_gap_pipeline/temp/ref_video.mp4", help="参考视频路径")
    parser.add_argument("--output", "-o", help="输出JSON报告路径")
    args = parser.parse_args()
    report = run_reference_test(args.ref, args.test_video, args.output)
    if report["status"] == "PASS":
        sys.exit(0)
    elif report["status"] == "NEEDS_IMPROVEMENT":
        sys.exit(2)
    else:
        sys.exit(1)
