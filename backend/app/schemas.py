from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class WhaleEvent(BaseModel):
    chain: str
    tx_hash: str
    block_ref: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    from_address: str
    to_address: str
    asset: str
    amount: float
    usd_value: float
    unit_price: float = 0.0
    explorer_url: str


class ChainHealth(BaseModel):
    name: str
    source: str  # "ws" or "polling"
    connected: bool = False
    latest_ref: int | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    cached_events: int
    chains: list[ChainHealth] = []
