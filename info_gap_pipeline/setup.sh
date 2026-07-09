#!/bin/bash
# setup.sh — 信息差流水线安装脚本

set -e

echo "=== 信息差新闻视频流水线 安装脚本 ==="

# 1. 安装Python依赖
echo "[1/5] 安装Python依赖..."
pip install -r requirements.txt

# 2. 安装Playwright浏览器
echo "[2/5] 安装Playwright Chromium..."
playwright install chromium

# 3. 检查FFmpeg
echo "[3/5] 检查FFmpeg..."
if command -v ffmpeg &> /dev/null; then
    ffmpeg -version | head -1
else
    echo "FFmpeg 未安装，请先安装: brew install ffmpeg"
fi

# 4. 创建目录结构
echo "[4/5] 创建目录..."
mkdir -p data/bgm data/cache outputs temp logs

# 5. 检查Cookies
echo "[5/5] 检查B站Cookies..."
if [ -f ~/.bilibili_cookies.json ]; then
    echo "Cookies已配置 ✓"
else
    echo "⚠ Cookies未配置，请创建 ~/.bilibili_cookies.json"
    echo " 格式: {\"SESSDATA\": \"xxx\", \"bili_jct\": \"xxx\", \"DedeUserID\": \"xxx\"}"
fi

echo ""
echo "=== 安装完成 ==="
echo ""
echo "运行方式:"
echo "  # 立即运行一次"
echo "  python3 main.py --once"
echo ""
echo "  # 启动定时调度"
echo "  python3 main.py --schedule"
echo ""
echo "  # 运行测试"
echo "  python3 -m pytest tests/ -v"