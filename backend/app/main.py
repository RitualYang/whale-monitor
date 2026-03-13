from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .clients import EtherscanClient, PriceClient, SolanaClient, ZanEthClient
from .config import settings
from .eth_ws import ZanEthWsSubscriber
from .poller import ChainPoller
from .schemas import HealthResponse, WhaleEvent
from .sol_grpc import ZanSolanaGrpcSubscriber
from .sol_ws import ZanSolanaWsSubscriber
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


class SourceRequest(BaseModel):
    eth_source: Literal["ws", "polling"] | None = None
    sol_source: Literal["ws", "polling"] | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    http_client = httpx.AsyncClient()
    price_client = PriceClient(http_client)

    # Choose ETH HTTP client: ZAN JSON-RPC (preferred) or Etherscan (legacy)
    eth_http_client = ZanEthClient(http_client, settings.resolved_eth_rpc_url())

    # JSON-RPC poller — ETH and SOL HTTP polling (disabled per-chain when WS is active)
    poller = ChainPoller(
        eth_client=eth_http_client,
        sol_client=SolanaClient(http_client, settings.resolved_sol_rpc_url()),
        price_client=price_client,
        store=store,
        on_event=ws_hub.broadcast_event,
        eth_polling_enabled=not settings.zan_eth_ws_enabled,
        sol_polling_enabled=not settings.zan_ws_enabled,
    )

    # ETH Priority 1 — WebSocket newHeads (real-time, no extra permissions)
    eth_ws = ZanEthWsSubscriber(
        ws_url=settings.resolved_eth_ws_url(),
        store=store,
        on_event=ws_hub.broadcast_event,
        eth_usd_getter=lambda: poller.prices.get("ETH", 0.0),
    )

    # SOL Priority 1 — WebSocket blockSubscribe (real-time, no extra permissions)
    sol_ws = ZanSolanaWsSubscriber(
        ws_url=settings.resolved_sol_ws_url(),
        store=store,
        on_event=ws_hub.broadcast_event,
        sol_usd_getter=lambda: poller.prices.get("SOL", 0.0),
    )

    # SOL Priority 2 — gRPC Yellowstone (needs ZAN dashboard activation)
    sol_grpc = ZanSolanaGrpcSubscriber(
        grpc_endpoint=settings.zan_grpc_endpoint,
        api_key=settings.zan_api_key,
        store=store,
        on_event=ws_hub.broadcast_event,
        sol_usd_getter=lambda: poller.prices.get("SOL", 0.0),
    )

    state["poller"] = poller
    state["eth_ws"] = eth_ws
    state["sol_ws"] = sol_ws
    state["sol_grpc"] = sol_grpc
    state["eth_source"] = "ws" if settings.zan_eth_ws_enabled else "polling"
    state["sol_source"] = "ws" if settings.zan_ws_enabled else "polling"

    await poller.refresh_prices()
    poller.start()

    # WS 始终保持监听，只由配置开关决定是否启用
    if settings.zan_eth_ws_enabled:
        eth_ws.start()

    if settings.zan_ws_enabled:
        sol_ws.start()

    if settings.zan_grpc_enabled:
        sol_grpc.start()

    try:
        yield
    finally:
        if settings.zan_eth_ws_enabled:
            eth_ws.stop()
        if settings.zan_ws_enabled:
            sol_ws.stop()
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
    eth_ws: ZanEthWsSubscriber = state["eth_ws"]
    sol_ws: ZanSolanaWsSubscriber = state["sol_ws"]
    sol_grpc: ZanSolanaGrpcSubscriber = state["sol_grpc"]
    latest_eth = eth_ws.latest_block or poller.latest_eth_block
    latest_sol = sol_ws.latest_slot or sol_grpc.latest_slot or poller.latest_sol_slot
    return HealthResponse(
        status="ok",
        latest_eth_block=latest_eth,
        latest_sol_slot=latest_sol,
        cached_events=store.size,
        eth_source=state["eth_source"],
        eth_ws_connected=eth_ws.connected,
        sol_source=state["sol_source"],
        sol_ws_connected=sol_ws.connected,
    )


@app.post("/api/source")
async def set_source(req: SourceRequest) -> dict[str, Any]:
    """Switch ETH or SOL *preferred*数据源（仅控制是否开启 HTTP 轮询，WS 始终保持监听）."""
    poller: ChainPoller = state["poller"]
    eth_ws: ZanEthWsSubscriber = state["eth_ws"]
    sol_ws: ZanSolanaWsSubscriber = state["sol_ws"]
    changed: list[str] = []

    if req.eth_source and req.eth_source != state["eth_source"]:
        if req.eth_source == "ws":
            # WS 始终保持监听，这里只关闭 ETH HTTP 轮询以减少请求
            poller.disable_eth_polling()
        else:
            # 选择轮询时，开启 ETH HTTP 轮询，WS 仍继续作为备份通道
            poller.enable_eth_polling()
        state["eth_source"] = req.eth_source
        changed.append(f"eth→{req.eth_source}")

    if req.sol_source and req.sol_source != state["sol_source"]:
        if req.sol_source == "ws":
            poller.disable_sol_polling()
        else:
            poller.enable_sol_polling()
        state["sol_source"] = req.sol_source
        changed.append(f"sol→{req.sol_source}")

    return {
        "eth_source": state["eth_source"],
        "sol_source": state["sol_source"],
        "changed": changed or ["no change"],
    }


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
