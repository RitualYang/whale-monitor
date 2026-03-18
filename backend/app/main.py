from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .clients import SolanaClient, ZanEthClient
from .config import ChainConfig, chain_configs, settings
from .detector import parse_eth_whale_transfers, parse_solana_whale_transfers
from .price import PriceClient
from .schemas import ChainHealth, HealthResponse, WhaleEvent
from .store import EventStore
from .subscribers import build_subscriber
from .subscribers.base import BaseSubscriber

# ── logging with timestamps ───────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(levelname)-5s %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _mask_url(url: str) -> str:
    """Mask API key in URL for safe logging: keep first 4 and last 4 chars."""
    parts = url.rsplit("/", 1)
    if len(parts) == 2 and len(parts[1]) > 8:
        key = parts[1]
        return f"{parts[0]}/{key[:4]}****{key[-4:]}"
    return url


class WsHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast_event(self, event: WhaleEvent) -> None:
        if not self.clients:
            return
        data = json.dumps(event.model_dump(mode="json"))
        stale: list[WebSocket] = []
        for ws in self.clients:
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


store = EventStore(limit=settings.event_store_limit)
ws_hub = WsHub()

# Runtime state — keyed by chain name
subscribers: dict[str, BaseSubscriber] = {}
chain_sources: dict[str, str] = {}  # chain_name → "ws" | "polling"
poller_jobs: dict[str, str] = {}  # chain_name → scheduler job id

# Shared references set during lifespan, used by set_source
_scheduler: AsyncIOScheduler | None = None
_http_client: httpx.AsyncClient | None = None
_price_client: PriceClient | None = None


class SourceRequest(BaseModel):
    chain: str
    source: str  # "ws" or "polling"


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _scheduler, _http_client, _price_client

    http_client = httpx.AsyncClient()
    price_client = PriceClient(http_client, chain_configs)
    scheduler = AsyncIOScheduler()

    _http_client = http_client
    _price_client = price_client
    _scheduler = scheduler

    await price_client.refresh()

    for cfg in chain_configs:
        chain_sources[cfg.name] = cfg.source
        logger.info("[%s] type=%s source=%s ws=%s rpc=%s", cfg.name, cfg.chain_type, cfg.source, _mask_url(cfg.ws_url), _mask_url(cfg.rpc_url))

        # Always build WS/gRPC subscriber so it's available for runtime switching
        sub = build_subscriber(
            cfg=cfg,
            store=store,
            on_event=ws_hub.broadcast_event,
            price_getter=lambda a=cfg.asset: price_client.get(a),
        )
        if sub:
            subscribers[cfg.name] = sub
            # Only start if source is ws
            if cfg.source == "ws":
                sub.start()

        # HTTP polling (runs when source is "polling")
        if cfg.source == "polling":
            _add_poll_job(scheduler, cfg, http_client, price_client)

    # Price refresh every 20s
    scheduler.add_job(price_client.refresh, "interval", seconds=20)
    scheduler.start()

    try:
        yield
    finally:
        for sub in subscribers.values():
            sub.stop()
        scheduler.shutdown(wait=False)
        _scheduler = None
        _http_client = None
        _price_client = None
        await http_client.aclose()


def _add_poll_job(
    scheduler: AsyncIOScheduler,
    cfg: ChainConfig,
    http_client: httpx.AsyncClient,
    price_client: PriceClient,
) -> None:
    """Register a polling job for the given chain."""
    job_id = f"poll_{cfg.name}"

    # Skip if already running
    if scheduler.get_job(job_id):
        return

    if cfg.chain_type == "evm":
        client = ZanEthClient(http_client, cfg.rpc_url)

        async def poll_evm(c=client, cf=cfg, pc=price_client):
            await _poll_evm_chain(c, cf, pc)

        scheduler.add_job(
            poll_evm, "interval", seconds=cfg.poll_seconds,
            max_instances=1, coalesce=True, id=job_id,
        )
    elif cfg.chain_type == "solana":
        client = SolanaClient(http_client, cfg.rpc_url)

        async def poll_sol(c=client, cf=cfg, pc=price_client):
            await _poll_solana_chain(c, cf, pc)

        scheduler.add_job(
            poll_sol, "interval", seconds=cfg.poll_seconds,
            max_instances=1, coalesce=True, id=job_id,
        )

    poller_jobs[cfg.name] = job_id
    logger.info("[%s] polling enabled (every %ds)", cfg.name, cfg.poll_seconds)


def _remove_poll_job(chain_name: str) -> None:
    """Remove a polling job if it exists."""
    job_id = poller_jobs.pop(chain_name, None)
    if job_id and _scheduler:
        job = _scheduler.get_job(job_id)
        if job:
            job.remove()
            logger.info("[%s] polling disabled", chain_name)


# ── polling helpers ───────────────────────────────────────────────────────────

_latest_refs: dict[str, int | None] = {}  # chain_name → latest block/slot


async def _poll_evm_chain(
    client: ZanEthClient, cfg: ChainConfig, price_client: PriceClient,
) -> None:
    try:
        price = price_client.get(cfg.asset)
        if price <= 0:
            await price_client.refresh()
            price = price_client.get(cfg.asset)
        latest = await client.get_latest_block_number()
        prev = _latest_refs.get(cfg.name)
        if prev is None:
            _latest_refs[cfg.name] = latest - 1
            prev = latest - 1
        for block_num in range(prev + 1, latest + 1):
            block = await client.get_block_by_number(block_num)
            events = parse_eth_whale_transfers(block=block, eth_usd=price, cfg=cfg)
            await _publish(events)
            _latest_refs[cfg.name] = block_num
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] poll failed: %s", cfg.name, exc)


async def _poll_solana_chain(
    client: SolanaClient, cfg: ChainConfig, price_client: PriceClient,
) -> None:
    try:
        price = price_client.get(cfg.asset)
        if price <= 0:
            await price_client.refresh()
            price = price_client.get(cfg.asset)
        latest = await client.get_slot()
        prev = _latest_refs.get(cfg.name)
        if prev is None:
            _latest_refs[cfg.name] = latest - 1
            prev = latest - 1
            logger.info("[%s] JSON-RPC online, latest slot: %d", cfg.name, latest)
        start = prev + 1
        end = min(latest, start + 4)
        for slot in range(start, end + 1):
            block = await client.get_block(slot)
            events = parse_solana_whale_transfers(slot=slot, block=block, sol_usd=price, cfg=cfg)
            await _publish(events)
            _latest_refs[cfg.name] = slot
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] poll failed: %s", cfg.name, exc)


async def _publish(events: list[WhaleEvent]) -> None:
    for event in events:
        if store.add(event):
            logger.info(
                "[%s] whale: %.4f %s = $%.0f | %s",
                event.chain, event.amount, event.asset, event.usd_value, event.tx_hash[:16],
            )
            await ws_hub.broadcast_event(event)


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Multi-Chain Whale Transfer Monitor", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    chains: list[ChainHealth] = []
    for cfg in chain_configs:
        sub = subscribers.get(cfg.name)
        chains.append(ChainHealth(
            name=cfg.name,
            source=chain_sources.get(cfg.name, cfg.source),
            connected=sub.connected if sub else False,
            latest_ref=sub.latest_ref if sub else _latest_refs.get(cfg.name),
        ))
    return HealthResponse(status="ok", cached_events=store.size, chains=chains)


@app.post("/api/source")
async def set_source(req: SourceRequest) -> dict[str, Any]:
    """Switch a chain's preferred data source at runtime."""
    cfg = next((c for c in chain_configs if c.name == req.chain), None)
    if not cfg:
        return {"error": f"unknown chain: {req.chain}"}

    old = chain_sources.get(cfg.name, cfg.source)
    if req.source == old:
        return {"chain": cfg.name, "source": old, "changed": False}

    chain_sources[cfg.name] = req.source
    sub = subscribers.get(cfg.name)

    if req.source == "ws":
        # Stop polling, start WS subscriber
        _remove_poll_job(cfg.name)
        if sub:
            sub.start()
            logger.info("[%s] switched to WS", cfg.name)
    elif req.source == "polling":
        # Stop WS subscriber, start polling
        if sub:
            sub.stop()
        if _scheduler and _http_client and _price_client:
            _add_poll_job(_scheduler, cfg, _http_client, _price_client)
        logger.info("[%s] switched to polling", cfg.name)

    return {"chain": cfg.name, "source": req.source, "changed": True}


@app.get("/api/events", response_model=list[WhaleEvent])
async def list_events(limit: int = 100) -> list[WhaleEvent]:
    return store.list_events(limit=max(1, min(300, limit)))


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    await ws_hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_hub.disconnect(ws)
    except Exception:  # noqa: BLE001
        ws_hub.disconnect(ws)
