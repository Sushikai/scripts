"""test_voiceover_v2.py — Round 3: 配音维度的回归测试

目标:
1. generate() 必须返回 (audio_path, audio_duration)
2. duration 用 ffprobe 而非估算
3. 段落 generate_segments() 返回 List[{idx, audio_path, audio_duration}]
"""

import unittest
from pathlib import Path


class TestGenerateWithDuration(unittest.TestCase):

    def test_generate_returns_duration(self):
        """generate() 必须返回 audio_duration"""
        from info_gap_pipeline.voiceover import generate_with_duration
        path, dur = generate_with_duration("这是一个测试文本,大约会生成 3 秒音频。", Path("/tmp/_v2_dur.wav"))
        self.assertIsNotNone(path)
        self.assertGreater(dur, 1.0)
        self.assertLess(dur, 30.0)


class TestSegmentVoiceover(unittest.TestCase):

    def test_generate_segments(self):
        """generate_segments() 返回 [{idx, audio_path, audio_duration}, ...]"""
        from info_gap_pipeline.voiceover import generate_segments
        segs = [
            {"idx": 0, "text": "这是第一段口播文本,内容是测试片段"},
            {"idx": 1, "text": "第二段也用于测试目的"},
        ]
        results = generate_segments(segs, Path("/tmp/_v2_segs"))
        self.assertEqual(len(results), len(segs))
        for r in results:
            self.assertIn("audio_duration", r)
            self.assertIn("audio_path", r)
            self.assertIn("idx", r)
            self.assertGreater(r["audio_duration"], 1.0)


class TestBackAndFront(unittest.TestCase):
    """前后端检查"""

    def test_no_silent_audio(self):
        """生成的音频不能是 0 时长或损坏 (允许 wav 或 mp3)"""
        from info_gap_pipeline.voiceover import generate_with_duration
        path, dur = generate_with_duration("测试短文", Path("/tmp/_v2_short.mp3"))
        if path:
            self.assertGreater(dur, 0.5)
            with open(path, "rb") as f:
                hdr = f.read(4)
            # wav: RIFF; mp3: 0xff 0xf?; id3: 'ID3'
            if hdr[:4] == b"RIFF":
                ok = True
            elif hdr[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
                ok = True
            elif hdr[:3] == b"ID3":
                ok = True
            elif hdr[:4] == b"OggS":
                ok = True
            elif hdr[:4] == b"fLaC":
                ok = True
            else:
                ok = False
            self.assertTrue(ok, f"unexpected header: {hdr!r}")
