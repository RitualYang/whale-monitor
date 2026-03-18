from __future__ import annotations

import abc
import asyncio
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..config import ChainConfig
    from ..store import EventStore


class BaseSubscriber(abc.ABC):
    """Common interface for all chain data subscribers."""

    def __init__(
        self,
        cfg: ChainConfig,
        store: EventStore,
        on_event: Callable,
        price_getter: Callable[[], float],
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.on_event = on_event
        self.price_getter = price_getter
        self._task: asyncio.Task | None = None
        self._running = False
        self.connected: bool = False
        self.latest_ref: int | None = None
        self.total_seen: int = 0
        self.total_whale: int = 0

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_with_retry())

    def stop(self) -> None:
        self._running = False
        self.connected = False
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
                self.connected = False
                self._on_disconnect(exc, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60.0)

    @abc.abstractmethod
    async def _stream(self) -> None: ...

    def _on_disconnect(self, exc: Exception, retry_delay: float) -> None:
        """Override for custom disconnect logging."""
