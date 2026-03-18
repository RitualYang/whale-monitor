"""Dynamic price client — Binance + CoinGecko fallback for all chain assets."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .config import ChainConfig

logger = logging.getLogger(__name__)

# Asset → Binance trading pair (only assets that exist on Binance)
_BINANCE_PAIR: dict[str, str] = {
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "BNB": "BNBUSDT",
    "POL": "POLUSDT",
    "ASTR": "ASTRUSDT",
    "STRK": "STRKUSDT",
    "TRX": "TRXUSDT",
    "AVAX": "AVAXUSDT",
    "ARB": "ARBUSDT",
    "OP": "OPUSDT",
    "MANTA": "MANTAUSDT",
}

# Asset → CoinGecko ID (for tokens not on Binance)
_COINGECKO_ID: dict[str, str] = {
    "MNT": "mantle",
    "CORE": "coredaoorg",
    "IP": "story-2",
    "OKB": "okb",
    "WEMIX": "wemix-token",
    "ZETA": "zetachain",
}


class PriceClient:
    """Fetches prices from Binance (primary) + CoinGecko (fallback)."""

    def __init__(self, client: httpx.AsyncClient, chain_configs: list[ChainConfig]) -> None:
        self.client = client
        # Deduplicate assets across chains
        all_assets = {cfg.asset for cfg in chain_configs}
        self._binance_assets: dict[str, str] = {}
        self._gecko_assets: dict[str, str] = {}
        for asset in all_assets:
            if asset in _BINANCE_PAIR:
                self._binance_assets[asset] = _BINANCE_PAIR[asset]
            elif asset in _COINGECKO_ID:
                self._gecko_assets[asset] = _COINGECKO_ID[asset]
            else:
                self._binance_assets[asset] = f"{asset}USDT"
        self.prices: dict[str, float] = {a: 0.0 for a in all_assets}

    async def refresh(self) -> dict[str, float]:
        await self._refresh_binance()
        await self._refresh_coingecko()
        logger.info(
            "prices — %s",
            "  ".join(f"{a}=${p:.4g}" for a, p in sorted(self.prices.items())),
        )
        return self.prices

    async def _refresh_binance(self) -> None:
        if not self._binance_assets:
            return
        pairs = list(set(self._binance_assets.values()))
        symbols_json = "[" + ",".join(f'"{p}"' for p in pairs) + "]"
        try:
            res = await self.client.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbols": symbols_json},
                timeout=10,
            )
            res.raise_for_status()
            data: list[dict[str, str]] = res.json()
            pair_to_assets: dict[str, list[str]] = {}
            for asset, pair in self._binance_assets.items():
                pair_to_assets.setdefault(pair, []).append(asset)
            for item in data:
                symbol = item.get("symbol", "")
                for asset in pair_to_assets.get(symbol, []):
                    price = float(item.get("price", 0))
                    if price > 0:
                        self.prices[asset] = price
        except Exception as exc:  # noqa: BLE001
            logger.warning("Binance price refresh failed: %s", exc)

    async def _refresh_coingecko(self) -> None:
        if not self._gecko_assets:
            return
        ids = ",".join(self._gecko_assets.values())
        try:
            res = await self.client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": ids, "vs_currencies": "usd"},
                timeout=10,
            )
            res.raise_for_status()
            data: dict = res.json()
            id_to_asset = {v: k for k, v in self._gecko_assets.items()}
            for gecko_id, prices in data.items():
                asset = id_to_asset.get(gecko_id)
                if asset and isinstance(prices, dict):
                    price = float(prices.get("usd", 0))
                    if price > 0:
                        self.prices[asset] = price
        except Exception as exc:  # noqa: BLE001
            logger.warning("CoinGecko price refresh failed: %s", exc)

    def get(self, asset: str) -> float:
        return self.prices.get(asset, 0.0)
