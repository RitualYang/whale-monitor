from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .schemas import WhaleEvent

if TYPE_CHECKING:
    from .config import ChainConfig


def parse_eth_whale_transfers(
    block: dict[str, Any],
    eth_usd: float,
    cfg: ChainConfig,
) -> list[WhaleEvent]:
    block_number = int(block["number"], 16)
    timestamp = datetime.fromtimestamp(int(block["timestamp"], 16), tz=timezone.utc)
    events: list[WhaleEvent] = []
    for tx in block.get("transactions", []):
        wei_value = int(tx.get("value", "0x0"), 16)
        if wei_value <= 0:
            continue
        eth_amount = wei_value / 10**18
        usd_value = eth_amount * eth_usd
        if usd_value < cfg.usd_threshold:
            continue
        tx_hash = tx.get("hash", "")
        events.append(
            WhaleEvent(
                chain=cfg.name,
                tx_hash=tx_hash,
                block_ref=str(block_number),
                timestamp=timestamp,
                from_address=tx.get("from", ""),
                to_address=tx.get("to") or "contract_creation",
                asset=cfg.asset,
                amount=eth_amount,
                usd_value=usd_value,
                explorer_url=f"{cfg.explorer}{tx_hash}",
            )
        )
    return events


def parse_solana_whale_transfers(
    slot: int,
    block: dict[str, Any] | None,
    sol_usd: float,
    cfg: ChainConfig,
) -> list[WhaleEvent]:
    if not block:
        return []
    block_time = block.get("blockTime")
    timestamp = (
        datetime.fromtimestamp(block_time, tz=timezone.utc)
        if block_time
        else datetime.now(timezone.utc)
    )
    events: list[WhaleEvent] = []
    for tx_wrap in block.get("transactions", []):
        tx = tx_wrap.get("transaction", {})
        message = tx.get("message", {})
        instructions = message.get("instructions", [])
        signatures = tx.get("signatures", [])
        tx_hash = signatures[0] if signatures else ""
        for ins in instructions:
            parsed = ins.get("parsed")
            if not isinstance(parsed, dict):
                continue
            if parsed.get("type") != "transfer":
                continue
            info = parsed.get("info", {})
            lamports = info.get("lamports")
            if lamports is None:
                continue
            sol_amount = float(lamports) / 10**9
            usd_value = sol_amount * sol_usd
            if usd_value < cfg.usd_threshold:
                continue
            events.append(
                WhaleEvent(
                    chain=cfg.name,
                    tx_hash=tx_hash,
                    block_ref=str(slot),
                    timestamp=timestamp,
                    from_address=info.get("source", ""),
                    to_address=info.get("destination", ""),
                    asset=cfg.asset,
                    amount=sol_amount,
                    usd_value=usd_value,
                    explorer_url=f"{cfg.explorer}{tx_hash}",
                )
            )
    return events
