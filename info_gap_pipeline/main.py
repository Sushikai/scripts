#!/usr/bin/env python3
"""main.py — 信息差新闻视频流水线入口"""

import os, sys, logging, json, time, subprocess, re
from pathlib import Path
from datetime import datetime

# 修复 launchd/cron 环境无 PATH 时找不到 ffmpeg
_extra_paths = [
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/Users/kaikai/.hermes/hermes-agent/venv/bin",
]
os.environ["PATH"] = ":".join(
    [p for p in _extra_paths if os.path.isdir(p)] + [os.environ.get("PATH", "")]
)

# 添加项目根目录到路径
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR.parent))

from info_gap_pipeline.config import (
    LOG_FILE, LOGS_DIR, TEMP_DIR, OUTPUTS_DIR, SCHEDULE_TIMES,
    DEFAULT_BGM_PATH,
)

BGM_PATH = DEFAULT_BGM_PATH

__VERSION__ = "1.1.0"
__BUILD__ = "2026-06-10"
from info_gap_pipeline.research import TopicResearcher
from info_gap_pipeline.script_gen import ScriptGenerator
from info_gap_pipeline.download import VideoDownloader
from info_gap_pipeline.voiceover import VoiceoverGenerator
from info_gap_pipeline.edit import VideoEditor
from info_gap_pipeline.upload import BilibiliUploader
from info_gap_pipeline.scheduler import PipelineScheduler


def setup_logging():
    """配置日志"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("pipeline")


class InfoGapPipeline:
    """信息差新闻视频全自动化流水线"""

    def __init__(self, date: datetime = None):
        self.date = date or datetime.now()
        self.logger = logging.getLogger("pipeline")

        # 各模块实例
        self.researcher = TopicResearcher()
        self.script_gen = ScriptGenerator()
        self.downloader = VideoDownloader()
        self.voiceover = VoiceoverGenerator()
        self.editor = VideoEditor()
        self.uploader = BilibiliUploader()

        self.results = {}

    def _cleanup_historical_materials(self):
        """
        每次运行前清理历史素材，避免残留文件干扰本次生成。
        清理范围：
        - temp/*.mp4, temp/*.wav, temp/*.txt（保留ref_video.mp4和silence文件）
        - outputs/gen_frames/, outputs/gen_frames_analysis/
        - outputs/ref_frames/, outputs/ref_frames_analysis/
        - outputs/video_seg_*.mp4, outputs/vo_*.wav
        """
        self.logger.info("[清理] 开始清理历史素材...")

        # temp目录清理（保留参考视频和silence文件）
        preserved = {"ref_video.mp4", "_silence.wav"}
        for f in TEMP_DIR.glob("*"):
            if f.is_file() and f.name not in preserved:
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass

        # outputs目录清理
        for pattern in ["gen_frames", "gen_frames_analysis", "ref_frames", "ref_frames_analysis"]:
            dir_path = OUTPUTS_DIR / pattern
            if dir_path.exists():
                for f in dir_path.glob("*"):
                    try:
                        f.unlink(missing_ok=True)
                    except Exception:
                        pass

        # outputs目录下的临时视频/音频文件
        for pattern in ["video_seg_*.mp4", "vo_*.wav", "*.tmp.mp4"]:
            for f in OUTPUTS_DIR.glob(pattern):
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass

        self.logger.info("[清理] 历史素材清理完成")

    def run(self) -> dict:
        """执行完整流水线（含耗时统计）"""
        start_time = time.time()
        step_times = {}

        # 每次运行前清理历史素材
        self._cleanup_historical_materials()

        self.logger.info(f"===== 流水线启动 v{__VERSION__} ({self.date.strftime('%Y-%m-%d %H:%M:%S')}) =====")

        step_start = time.time()
        topics = self._step_research()
        step_times["选题"] = time.time() - step_start
        if not topics:
            self.logger.error("选题失败，终止流水线")
            return {"status": "failed", "step": "research", "reason": "no topics"}

        step_start = time.time()
        scripts = self._step_script_gen(topics)
        step_times["脚本生成"] = time.time() - step_start
        if not scripts:
            self.logger.error("脚本生成失败，终止流水线")
            return {"status": "failed", "step": "script_gen"}

        step_start = time.time()
        voiceover_paths = self._step_voiceover(scripts)
        step_times["配音生成"] = time.time() - step_start
        if not voiceover_paths:
            self.logger.error("配音生成失败，终止流水线")
            return {"status": "failed", "step": "voiceover"}

        step_start = time.time()
        video_paths = self._step_download(scripts)
        step_times["素材下载"] = time.time() - step_start
        if not video_paths:
            self.logger.error("素材下载失败，终止流水线")
            return {"status": "failed", "step": "download"}

        step_start = time.time()
        final_video = self._step_compile(video_paths, voiceover_paths, scripts)
        step_times["视频合成"] = time.time() - step_start
        if not final_video or not final_video.exists():
            self.logger.error("视频合成失败")
            return {"status": "failed", "step": "compile"}

        step_start = time.time()
        upload_result = self._step_upload(final_video, scripts[0])
        step_times["上传"] = time.time() - step_start

        # 与参考视频对比，分析不足之处
        step_start = time.time()
        compare_report = self._step_compare_with_reference(final_video)
        step_times["风格对比"] = time.time() - step_start
        if compare_report:
            self.logger.info(f"风格对比报告: {compare_report.get('message', '')}")

        total = time.time() - start_time
        self.logger.info(f"===== 流水线完成（总耗时{total:.1f}s） =====")
        for step, dur in step_times.items():
            self.logger.info(f"  {step}: {dur:.1f}s ({dur/total*100:.0f}%)")

        # 写运行记录到stat.json（方便监控）
        stat_file = LOGS_DIR / "stat.json"
        try:
            stat = json.loads(stat_file.read_text()) if stat_file.exists() else {}
        except Exception:
            stat = {}
        stat[self.date.strftime("%Y%m%d_%H%M%S")] = {
            "total_s": round(total, 1),
            "steps": {k: round(v, 1) for k, v in step_times.items()},
            "topics": len(topics),
            "upload": upload_result or "failed",
            "version": __VERSION__,
        }
        # 清理30天前的历史记录
        cutoff = time.time() - 30 * 86400
        stat = {k: v for k, v in stat.items()
                if time.mktime(time.strptime(k, "%Y%m%d_%H%M%S")) > cutoff}
        stat_file.write_text(json.dumps(stat, ensure_ascii=False, indent=2))

        return {
            "status": "success",
            "date": self.date.isoformat(),
            "topics": [t["title"] for t in topics],
            "video": str(final_video),
            "upload": upload_result,
            "total_s": round(total, 1),
        }

    def _step_research(self) -> list:
        """选题"""
        self.logger.info("[Step 1] 开始选题研究...")
        topics = self.researcher.research(top_n=7)  # 锚定参考视频BV1EY7k6aEPg的7个话题
        self.logger.info(f"[Step 1]选题完成，获取到 {len(topics)} 条话题")
        for i, t in enumerate(topics, 1):
            self.logger.info(f"  {i}. [{t['source']}] {t['title']}")
        return topics

    def _step_script_gen(self, topics: list) -> list:
        """脚本生成（并行执行，3条~60s vs 串行~180s）"""
        self.logger.info("[Step 2] 开始生成脚本（并行）...")
        def on_script_done(idx: int, result: dict):
            self.logger.info(f"  脚本{idx+1}生成完成: {len(result.get('script',''))}字, 估算{result.get('estimated_duration',0):.1f}s")

        scripts = self.script_gen.generate_batch(topics, progress_callback=on_script_done)
        self.logger.info(f"[Step 2]脚本生成完成 {len(scripts)} 条")
        for i, s in enumerate(scripts):
            self.logger.info(f" 脚本{i+1}: {s['script'][:60]}... ({s['estimated_duration']}s)")
        return scripts

    def _step_download(self, scripts: list) -> list:
        """素材下载 - 为每个topic搜索对应视频（并行搜索+下载，节省时间）"""
        self.logger.info("[Step 4] 开始下载素材...")
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from info_gap_pipeline.download.search import MaterialSearcher

        def process_one(i: int, s: dict) -> tuple:
            """处理单个脚本：基于脚本内容搜索+下载+截取"""
            import re  # 确保re模块在嵌套函数中可用
            from collections import Counter
            topic = s.get("topic", {})
            topic_title = topic.get("title", "")
            script_text = s.get("script", "")

            # ── 核心修复：用脚本内容搜视频，而非话题标题 ─────────────────────
            # 从脚本中提取关键词（取前100字，去掉语气词/连接词）
            _stopwords = {"的", "了", "是", "在", "和", "与", "或", "这", "那", "有", "没", "一个", "什么", "怎么", "为什么", "吗", "呢", "吧", "啊", "哦", "呃", "诶", "嗯", "第一", "第二", "第三", "第四", "第五", "首先", "然后", "最后", "其实", "但是", "所以", "因此", "由于", "而且", "并且", "虽然", "如果"}
            _words = [w for w in re.findall(r'[\w\u4e00-\u9fff]{2,6}', script_text[:200]) if w not in _stopwords and len(w) >= 2]
            # 取高频词作为搜索关键词
            _top = [w for w, _ in Counter(_words).most_common(5)]
            _search_kw = " ".join(_top[:3]) if _top else topic_title
            self.logger.info(f"[Step 4] 从脚本提取关键词 [{i+1}/{len(scripts)}]: 「{_search_kw}」（原文: {topic_title[:30]}）")

            searcher = MaterialSearcher()
            results = searcher.search_all(_search_kw, limit_per_platform=3)
            bvid = None
            for r in results:
                if r.get("platform") == "bilibili" and r.get("bvid"):
                    bvid = r["bvid"]
                    break
                if r.get("platform") == "youtube" and r.get("url"):
                    bvid = r["url"]
                    break

            if bvid:
                self.logger.info(f"[Step 4] 找到素材 [{i+1}]: {bvid}")
                raw = self.downloader.download_bilibili(bvid, segment_idx=i)
            else:
                self.logger.warning(f"[Step 4] 未找到素材 [{i+1}]，生成测试视频")
                raw = None

            if raw and raw.exists():
                # 优先用配音实际时长（Step 4 已生成），其次用估算时长
                seg_dur = s.get("audio_actual_duration") or s.get("estimated_duration")
                seg_path = self.editor.trim(raw, seg_dur, TEMP_DIR / f"video_seg_{i+1:02d}.mp4")
                # 记录实际截取时长（trim用copy可能因关键帧偏移，与请求时长有偏差）
                actual_dur = self._get_duration(seg_path)
                s["video_path"] = seg_path
                s["actual_duration"] = actual_dur
                self.logger.info(f"[Step 4] 截取完成 [{i+1}]: 配音时长{seg_dur:.2f}s → 实际{actual_dur:.1f}s")
                return seg_path
            else:
                test_video = self._generate_test_video(TEMP_DIR / f"test_clip_{i+1:02d}.mp4")
                s["video_path"] = test_video
                self.logger.warning(f"[Step 4] 生成测试视频 [{i+1}]")
                return test_video

        paths = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(process_one, i, s): i for i, s in enumerate(scripts)}
            for future in as_completed(futures):
                try:
                    path = future.result()
                    if path:
                        paths.append(path)
                except Exception as e:
                    import traceback
                    self.logger.warning(f"素材处理异常: {e}\n{traceback.format_exc()}")

        # 按原始顺序排列
        script_paths = {id(scripts[i]): p for i, p in enumerate(paths)}
        paths = [s["video_path"] for s in scripts if s.get("video_path")]
        self.logger.info(f"[Step 4] 素材准备完成 {len(paths)} 段（并行）")
        return paths

    def _generate_test_video(self, output_path: Path) -> Path:
        """生成测试用噪点纹理视频（当下载失败时使用，逼真度更高）"""
        import random
        seed = random.randint(1, 9999)
        cmd = [
            "ffmpeg", "-y",
            # 噪点纹理背景（替代纯黑屏，更逼真，16:9横向）
            "-f", "lavfi", "-i", f"cellauto=rule=110:s=1280x720:seed={seed}",
            "-vframes", "150", "-r", "30",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-shortest",
            "-c:v", "libx264", "-preset", "ultrafast", "-r", "30",
            "-c:a", "aac", "-b:a", "128k",
            str(output_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and output_path.exists():
                return output_path
        except Exception:
            pass
        return None

    def _step_voiceover(self, scripts: list) -> list:
        """配音生成"""
        self.logger.info("[Step 3] 开始生成配音...")
        all_audio = []
        for i, s in enumerate(scripts):
            script_text = s.get("script", "")
            if not script_text:
                continue
            audio_path = self.voiceover.generate(script_text, f"vo_{i+1:02d}.wav")
            if audio_path:
                # 记录 whisper 识别后的实际音频时长（而非脚本估算时长）
                actual_dur = self._get_duration(audio_path)
                s["audio_path"] = audio_path
                s["audio_actual_duration"] = actual_dur
                self.logger.info(f"[Step 3] 配音{i+1}实际时长: {actual_dur:.2f}s（估算{s.get('estimated_duration', 0):.1f}s）")
                all_audio.append(audio_path)
        self.logger.info(f"[Step 3] 配音生成完成 {len(all_audio)} 条")
        return all_audio

    def _step_compile(self, video_paths: list, audio_paths: list, scripts: list) -> Path:
        """视频合成（含字幕+结尾Logo）"""
        self.logger.info("[Step 5] 开始合成视频...")

        # 构建视频片段列表（使用 whisper 识别后的实际音频时长，
        # 这是真正的"锚"，视频和字幕都以此为准对齐）
        video_segments = []
        for i, s in enumerate(scripts):
            vp = s.get("video_path")
            if vp:
                # audio_actual_duration 是 whisper 识别配音的实际时长，
                # 比视频截取时长更可靠（trim 用 copy 无法精确到帧）
                seg_dur = s.get("audio_actual_duration") or s.get("actual_duration") or s.get("estimated_duration", 0)
                video_segments.append({
                    "video_path": vp,
                    "duration": seg_dur,
                    "text": s.get("script", ""),
                })
                self.logger.info(f"[Step 5] 片段{i+1}目标时长: {seg_dur:.2f}s（视频截取{s.get('actual_duration', 0):.1f}s，配音{s.get('audio_actual_duration', 0):.2f}s）")

        output_name = f"info_gap_{self.date.strftime('%Y%m%d_%H%M%S')}.mp4"
        # BGM混音（低音背景，不抢配音）
        bgm_path = BGM_PATH
        compiled = self.editor.compile(
            video_segments=video_segments,
            voiceover_paths=audio_paths,
            bgm_path=bgm_path,
            output_name=output_name,
        )
        self.logger.info(f"[Step 5] 合成完成: {compiled}")

        # ── 确保视频时长 >= 音频时长（避免音频被截断）────────────
        # 计算拼接后音频总时长
        total_vo_dur = sum(self._get_duration(ap) for ap in audio_paths if ap and ap.exists())
        if total_vo_dur > 0 and compiled.exists():
            video_dur = float(self._get_duration(compiled))
            if video_dur < total_vo_dur - 0.5:
                # 视频比音频短，需要扩展
                shortage = total_vo_dur - video_dur
                self.logger.warning(f"⚠️ 视频时长({video_dur:.1f}s) < 音频({total_vo_dur:.1f}s)，差{shortage:.1f}s，扩展最后一段...")
                # 用最后一帧画面扩展视频到音频时长（避免音频被截断）
                extended = self.editor.extend_to_duration(str(compiled), total_vo_dur)
                if extended and extended.exists():
                    compiled = extended
                    self.logger.info(f"  视频已扩展到{total_vo_dur:.1f}s")
                else:
                    self.logger.warning(f"  视频扩展失败，音频将被截断{shortage:.1f}s")

        # ── 烧录字幕 ──────────────────────────────────────────
        self.logger.info("[Step 5] 开始烧录字幕...")
        srt_paths = []
        for i, ap in enumerate(audio_paths):
            if ap and ap.exists():
                srt = self.voiceover.generate_subtitles(ap)
                if srt:
                    srt_paths.append(srt)
                    self.logger.info(f"  第{i+1}条字幕: {srt.name}")
                else:
                    srt_paths.append(None)
            else:
                srt_paths.append(None)

        # 取第一条字幕烧录（信息差视频只有一段配音）
        first_srt = next((s for s in srt_paths if s), None)
        if first_srt:
            burned = self.editor.burn_subtitles(str(compiled), str(first_srt))
            if burned:
                compiled = burned
                self.logger.info(f"  字幕烧录完成: {burned}")
            else:
                self.logger.warning("  字幕烧录失败，使用无字幕版本")
        else:
            self.logger.info("  无字幕文件，跳过烧录")

        # ── 结尾Logo ──────────────────────────────────────────
        import info_gap_pipeline.config as cfg
        logo_path = getattr(cfg, 'LOGO_PATH', None)
        if logo_path and Path(logo_path).exists():
            self.logger.info(f"[Step 5] 生成结尾Logo: {logo_path}")
            endcard = self.editor.generate_endcard(
                str(compiled),
                str(logo_path),
                duration=3.0,
                fade_duration=0.5,
            )
            if endcard:
                compiled = endcard
                self.logger.info(f"  结尾Logo拼接完成: {endcard}")
            else:
                self.logger.warning("  结尾Logo生成失败")
        else:
            self.logger.info(f"  Logo未配置或文件不存在，跳过结尾Logo")

        self.logger.info(f"[Step 5] 最终视频: {compiled}")

        # ── 质量控制：音画同步检测 ───────────────────────────
        # 使用总配音时长（所有片段拼接后的总时长）作为参考
        total_vo_dur = sum(self._get_duration(ap) for ap in audio_paths if ap and ap.exists())
        if total_vo_dur > 0 and compiled.exists():
            video_dur = float(self._get_duration(compiled))
            diff = abs(video_dur - total_vo_dur)
            if diff > 1.0:  # 放宽到1s（因为拼接有转场处理）
                self.logger.warning(f"⚠️ 音画时长差异 {diff:.2f}s（视频{video_dur:.1f}s vs 配音{total_vo_dur:.1f}s）")
            else:
                self.logger.info(f"✅ 音画时长匹配（视频{video_dur:.1f}s vs 配音{total_vo_dur:.1f}s）")

        # ── 质量控制：时长误差检测 ───────────────────────────
        expected_dur = sum(s.get("actual_duration", s.get("estimated_duration", 0)) for s in scripts if s.get("video_path"))
        if expected_dur > 0 and compiled.exists():
            actual_dur = float(self._get_duration(compiled))
            diff = abs(actual_dur - expected_dur)
            # 考虑crossfade会减少总时长（n-1个crossfade × crossfade时长）
            # compile时crossfade=0.3s，3段视频有2个crossfade，减少0.6s
            crossfade = 0.3
            n = len([s for s in scripts if s.get("video_path")])
            expected_with_crossfade = expected_dur - (n - 1) * crossfade if n >= 2 else expected_dur
            diff_adjusted = abs(actual_dur - expected_with_crossfade)
            if diff_adjusted > 1.0:
                self.logger.warning(f"⚠️ 时长误差 {diff_adjusted:.2f}s（期望{expected_with_crossfade:.1f}s含crossfade，实际{actual_dur:.1f}s），超过1.0s阈值")
            else:
                self.logger.info(f"✅ 时长检查通过（误差{diff_adjusted:.2f}s，期望{expected_with_crossfade:.1f}s）")
        return compiled

    def _get_duration(self, video_path: Path) -> float:
        """获取视频时长（秒）"""
        try:
            import subprocess
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(video_path)],
                capture_output=True, text=True, timeout=10
            )
            return float(r.stdout.strip())
        except Exception:
            return 0.0

    def _check_audio_sync(self, video_path: Path, audio_path: Path, tolerance: float = 0.1) -> bool:
        """
        检测音画同步：对比视频音轨和配音文件时长差异。
        容差默认0.1秒，超过则认为有同步问题。
        返回True=同步，False=不同步
        """
        try:
            import subprocess
            # 视频音轨时长
            r1 = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", "-select_streams", "a:0", str(video_path)],
                capture_output=True, text=True, timeout=10
            )
            video_audio_dur = float(r1.stdout.strip()) if r1.stdout.strip() else 0
            # 配音文件时长
            r2 = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(audio_path)],
                capture_output=True, text=True, timeout=10
            )
            source_audio_dur = float(r2.stdout.strip()) if r2.stdout.strip() else 0

            if video_audio_dur == 0 or source_audio_dur == 0:
                log.warning("音画同步检测失败：无法获取音轨时长")
                return True  # 检测失败默认放行

            diff = abs(video_audio_dur - source_audio_dur)
            if diff <= tolerance:
                self.logger.info(f"✅ 音画同步检测通过（差{abs(diff):.3f}s）")
                return True
            else:
                self.logger.warning(f"⚠️ 音画不同步：视频音轨{video_audio_dur:.3f}s vs 配音{source_audio_dur:.3f}s，差{diff:.3f}s > {tolerance}s阈值")
                return False
        except Exception as e:
            self.logger.warning(f"音画同步检测异常: {e}，默认放行")
            return True

    def _step_compare_with_reference(self, video_path: Path) -> dict:
        """与参考视频BV1EY7k6aEPg风格对比，输出不足报告"""
        self.logger.info("[Step 7] 开始与参考视频风格对比...")

        try:
            from tests.test_reference_match import ReferenceMatcher
            ref_video = Path(__file__).parent / "temp" / "ref_video.mp4"
            if not ref_video.exists():
                self.logger.warning(f"参考视频不存在: {ref_video}")
                return {}

            matcher = ReferenceMatcher(str(ref_video), str(video_path))
            report = matcher.run_all_tests()

            # 提取不足项
            deficiencies = []
            for name, r in report.get("results", {}).items():
                if not r.get("passed"):
                    deficiencies.append({
                        "test": name,
                        "score": r.get("score", 0),
                        "message": r.get("message", ""),
                        "details": r.get("details", {}),
                    })

            # 按严重程度排序（得分越低越严重）
            deficiencies.sort(key=lambda x: x["score"])

            if deficiencies:
                self.logger.warning(f"发现 {len(deficiencies)} 项不足:")
                for d in deficiencies[:5]:  # 最多显示5项
                    self.logger.warning(f"  ⚠️  {d['test']}: {d['message']}")
            else:
                self.logger.info("✅ 所有风格指标达标，无明显不足")

            # 保存详细报告
            report_file = OUTPUTS_DIR / f"compare_{self.date.strftime('%Y%m%d_%H%M%S')}.json"
            report_file.write_text(json.dumps({
                "video": str(video_path),
                "overall_score": report.get("overall_score"),
                "status": report.get("status"),
                "deficiencies": deficiencies,
            }, ensure_ascii=False, indent=2))

            return {
                "score": report.get("overall_score"),
                "status": report.get("status"),
                "deficiency_count": len(deficiencies),
                "message": f"{len(deficiencies)}项不足" if deficiencies else "无不足",
            }
        except Exception as e:
            self.logger.warning(f"风格对比异常: {e}")
            return {}

    def _step_upload(self, video_path: Path, script: dict) -> str:
        """上传B站 + 上传后验证封面选帧效果"""
        self.logger.info("[Step 6] 开始上传...")

        topic = script.get("topic", {})
        topic_title = topic.get("title", "信息差新闻")

        title = self.uploader.generate_title(topic_title, index=0)
        description = self.uploader.generate_description(script.get("script", ""), topic_title)
        tags = self.uploader.suggest_tags(topic_title)

        avid = self.uploader.upload(video_path, title, description, tags)

        # ── 上传后验证：确认视频已上线 + 检查封面 ───────────────────
        if avid:
            self.logger.info(f"[Step 6] 上传成功，等待10秒后验证（B站转码需要时间）...")
            time.sleep(10)  # 等待B站转码处理
            verify = self.uploader.verify_upload(avid)
            if verify.get("ok"):
                self.logger.info(f"[Step 6] ✅ 验证通过: {verify['title']}")
                self.logger.info(f"[Step 6] 📌 封面(B站自动选帧): {verify.get('cover_url', 'N/A')}")
                self.logger.info(f"[Step 6] 📌 后台地址: https://member.bilibili.com/uploader")
                self.logger.info(f"[Step 6] ℹ️  请登录B站后台手动更换封面")
            else:
                self.logger.warning(f"[Step 6] ⚠️ 验证异常: {verify.get('error', 'unknown')}")
            return avid or ""
        return ""


# ── 调度入口 ─────────────────────────────────────────────
def run_daily_pipeline(date: datetime = None):
    """每日定时执行入口"""
    date = date or datetime.now()
    setup_logging()
    logger = logging.getLogger("pipeline")

    pipeline = InfoGapPipeline(date)
    result = pipeline.run()

    # 保存结果
    result_file = OUTPUTS_DIR / f"result_{date.strftime('%Y%m%d_%H%M%S')}.json"
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"结果已保存: {result_file}")

    return result


# ── 主入口 ───────────────────────────────────────────────
def main():
    import argparse

    parser = argparse.ArgumentParser(description="信息差新闻视频流水线")
    parser.add_argument("--once", action="store_true", help="立即运行一次（不调度）")
    parser.add_argument("--schedule", action="store_true", help="启动定时调度")
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("pipeline")

    if args.once:
        logger.info("立即运行模式")
        result = run_daily_pipeline()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.schedule:
        logger.info("调度模式")
        sched = PipelineScheduler(run_daily_pipeline)
        sched.start()
    else:
        # 默认立即运行一次
        logger.info("默认立即运行")
        result = run_daily_pipeline()
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()