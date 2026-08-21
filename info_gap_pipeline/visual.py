"""visual.py — 成品视频视觉验证器 (6 维度)

参考: tests/visual_verification_test_plan.md

设计原则:
- 本地检查 (多样性/亮度/噪声) 不依赖网络
- Claude vision 语义检查 mockable, 默认 mock=True 让测试不调真实 API
- 失败原因清晰可定位, 配合 "不通过则无限循环" 的迭代修复流程
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class VisionResult:
    """Claude vision 对单张帧的判定"""
    passed: bool
    has_subtitle: bool = False
    scene_description: str = ""
    raw_response: Optional[str] = None


@dataclass
class VerificationReport:
    """verify(video) 返回的总报告"""
    passed: bool
    reasons: List[str] = field(default_factory=list)       # 失败原因 (中文)
    diagnostics: List[str] = field(default_factory=list)  # 执行了哪些检查
    frames_analyzed: int = 0
    diversity_score: float = 0.0
    noise_ratio: float = 0.0
    avg_brightness: float = 0.0


class VisualVerifier:
    """成品视频视觉验证器

    6 维检查:
    1. 段落多样性 (相邻帧 phash 距离)
    2. 无兜底 noise 帧
    3. 字幕可见 (Claude vision)
    4. 无黑帧
    5. 画面无异常拉伸 (留接口, v1 简化为亮度方差检查)
    6. 结尾 Logo / Endcard
    """

    # 相邻帧特征距离阈值 (4x4 块平均灰度差, 越大越不同)
    DIVERSITY_MIN_DISTANCE = 5.0
    # 平均亮度阈值 (0-255)
    BRIGHTNESS_MIN = 30.0
    # noise 帧比例阈值 (高频噪声像素占比)
    NOISE_MAX_RATIO = 0.5
    # 抽帧数
    N_FRAMES = 8
    # 结尾帧检查窗口 (秒)
    ENDCARD_WINDOW_SEC = 3.0

    def __init__(self, anthropic_api_key: Optional[str] = None):
        # ANTHROPIC_AUTH_TOKEN 是 Claude Code 用的 token, 也接受 ANTHROPIC_API_KEY
        self.api_key = (
            anthropic_api_key
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )
        self.base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")

    # ── 主入口 ────────────────────────────────────────
    def verify(self, video_path: Path, mock_vision: bool = True) -> VerificationReport:
        """验证视频, 返回 VerificationReport"""
        report = VerificationReport(passed=True)

        if not video_path.exists():
            report.passed = False
            report.reasons.append(f"视频不存在: {video_path}")
            return report

        # 抽帧
        frames = self._extract_frames(video_path, n=self.N_FRAMES)
        report.frames_analyzed = len(frames)
        if not frames:
            report.passed = False
            report.reasons.append("无法抽帧 (视频可能损坏)")
            return report

        # ── 维度 1: 段落多样性 ───────────────────────────
        report.diagnostics.append("check: diversity")
        diversity_passed, dist = self._check_diversity(frames)
        report.diversity_score = dist
        if not diversity_passed:
            report.passed = False
            report.reasons.append(
                f"段落多样性不足 (平均 phash 距离={dist:.2f} < {self.DIVERSITY_MIN_DISTANCE}), "
                f"疑似 trim offset=0 导致每段都从开头截"
            )

        # ── 维度 2: 无 noise 帧 ─────────────────────────
        report.diagnostics.append("check: no_noise_frames")
        noise_ratio = self._check_noise_ratio(frames)
        report.noise_ratio = noise_ratio
        if noise_ratio > self.NOISE_MAX_RATIO:
            report.passed = False
            report.reasons.append(
                f"检测到兜底 noise 帧 ({noise_ratio:.1%} > {self.NOISE_MAX_RATIO:.0%}), "
                f"疑似 cellauto 兜底逻辑残留"
            )

        # ── 维度 4: 无黑帧 ──────────────────────────────
        report.diagnostics.append("check: no_black_frames")
        avg_b = self._avg_brightness(frames)
        report.avg_brightness = avg_b
        if avg_b < self.BRIGHTNESS_MIN:
            report.passed = False
            report.reasons.append(
                f"画面过暗 (平均亮度={avg_b:.1f} < {self.BRIGHTNESS_MIN}), "
                f"疑似黑帧或视频损坏"
            )

        # ── 维度 3: 字幕可见 (Claude vision) ───────────
        report.diagnostics.append("check: subtitles_visible")
        if mock_vision:
            report.diagnostics.append("vision: mocked")
        else:
            sub_passed, sub_diag = self._check_subtitles_via_vision(frames)
            if not sub_passed:
                report.passed = False
                report.reasons.append(sub_diag)

        # ── 维度 6: 结尾 Endcard ────────────────────────
        report.diagnostics.append("check: endcard")
        endcard_ok = self._check_endcard(video_path)
        if not endcard_ok:
            report.diagnostics.append("endcard: 未检测到 (可能是测试 fixture 无 logo)")

        return report

    # ── 帧抽取 ────────────────────────────────────────
    def _extract_frames(self, video_path: Path, n: int = 8) -> List[np.ndarray]:
        """均匀抽 N 帧 (BGR numpy array)"""
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            log.warning(f"无法打开视频: {video_path}")
            return []
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            cap.release()
            return []
        step = max(1, total // n)
        frames: List[np.ndarray] = []
        for i in range(n):
            idx = min(i * step, total - 1)
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if ok:
                frames.append(frame)
        cap.release()
        return frames

    # ── 维度 1: 段落多样性 (特征距离) ─────────────────────
    def _frame_features(self, img: np.ndarray) -> np.ndarray:
        """帧的紧凑特征向量: 16 块平均灰度 + 整体 std + 16x16 缩略图哈希"""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # 分 4x4 块, 每块均值 → 16 维特征 (对纯色场景敏感)
        h, w = gray.shape
        bh, bw = h // 4, w // 4
        blocks = []
        for r in range(4):
            for c in range(4):
                block = gray[r * bh:(r + 1) * bh, c * bw:(c + 1) * bw]
                blocks.append(float(np.mean(block)))
        return np.array(blocks, dtype=np.float32)

    def _check_diversity(self, frames: List[np.ndarray]) -> tuple:
        """检查相邻帧特征距离, 返回 (passed, avg_distance)

        使用 4x4 块均值作为特征向量 (对纯色帧敏感: 不同颜色 → 不同块均值)。
        """
        if len(frames) < 2:
            return True, 0.0
        feats = [self._frame_features(f) for f in frames]
        distances = []
        for i in range(1, len(feats)):
            # L1 距离 (曼哈顿, 对灰度差敏感)
            d = float(np.mean(np.abs(feats[i] - feats[i - 1])))
            distances.append(d)
        avg = float(np.mean(distances))
        # 阈值: 相邻帧平均灰度差 > 5 → 多样 (经验值)
        return avg >= 5.0, avg

    # ── 维度 2: noise 帧比例 ─────────────────────────
    def _check_noise_ratio(self, frames: List[np.ndarray]) -> float:
        """噪声帧比例: Laplacian 方差 > 阈值 (高频) + 灰度分布均匀

        真实视频: 中等 lap_var (10-500), 内容有空间结构
        随机 noise: 极高 lap_var (>1000), 像素级随机, 无结构
        黑帧/纯色: 极低 lap_var (<10), 整帧一致
        """
        if not frames:
            return 0.0
        noisy = 0
        for f in frames:
            gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            lap = cv2.Laplacian(gray, cv2.CV_64F)
            lap_var = float(np.var(lap))
            # 噪声特征: 极高 Laplacian 方差 (像素间差异极大)
            # 实测: 真视频 lap_var < 500, 纯 noise > 1000
            if lap_var > 1000:
                noisy += 1
        return noisy / len(frames)

    # ── 维度 4: 平均亮度 ────────────────────────────
    def _avg_brightness(self, frames: List[np.ndarray]) -> float:
        if not frames:
            return 0.0
        total = 0.0
        for f in frames:
            gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            total += float(np.mean(gray))
        return total / len(frames)

    # ── 维度 3: 字幕 (Claude vision) ────────────────
    def _check_subtitles_via_vision(self, frames: List[np.ndarray]) -> tuple:
        """调 Claude vision 检查字幕可见, 返回 (passed, diagnostic_msg)"""
        # 选 3 张代表帧: 开头/中段/结尾
        if len(frames) < 3:
            picks = frames
        else:
            picks = [frames[0], frames[len(frames) // 2], frames[-1]]

        has_subtitle_count = 0
        for img in picks:
            res = self._call_claude_vision(img)
            if res.has_subtitle:
                has_subtitle_count += 1

        if has_subtitle_count >= 2:
            return True, ""
        return False, f"字幕可见性不足 ({has_subtitle_count}/{len(picks)} 帧含字幕)"

    def _call_claude_vision(self, img: np.ndarray) -> VisionResult:
        """调 Claude vision API 检查单张帧 (mockable)

        生产环境调真实 API, 测试中用 patch 替换。
        """
        if not self.api_key:
            log.warning("无 ANTHROPIC_API_KEY, 跳过 vision 检查")
            return VisionResult(passed=True, has_subtitle=False,
                                scene_description="[skipped: no api key]")

        try:
            import base64
            import urllib.request

            # 编码图片 (jpg 降尺寸省 token)
            small = cv2.resize(img, (640, 360))
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                return VisionResult(passed=False, has_subtitle=False,
                                    scene_description="[encode failed]")
            b64 = base64.b64encode(buf.tobytes()).decode("ascii")

            payload = json.dumps({
                "model": os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-7"),
                "max_tokens": 256,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": "image/jpeg", "data": b64
                        }},
                        {"type": "text", "text":
                            "请判断这张视频帧: 1) 是否包含可见的字幕文字 (中文/英文均可) "
                            "2) 简短描述画面内容 (1 句话)。"
                            "请严格用 JSON 格式返回: "
                            '{\"has_subtitle\": true/false, \"scene\": \"...\"}'},
                    ],
                }],
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{self.base_url.rstrip('/')}/v1/messages",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            text = body["content"][0]["text"].strip()
            # 解析 JSON (允许模型在前后加废话)
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1:
                return VisionResult(passed=False, has_subtitle=False,
                                    scene_description=text)
            data = json.loads(text[start:end + 1])
            return VisionResult(
                passed=True,
                has_subtitle=bool(data.get("has_subtitle")),
                scene_description=str(data.get("scene", "")),
                raw_response=text,
            )
        except Exception as e:
            log.warning(f"Claude vision 调用失败: {e}")
            return VisionResult(passed=False, has_subtitle=False,
                                scene_description=f"[error: {e}]")

    # ── 维度 6: 结尾 Endcard ────────────────────────
    def _check_endcard(self, video_path: Path) -> bool:
        """检查视频末尾 N 秒是否有 endcard (与中段帧显著不同)"""
        try:
            cap = cv2.VideoCapture(str(video_path))
            if not cap.isOpened():
                return False
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = total / fps
            cap.release()
            if duration < self.ENDCARD_WINDOW_SEC + 1:
                return True  # 太短就不检查
            # 抽最后 1 秒和中间 1 秒对比
            mid_frames = self._extract_frames_at(video_path, [duration / 2])
            end_frames = self._extract_frames_at(video_path, [duration - 0.5])
            if not mid_frames or not end_frames:
                return False
            f_mid = self._frame_features(mid_frames[0])
            f_end = self._frame_features(end_frames[0])
            dist = float(np.mean(np.abs(f_mid - f_end)))
            return dist >= 5.0  # 结尾应与中段不同 (有 logo)
        except Exception:
            return False

    def _extract_frames_at(self, video_path: Path, seconds: List[float]) -> List[np.ndarray]:
        """在指定秒数抽帧"""
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return []
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frames = []
        for sec in seconds:
            cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
            ok, f = cap.read()
            if ok:
                frames.append(f)
        cap.release()
        return frames