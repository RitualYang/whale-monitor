#!/usr/bin/env bash
# 一键停止所有服务

ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT/.run/pids"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[stop]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }

if [ ! -f "$PID_FILE" ]; then
  warn "未找到 .run/pids，服务可能未在运行"
  # 兜底：按端口强制清理
  for PORT in 8000 5173; do
    PID=$(lsof -ti tcp:$PORT 2>/dev/null) || true
    if [ -n "$PID" ]; then
      info "强制终止占用端口 $PORT 的进程 (PID: $PID)"
      kill -TERM $PID 2>/dev/null || true
    fi
  done
  exit 0
fi

while IFS='=' read -r name pid; do
  [ -z "$pid" ] && continue
  if kill -0 "$pid" 2>/dev/null; then
    info "停止 $name (PID: $pid)..."
    kill -TERM "$pid" 2>/dev/null || true
    # 等待最多 5 秒后强制
    for i in $(seq 1 5); do
      sleep 1
      kill -0 "$pid" 2>/dev/null || break
      if [ $i -eq 5 ]; then
        warn "$name 未响应 SIGTERM，发送 SIGKILL"
        kill -9 "$pid" 2>/dev/null || true
      fi
    done
    info "$name 已停止"
  else
    warn "$name (PID: $pid) 已不在运行"
  fi
done < "$PID_FILE"

rm -f "$PID_FILE"
info "所有服务已停止"
