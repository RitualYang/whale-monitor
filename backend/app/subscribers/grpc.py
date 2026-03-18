"""gRPC Yellowstone subscriber — generalized from sol_grpc.py."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

import base58
import grpc
import grpc.aio

from ..config import settings
from ..schemas import WhaleEvent
from ..store import EventStore
from .base import BaseSubscriber

logger = logging.getLogger(__name__)

try:
    from ..proto_gen import geyser_pb2, geyser_pb2_grpc  # type: ignore[attr-defined]

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
    cfg,
) -> WhaleEvent | None:
    try:
        tx_info = tx_update.transaction
        meta = tx_info.meta
        tx = tx_info.transaction

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
        if usd_value < cfg.usd_threshold:
            return None

        keys = list(tx.message.account_keys)
        gain_idx = deltas.index(max_gain)
        loss_idx = deltas.index(min(deltas))

        to_addr = _bytes_to_b58(keys[gain_idx]) if gain_idx < len(keys) else "unknown"
        from_addr = _bytes_to_b58(keys[loss_idx]) if loss_idx < len(keys) else "unknown"
        sig_b58 = _bytes_to_b58(tx_info.signature) if tx_info.signature else "unknown"

        return WhaleEvent(
            chain=cfg.name,
            tx_hash=sig_b58,
            block_ref=str(slot),
            timestamp=datetime.now(timezone.utc),
            from_address=from_addr,
            to_address=to_addr,
            asset=cfg.asset,
            amount=sol_amount,
            usd_value=usd_value,
            unit_price=sol_usd,
            explorer_url=f"{cfg.explorer}{sig_b58}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("tx parse error: %s", exc)
        return None


class GrpcSubscriber(BaseSubscriber):
    def __init__(self, cfg, store, on_event, price_getter) -> None:
        super().__init__(cfg, store, on_event, price_getter)
        self.api_key = settings.zan_api_key

    def start(self) -> None:
        if not _GRPC_AVAILABLE:
            logger.error("[%s] gRPC stubs not available. Run setup_proto.sh.", self.cfg.name)
            return
        super().start()
        logger.info("[%s] gRPC subscriber started -> %s", self.cfg.name, self.cfg.grpc_endpoint)

    def _on_disconnect(self, exc: Exception, retry_delay: float) -> None:
        logger.warning("[%s] gRPC disconnected: %s – retry in %.0fs", self.cfg.name, exc, retry_delay)

    async def _stream(self) -> None:
        def _auth(ctx, cb):  # noqa: ANN001
            cb((("x-token", self.api_key),), None)

        creds = grpc.composite_channel_credentials(
            grpc.ssl_channel_credentials(),
            grpc.metadata_call_credentials(_auth),
        )

        async with grpc.aio.secure_channel(
            self.cfg.grpc_endpoint,
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

            self.connected = True
            logger.info("[%s] gRPC stream connected", self.cfg.name)
            async for update in stub.Subscribe(_req_gen()):
                if not self._running:
                    break
                if update.HasField("ping") or update.HasField("pong"):
                    continue
                if not update.HasField("transaction"):
                    continue

                self.total_seen += 1
                self.latest_ref = update.transaction.slot

                price = self.price_getter()
                if price <= 0:
                    continue

                event = _detect_whale(
                    tx_update=update.transaction,
                    slot=update.transaction.slot,
                    sol_usd=price,
                    cfg=self.cfg,
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
