# 短视频素材自动采集系统

> Mac Mini M系列 32GB 内存 | MuMu Player Pro | Python 3.11+
> 支持平台：抖音 / B站 / 小红书
> 内容风格：火花宝宝（可爱萌娃） + 不存在的小镇（荒诞探险）

---

## 目录

- [快速开始](#快速开始)
- [安装步骤](#安装步骤)
- [项目结构](#项目结构)
- [配置说明](#配置说明)
- [使用示例](#使用示例)
- [防封号指南](#防封号指南)

---

## 快速开始

```bash
# 1. 克隆/创建项目
cd ~/material_collector

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动 MuMu Player Pro（确保 ADB 已开启）

# 4. 采集素材
python main.py -p douyin -k "宝宝可爱" -k "萌娃" -d 1800

# 5. AI 处理
python main.py -p douyin --process-only

# 6. 导出到 MoneyPrinterTurbo
python moneyprinter/feed_to_mpt.py
```

---

## 安装步骤

### 1. MuMu Player Pro 安装与配置

**下载地址**: https://mumu.163.com/mac.html

**ADB 开启方法**:
1. 打开 MuMu Player Pro 设置
2. 高级设置 → 开启 ADB 调试（端口: 16384）
3. 确保模拟器已完全启动

**验证连接**:
```bash
adb connect 127.0.0.1:16384
# 应显示: connected to 127.0.0.1:16384
```

### 2. Python 环境

```bash
# 推荐使用 conda（M系列芯片兼容性更好）
conda create -n material_collector python=3.11
conda activate material_collector

# 或使用 venv
python3.11 -m venv venv
source venv/bin/activate
```

### 3. 安装 PaddleOCR（M系列 Mac）

```bash
# 安装 PaddlePaddle（CPU 版，M系列用不了 CUDA）
pip install paddlepaddle

# 安装 PaddleOCR
pip install paddleocr

# 验证
python -c "from paddleocr import PaddleOCR; print('OK')"
```

### 4. 安装其他依赖

```bash
pip install -r requirements.txt
```

---

## 项目结构

```
material_collector/
├── collector/                  # 采集核心
│   ├── adb_controller.py       # ADB 控制（截图/点击/滑动）
│   ├── ocr_processor.py       # OCR 识别（PaddleOCR / 百度OCR）
│   └── collector_core.py       # 采集器基类 + 平台实现
│
├── processor/                  # AI 处理
│   └── material_processor.py   # Ollama 本地模型分析
│
├── database/                   # 数据存储
│   └── materials_db.py         # SQLite + JSON
│
├── moneyprinter/              # MoneyPrinterTurbo 集成
│   └── feed_to_mpt.py          # 素材导出 + 配置生成
│
├── materials/                  # 素材目录
│   ├── raw/                    # 原始截图 + JSON
│   └── processed/             # AI 处理后素材
│
├── logs/                       # 日志
│
├── config.py                   # 全局配置
├── main.py                     # 入口
├── requirements.txt            # Python 依赖
└── README.md                   # 本文件
```

---

## 配置说明

编辑 `config.py` 或通过命令行参数覆盖：

### ADB 配置
```python
ADB_HOST = "127.0.0.1"
ADB_PORT = 16384  # MuMu Player Pro 默认
```

### OCR 配置
```python
OCR_CONFIG = {
    "engine": "paddle",  # paddle | baidu
    "paddle": {
        "use_gpu": True,   # Mac M系列不支持
        "lang": "ch",
    },
    "min_text_height": 15,
}
```

### Ollama 配置
```bash
# 确保 Ollama 已安装并运行
ollama serve

# 拉取模型（如未安装）
ollama pull qwen2.5:32b-instruct-q4_K_M
ollama pull gemma3:4b
```

### 风格配置
```python
STYLE_PROMPTS = {
    "火花宝宝": {
        "keywords": ["宝宝", "萌娃", "可爱", "亲子"],
        "mood": "温暖、治愈、可爱",
    },
    "不存在的小镇": {
        "keywords": ["探险", "荒诞", "奇幻", "梦境"],
        "mood": "荒诞、奇幻、神秘",
    },
}
```

---

## 使用示例

### 基础采集

```bash
# 抖音 - 采集宝宝相关内容
python main.py -p douyin -k "宝宝可爱" -k "萌娃日常" -d 1800

# B站 - 采集萌娃弹幕
python main.py -p bilibili -k "火花宝宝" --scroll 30

# 小红书
python main.py -p xiaohongshu -k "亲子萌娃" --scroll 20

# 全平台
python main.py -p all -k "火花宝宝" --style "火花宝宝"
```

### 仅处理已采集素材

```bash
python main.py --process-only
```

### 仅采集不处理

```bash
python main.py -p douyin -k "宝宝" --no-process
```

### 批量处理 + 导出

```bash
# 运行采集 + 处理
python main.py -p all -k "火花宝宝" -k "不存在的小镇" -d 3600

# 导出到 MoneyPrinterTurbo
python -c "
from moneyprinter.feed_to_mpt import MoneyPrinterFeeder
feeder = MoneyPrinterFeeder()
for cat in ['火花宝宝', '不存在的小镇']:
    config = feeder.generate_config_snippet(cat)
    print(f'{cat}:', config)
"
```

---

## 防封号指南

> ⚠️ 自动采集行为可能被平台检测，请谨慎使用

### 通用措施

1. **控制频率**
   - 滑动间隔 ≥ 1.5 秒
   - 每小时最大采集 ≤ 500 条
   - 避免深夜高频操作

2. **随机化操作**
   - 滑动时长随机 250-350ms
   - 截图间隔随机 1.5-2.5 秒
   - 避免完全规律的采集模式

3. **内容过滤**
   - 自动过滤广告（ad_score > 0.5）
   - 过滤敏感词
   - 去重窗口 24 小时

4. **User-Agent**
   - 模拟器使用模拟的 UA
   - 避免明显 bot 特征

### 平台策略

**抖音**:
- 搜索后等待 2 秒再滑动
- 避免连续搜索同一关键词

**B站**:
- 弹幕采集频率更低（弹幕密度高）
- 优先采集标题，少采集弹幕

**小红书**:
- 小红书检测较严，控制频率
- 建议使用 --scroll 15 以下

### 建议

1. 使用多账号轮换
2. 采集 + 处理分时运行
3. 优先白天操作，避免凌晨

---

## 数据库结构

### materials（原始素材）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT | UUID |
| platform | TEXT | douyin/bilibili/xiaohongshu |
| keyword | TEXT | 采集关键词 |
| raw_text | TEXT | 原始文字 |
| content_hash | TEXT | 去重用 |
| ad_score | REAL | 广告分数 |
| processed | INTEGER | 是否已处理 |

### processed_materials（处理后素材）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT | UUID |
| clean_text | TEXT | 清洗后文字 |
| category | TEXT | 火花宝宝/不存在的小镇/通用 |
| mood | TEXT | 情绪标签 |
| tags | TEXT | JSON 数组 |
| usable | INTEGER | 是否可用 |

---

## 故障排查

### ADB 连接失败
```bash
# 确认 MuMu ADB 端口
adb devices
# 如显示 unauthorized，重启模拟器

# 强制重连
adb kill-server
adb connect 127.0.0.1:16384
```

### OCR 识别慢
- M系列 Mac 不支持 CUDA，用 CPU 版
- 降低 `screenshot_interval` 到 2.5 秒
- 减少每次滑动后的等待

### Ollama 超时
```bash
# 确认 Ollama 运行
curl http://localhost:11434/api/tags

# 重启 Ollama
pkill -f "ollama serve"
ollama serve &
```

---

## License

仅供学习研究使用，内容采集请遵守平台服务条款。