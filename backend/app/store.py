from __future__ import annotations

from collections import deque

from .schemas import WhaleEvent


class EventStore:
    def __init__(self, limit: int = 500) -> None:
        self._events: deque[WhaleEvent] = deque(maxlen=limit)
        self._seen_tx: set[str] = set()

    def add(self, event: WhaleEvent) -> bool:
        key = f"{event.chain}:{event.tx_hash}"
        if key in self._seen_tx:
            return False
        self._events.appendleft(event)
        self._seen_tx.add(key)
        if len(self._seen_tx) > self._events.maxlen * 3:
            self._seen_tx = {f"{e.chain}:{e.tx_hash}" for e in self._events}
        return True

    def list_events(self, limit: int = 100) -> list[WhaleEvent]:
        return list(self._events)[:limit]

    @property
    def size(self) -> int:
        return len(self._events)
