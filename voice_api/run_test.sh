#!/bin/bash
# Voice API 测试快捷脚本
cd /Users/kaikai/scripts/voice_api
echo "🧪 运行 Voice API 单元测试..."
/Users/kaikai/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/ -v --tb=short
echo ""
echo "✅ 测试完成"
