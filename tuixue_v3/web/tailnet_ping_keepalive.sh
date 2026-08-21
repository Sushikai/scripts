#!/usr/bin/env bash
# tailnet_ping_keepalive.sh — R-fix 2026-08-17 (v2)
#   维持 tailscale DERP 中继连接, 防止 iPhone 4G 长时间无流量被 DERP 节点 reset
#   每 60s ping iphone 一次 (走 DERP), 强迫本机 tailscaled 持续保持中继 handshake
#
# v2 改动: 用 perl 强超时包 tailscale ping (8s 超时必杀), 避免 DERP 协商卡死整个循环
#
# 启动: launchd com.kaikai.tuixue.tailnet-ping-keepalive (KeepAlive=true)
# 日志: /tmp/tuixue_tunnels/tailnet_ping_keepalive.log

set -u

LOG="/tmp/tuixue_tunnels/tailnet_ping_keepalive.log"
mkdir -p /tmp/tuixue_tunnels

note() { echo "[$(date '+%H:%M:%S')] $*" >> "$LOG"; }

IPHONE_IP="100.108.203.96"
INTERVAL=60  # 60s 一次, 既保活又不浪费流量 (~3KB/min)

note "🚀 tailnet_ping_keepalive v2 启动, 目标 iPhone=${IPHONE_IP}, 间隔=${INTERVAL}s"

# 用 perl 强超时包 tailscale ping, 防止 DERP 协商卡死整个循环
# 8s 超时必杀 (tailscale ping 内部 5s 超时 + perl 3s 余量)
safe_ping() {
  perl -e '
    use POSIX ":sys_wait_h";
    my $pid = fork();
    if ($pid == 0) {
      # 子进程: exec tailscale ping
      exec("tailscale", "ping", "--c", "1", "--timeout", "5s", "'"$IPHONE_IP"'");
      exit 127;
    }
    # 父进程: 8s 超时必杀
    my $waited = 0;
    while ($waited < 8) {
      my $r = waitpid($pid, WNOHANG);
      if ($r > 0) { exit 0; }
      select(undef, undef, undef, 0.5);
      $waited += 0.5;
    }
    # 超时: kill 子进程
    kill "TERM", $pid;
    select(undef, undef, undef, 0.5);
    kill "KILL", $pid;
    waitpid($pid, 0);
    print STDERR "⏰ tailscale ping 超时 8s, 已 KILL\n";
    exit 1;
  ' 2>&1
}

while true; do
  RESULT=$(safe_ping)
  echo "$RESULT" >> "$LOG"
  if echo "$RESULT" | grep -q "pong from"; then
    :  # 成功, 静默 (避免日志暴涨)
  elif echo "$RESULT" | grep -q "⏰"; then
    note "⏰ ping 超时 8s, tailscale DERP 协商异常 (下次 60s 后重试)"
  else
    note "⚠ ping 失败 (路由异常), 下次重试"
  fi
  sleep "${INTERVAL}"
done