from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .clients import EtherscanClient, PriceClient, SolanaClient
from .config import settings
from .poller import ChainPoller
from .schemas import HealthResponse, WhaleEvent
from .sol_grpc import ZanSolanaGrpcSubscriber
from .store import EventStore


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
state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    http_client = httpx.AsyncClient()
    price_client = PriceClient(http_client)

    # JSON-RPC poller (Ethereum + Solana via ZAN, always available)
    poller = ChainPoller(
        eth_client=EtherscanClient(http_client, settings.etherscan_api_key),
        sol_client=SolanaClient(http_client, settings.zan_sol_rpc_url),
        price_client=price_client,
        store=store,
        on_event=ws_hub.broadcast_event,
    )

    # gRPC streaming subscriber (Solana upgrade path, optional)
    sol_grpc = ZanSolanaGrpcSubscriber(
        grpc_endpoint=settings.zan_grpc_endpoint,
        api_key=settings.zan_api_key,
        store=store,
        on_event=ws_hub.broadcast_event,
        sol_usd_getter=lambda: poller.prices.get("SOL", 0.0),
    )

    state["poller"] = poller
    state["sol_grpc"] = sol_grpc

    await poller.refresh_prices()
    poller.start()

    if settings.zan_grpc_enabled:
        sol_grpc.start()

    try:
        yield
    finally:
        if settings.zan_grpc_enabled:
            sol_grpc.stop()
        poller.stop()
        await http_client.aclose()


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
    poller: ChainPoller = state["poller"]
    sol_grpc: ZanSolanaGrpcSubscriber = state["sol_grpc"]
    return HealthResponse(
        status="ok",
        latest_eth_block=poller.latest_eth_block,
        latest_sol_slot=poller.latest_sol_slot or sol_grpc.latest_slot,
        cached_events=store.size,
    )


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
