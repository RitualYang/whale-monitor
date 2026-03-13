from __future__ import annotations

from typing import Any

import httpx


class EtherscanClient:
    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        self.client = client
        self.api_key = api_key
        self.base_url = "https://api.etherscan.io/api"

    async def _proxy(self, action: str, **params: Any) -> Any:
        query = {
            "module": "proxy",
            "action": action,
            "apikey": self.api_key,
            **params,
        }
        res = await self.client.get(self.base_url, params=query, timeout=12)
        res.raise_for_status()
        data = res.json()
        if "result" not in data:
            raise RuntimeError(f"Etherscan proxy missing result: {data}")
        return data["result"]

    async def get_latest_block_number(self) -> int:
        hex_block = await self._proxy("eth_blockNumber")
        return int(hex_block, 16)

    async def get_block_by_number(self, block_number: int) -> dict[str, Any]:
        return await self._proxy(
            "eth_getBlockByNumber",
            tag=hex(block_number),
            boolean="true",
        )


class SolanaClient:
    """Solana JSON-RPC client backed by ZAN's dedicated node."""

    def __init__(self, client: httpx.AsyncClient, rpc_url: str) -> None:
        self.client = client
        self.rpc_url = rpc_url

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        res = await self.client.post(self.rpc_url, json=payload, timeout=15)
        res.raise_for_status()
        data = res.json()
        if data.get("error"):
            raise RuntimeError(f"Solana RPC error: {data['error']}")
        return data.get("result")

    async def get_slot(self) -> int:
        return int(await self._rpc("getSlot", [{"commitment": "confirmed"}]))

    async def get_block(self, slot: int) -> dict[str, Any] | None:
        return await self._rpc(
            "getBlock",
            [
                slot,
                {
                    "encoding": "jsonParsed",
                    "transactionDetails": "full",
                    "maxSupportedTransactionVersion": 0,
                    "rewards": False,
                },
            ],
        )


class PriceClient:
    """
    Price client backed by Binance public REST API (no key required).
    Falls back to last known prices on error.
    """

    _SYMBOLS = {"ETH": "ETHUSDT", "SOL": "SOLUSDT"}

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self._base = "https://api.binance.com/api/v3/ticker/price"

    async def get_prices(self) -> dict[str, float]:
        symbols_json = '["ETHUSDT","SOLUSDT"]'
        res = await self.client.get(
            self._base,
            params={"symbols": symbols_json},
            timeout=10,
        )
        res.raise_for_status()
        data: list[dict[str, str]] = res.json()
        result: dict[str, float] = {}
        for item in data:
            symbol = item.get("symbol", "")
            price = float(item.get("price", 0))
            if symbol == "ETHUSDT":
                result["ETH"] = price
            elif symbol == "SOLUSDT":
                result["SOL"] = price
        return result
