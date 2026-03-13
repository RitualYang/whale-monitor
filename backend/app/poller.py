"""
Multi-chain periodic poller.
- Ethereum: ZAN JSON-RPC (primary when ETH WS disabled) or Etherscan (legacy).
- Solana  : ZAN JSON-RPC polling (fallback when Solana WS disabled).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Union

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .clients import EtherscanClient, PriceClient, SolanaClient, ZanEthClient
from .config import settings
from .detector import parse_eth_whale_transfers, parse_solana_whale_transfers
from .schemas import WhaleEvent
from .store import EventStore

logger = logging.getLogger(__name__)


class ChainPoller:
    def __init__(
        self,
        eth_client: Union[EtherscanClient, ZanEthClient],
        sol_client: SolanaClient,
        price_client: PriceClient,
        store: EventStore,
        on_event,
        eth_polling_enabled: bool = True,
        sol_polling_enabled: bool = True,
    ) -> None:
        self.eth_client = eth_client
        self.sol_client = sol_client
        self.price_client = price_client
        self.store = store
        self.on_event = on_event
        self.eth_polling_enabled = eth_polling_enabled
        self.sol_polling_enabled = sol_polling_enabled
        self.scheduler = AsyncIOScheduler()
        self.latest_eth_block: int | None = None
        self.latest_sol_slot: int | None = None
        self.prices: dict[str, float] = {"ETH": 0.0, "SOL": 0.0}
        self._eth_lock = asyncio.Lock()
        self._sol_lock = asyncio.Lock()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    _ETH_JOB_ID = "poll_eth"
    _SOL_JOB_ID = "poll_solana"

    def start(self) -> None:
        self.scheduler.add_job(self.refresh_prices, "interval", seconds=20)

        if self.eth_polling_enabled:
            self.scheduler.add_job(
                self.poll_eth,
                "interval",
                seconds=settings.eth_poll_seconds,
                max_instances=1,
                coalesce=True,
                id=self._ETH_JOB_ID,
            )

        if self.sol_polling_enabled:
            self.scheduler.add_job(
                self.poll_solana,
                "interval",
                seconds=settings.sol_poll_seconds,
                max_instances=1,
                coalesce=True,
                id=self._SOL_JOB_ID,
            )

        eth_status = f"every {settings.eth_poll_seconds}s" if self.eth_polling_enabled else "DISABLED (WS active)"
        sol_status = f"every {settings.sol_poll_seconds}s" if self.sol_polling_enabled else "DISABLED (WS active)"
        logger.info("ChainPoller started — ETH: %s  SOL: %s", eth_status, sol_status)
        self.scheduler.start()

    def stop(self) -> None:
        self.scheduler.shutdown(wait=False)

    # ── dynamic polling control ────────────────────────────────────────────────

    def enable_eth_polling(self) -> None:
        """Start ETH polling if not already running."""
        if self.scheduler.get_job(self._ETH_JOB_ID):
            return
        self.latest_eth_block = None
        self.scheduler.add_job(
            self.poll_eth,
            "interval",
            seconds=settings.eth_poll_seconds,
            max_instances=1,
            coalesce=True,
            id=self._ETH_JOB_ID,
        )
        self.eth_polling_enabled = True
        logger.info("ETH HTTP polling enabled (every %ds)", settings.eth_poll_seconds)

    def disable_eth_polling(self) -> None:
        """Stop ETH polling."""
        job = self.scheduler.get_job(self._ETH_JOB_ID)
        if job:
            job.remove()
        self.eth_polling_enabled = False
        logger.info("ETH HTTP polling disabled")

    def enable_sol_polling(self) -> None:
        """Start Solana JSON-RPC polling if not already running."""
        if self.scheduler.get_job(self._SOL_JOB_ID):
            return
        self.latest_sol_slot = None  # re-sync from latest slot on resume
        self.scheduler.add_job(
            self.poll_solana,
            "interval",
            seconds=settings.sol_poll_seconds,
            max_instances=1,
            coalesce=True,
            id=self._SOL_JOB_ID,
        )
        self.sol_polling_enabled = True
        logger.info("Solana JSON-RPC polling enabled (every %ds)", settings.sol_poll_seconds)

    def disable_sol_polling(self) -> None:
        """Stop Solana JSON-RPC polling."""
        job = self.scheduler.get_job(self._SOL_JOB_ID)
        if job:
            job.remove()
        self.sol_polling_enabled = False
        logger.info("Solana JSON-RPC polling disabled")

    # ── price ─────────────────────────────────────────────────────────────────

    async def refresh_prices(self) -> None:
        try:
            latest = await self.price_client.get_prices()
            if latest.get("ETH", 0) > 0:
                self.prices["ETH"] = latest["ETH"]
            if latest.get("SOL", 0) > 0:
                self.prices["SOL"] = latest["SOL"]
            logger.info(
                "prices — ETH=$%.0f  SOL=$%.2f",
                self.prices["ETH"],
                self.prices["SOL"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("price refresh failed: %s", exc)

    # ── Ethereum ──────────────────────────────────────────────────────────────

    async def poll_eth(self) -> None:
        if not settings.etherscan_api_key:
            return
        if self._eth_lock.locked():
            return
        async with self._eth_lock:
            try:
                if self.prices["ETH"] <= 0:
                    await self.refresh_prices()
                latest = await self.eth_client.get_latest_block_number()
                if self.latest_eth_block is None:
                    self.latest_eth_block = latest - 1
                for block_num in range(self.latest_eth_block + 1, latest + 1):
                    block = await self.eth_client.get_block_by_number(block_num)
                    events = parse_eth_whale_transfers(
                        block=block,
                        eth_usd=self.prices["ETH"],
                        threshold_usd=settings.eth_usd_threshold,
                    )
                    await self._publish(events)
                    self.latest_eth_block = block_num
            except Exception as exc:  # noqa: BLE001
                logger.warning("eth poll failed: %s", exc)

    # ── Solana JSON-RPC (primary, ZAN endpoint) ───────────────────────────────

    async def poll_solana(self) -> None:
        if self._sol_lock.locked():
            return
        async with self._sol_lock:
            try:
                if self.prices["SOL"] <= 0:
                    await self.refresh_prices()
                latest = await self.sol_client.get_slot()
                if self.latest_sol_slot is None:
                    self.latest_sol_slot = latest - 1
                    logger.info("Solana JSON-RPC online, latest slot: %d", latest)
                start = self.latest_sol_slot + 1
                # process at most 5 slots per tick to avoid falling too far behind
                end = min(latest, start + 4)
                for slot in range(start, end + 1):
                    block = await self.sol_client.get_block(slot)
                    events = parse_solana_whale_transfers(
                        slot=slot,
                        block=block,
                        sol_usd=self.prices["SOL"],
                        threshold_usd=settings.eth_usd_threshold,
                    )
                    await self._publish(events)
                    self.latest_sol_slot = slot
            except Exception as exc:  # noqa: BLE001
                logger.warning("solana poll failed: %s", exc)

    # ── internal ──────────────────────────────────────────────────────────────

    async def _publish(self, events: list[WhaleEvent]) -> None:
        for event in events:
            if self.store.add(event):
                logger.info(
                    "%s whale: %.4f %s = $%.0f | %s",
                    event.chain.upper(),
                    event.amount,
                    event.asset,
                    event.usd_value,
                    event.tx_hash[:16],
                )
                await self.on_event(event)
