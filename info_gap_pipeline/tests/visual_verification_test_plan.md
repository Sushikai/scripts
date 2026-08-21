# Visual Verification Test Plan — 信息差视频流水线成品验证

> 用户痛点: 之前 10+ 成品视频都用了同一段老素材, 字幕也没有, 但 pipeline 自身
> 全部"成功"完成。用户必须靠肉眼才能发现问题 — 这是 silent failure 的根源。
>
> **目标**: 把"人眼检查"自动化为机器可执行的视觉验证, 不通过则无限循环修复。

## 1. 验证对象

`pipeline` 产出的最终视频 `info_gap_YYYYMMDD_HHMMSS.mp4`, 由以下步骤生成:
1. research (选题)
2. script_gen (脚本分镜)
3. download (B 站 / 通用素材下载, 每段独立)
4. voiceover (edge-tts 配音 + WordBoundary 字幕)
5. edit (裁剪 + crop + 烧录字幕 + 结尾 Logo)

之前发现的 5 个 bug 都集中在 step 3-5, 所以视觉验证必须覆盖这 3 步产物。

## 2. 验证维度 (6 维)

| # | 维度 | 判定方法 | 阈值 | 对应 bug |
|---|------|----------|------|----------|
| 1 | **段落多样性** | 相邻两段视频帧的 SSIM/感知哈希距离 | 平均距离 > 0.05 | Bug #1 (trim offset=0) |
| 2 | **无兜底帧** | 任意帧不能是 cellauto noise (黑白噪点图) | noise 帧比例 < 5% | Bug #2 (cellauto 兜底) |
| 3 | **字幕可见** | OCR / 视觉模型读出字幕文字 | 字幕文字可读 + 出现 ≥ N 秒 | Bug #3 (无字幕) |
| 4 | **黑帧检测** | 帧平均亮度 > 阈值 | 平均亮度 > 30/255 | 任何 step 失败假象 |
| 5 | **画面无拉伸** | 检测人脸/物体形变 (拉伸检测) | 无显著拉伸 | crop 副作用 |
| 6 | **结尾 Logo 出现** | 视频末尾 5 秒应出现 Logo | Logo 帧 ≥ 1 | 结尾拼接 |

## 3. 实现架构

```
┌─────────────────────────────────────────────────┐
│ VisualVerifier (info_gap_pipeline/visual.py)    │
│                                                  │
│  verify(video_path) -> VerificationReport       │
│    ├─ 1. ffmpeg 抽帧 (N 帧均匀分布)            │
│    ├─ 2. 多维度本地检查 (SSIM/brightness/noise) │
│    ├─ 3. Claude vision API 语义检查             │
│    └─ 4. 聚合 + 阈值判定 → pass/fail            │
└─────────────────────────────────────────────────┘
            ↓
       pytest test_visual_verification.py
            ↓
       CI 跑通 → ship
       CI 失败 → 修 pipeline → 重跑 → 无限循环
```

### 3.1 本地检查 (不需要 API)

- **段落多样性**: 用 ffmpeg 抽 N 帧, 两两比较感知哈希 (phash) 距离
- **无兜底帧**: noise 帧特征 = 高频随机 + 无字幕 + 平均亮度均匀
- **黑帧检测**: numpy.mean(frame) < 30 → 黑帧
- **结尾 Logo**: 用模板匹配 (cv2.matchTemplate) 检测最后 5 秒帧

### 3.2 Claude vision 语义检查

抽 3 张代表帧 (开头 / 中段 / 结尾), 调 Claude vision:
- "请描述这张图片的内容, 并判断是否包含字幕文字"
- 期望: 每段画面描述不同, 字幕可见

## 4. 测试用例 (tests/test_visual_verification.py)

```python
class TestVisualVerification:
    def test_verify_returns_report_for_valid_video(tmp_path)
    def test_segments_are_visually_diverse(tmp_path)        # Bug #1 回归
    def test_no_cellauto_noise_frames(tmp_path)             # Bug #2 回归
    def test_subtitles_visible_in_video(tmp_path)            # Bug #3 回归
    def test_no_black_frames(tmp_path)
    def test_endcard_logo_visible(tmp_path)
    def test_pipeline_output_passes_visual_check()           # 端到端: 跑 pipeline + verify
```

## 5. 迭代策略 ("不通过 则无限循环")

```python
MAX_ITER = 10
for i in range(MAX_ITER):
    video = pipeline.run()
    report = VisualVerifier().verify(video)
    if report.passed:
        ship(video)
        break
    # 失败 → 根据 report.reasons 自动定位:
    #   "相邻段相同" → 修 trim offset
    #   "noise 帧"   → 修 download fallback
    #   "无字幕"     → 修 edge-tts SubMaker
    diagnose_and_fix(report.reasons)
else:
    raise RuntimeError(f"{MAX_ITER} 轮仍未通过视觉验证")
```

## 6. mock 策略 (测试中)

测试不调真实 Claude API, 用 `unittest.mock` patch `_call_claude_vision`,
返回预设的判定结果。这样 CI 跑测试不需要 API key。

实际验证 (`test_pipeline_output_passes_visual_check`) 才调真实 API。

## 7. 验收标准

- ✅ 测试文件 `tests/test_visual_verification.py` 全部 PASS
- ✅ VisualVerifier 对一段 30s 测试视频返回 `passed=True`
- ✅ VisualVerifier 对 cellauto noise 视频返回 `passed=False, reason=noise`
- ✅ VisualVerifier 对 trim 偏移=0 视频返回 `passed=False, reason=identical_segments`
- ✅ Pipeline 产物通过 VisualVerifier (实跑验证)