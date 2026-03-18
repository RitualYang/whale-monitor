# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Whale Monitor is a real-time multi-chain large asset transfer monitoring system for Ethereum and Solana. It tracks whale transactions (transfers exceeding a USD threshold) via ZAN node services and displays them on a React dashboard.

## Development Commands

### Quick Start
```bash
./start.sh   # Starts both backend (port 8000) and frontend (port 5173)
./stop.sh    # Stops all services
```

### Backend (Python/FastAPI)
```bash
cd backend
python3 -m venv ../.venv
../.venv/bin/pip install -r requirements.txt
bash setup_proto.sh  # Compile gRPC proto stubs (run once, or after proto changes)
../.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend (React/Vite)
```bash
cd frontend
npm install
npm run dev      # Dev server at http://localhost:5173
npm run build    # Production build to dist/
npm run preview  # Preview production build
```

### Configuration
Copy `backend/.env.example` to `backend/.env` and fill in:
- `ZAN_API_KEY` — primary ZAN node API key
- `CHAINS` — comma-separated chain names (e.g. `ethereum,solana`)
- Per-chain `<NAME>_*` env vars (see `.env.example`)

## Architecture

```
React Frontend (Vite, port 5173)
    ↕ REST /api/* + WebSocket /ws/events
FastAPI Backend (uvicorn, port 8000)
    ├── subscribers/           — pluggable per-chain data sources
    │   ├── EvmWsSubscriber    — EVM real-time via WebSocket newHeads
    │   ├── SolanaWsSubscriber — Solana real-time via WebSocket blockSubscribe
    │   └── GrpcSubscriber     — Solana via Yellowstone gRPC (optional)
    ├── build_subscriber()     — factory: ChainConfig → BaseSubscriber
    ├── HTTP polling           — fallback, scheduled in main.py via APScheduler
    ├── Detector               — parses blocks, identifies whale transfers (accepts ChainConfig)
    ├── EventStore             — in-memory deque (max 500), deduplicates by chain:tx_hash
    ├── WsHub                  — broadcasts new events to all connected frontend clients
    └── PriceClient            — Binance API, dynamic per configured assets (refreshed every 20s)
```

**Data flow:** Block data arrives via WS/gRPC/HTTP → Detector extracts large transfers → EventStore deduplicates and caches → WsHub broadcasts to frontend WebSocket clients.

**Dynamic chain config:** Chains are defined in `.env` via `CHAINS=ethereum,solana` with per-chain `<NAME>_*` env vars. Adding a new chain requires only `.env` changes.

**Dynamic source switching:** `POST /api/source { "chain": "ethereum", "source": "ws" }` toggles between WS and HTTP polling at runtime.

## Key Files

| File | Purpose |
|------|---------|
| `backend/app/main.py` | FastAPI app, lifespan, routes, WsHub, polling scheduler |
| `backend/app/config.py` | ChainConfig dataclass + Settings, dynamic chain parsing |
| `backend/app/schemas.py` | `WhaleEvent`, `ChainHealth`, `HealthResponse` |
| `backend/app/detector.py` | Block parsing and whale transfer detection (accepts ChainConfig) |
| `backend/app/store.py` | In-memory event cache with deduplication |
| `backend/app/price.py` | Dynamic PriceClient (Binance, auto-discovers assets from chain configs) |
| `backend/app/clients.py` | HTTP clients for Etherscan, ZAN ETH/SOL RPC |
| `backend/app/subscribers/__init__.py` | `build_subscriber` factory |
| `backend/app/subscribers/base.py` | `BaseSubscriber` ABC |
| `backend/app/subscribers/evm_ws.py` | EVM WebSocket subscriber |
| `backend/app/subscribers/solana_ws.py` | Solana WebSocket subscriber |
| `backend/app/subscribers/grpc.py` | Solana gRPC subscriber |
| `frontend/src/App.jsx` | Main dashboard component (dynamic chain toggles) |
| `frontend/src/api.js` | REST + WebSocket client functions |

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Service status, latest block/slot, connection state |
| GET | `/api/events?limit=100` | Historical whale events |
| POST | `/api/source` | Switch data source: `{ "chain": "ethereum", "source": "ws" }` |
| WS | `/ws/events` | Real-time event stream |

## Per-Chain Data Source Priority

Each chain's `<NAME>_SOURCE` env var determines the primary source:
1. `ws` — WebSocket real-time push (default)
2. `polling` — HTTP JSON-RPC polling fallback

Solana additionally supports gRPC Yellowstone when `SOLANA_GRPC_ENABLED=true`.

Legacy env vars (ZAN_ETH_WS_BASE, ZAN_SOL_WS_BASE, etc.) are still supported for backward compatibility when `CHAINS` is not set.

## Proto Generation

gRPC stubs in `backend/app/proto_gen/` are auto-generated from `backend/proto/*.proto`. Regenerate with:
```bash
cd backend && bash setup_proto.sh
```
