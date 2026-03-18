#!/usr/bin/env bash
# Stop all services

ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT/.run/pids"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[stop]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }

if [ ! -f "$PID_FILE" ]; then
  warn "No .run/pids found — services may not be running"
  for PORT in 8000 5173; do
    PID=$(lsof -ti tcp:$PORT 2>/dev/null) || true
    if [ -n "$PID" ]; then
      info "Killing process on port $PORT (PID: $PID)"
      kill -TERM $PID 2>/dev/null || true
    fi
  done
  exit 0
fi

while IFS='=' read -r name pid; do
  [ -z "$pid" ] && continue
  if kill -0 "$pid" 2>/dev/null; then
    info "Stopping $name (PID: $pid)..."
    kill -TERM "$pid" 2>/dev/null || true
    for i in $(seq 1 5); do
      sleep 1
      kill -0 "$pid" 2>/dev/null || break
      if [ $i -eq 5 ]; then
        warn "$name did not respond to SIGTERM, sending SIGKILL"
        kill -9 "$pid" 2>/dev/null || true
      fi
    done
    info "$name stopped"
  else
    warn "$name (PID: $pid) is not running"
  fi
done < "$PID_FILE"

rm -f "$PID_FILE"
info "All services stopped"
