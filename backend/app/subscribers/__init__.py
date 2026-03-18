"""Subscriber factory — builds the right subscriber for each ChainConfig."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from .base import BaseSubscriber

if TYPE_CHECKING:
    from ..config import ChainConfig
    from ..store import EventStore

logger = logging.getLogger(__name__)


def build_subscriber(
    cfg: ChainConfig,
    store: EventStore,
    on_event: Callable,
    price_getter: Callable[[], float],
) -> BaseSubscriber | None:
    """Return the appropriate subscriber for the given chain config, or None."""
    if cfg.source == "ws" and cfg.chain_type == "evm":
        from .evm_ws import EvmWsSubscriber

        return EvmWsSubscriber(cfg, store, on_event, price_getter)

    if cfg.source == "ws" and cfg.chain_type == "solana":
        from .solana_ws import SolanaWsSubscriber

        return SolanaWsSubscriber(cfg, store, on_event, price_getter)

    if cfg.grpc_enabled and cfg.chain_type == "solana":
        from .grpc import GrpcSubscriber

        return GrpcSubscriber(cfg, store, on_event, price_getter)

    # polling — no dedicated subscriber, handled by ChainPoller
    logger.info("[%s] source=%s, no WS/gRPC subscriber created", cfg.name, cfg.source)
    return None
