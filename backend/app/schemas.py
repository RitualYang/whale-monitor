from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class WhaleEvent(BaseModel):
    chain: Literal["ethereum", "solana"]
    tx_hash: str
    block_ref: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    from_address: str
    to_address: str
    asset: Literal["ETH", "SOL"]
    amount: float
    usd_value: float
    explorer_url: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    latest_eth_block: int | None
    latest_sol_slot: int | None
    cached_events: int
    eth_source: Literal["ws", "polling"] = "ws"
    eth_ws_connected: bool = False
    sol_source: Literal["ws", "polling"] = "ws"
    sol_ws_connected: bool = False
