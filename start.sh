#!/usr/bin/env bash
# 一键启动：后端 FastAPI + 前端 Vite
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV_PY="$ROOT/.venv/bin/python"
PID_FILE="$ROOT/.run/pids"
LOG_DIR="$ROOT/.run/logs"

# ── 颜色 ──────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[start]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
error() { echo -e "${RED}[error]${NC} $*"; }

mkdir -p "$ROOT/.run/logs"

# ── 检查是否已在运行 ──────────────────────────────────────────────────────────
if [ -f "$PID_FILE" ]; then
  warn "检测到 .run/pids 文件，可能已在运行。请先执行 ./stop.sh"
  exit 1
fi

# ── 检查 .env ─────────────────────────────────────────────────────────────────
if [ ! -f "$BACKEND/.env" ]; then
  warn "未找到 $BACKEND/.env，使用 .env.example 作为默认配置"
  cp "$BACKEND/.env.example" "$BACKEND/.env"
fi

# ── 检查 Python 虚拟环境 ──────────────────────────────────────────────────────
if [ ! -f "$VENV_PY" ]; then
  info "创建 Python 虚拟环境..."
  python3 -m venv "$ROOT/.venv"
fi

info "安装/更新 Python 依赖..."
"$VENV_PY" -m pip install -q -r "$BACKEND/requirements.txt"

# ── 检查 proto 存根 ───────────────────────────────────────────────────────────
if [ ! -f "$BACKEND/app/proto_gen/geyser_pb2.py" ]; then
  info "生成 gRPC proto 存根..."
  VENV_PYTHON="$VENV_PY" bash "$BACKEND/setup_proto.sh"
fi

# ── 检查前端 node_modules ─────────────────────────────────────────────────────
if [ ! -d "$FRONTEND/node_modules" ]; then
  info "安装前端依赖..."
  cd "$FRONTEND" && npm install --silent
fi

# ── 启动后端 ──────────────────────────────────────────────────────────────────
info "启动后端 FastAPI (端口 8000)..."
cd "$BACKEND"
"$VENV_PY" -m uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 \
  --log-level info \
  >> "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
echo "backend=$BACKEND_PID" >> "$PID_FILE"
info "后端 PID: $BACKEND_PID  日志: .run/logs/backend.log"

# ── 等待后端就绪 ──────────────────────────────────────────────────────────────
info "等待后端就绪..."
for i in $(seq 1 15); do
  if curl -s http://127.0.0.1:8000/api/health > /dev/null 2>&1; then
    info "后端已就绪 ✓"
    break
  fi
  sleep 1
  if [ $i -eq 15 ]; then
    error "后端启动超时，请查看 .run/logs/backend.log"
    cat "$LOG_DIR/backend.log" | tail -20
    exit 1
  fi
done

# ── 启动前端 ──────────────────────────────────────────────────────────────────
info "启动前端 Vite (端口 5173)..."
cd "$FRONTEND"
npm run dev \
  >> "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo "frontend=$FRONTEND_PID" >> "$PID_FILE"
info "前端 PID: $FRONTEND_PID  日志: .run/logs/frontend.log"

# ── 完成 ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  监控面板已启动                              ${NC}"
echo -e "${GREEN}  后端 API : http://localhost:8000/api/health ${NC}"
echo -e "${GREEN}  前端面板 : http://localhost:5173             ${NC}"
echo -e "${GREEN}  停止服务 : ./stop.sh                        ${NC}"
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
