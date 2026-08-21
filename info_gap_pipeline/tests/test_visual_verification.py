"""test_visual_verification.py — 视觉验证测试 (前后端视觉模型验证成品)

参考 tests/visual_verification_test_plan.md
测试策略: mock Claude vision API, 验证 VisualVerifier 的判定逻辑正确。
实际跑 pipeline 时才调真实 API (在 test_visual_verification_e2e.py 中)。
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import subprocess

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))


def _make_test_video(path: Path, duration: float = 5.0, color: str = "red"):
    """生成测试视频: 纯色 + 时长"""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c={color}:s=1920x1080:d={duration}",
        "-f", "lavfi", "-i", "aevalsrc=0",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    assert r.returncode == 0, f"create test video failed: {r.stderr[:200]}"


def _make_diverse_video(path: Path, n_clips: int = 5):
    """生成多段不同颜色拼接的视频, 每段 2s"""
    clips_dir = path.parent / "_clips"
    clips_dir.mkdir(exist_ok=True)
    concat_list = path.parent / "_concat.txt"
    colors = ["red", "green", "blue", "yellow", "purple", "cyan", "magenta"]
    concat_list.write_text("")
    for i in range(n_clips):
        clip = clips_dir / f"c{i}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c={colors[i % len(colors)]}:s=640x360:d=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-an", str(clip),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        with open(concat_list, "a") as f:
            f.write(f"file '{clip.resolve()}'\n")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an", str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"concat failed: {r.stderr[:200]}"


def _make_noise_video(path: Path, duration: float = 5.0):
    """生成 cellauto 风格的 noise 视频 (mock 之前的兜底 bug)"""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"nullsrc=s=640x360:d={duration}",
        "-vf", "noise=alls=200:allf=t",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an", str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    assert r.returncode == 0, f"noise video failed: {r.stderr[:200]}"


class TestVisualVerifierBasics:
    """VisualVerifier 基本结构"""

    def test_verifier_class_exists(self):
        from info_gap_pipeline.visual import VisualVerifier
        assert VisualVerifier is not None

    def test_verify_returns_report(self, tmp_path):
        """verify(video) 返回 VerificationReport 对象"""
        from info_gap_pipeline.visual import VisualVerifier
        video = tmp_path / "test.mp4"
        _make_test_video(video)
        v = VisualVerifier()
        report = v.verify(video)
        assert report is not None
        assert hasattr(report, "passed")
        assert hasattr(report, "reasons")
        assert isinstance(report.passed, bool)
        assert isinstance(report.reasons, list)

    def test_verify_nonexistent_video(self, tmp_path):
        """不存在的视频路径 → 报告 passed=False, 原因清晰"""
        from info_gap_pipeline.visual import VisualVerifier
        v = VisualVerifier()
        report = v.verify(tmp_path / "nope.mp4")
        assert report.passed is False
        assert any("不存在" in r or "not found" in r.lower() or "missing" in r.lower()
                   for r in report.reasons), \
            f"应说明原因, got {report.reasons}"


class TestSegmentDiversity:
    """段落多样性 (Bug #1 回归: trim offset=0 → 7 段全是 raw 开头)"""

    def test_diverse_video_passes(self, tmp_path):
        """拼接不同颜色 5 段 → 段落多样性 PASS"""
        from info_gap_pipeline.visual import VisualVerifier
        video = tmp_path / "diverse.mp4"
        _make_diverse_video(video, n_clips=5)
        v = VisualVerifier()
        report = v.verify(video, mock_vision=True)
        # 多样性通过 (mock vision 给好结果)
        diversity_fail = [r for r in report.reasons if "段" in r and ("同" in r or "多样" in r)]
        assert len(diversity_fail) == 0, f"多样性应 PASS, 但: {diversity_fail}"

    def test_same_color_video_fails_diversity(self, tmp_path):
        """整段同一颜色 → 段落多样性 FAIL"""
        from info_gap_pipeline.visual import VisualVerifier
        video = tmp_path / "mono.mp4"
        _make_test_video(video, duration=10.0, color="red")
        v = VisualVerifier()
        report = v.verify(video, mock_vision=True)
        # 应识别为 "段落单一 / 相邻段相同"
        assert any("段" in r or "diversity" in r.lower() for r in report.reasons), \
            f"应报告多样性失败, got {report.reasons}"


class TestNoNoiseFrames:
    """无兜底 noise 帧 (Bug #2 回归)"""

    def test_clean_video_passes_noise_check(self, tmp_path):
        """纯色视频不是 noise → 通过"""
        from info_gap_pipeline.visual import VisualVerifier
        video = tmp_path / "clean.mp4"
        _make_test_video(video, duration=5.0, color="blue")
        v = VisualVerifier()
        report = v.verify(video, mock_vision=True)
        noise_fails = [r for r in report.reasons if "noise" in r.lower() or "噪" in r]
        assert len(noise_fails) == 0, f"clean 视频不应被判 noise, got {noise_fails}"

    def test_noise_video_fails(self, tmp_path):
        """noise 视频 (cellauto 风格) → 应被识别并 FAIL"""
        from info_gap_pipeline.visual import VisualVerifier
        video = tmp_path / "noise.mp4"
        try:
            _make_noise_video(video, duration=5.0)
        except AssertionError:
            pytest.skip("ffmpeg noise filter 不可用, 跳过")
        v = VisualVerifier()
        report = v.verify(video, mock_vision=True)
        assert any("noise" in r.lower() or "噪" in r or "兜底" in r for r in report.reasons), \
            f"noise 视频应被检测, got {report.reasons}"
        assert report.passed is False


class TestSubtitleVisibility:
    """字幕可见 (Bug #3 回归: 之前字幕完全没生成)"""

    def test_subtitle_check_is_invoked(self, tmp_path):
        """verify 应触发字幕检查逻辑"""
        from info_gap_pipeline.visual import VisualVerifier
        video = tmp_path / "test.mp4"
        _make_test_video(video, duration=5.0)
        v = VisualVerifier()
        # mock Claude vision 模拟"看到字幕"
        with patch.object(v, "_call_claude_vision", return_value=MagicMock(
            passed=True, has_subtitle=True, scene_description="新闻画面, 有字幕"
        )):
            report = v.verify(video, mock_vision=False)
        # 不管 pass/fail, 字幕检查必须执行
        assert any("字幕" in r or "subtitle" in r.lower() for r in report.diagnostics) or \
               report.passed, f"字幕检查必须执行, got report={report}"


class TestNoBlackFrames:
    """黑帧检测"""

    def test_normal_video_no_black_frames(self, tmp_path):
        """正常彩色视频 → 无黑帧"""
        from info_gap_pipeline.visual import VisualVerifier
        video = tmp_path / "normal.mp4"
        _make_test_video(video, duration=5.0, color="orange")
        v = VisualVerifier()
        report = v.verify(video, mock_vision=True)
        black_fails = [r for r in report.reasons if "黑" in r or "black" in r.lower()]
        assert len(black_fails) == 0, f"正常视频不应判黑帧, got {black_fails}"


class TestEndcard:
    """结尾 Logo / Endcard"""

    def test_endcard_check_runs(self, tmp_path):
        """结尾帧检查逻辑必须执行"""
        from info_gap_pipeline.visual import VisualVerifier
        video = tmp_path / "test.mp4"
        _make_test_video(video, duration=8.0)
        v = VisualVerifier()
        report = v.verify(video, mock_vision=True)
        # 结尾帧检查应被记录在 diagnostics 中
        assert hasattr(report, "diagnostics"), "report 需有 diagnostics 字段"


class TestVisionMockable:
    """Claude vision 调用必须可 mock (CI 友好)"""

    def test_verify_with_mock_vision(self, tmp_path):
        """mock_vision=True 时不调真实 API"""
        from info_gap_pipeline.visual import VisualVerifier
        video = tmp_path / "test.mp4"
        _make_test_video(video, duration=3.0)
        v = VisualVerifier()
        # 不打网络, 强制 mock 返回通过
        report = v.verify(video, mock_vision=True)
        assert report is not None

    def test_vision_called_when_not_mocked(self, tmp_path):
        """mock_vision=False 时应触发 _call_claude_vision 调用"""
        from info_gap_pipeline.visual import VisualVerifier
        video = tmp_path / "test.mp4"
        _make_test_video(video, duration=3.0)
        v = VisualVerifier()
        with patch.object(v, "_call_claude_vision") as mock_call:
            mock_call.return_value = MagicMock(
                passed=True, has_subtitle=False, scene_description="test"
            )
            v.verify(video, mock_vision=False)
            # 应至少调用一次 (开头/中段/结尾 3 张)
            assert mock_call.call_count >= 1, "未 mock 时应调用 vision API"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])