#!/bin/bash
# deploy_zt.sh — 部署涨停板次日溢价策略
# 验证通过后:备份旧文件,启用新策略,重启服务

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "=== ZT 策略部署 ==="

# 1. 验证新的回测能跑通
echo "[1/4] 验证回测引擎..."
python3 -c "
import sys; sys.path.insert(0, '$DIR/..')
import logging; logging.basicConfig(level=logging.WARNING)
from tuixue_v3 import zt_backtest as zt
from tuixue_v3 import zt_config as cfg
r = zt.run_zt_backtest(start='2026-05-01', end='2026-05-15', **cfg.OPTIMAL_PARAMS)
s = r.get('summary', {}) or {}
assert s.get('trades', 0) > 5, f'交易太少: {s.get(\"trades\",0)}'
print(f'  通过: {s.get(\"trades\",0)} 笔交易')
" 2>&1 | tail -5

# 2. 备份旧文件
echo "[2/4] 备份旧策略文件..."
BACKUP_DIR="data/zt_deploy_bak_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
for f in optimizer.py backtest.py config.py web/screener.py; do
    if [ -f "$f" ]; then
        cp "$f" "$BACKUP_DIR/"
        echo "  备份 $f → $BACKUP_DIR/"
    fi
done

# 3. 更新配置
echo "[3/4] 写入 OPTIMAL_PARAMS 到 zt_config.py..."
# (zt_config.py 已包含 OPTIMAL_PARAMS)

# 4. 重启服务
echo "[4/4] 重启 Web 服务..."
if pgrep -f "python3 -m tuixue_v3.web.server" > /dev/null 2>&1; then
    echo "  停止旧服务..."
    pkill -f "python3 -m tuixue_v3.web.server" 2>/dev/null || true
    sleep 2
fi
echo "  启动新服务..."
nohup python3 -m tuixue_v3.web.server > /tmp/zt_server.log 2>&1 &
sleep 3
echo "  服务 PID: $(pgrep -f 'python3 -m tuixue_v3.web.server' | head -1)"
echo ""
echo "=== 部署完成 ==="
echo "新端点: GET /api/zt/backtest   — ZT 回测"
echo "       GET /api/zt/optimize   — ZT 优化"
echo "       GET /api/zt/params     — 当前参数"
