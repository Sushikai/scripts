# 🎙️ Voice API

声音克隆 + 文字转语音 接口服务。

## 功能

- **TTS** — 文字转语音（edge-tts，支持 15+ 种音色，中/英/日/韩）
- **声音克隆** — XTTS 将文字用参考音频的音色朗读
- **接口化** — FastAPI，HTTP 调用，其他程序可集成

## 快速启动

```bash
cd /Users/kaikai/scripts/voice_api
pip install -r requirements.txt
python api_server.py
# 服务运行在 http://0.0.0.0:8899
```

## API 接口

### 文字转语音
```bash
curl -X POST http://localhost:8899/tts \
  -H "Content-Type: application/json" \
  -d '{
    "text": "你好，这是测试音频",
    "voice": "zh-CN-XiaoxiaoNeural",
    "rate": "+0%",
    "output_file": "test.mp3"
  }'
```

### 获取可用音色
```bash
curl http://localhost:8899/voices
```

### 声音克隆（上传参考音频）
```bash
curl -X POST http://localhost:8899/clone_upload \
  -F "text=这是用你的声音朗读的" \
  -F "file=@reference.wav"
```

### 声音克隆（已有参考音频文件）
```bash
curl -X POST http://localhost:8899/clone \
  -H "Content-Type: application/json" \
  -d '{
    "text": "你好",
    "reference_audio_path": "/path/to/ref.wav"
  }'
```

### 下载音频
```bash
curl -O http://localhost:8899/download/filename.mp3
```

## 可用音色（edge-tts）

| 音色 ID | 名称 |
|--------|------|
| zh-CN-XiaoxiaoNeural | 晓晓（女） |
| zh-CN-YunxiNeural | 云希（男） |
| zh-CN-YunxiaNeural | 云夏（女） |
| zh-CN-YunyangNeural | 云扬（男） |
| zh-CN-shaanxi-XiaoyaoNeural | 小雅（陕西话） |
| en-US-AriaNeural | Aria（英文女） |
| en-US-JennyNeural | Jenny（英文女） |
| en-US-GuyNeural | Guy（英文男） |

## 项目结构

```
voice_api/
├── api_server.py      # FastAPI 主服务
├── config.json        # 配置文件
├── requirements.txt   # Python依赖
├── output/            # 生成的音频文件
├── temp/             # 临时文件
└── models/           # 模型文件（XTTS等）
    └── voiceprints/   # 缓存的声纹
```

## 模型说明

- **TTS**: edge-tts（微软 Azure，开箱即用，CPU 友好）
- **声音克隆**: XTTS (Coqui TTS)，首次调用自动下载模型，需约 500MB 磁盘

## 其他程序调用示例

```python
import requests

# TTS
resp = requests.post("http://localhost:8899/tts", json={
    "text": "你好世界",
    "voice": "zh-CN-XiaoxiaoNeural"
})
print(resp.json()["download_url"])

# 声音克隆
resp = requests.post("http://localhost:8899/clone_upload", files={
    "file": open("my_voice.wav", "rb")
}, data={"text": "你好用我的声音读的"})
print(resp.json()["download_url"])
```
