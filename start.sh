#!/usr/bin/env bash
# Start backend (FastAPI) + frontend (Vite)
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV_PY="$ROOT/.venv/bin/python"
PID_FILE="$ROOT/.run/pids"
LOG_DIR="$ROOT/.run/logs"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[start]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC}  $*"; }
error() { echo -e "${RED}[error]${NC} $*"; }

mkdir -p "$ROOT/.run/logs"

# ── Check if already running ──────────────────────────────────────────────────
if [ -f "$PID_FILE" ]; then
  warn "Found .run/pids — services may already be running. Run ./stop.sh first."
  exit 1
fi

# ── Check .env ────────────────────────────────────────────────────────────────
if [ ! -f "$BACKEND/.env" ]; then
  warn "$BACKEND/.env not found, copying from .env.example"
  cp "$BACKEND/.env.example" "$BACKEND/.env"
fi

# ── Python venv ───────────────────────────────────────────────────────────────
if [ ! -f "$VENV_PY" ]; then
  info "Creating Python virtual environment..."
  python3 -m venv "$ROOT/.venv"
fi

info "Installing Python dependencies..."
"$VENV_PY" -m pip install -q -r "$BACKEND/requirements.txt"

# ── Proto stubs ───────────────────────────────────────────────────────────────
if [ ! -f "$BACKEND/app/proto_gen/geyser_pb2.py" ]; then
  info "Generating gRPC proto stubs..."
  VENV_PYTHON="$VENV_PY" bash "$BACKEND/setup_proto.sh"
fi

# ── Frontend node_modules ─────────────────────────────────────────────────────
if [ ! -d "$FRONTEND/node_modules" ]; then
  info "Installing frontend dependencies..."
  cd "$FRONTEND" && npm install --silent
fi

# ── Start backend ─────────────────────────────────────────────────────────────
info "Starting backend (port 8000)..."
cd "$BACKEND"
"$VENV_PY" -m uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 \
  --log-level info \
  >> "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
echo "backend=$BACKEND_PID" >> "$PID_FILE"
info "Backend PID: $BACKEND_PID  Log: .run/logs/backend.log"

# ── Wait for backend ready ────────────────────────────────────────────────────
info "Waiting for backend..."
for i in $(seq 1 15); do
  if curl -s http://127.0.0.1:8000/api/health > /dev/null 2>&1; then
    info "Backend ready"
    break
  fi
  sleep 1
  if [ $i -eq 15 ]; then
    error "Backend startup timed out. Check .run/logs/backend.log"
    cat "$LOG_DIR/backend.log" | tail -20
    exit 1
  fi
done

# ── Start frontend ────────────────────────────────────────────────────────────
info "Starting frontend (port 5173)..."
cd "$FRONTEND"
npm run dev \
  >> "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo "frontend=$FRONTEND_PID" >> "$PID_FILE"
info "Frontend PID: $FRONTEND_PID  Log: .run/logs/frontend.log"

echo ""
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Whale Monitor started                       ${NC}"
echo -e "${GREEN}  API    : http://localhost:8000/api/health    ${NC}"
echo -e "${GREEN}  UI     : http://localhost:5173               ${NC}"
echo -e "${GREEN}  Stop   : ./stop.sh                          ${NC}"
echo -e "${GREEN}══════════════════════════════════════════════${NC}"
