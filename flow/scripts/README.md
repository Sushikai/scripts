# flow/scripts/

所有视频生产脚本的集中入口(都是 symlink,不复制原文件)。

## 结构

| 链接 | 指向 | 用途 |
|------|------|------|
| `info_gap_pipeline/` | `/Users/kaikai/scripts/info_gap_pipeline/` | 7 步信息差流水线(MVP 重点) |
| `fengge_pipeline` | `../video/` | 峰哥直播切片搬运 |
| `tiktok_story_bili/` | 同上 | TikTok/YouTube 故事搬运 |
| `material_collector/` | 同上 | 素材多源采集 |
| `voice_api/` | 同上 | FastAPI TTS(8899) |
| `bilibili_utils/` | 同上 | B 站工具库(cookies/session/wbi/回复/DM) |
| `news/` | 同上 | 信息差 v9 老版本 |
| `yinliu/` | 同上 | 引流评论 |
| `config/` | 同上 | LLM/cookies 配置 |
| `llm_utils/` | 同上 | LLM 客户端 |
| `ai_video_project/` | `/Users/kaikai/ai_video_project/` | news_outputs 产物 |
| `ai_video_upload/` | `/Users/kaikai/ai_video_upload/` | bili/douyin 上传 |
| `tiktok_automation/` | `/Users/kaikai/tiktok_automation/` | fengge 自动化 |
| `zhihu_video_system/` | `/Users/kaikai/zhihu_video_system/` | 知乎视频流水线 |
| `fengge_downloader/` | `/Users/kaikai/workspace-agents/fengge_downloader/` | 听泉鉴宝专项 |

## 重要铁律

1. **不修改原文件** — 只通过 wrapper 调用,绝不动原脚本逻辑
2. **新功能在 flow/backend/wrappers/ 下** — 每个工具一个 wrapper
3. **wrapper 通过 `scripts/<name>` 调用原 CLI**(subprocess) 或 import 关键函数
4. **路径都用绝对路径** — 避免 symlink 路径解析的坑