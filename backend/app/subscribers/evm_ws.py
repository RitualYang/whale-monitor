"""EVM WebSocket subscriber — generalized from eth_ws.py.
Subscribes to newHeads, fetches full blocks, detects whale transfers.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..detector import parse_eth_whale_transfers
from .base import BaseSubscriber

logger = logging.getLogger(__name__)

try:
    import websockets.asyncio.client as _ws_client  # noqa: F401

    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False


class EvmWsSubscriber(BaseSubscriber):
    WS_MAX_SIZE = 8 * 1024 * 1024

    def start(self) -> None:
        if not _WS_AVAILABLE:
            logger.error("[%s] websockets not installed.", self.cfg.name)
            return
        super().start()
        logger.info("[%s] EVM WS subscriber started -> %s", self.cfg.name, self.cfg.ws_url)

    def _on_disconnect(self, exc: Exception, retry_delay: float) -> None:
        logger.warning("[%s] EVM WS disconnected: %s – retry in %.0fs", self.cfg.name, exc, retry_delay)

    async def _stream(self) -> None:
        import websockets.asyncio.client as ws_client

        self._send_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future] = {}
        self._req_counter = 0
        self._ws: Any = None

        async with ws_client.connect(
            self.cfg.ws_url,
            max_size=self.WS_MAX_SIZE,
            ping_interval=30,
            ping_timeout=20,
            open_timeout=20,
        ) as ws:
            self._ws = ws
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "id": 0,
                "method": "eth_subscribe",
                "params": ["newHeads"],
            }))
            logger.info("[%s] newHeads subscription sent…", self.cfg.name)

            sub_id: str | None = None

            try:
                async for raw in ws:
                    if not self._running:
                        break
                    data: dict[str, Any] = json.loads(raw)

                    # RPC response
                    if "id" in data:
                        req_id: int = data["id"]
                        if req_id == 0:
                            sub_id = data.get("result")
                            if sub_id:
                                self.connected = True
                                logger.info("[%s] newHeads subscribed (sub=%s)", self.cfg.name, sub_id)
                        else:
                            future = self._pending.pop(req_id, None)
                            if future and not future.done():
                                err = data.get("error")
                                if err:
                                    future.set_exception(RuntimeError(str(err)))
                                else:
                                    future.set_result(data.get("result"))
                        continue

                    # Subscription notification
                    if data.get("method") == "eth_subscription":
                        params = data.get("params") or {}
                        if params.get("subscription") == sub_id:
                            header = params.get("result") or {}
                            self.total_seen += 1
                            asyncio.create_task(self._process_block(header))
            finally:
                self._ws = None
                self.connected = False
                for f in self._pending.values():
                    if not f.done():
                        f.cancel()
                self._pending.clear()

    async def _rpc(self, method: str, params: list[Any]) -> Any:
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

            self.latest_ref = int(block_number_hex, 16)
            price = self.price_getter()
            if price <= 0:
                return

            events = parse_eth_whale_transfers(
                block=block,
                eth_usd=price,
                cfg=self.cfg,
            )
            for event in events:
                if self.store.add(event):
                    self.total_whale += 1
                    logger.info(
                        "[%s] whale: %.4f %s = $%.0f | %s",
                        self.cfg.name,
                        event.amount,
                        event.asset,
                        event.usd_value,
                        event.tx_hash[:16],
                    )
                    await self.on_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] block processing error (%s): %s", self.cfg.name, block_hash[:10], exc)
