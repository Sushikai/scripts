# 信息差新闻视频流水线

全自动化每日生产流水线，为 Bilibili/抖音/小红书 生成"信息差新闻"短视频。

## 项目结构

```
info_gap_pipeline/
├── main.py              # 入口：--once(立即运行) / --schedule(定时调度)
├── config.py            # 配置中心
├── requirements.txt     # 依赖
├── research/            # 选题：多平台热榜扫描
├── script_gen/          # 脚本：LLM生成口播文案
├── download/            # 素材：视频下载+搜索匹配
│   └── search.py        # 素材搜索（B站/抖音/YouTube）
├── voiceover/           # 配音：Edge-TTS + Whisper字幕
├── edit/                # 剪辑：FFmpeg裁剪/拼接/字幕/混音
├── upload/              # 上传：B站cookies认证
├── scheduler/           # 调度：APScheduler（8:00/12:00/17:30）
├── utils/               # 工具函数
├── tests/               # pytest测试套件
├── data/                # 素材/BGM/缓存
│   ├── bgm/             # 背景音乐（放.mp3/.wav）
│   └── cache/
├── outputs/             # 成品视频
├── temp/                # 临时文件
└── logs/                # 日志
```

## 快速开始

### 1. 安装依赖

```bash
cd /Users/kaikai/scripts/info_gap_pipeline
pip install -r requirements.txt
playwright install chromium # 仅首次需要
```

### 2. 配置 Cookies

B站上传需要 cookies认证：

```bash
# 使用Chrome扩展导出B站cookies为JSON，保存为:
~/.bilibili_cookies.json
```

格式：
```json
{
  "SESSDATA": "xxx",
  "bili_jct": "xxx",
  "DedeUserID": "xxx"
}
```

### 3. 添加背景音乐（可选）

将 BGM 文件（.mp3/.wav）放到 `data/bgm/` 目录。

### 4. 运行

```bash
# 立即运行一次
python3 main.py --once

# 启动定时调度（每天8:00 / 12:00 / 17:30）
python3 main.py --schedule

# 运行测试
python3 -m pytest tests/ -v
```

## 核心流程

```
选题扫描 → 脚本生成 → 素材搜索 → 视频下载
        → 配音生成 → 视频合成 → B站上传
```

### 选题 (research/)
- 知乎热榜、微博热搜、百度热搜、B站热榜、36氪、财经、腾讯新闻
- 信息差关键词过滤（内幕/揭秘/99%人不知道...）

### 脚本生成 (script_gen/)
- 调用 MiniMax LLM 生成 120-150 字诙谐口播
- 自动分段（每段15-30 秒）

### 素材搜索 (download/search.py)
- 根据话题关键词搜索 B站/抖音/YouTube 视频
- 多平台结果合并去重

### 配音 (voiceover/)
- Edge-TTS 自然女声（zh-CN-XiaoxiaoNeural）
- Whisper 语音转字幕（srt格式）

### 视频合成 (edit/)
- FFmpeg 全流程：裁剪竖版9:16 → 静音 → 截取时长 → 拼接
- 字幕烧录（ass格式）
- BGM混音（音量0.15，不抢戏）

### 上传 (upload/)
- B站 cookies 认证上传
- 自动生成标题/描述/标签

## 视频规格

| 参数 | 值 |
|------|-----|
| 分辨率 | 1080x1920（竖版9:16） |
| 帧率 | 30fps |
| 编码 | H.264 / AAC |
| 时长 | 4-6分钟/条 |

## TTS 说明

优先使用 Edge-TTS（网络需访问 Microsoft），如遇网络限制可替换为：
- `Coqui TTS`（本地开源）
- `gTTS`（Google Translate TTS）
- macOS 内置 `say` 命令（测试用）

## 防封建议

- 多账号轮换
- 标题/描述/标签 SEO（含热点词但自然）
- 多样化 IP（代理池）
- 不要每条视频完全相同结构

## 扩展

- 多账号：修改 `upload/` 模块支持多 cookies轮换
- 多平台：抖音/小红书上传接口类似，可复用
- A/B测试：标题/缩略图variations自动对比CTR