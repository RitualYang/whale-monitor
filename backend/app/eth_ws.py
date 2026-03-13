"""
ZAN Ethereum WebSocket subscriber.

Flow:
  1. Connect to ZAN ETH WS endpoint.
  2. Subscribe to "newHeads" — receive a notification on every new block header.
  3. For each new head, fetch the full block (with transactions) via
     eth_getBlockByHash sent over the same WS connection.
  4. Detect whale transfers using the same parse_eth_whale_transfers logic
     already used by the HTTP poller.

Priority: this is the PRIMARY Ethereum data source when ZAN_ETH_WS_ENABLED=true.
Falls back to Etherscan/ZAN HTTP polling automatically.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Callable

from .config import settings
from .detector import parse_eth_whale_transfers

if TYPE_CHECKING:
    from .store import EventStore

logger = logging.getLogger(__name__)

try:
    import websockets.asyncio.client as _ws_client

    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False


class ZanEthWsSubscriber:
    """
    Real-time Ethereum block subscriber via ZAN WebSocket (eth_subscribe newHeads).
    Fetches full blocks over the same connection using standard JSON-RPC calls.
    """

    WS_MAX_SIZE = 8 * 1024 * 1024  # 8 MB

    def __init__(
        self,
        ws_url: str,
        store: "EventStore",
        on_event: Callable,
        eth_usd_getter: Callable[[], float],
    ) -> None:
        self.ws_url = ws_url
        self.store = store
        self.on_event = on_event
        self.eth_usd_getter = eth_usd_getter
        self._task: asyncio.Task | None = None
        self._running = False
        self.latest_block: int | None = None
        self.total_seen: int = 0
        self.total_whale: int = 0
        self.connected: bool = False

        # Bidirectional RPC state
        self._ws: Any = None
        self._req_counter: int = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._send_lock: asyncio.Lock | None = None  # created inside the event loop

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not _WS_AVAILABLE:
            logger.error("websockets library not installed. Cannot start ETH WS.")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_with_retry())
        logger.info("ETH WS subscriber started -> %s", self.ws_url)

    def stop(self) -> None:
        self._running = False
        self.connected = False
        if self._task and not self._task.done():
            self._task.cancel()

    # ── internal ───────────────────────────────────────────────────────────────

    async def _run_with_retry(self) -> None:
        retry_delay = 2.0
        while self._running:
            try:
                await self._stream()
                retry_delay = 2.0
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                self.connected = False
                logger.warning(
                    "ETH WS disconnected: %s – retry in %.0fs", exc, retry_delay
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60.0)

    async def _stream(self) -> None:
        import websockets.asyncio.client as ws_client

        self._send_lock = asyncio.Lock()
        self._pending.clear()
        self._req_counter = 0

        async with ws_client.connect(
            self.ws_url,
            max_size=self.WS_MAX_SIZE,
            ping_interval=30,
            ping_timeout=20,
            open_timeout=20,
        ) as ws:
            self._ws = ws
            # Subscribe to newHeads (id=0 is reserved for the subscription ACK)
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "id": 0,
                "method": "eth_subscribe",
                "params": ["newHeads"],
            }))
            logger.info("ETH WS newHeads subscription sent…")

            sub_id: str | None = None

            try:
                async for raw in ws:
                    if not self._running:
                        break
                    data: dict[str, Any] = json.loads(raw)

                    # ── RPC response (reply to a getBlockByHash or subscribe call)
                    if "id" in data:
                        req_id: int = data["id"]
                        if req_id == 0:
                            sub_id = data.get("result")
                            if sub_id:
                                self.connected = True
                                logger.info("ETH WS newHeads subscribed (sub=%s)", sub_id)
                        else:
                            future = self._pending.pop(req_id, None)
                            if future and not future.done():
                                err = data.get("error")
                                if err:
                                    future.set_exception(RuntimeError(str(err)))
                                else:
                                    future.set_result(data.get("result"))
                        continue

                    # ── Subscription notification
                    if data.get("method") == "eth_subscription":
                        params = data.get("params") or {}
                        if params.get("subscription") == sub_id:
                            header = params.get("result") or {}
                            self.total_seen += 1
                            asyncio.create_task(self._process_block(header))
            finally:
                self._ws = None
                self.connected = False
                # Cancel any pending RPC futures so callers don't hang
                for f in self._pending.values():
                    if not f.done():
                        f.cancel()
                self._pending.clear()

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        """Send a JSON-RPC call over the active WS and await its response."""
        if self._ws is None or self._send_lock is None:
            raise RuntimeError("WS not connected")
        async with self._send_lock:
            self._req_counter += 1
            req_id = self._req_counter
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            self._pending[req_id] = future
            await self._ws.send(json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            }))
        return await asyncio.wait_for(future, timeout=30)

    async def _process_block(self, header: dict[str, Any]) -> None:
        block_hash: str = header.get("hash", "")
        block_number_hex: str = header.get("number", "0x0")
        if not block_hash:
            return
        try:
            block = await self._rpc("eth_getBlockByHash", [block_hash, True])
            if not block:
                return

            self.latest_block = int(block_number_hex, 16)
            eth_usd = self.eth_usd_getter()
            if eth_usd <= 0:
                return

            events = parse_eth_whale_transfers(
                block=block,
                eth_usd=eth_usd,
                threshold_usd=settings.eth_usd_threshold,
            )
            for event in events:
                if self.store.add(event):
                    self.total_whale += 1
                    logger.info(
                        "ETH WS whale: %.4f ETH = $%.0f | %s",
                        event.amount,
                        event.usd_value,
                        event.tx_hash[:16],
                    )
                    await self.on_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("ETH WS block processing error (block=%s): %s", block_hash[:10], exc)
