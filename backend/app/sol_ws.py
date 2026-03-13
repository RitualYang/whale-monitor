"""
ZAN Solana WebSocket subscriber using blockSubscribe (Solana pubsub).
Streams confirmed blocks in real-time and emits WhaleEvent when a
single SOL balance delta >= configured USD threshold.

Priority: this is the PRIMARY Solana data source when available.
Falls back to gRPC or JSON-RPC polling automatically.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from .config import settings
from .schemas import WhaleEvent

if TYPE_CHECKING:
    from .store import EventStore

logger = logging.getLogger(__name__)

try:
    import websockets
    import websockets.asyncio.client as _ws_client

    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    logger.warning("websockets not installed. Run: pip install websockets")


def _detect_whale_from_block_tx(
    tx: dict[str, Any],
    slot: int,
    sol_usd: float,
    threshold_usd: float,
) -> WhaleEvent | None:
    """
    Parse a single transaction from a blockNotification and check
    if any account received SOL above the threshold.
    """
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

        # Extract tx signature and account keys
        tx_obj = tx.get("transaction") or {}
        signatures: list[str] = tx_obj.get("signatures") or []
        sig = signatures[0] if signatures else "unknown"

        # Account keys may be plain strings or {"pubkey": ..., "signer": ...} dicts
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
            chain="solana",
            tx_hash=sig,
            block_ref=str(slot),
            timestamp=datetime.now(timezone.utc),
            from_address=from_addr,
            to_address=to_addr,
            asset="SOL",
            amount=sol_amount,
            usd_value=usd_value,
            explorer_url=f"https://solscan.io/tx/{sig}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("WS tx parse error: %s", exc)
        return None


class ZanSolanaWsSubscriber:
    """
    Real-time Solana block subscriber via ZAN WebSocket (blockSubscribe).
    This is the highest-priority Solana data source.
    """

    WS_MAX_SIZE = 32 * 1024 * 1024  # 32 MB – blocks can be ~7 MB

    def __init__(
        self,
        ws_url: str,
        store: "EventStore",
        on_event: Callable,
        sol_usd_getter: Callable[[], float],
    ) -> None:
        self.ws_url = ws_url
        self.store = store
        self.on_event = on_event
        self.sol_usd_getter = sol_usd_getter
        self._task: asyncio.Task | None = None
        self._running = False
        self.latest_slot: int | None = None
        self.total_seen: int = 0
        self.total_whale: int = 0
        self.connected: bool = False

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not _WS_AVAILABLE:
            logger.error("websockets library not installed. Cannot start WS subscriber.")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_with_retry())
        logger.info("Solana WS subscriber started -> %s", self.ws_url)

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
                    "Solana WS disconnected: %s – retry in %.0fs", exc, retry_delay
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60.0)

    async def _stream(self) -> None:
        import websockets.asyncio.client as ws_client  # local import for lazy load

        sub_request = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "blockSubscribe",
            "params": [
                "all",
                {
                    "commitment": "confirmed",
                    "encoding": "jsonParsed",
                    "transactionDetails": "full",
                    "maxSupportedTransactionVersion": 0,
                    "showRewards": False,
                },
            ],
        })

        async with ws_client.connect(
            self.ws_url,
            max_size=self.WS_MAX_SIZE,
            ping_interval=30,
            ping_timeout=20,
            open_timeout=20,
        ) as ws:
            await ws.send(sub_request)
            self.connected = True
            logger.info("Solana WS blockSubscribe sent, awaiting subscription ID…")

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

        # Subscription confirm
        if "result" in data and "id" in data:
            logger.info("Solana WS subscription confirmed (id=%s)", data.get("result"))
            return

        if data.get("method") != "blockNotification":
            return

        params = data.get("params") or {}
        result = params.get("result") or {}
        value = result.get("value") or {}
        slot: int = value.get("slot", 0)
        block: dict[str, Any] = value.get("block") or {}
        transactions: list[dict[str, Any]] = block.get("transactions") or []

        self.latest_slot = slot
        self.total_seen += len(transactions)

        sol_usd = self.sol_usd_getter()
        if sol_usd <= 0:
            return

        for tx in transactions:
            event = _detect_whale_from_block_tx(
                tx=tx,
                slot=slot,
                sol_usd=sol_usd,
                threshold_usd=settings.eth_usd_threshold,
            )
            if event and self.store.add(event):
                self.total_whale += 1
                logger.info(
                    "Solana WS whale: %.2f SOL = $%.0f | %s",
                    event.amount,
                    event.usd_value,
                    event.tx_hash[:16],
                )
                await self.on_event(event)
