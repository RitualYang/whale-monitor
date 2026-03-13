from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Ethereum — Etherscan (legacy, optional)
    etherscan_api_key: str = ""
    eth_poll_seconds: int = 4

    # ZAN API Key（明文，由用户在 .env 中手动填写）
    zan_api_key: str = "your_zan_api_key_here"

    # Ethereum — ZAN WebSocket（优先级 1，直接使用完整 URL，包含 apiKey）
    zan_eth_ws_url: str = (
        "wss://api.zan.top/node/ws/v1/eth/mainnet/your_zan_api_key_here"
    )
    zan_eth_ws_enabled: bool = True

    # Ethereum — ZAN JSON-RPC HTTP（优先级 2 / WS 关闭时启用）
    zan_eth_rpc_url: str = (
        "https://api.zan.top/node/v1/eth/mainnet/your_zan_api_key_here"
    )

    # Solana — ZAN WebSocket blockSubscribe（优先级 1，完整 URL）
    zan_sol_ws_url: str = (
        "wss://api.zan.top/node/ws/v1/solana/mainnet/your_zan_api_key_here"
    )
    zan_ws_enabled: bool = True

    # Solana — ZAN JSON-RPC HTTP（优先级 2 / WS 关闭时启用）
    zan_sol_rpc_url: str = (
        "https://api.zan.top/node/v1/solana/mainnet/your_zan_api_key_here"
    )
    sol_poll_seconds: int = 8

    # Solana — ZAN gRPC Yellowstone（需 ZAN 控制台开通）
    zan_grpc_endpoint: str = "grpc.zan.top:443"
    zan_grpc_enabled: bool = False

    # Threshold & store
    eth_usd_threshold: float = 100_000.0
    event_store_limit: int = 500

    # Frontend
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


settings = Settings()
