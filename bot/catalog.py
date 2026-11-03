"""Asset catalog — full names and asset classes for exchange tickers.

ccxt knows a market's ticker but not what it is. Binance publishes a product
list that names every asset and tags tokenized stocks ("bStocks"), tokenized
commodities and stablecoins, which is what lets search match "nvidia" and
lets the scanner tell a stock from a coin. Other venues fall back to
ticker-only search with every asset treated as crypto.
"""
from __future__ import annotations

import logging
import threading
import time

import requests

log = logging.getLogger("bot.catalog")

BINANCE_PRODUCTS_URL = (
    "https://www.binance.com/bapi/asset/v2/public/asset-service/product/get-products"
)


def classify(tags: list[str]) -> str:
    tags = set(tags or [])
    if "bStocks" in tags:
        return "stock"
    if "tCommodities" in tags:
        return "commodity"
    if "stablecoin" in tags:
        return "stablecoin"
    return "crypto"


def clean_name(name: str) -> str:
    """'NVIDIA (bStocks)' → 'NVIDIA'."""
    return name.replace("(bStocks)", "").strip() if name else ""


class AssetCatalog:
    def __init__(self, exchange: str, ttl: int = 6 * 3600, timeout: int = 20):
        self.exchange = exchange.lower()
        self.ttl = ttl
        self.timeout = timeout
        self._lock = threading.RLock()
        self._assets: dict[str, dict] = {}
        self._loaded_at = 0.0

    def _fetch(self) -> dict[str, dict]:
        if self.exchange != "binance":
            return {}
        r = requests.get(BINANCE_PRODUCTS_URL, params={"includeEtf": "true"},
                         timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        out = {}
        for row in r.json().get("data") or []:
            base = row.get("b")
            if base and base not in out:
                out[base] = {"name": clean_name(row.get("an", "")),
                             "asset_class": classify(row.get("tags"))}
        return out

    def assets(self) -> dict[str, dict]:
        now = time.time()
        with self._lock:
            if now - self._loaded_at < self.ttl:
                return self._assets
        try:
            fresh = self._fetch()
        except Exception as exc:
            log.warning("asset catalog unavailable: %s", exc)
            fresh = {}
        with self._lock:
            if fresh:
                self._assets, self._loaded_at = fresh, now
            elif not self._assets:
                self._loaded_at = now - self.ttl + 600   # retry in 10 minutes
            return self._assets

    def lookup(self, base: str) -> dict:
        return self.assets().get(base) or {"name": "", "asset_class": "crypto"}
