from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env into os.environ so per-chain vars are accessible via os.environ.get
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for k, v in dotenv_values(_env_file).items():
        if v is not None:
            os.environ.setdefault(k, v)


@dataclass
class ChainConfig:
    """Per-chain configuration built dynamically from env vars."""

    name: str  # e.g. "ethereum", "solana"
    chain_type: str  # "evm" or "solana"
    ws_url: str = ""
    rpc_url: str = ""
    source: str = "ws"  # "ws" or "polling"
    usd_threshold: float = 100_000.0
    asset: str = ""  # e.g. "ETH", "SOL"
    explorer: str = ""  # e.g. "https://etherscan.io/tx/"
    poll_seconds: int = 8
    # gRPC (Solana-only for now)
    grpc_endpoint: str = ""
    grpc_enabled: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore",
    )

    # ── global ────────────────────────────────────────────────────────────────
    zan_api_key: str = "your_zan_api_key_here"
    etherscan_api_key: str = ""
    event_store_limit: int = 500
    cors_origins: str = "http://localhost:5173"

    # Chain list (comma-separated), e.g. "ethereum,solana"
    chains: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

    def _url(self, base: str) -> str:
        """Append /<apiKey> to a base URL."""
        return f"{base.rstrip('/')}/{self.zan_api_key}"


def _build_chains(s: Settings) -> list[ChainConfig]:
    """Read CHAINS env var and per-chain env vars to build ChainConfig list."""
    raw = s.chains.strip()
    if not raw:
        return _build_legacy_chains(s)

    chains: list[ChainConfig] = []
    for name in (n.strip().lower() for n in raw.split(",") if n.strip()):
        prefix = name.upper()
        e = os.environ.get

        ws_base = e(f"{prefix}_WS_URL", "")
        rpc_base = e(f"{prefix}_RPC_URL", "")

        chains.append(ChainConfig(
            name=name,
            chain_type=e(f"{prefix}_CHAIN_TYPE", "evm"),
            ws_url=s._url(ws_base) if ws_base else "",
            rpc_url=s._url(rpc_base) if rpc_base else "",
            source=e(f"{prefix}_SOURCE", "ws"),
            usd_threshold=float(e(f"{prefix}_USD_THRESHOLD", "100000")),
            asset=e(f"{prefix}_ASSET", name[:3].upper()),
            explorer=e(f"{prefix}_EXPLORER", ""),
            poll_seconds=int(e(f"{prefix}_POLL_SECONDS", "8")),
            grpc_endpoint=e(f"{prefix}_GRPC_ENDPOINT", ""),
            grpc_enabled=e(f"{prefix}_GRPC_ENABLED", "false").lower() == "true",
        ))
    return chains


def _build_legacy_chains(s: Settings) -> list[ChainConfig]:
    """Fallback: build chain configs from the old-style env vars."""
    e = os.environ.get
    eth_ws_enabled = e("ZAN_ETH_WS_ENABLED", "false").lower() == "true"
    sol_ws_enabled = e("ZAN_WS_ENABLED", "true").lower() == "true"

    eth_ws_base = e("ZAN_ETH_WS_BASE", "wss://api.zan.top/node/ws/v1/eth/mainnet")
    eth_rpc_base = e("ZAN_ETH_RPC_BASE", "https://api.zan.top/node/v1/eth/mainnet")
    sol_ws_base = e("ZAN_SOL_WS_BASE", "wss://api.zan.top/node/ws/v1/solana/mainnet")
    sol_rpc_base = e("ZAN_SOL_RPC_BASE", "https://api.zan.top/node/v1/solana/mainnet")

    return [
        ChainConfig(
            name="ethereum",
            chain_type="evm",
            ws_url=s._url(eth_ws_base),
            rpc_url=s._url(eth_rpc_base),
            source="ws" if eth_ws_enabled else "polling",
            usd_threshold=float(e("ETH_USD_THRESHOLD", "100000")),
            asset="ETH",
            explorer="https://etherscan.io/tx/",
            poll_seconds=int(e("ETH_POLL_SECONDS", "4")),
        ),
        ChainConfig(
            name="solana",
            chain_type="solana",
            ws_url=s._url(sol_ws_base),
            rpc_url=s._url(sol_rpc_base),
            source="ws" if sol_ws_enabled else "polling",
            usd_threshold=float(e("ETH_USD_THRESHOLD", "100000")),
            asset="SOL",
            explorer="https://solscan.io/tx/",
            poll_seconds=int(e("SOL_POLL_SECONDS", "8")),
            grpc_endpoint=e("ZAN_GRPC_ENDPOINT", "grpc.zan.top:443"),
            grpc_enabled=e("ZAN_GRPC_ENABLED", "false").lower() == "true",
        ),
    ]


settings = Settings()
chain_configs: list[ChainConfig] = _build_chains(settings)
