"""
ZAN Solana gRPC subscriber using Yellowstone Geyser protocol.
Streams all confirmed non-vote transactions and emits WhaleEvent
when a single SOL balance delta >= configured USD threshold.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

import base58
import grpc
import grpc.aio

from .config import settings
from .schemas import WhaleEvent

if TYPE_CHECKING:
    from .store import EventStore

logger = logging.getLogger(__name__)

try:
    from .proto_gen import geyser_pb2, geyser_pb2_grpc  # type: ignore[attr-defined]

    _GRPC_AVAILABLE = True
except Exception as exc:  # noqa: BLE001
    logger.warning("gRPC proto stubs not found (%s). Run setup_proto.sh.", exc)
    _GRPC_AVAILABLE = False


def _bytes_to_b58(raw: bytes) -> str:
    return base58.b58encode(raw).decode()


def _detect_whale(
    tx_update,
    slot: int,
    sol_usd: float,
    threshold_usd: float,
) -> WhaleEvent | None:
    """
    Inspect a SubscribeUpdateTransaction for a large SOL transfer.
    Finds the account with the maximum positive lamport delta; if
    that exceeds the threshold it's treated as the receiver and an
    event is emitted.
    """
    try:
        tx_info = tx_update.transaction  # SubscribeUpdateTransactionInfo
        meta = tx_info.meta
        tx = tx_info.transaction  # solana.storage.ConfirmedBlock.Transaction

        pre = list(meta.pre_balances)
        post = list(meta.post_balances)
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

        keys = list(tx.message.account_keys)
        gain_idx = deltas.index(max_gain)
        loss_idx = deltas.index(min(deltas))

        to_addr = _bytes_to_b58(keys[gain_idx]) if gain_idx < len(keys) else "unknown"
        from_addr = _bytes_to_b58(keys[loss_idx]) if loss_idx < len(keys) else "unknown"
        sig_b58 = _bytes_to_b58(tx_info.signature) if tx_info.signature else "unknown"

        return WhaleEvent(
            chain="solana",
            tx_hash=sig_b58,
            block_ref=str(slot),
            timestamp=datetime.now(timezone.utc),
            from_address=from_addr,
            to_address=to_addr,
            asset="SOL",
            amount=sol_amount,
            usd_value=usd_value,
            explorer_url=f"https://solscan.io/tx/{sig_b58}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("tx parse error: %s", exc)
        return None


class ZanSolanaGrpcSubscriber:
    """Long-running gRPC stream subscriber for ZAN Solana Geyser."""

    def __init__(
        self,
        grpc_endpoint: str,
        api_key: str,
        store: "EventStore",
        on_event: Callable,
        sol_usd_getter: Callable[[], float],
    ) -> None:
        self.grpc_endpoint = grpc_endpoint
        self.api_key = api_key
        self.store = store
        self.on_event = on_event
        self.sol_usd_getter = sol_usd_getter
        self._task: asyncio.Task | None = None
        self._running = False
        self.latest_slot: int | None = None
        self.total_seen: int = 0
        self.total_whale: int = 0

    def start(self) -> None:
        if not _GRPC_AVAILABLE:
            logger.error("gRPC stubs not available. Run setup_proto.sh first.")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_with_retry())
        logger.info("Solana gRPC subscriber started -> %s", self.grpc_endpoint)

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run_with_retry(self) -> None:
        retry_delay = 2.0
        while self._running:
            try:
                await self._stream()
                retry_delay = 2.0
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Solana gRPC disconnected: %s – retry in %.0fs", exc, retry_delay
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60.0)

    async def _stream(self) -> None:
        def _auth(ctx, cb):  # noqa: ANN001
            cb((("x-token", self.api_key),), None)

        creds = grpc.composite_channel_credentials(
            grpc.ssl_channel_credentials(),
            grpc.metadata_call_credentials(_auth),
        )

        async with grpc.aio.secure_channel(
            self.grpc_endpoint,
            credentials=creds,
            options=[
                ("grpc.max_receive_message_length", 64 * 1024 * 1024),
                ("grpc.keepalive_time_ms", 30_000),
                ("grpc.keepalive_timeout_ms", 10_000),
            ],
        ) as channel:
            stub = geyser_pb2_grpc.GeyserStub(channel)

            sub_req = geyser_pb2.SubscribeRequest()
            sub_req.transactions["whale_watch"].vote = False
            sub_req.transactions["whale_watch"].failed = False
            sub_req.commitment = geyser_pb2.CommitmentLevel.CONFIRMED

            async def _req_gen():
                yield sub_req
                ping_id = 0
                while self._running:
                    await asyncio.sleep(25)
                    ping_id += 1
                    ping_req = geyser_pb2.SubscribeRequest()
                    ping_req.ping.id = ping_id
                    yield ping_req

            logger.info("Solana gRPC stream connected")
            async for update in stub.Subscribe(_req_gen()):
                if not self._running:
                    break
                if update.HasField("ping") or update.HasField("pong"):
                    continue
                if not update.HasField("transaction"):
                    continue

                self.total_seen += 1
                self.latest_slot = update.transaction.slot

                sol_usd = self.sol_usd_getter()
                if sol_usd <= 0:
                    continue

                event = _detect_whale(
                    tx_update=update.transaction,
                    slot=update.transaction.slot,
                    sol_usd=sol_usd,
                    threshold_usd=settings.eth_usd_threshold,
                )
                if event and self.store.add(event):
                    self.total_whale += 1
                    logger.info(
                        "Solana whale: %.2f SOL = $%.0f | %s",
                        event.amount,
                        event.usd_value,
                        event.tx_hash[:16],
                    )
                    await self.on_event(event)
