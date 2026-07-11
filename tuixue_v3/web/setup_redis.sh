#!/bin/bash
# tuixue_v3/web/setup_redis.sh
# 一键启动/校验 Redis (127.0.0.1:6379) — tuixue_v3 统一存储依赖
# 幂等:已运行则提示 OK;未运行则加载 homebrew redis 并应用配置
# 用法: bash web/setup_redis.sh [verify|restart]
set -euo pipefail

REDIS_BIN="/opt/homebrew/bin/redis-server"
REDIS_CLI="/opt/homebrew/bin/redis-cli"
REDIS_CONF="/opt/homebrew/etc/redis.conf"

# ── 1. 检查 redis-server ─────────────────────────────────
if [ ! -x "$REDIS_BIN" ]; then
    echo "❌ redis-server 未安装: brew install redis"
    exit 1
fi
if [ ! -f "$REDIS_CONF" ]; then
    echo "❌ $REDIS_CONF 不存在"
    exit 1
fi

# ── 2. 校验配置 (maxmemory 2gb / allkeys-lru / appendonly yes) ──
if ! grep -qE "^maxmemory 2gb" "$REDIS_CONF"; then
    echo "⚙️  注入 maxmemory 2gb"
    sed -i.bak 's/^# maxmemory <bytes>$/# maxmemory <bytes>\nmaxmemory 2gb/' "$REDIS_CONF"
fi
if ! grep -qE "^maxmemory-policy allkeys-lru" "$REDIS_CONF"; then
    echo "⚙️  注入 maxmemory-policy allkeys-lru"
    sed -i.bak 's/^# maxmemory-policy noeviction$/# maxmemory-policy noeviction\nmaxmemory-policy allkeys-lru/' "$REDIS_CONF"
fi
if ! grep -qE "^appendonly yes" "$REDIS_CONF"; then
    echo "⚙️  注入 appendonly yes"
    sed -i '' 's/^appendonly no$/appendonly yes/' "$REDIS_CONF"
fi

# ── 3. 启动 (homebrew services 接管) ───────────────────────
if ! pgrep -f "redis-server" > /dev/null; then
    echo "🚀 启动 redis-server (homebrew services)"
    brew services start redis
    sleep 2
fi

# ── 4. 健康校验 ───────────────────────────────────────────
if ! $REDIS_CLI ping > /dev/null 2>&1; then
    echo "❌ Redis 启动失败,日志:"
    tail -20 /opt/homebrew/var/log/redis.log 2>/dev/null || true
    exit 1
fi

# 应用 runtime config (避免重启)
$REDIS_CLI CONFIG SET maxmemory 2gb > /dev/null
$REDIS_CLI CONFIG SET maxmemory-policy allkeys-lru > /dev/null
$REDIS_CLI CONFIG SET appendonly yes > /dev/null

PONG=$($REDIS_CLI ping)
DBSIZE=$($REDIS_CLI DBSIZE)
MEM=$($REDIS_CLI INFO memory | grep used_memory_human | cut -d: -f2 | tr -d '\r')
MAXMEM=$($REDIS_CLI CONFIG GET maxmemory | tail -1)
POLICY=$($REDIS_CLI CONFIG GET maxmemory-policy | tail -1)
AOF=$($REDIS_CLI CONFIG GET appendonly | tail -1)

echo "✅ Redis OK | ping=$PONG | dbsize=$DBSIZE | mem=$MEM / ${MAXMEM}B | policy=$POLICY | aof=$AOF"