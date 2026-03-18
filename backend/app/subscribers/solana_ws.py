"""Solana WebSocket subscriber — generalized from sol_ws.py.
Subscribes to blockSubscribe, detects whale transfers via balance deltas.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from ..schemas import WhaleEvent
from .base import BaseSubscriber

logger = logging.getLogger(__name__)

try:
    import websockets.asyncio.client as _ws_client  # noqa: F401

    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False


def _detect_whale_from_block_tx(
    tx: dict[str, Any],
    slot: int,
    sol_usd: float,
    threshold_usd: float,
    chain_name: str,
    asset: str,
    explorer: str,
) -> WhaleEvent | None:
    try:
        meta = tx.get("meta") or {}
        pre: list[int] = meta.get("preBalances") or []
        post: list[int] = meta.get("postBalances") or []
        if len(pre) < 2 or len(post) < 2:
            return None

        n = min(len(pre), len(post))
        deltas = [post[i] - pre[i] for i in range(n)]
        max_gain = max(deltas)
        if max_gain <= 0:
            return None

        sol_amount = max_gain / 1e9
        usd_value = sol_amount * sol_usd
        if usd_value < threshold_usd:
            return None

        tx_obj = tx.get("transaction") or {}
        signatures: list[str] = tx_obj.get("signatures") or []
        sig = signatures[0] if signatures else "unknown"

        raw_keys = []
        msg = tx_obj.get("message") or {}
        raw_keys = msg.get("accountKeys") or []

        def _key(k: Any) -> str:
            if isinstance(k, dict):
                return k.get("pubkey", "unknown")
            return str(k)

        gain_idx = deltas.index(max_gain)
        loss_idx = deltas.index(min(deltas))

        to_addr = _key(raw_keys[gain_idx]) if gain_idx < len(raw_keys) else "unknown"
        from_addr = _key(raw_keys[loss_idx]) if loss_idx < len(raw_keys) else "unknown"

        return WhaleEvent(
            chain=chain_name,
            tx_hash=sig,
            block_ref=str(slot),
            timestamp=datetime.now(timezone.utc),
            from_address=from_addr,
            to_address=to_addr,
            asset=asset,
            amount=sol_amount,
            usd_value=usd_value,
            unit_price=sol_usd,
            explorer_url=f"{explorer}{sig}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("WS tx parse error: %s", exc)
        return None


class SolanaWsSubscriber(BaseSubscriber):
    WS_MAX_SIZE = 32 * 1024 * 1024

    def start(self) -> None:
        if not _WS_AVAILABLE:
            logger.error("[%s] websockets not installed.", self.cfg.name)
            return
        super().start()
        logger.info("[%s] Solana WS subscriber started -> %s", self.cfg.name, self.cfg.ws_url)

    def _on_disconnect(self, exc: Exception, retry_delay: float) -> None:
        logger.warning("[%s] Solana WS disconnected: %s – retry in %.0fs", self.cfg.name, exc, retry_delay)

    async def _stream(self) -> None:
        import websockets.asyncio.client as ws_client

        sub_request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "blockSubscribe",
            "params": [
                "all",
                {
                    "commitment": "confirmed",
                    "encoding": "json",
                    "transactionDetails": "full",
                    "maxSupportedTransactionVersion": 0,
                    "showRewards": False,
                },
            ],
        })

        async with ws_client.connect(
            self.cfg.ws_url,
            max_size=self.WS_MAX_SIZE,
            ping_interval=30,
            ping_timeout=20,
            open_timeout=20,
        ) as ws:
            await ws.send(sub_request)
            logger.info("[%s] blockSubscribe sent, awaiting subscription ID…", self.cfg.name)

            async for raw in ws:
                if not self._running:
                    break
                await self._handle_message(raw)

        self.connected = False

    async def _handle_message(self, raw: str | bytes) -> None:
        try:
            data: dict[str, Any] = json.loads(raw)
        except Exception:
            return

        if "result" in data and "id" in data:
            self.connected = True
            logger.info("[%s] WS subscription confirmed (id=%s)", self.cfg.name, data.get("result"))
            return

        if data.get("method") != "blockNotification":
            return

        params = data.get("params") or {}
        result = params.get("result") or {}
        value = result.get("value") or {}
        slot: int = value.get("slot", 0)
        block: dict[str, Any] = value.get("block") or {}
        transactions: list[dict[str, Any]] = block.get("transactions") or []

        self.latest_ref = slot
        self.total_seen += len(transactions)

        price = self.price_getter()
        if price <= 0:
            return

        for tx in transactions:
            event = _detect_whale_from_block_tx(
                tx=tx,
                slot=slot,
                sol_usd=price,
                threshold_usd=self.cfg.usd_threshold,
                chain_name=self.cfg.name,
                asset=self.cfg.asset,
                explorer=self.cfg.explorer,
            )
            if event and self.store.add(event):
                self.total_whale += 1
                logger.info(
                    "[%s] whale: %.2f %s = $%.0f | %s",
                    self.cfg.name,
                    event.amount,
                    event.asset,
                    event.usd_value,
                    event.tx_hash[:16],
                )
                await self.on_event(event)
