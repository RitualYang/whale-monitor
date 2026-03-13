from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Ethereum (Etherscan)
    etherscan_api_key: str = ""
    eth_poll_seconds: int = 4

    # Solana — ZAN WebSocket blockSubscribe (最高优先级，实时推送)
    zan_api_key: str = "25a51188cb25466986e5d7e48c6217e9"
    zan_sol_ws_url: str = (
        "wss://api.zan.top/node/ws/v1/solana/mainnet/25a51188cb25466986e5d7e48c6217e9"
    )
    zan_ws_enabled: bool = True

    # Solana — ZAN JSON-RPC (HTTP 轮询，当 WS 不可用时自动启用)
    zan_sol_rpc_url: str = (
        "https://api.zan.top/node/v1/solana/mainnet/25a51188cb25466986e5d7e48c6217e9"
    )
    sol_poll_seconds: int = 8

    # Solana — ZAN gRPC Yellowstone (备用，需在 ZAN 控制台手动开通)
    zan_grpc_endpoint: str = "grpc.zan.top:443"
    zan_grpc_enabled: bool = True

    # Threshold & store
    eth_usd_threshold: float = 100_000.0
    event_store_limit: int = 500

    # Frontend
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


settings = Settings()
