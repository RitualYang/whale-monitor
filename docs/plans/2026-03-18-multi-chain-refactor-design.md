# Multi-Chain Refactor Design

Date: 2026-03-18

## Problem

Current code hardcodes ETH and SOL at every layer — config fields, poller methods, WS classes, health API, and frontend toggles. Adding a new chain requires changes across 6+ files.

## Goal

Adding a new chain requires only `.env` changes. No code modifications.

## Data Source Clarification

Two source types:
- **ws** — WebSocket real-time push
- **grpc** — HTTP/RPC polling (previously called "polling", renamed to grpc for clarity)

## Solution: Method B — Chain Config List + chain_type Adapter

### .env Structure

```env
ZAN_API_KEY=xxx
CHAINS=ethereum,solana

ETHEREUM_CHAIN_TYPE=evm
ETHEREUM_WS_URL=wss://api.zan.top/node/ws/v1/eth/mainnet
ETHEREUM_RPC_URL=https://api.zan.top/node/v1/eth/mainnet
ETHEREUM_SOURCE=ws
ETHEREUM_USD_THRESHOLD=100000
ETHEREUM_ASSET=ETH
ETHEREUM_EXPLORER=https://etherscan.io/tx/

SOLANA_CHAIN_TYPE=solana
SOLANA_WS_URL=wss://api.zan.top/node/ws/v1/solana/mainnet
SOLANA_RPC_URL=https://api.zan.top/node/v1/solana/mainnet
SOLANA_SOURCE=ws
SOLANA_USD_THRESHOLD=100000
SOLANA_ASSET=SOL
SOLANA_EXPLORER=https://solscan.io/tx/
```

### Backend Changes

**config.py**
- Add `ChainConfig` dataclass with fields: `name`, `chain_type`, `ws_url`, `rpc_url`, `source`, `usd_threshold`, `asset`, `explorer`
- `Settings` reads `CHAINS` env var, dynamically builds `list[ChainConfig]`
- Remove all `zan_eth_*`, `zan_sol_*`, `zan_grpc_*` hardcoded fields

**subscribers/** (new directory)
- `base.py` — `BaseSubscriber` abstract class with `start()`, `stop()`, `connected: bool`, `latest_ref: int | None`
- `evm_ws.py` — `EvmWsSubscriber` (generalized from `eth_ws.py`), accepts `ChainConfig`
- `solana_ws.py` — `SolanaWsSubscriber` (generalized from `sol_ws.py`), accepts `ChainConfig`
- `grpc.py` — `GrpcSubscriber` (generalized from `sol_grpc.py`), accepts `ChainConfig`

**Factory function** in `subscribers/__init__.py`:
```python
def build_subscriber(cfg: ChainConfig, ...) -> BaseSubscriber:
    if cfg.source == "ws" and cfg.chain_type == "evm":
        return EvmWsSubscriber(cfg, ...)
    if cfg.source == "ws" and cfg.chain_type == "solana":
        return SolanaWsSubscriber(cfg, ...)
    if cfg.source == "grpc":
        return GrpcSubscriber(cfg, ...)
```

**main.py lifespan**
```python
subscribers: dict[str, BaseSubscriber] = {}
for cfg in settings.chains:
    sub = build_subscriber(cfg, store, ws_hub.broadcast_event, price_getter)
    subscribers[cfg.name] = sub
    sub.start()
```

**schemas.py**
- `chain: str` (was `Literal["ethereum", "solana"]`)
- `asset: str` (was `Literal["ETH", "SOL"]`)

**Health API response**
```json
{
  "status": "ok",
  "cached_events": 42,
  "chains": [
    {"name": "ethereum", "source": "ws", "connected": true, "latest_ref": 12345},
    {"name": "solana",   "source": "grpc", "connected": true, "latest_ref": 99999}
  ]
}
```

**Source switch API**
```
POST /api/source
{ "chain": "ethereum", "source": "ws" }
```

**clients.py / detector.py**
- `PriceClient` fetches prices for all assets collected from chain configs
- `detector.py` parse functions accept `ChainConfig` instead of hardcoded asset names

### Frontend Changes

**api.js** — no structural change, health response shape changes

**App.jsx**
- `health.chains[]` drives `ChainToggle` rendering dynamically
- Remove hardcoded `chain === "ETH"` branches
- `handleSwitch(chainName, source)` sends `{ chain: chainName, source }`

### Files to Delete
- `backend/app/eth_ws.py`
- `backend/app/sol_ws.py`
- `backend/app/sol_grpc.py`
- `backend/app/poller.py`

### Files to Create
- `backend/app/subscribers/__init__.py`
- `backend/app/subscribers/base.py`
- `backend/app/subscribers/evm_ws.py`
- `backend/app/subscribers/solana_ws.py`
- `backend/app/subscribers/grpc.py`
- `backend/app/price.py` (extracted from clients.py)
