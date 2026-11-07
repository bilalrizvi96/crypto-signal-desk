"""Market data access via ccxt: universe discovery + OHLCV fetching with caching."""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

import pandas as pd

from .catalog import AssetCatalog
from .config import UniverseCfg

log = logging.getLogger("bot.data")

_TF_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
    "1d": 86400, "3d": 259200, "1w": 604800,
}


def tf_seconds(tf: str) -> int:
    if tf not in _TF_SECONDS:
        raise ValueError(f"unsupported timeframe: {tf}")
    return _TF_SECONDS[tf]


@dataclass
class SymbolInfo:
    symbol: str
    base: str
    quote_volume: float
    last: float
    change_24h: float
    rank: int
    bucket: str          # "large" or "small"
    spread_pct: float
    name: str = ""
    asset_class: str = "crypto"   # "crypto" | "stock" | "commodity" | "stablecoin"


BINANCE_PUBLIC_DATA = "https://data-api.binance.vision/api/v3"


class RateLimited(RuntimeError):
    """The exchange has banned or throttled this IP; retrying early extends the ban."""


_BAN_UNTIL = re.compile(r"banned until (\d{13})")


class MarketData:
    """Thin ccxt wrapper. One instance per process; safe to call from threads."""

    def __init__(self, cfg: UniverseCfg):
        self.cfg = cfg
        self._lock = threading.RLock()
        self._ohlcv_cache: dict[tuple[str, str, str], tuple[float, pd.DataFrame]] = {}
        self._tickers_cache: tuple[float, str, dict] | None = None
        self.primary = self._build_exchange(cfg.exchange)
        self.backup = (self._build_exchange(cfg.fallback_exchange)
                       if cfg.fallback_exchange and cfg.fallback_exchange.lower() != cfg.exchange.lower()
                       else None)
        self.catalog = AssetCatalog(cfg.exchange)

    @property
    def exchange(self):
        """Where market data comes from right now: the backup exchange while
        the main one has this server banned, the main one otherwise."""
        if self.backup is not None and time.time() < self.banned_until:
            return self.backup
        return self.primary

    @property
    def source(self) -> str:
        return self.exchange.id

    # ---------------------------------------------------------------- setup
    def _build_exchange(self, name: str):
        import ccxt

        name = name.lower()
        if not hasattr(ccxt, name):
            raise ValueError(f"unknown exchange '{name}' — check ccxt.exchanges")
        klass = getattr(ccxt, name)
        opts = {"enableRateLimit": True, "timeout": 20000}
        if self.cfg.market_type in ("swap", "future"):
            opts["options"] = {"defaultType": "swap"}
        elif name == "binance":
            opts["options"] = {"fetchMarkets": ["spot"]}   # skip futures market lists we never use
        elif name == "okx":
            opts["options"] = {"fetchMarkets": {"types": ["spot"]}}
        ex = klass(opts)
        if name == "binance" and self.cfg.market_type not in ("swap", "future"):
            # Binance's market-data-only host: same public data, separate limits.
            # Shared cloud IPs (Render, etc.) often get banned on api.binance.com
            # because of other tenants' traffic; this host keeps the scanner alive.
            ex.urls["api"]["public"] = BINANCE_PUBLIC_DATA
        return ex

    def load_markets(self):
        with self._lock:
            if not self.exchange.markets:
                self._call("load_markets")
        return self.exchange.markets

    # ------------------------------------------------------------ backoff
    banned_until: float = 0.0     # epoch seconds; no requests before this

    def _call(self, method: str, *args, **kwargs):
        """Every exchange request goes through here. After a 418/429 the
        exchange says how long the ban lasts; sending anything sooner only
        makes it longer, so requests stop until then and go to the backup
        exchange instead, if there is one."""
        ex = self.exchange
        if ex is self.primary and time.time() < self.banned_until:
            raise RateLimited(self.ban_message())
        try:
            return getattr(ex, method)(*args, **kwargs)
        except Exception as exc:
            if ex is not self.primary:
                raise

            text = str(exc)
            m = _BAN_UNTIL.search(text)
            if m:
                until = int(m.group(1)) / 1000.0
            elif " 418 " in text or " 429 " in text or "-1003" in text:
                until = time.time() + 120
            else:
                raise
            if until > self.banned_until:
                self.banned_until = until
                log.warning("exchange rate limit: pausing %s requests until %s%s", self.cfg.exchange,
                            time.strftime("%H:%M UTC", time.gmtime(until)),
                            f", reading prices from {self.backup.id}" if self.backup else "")
            if self.backup is not None:
                return self._call(method, *args, **kwargs)
            raise RateLimited(self.ban_message()) from exc

    def ban_message(self) -> str:
        msg = (f"{self.cfg.exchange.capitalize()} is rate-limiting this server until "
               f"{time.strftime('%H:%M UTC', time.gmtime(self.banned_until))}")
        if self.backup is not None:
            msg += f", so prices come from {self.backup.id.upper()} meanwhile (its coins, not {self.cfg.exchange.capitalize()}'s stock tokens)"
        return msg

    # ------------------------------------------------------------- universe
    def fetch_tickers(self, ttl: int = 120) -> dict:
        now = time.time()
        source = self.source
        with self._lock:
            if self._tickers_cache and self._tickers_cache[1] == source and now - self._tickers_cache[0] < ttl:
                return self._tickers_cache[2]
        tickers = self._call("fetch_tickers")
        with self._lock:
            self._tickers_cache = (now, source, tickers)
        return tickers

    def market_rows(self, include_stable: bool = False) -> list[dict]:
        """Every tradable pair in the configured quote, ranked by 24h volume."""
        self.load_markets()
        tickers = self.fetch_tickers()
        cfg = self.cfg
        want_swap = cfg.market_type in ("swap", "future")
        assets = self.catalog.assets()

        rows: list[dict] = []
        for symbol, t in tickers.items():
            market = self.exchange.markets.get(symbol)
            if not market or not market.get("active", True):
                continue
            if market.get("quote") != cfg.quote:
                continue
            if want_swap and not market.get("swap"):
                continue
            if not want_swap and not market.get("spot"):
                continue
            if symbol in cfg.exclude or market.get("base") in cfg.exclude:
                continue
            base = market.get("base", "")
            # leveraged tokens (BTCUP/BTCDOWN/3L/3S) track a derivative, not the coin
            if any(base.endswith(sfx) for sfx in ("UP", "DOWN", "3L", "3S", "5L", "5S")):
                continue

            qv = t.get("quoteVolume") or 0.0
            last = t.get("last") or t.get("close") or 0.0
            if not qv or not last:
                continue

            # stable/stable pairs barely move, so their "signals" are noise
            asset = assets.get(base) or {}
            if asset.get("asset_class") == "stablecoin" and not include_stable:
                continue

            bid, ask = t.get("bid"), t.get("ask")
            spread = ((ask - bid) / ask * 100.0) if (bid and ask and ask > 0) else 0.0

            rows.append({
                "symbol": symbol,
                "base": base,
                "quote_volume": float(qv),
                "last": float(last),
                "change_24h": float(t.get("percentage") or 0.0),
                "spread_pct": float(spread),
                "name": asset.get("name", ""),
                "asset_class": asset.get("asset_class", "crypto"),
            })

        rows.sort(key=lambda r: r["quote_volume"], reverse=True)
        for i, r in enumerate(rows):
            r["rank"] = i + 1
        return rows

    def build_universe(self) -> list[SymbolInfo]:
        """Rank tradable pairs by 24h quote volume, then take a large-cap band
        and a small-cap band so the scan covers both ends of the market."""
        cfg = self.cfg
        rows = self.market_rows()

        def liquid(r: dict) -> bool:
            if r["quote_volume"] < cfg.min_quote_volume_usd:
                return False
            if cfg.max_spread_pct and r["spread_pct"] > cfg.max_spread_pct:
                return False
            return True

        large = [r for r in rows[: cfg.large_cap_top] if liquid(r)]
        small_band = rows[cfg.small_cap_from - 1: cfg.small_cap_to]
        small = [r for r in small_band if liquid(r)][: cfg.small_cap_count]

        chosen: dict[str, SymbolInfo] = {}
        for r in large:
            chosen[r["symbol"]] = SymbolInfo(bucket="large", **r)
        for r in small:
            chosen.setdefault(r["symbol"], SymbolInfo(bucket="small", **r))

        # explicit includes always make the cut, liquidity filters or not
        by_symbol = {r["symbol"]: r for r in rows}
        for sym in cfg.include:
            if sym in by_symbol and sym not in chosen:
                r = by_symbol[sym]
                bucket = "large" if r["rank"] <= cfg.large_cap_top else "small"
                chosen[sym] = SymbolInfo(bucket=bucket, **r)

        out = sorted(chosen.values(), key=lambda s: s.rank)
        log.info("universe: %d symbols (%d large, %d small)", len(out),
                 sum(1 for s in out if s.bucket == "large"),
                 sum(1 for s in out if s.bucket == "small"))
        return out

    def search(self, query: str, limit: int = 12) -> list[dict]:
        """Match pairs by ticker or full name ("btc", "nvidia", "apple").

        Exact ticker first, then ticker prefix, then name match; volume
        breaks ties so the liquid pair wins over a lookalike.
        """
        q = query.strip().upper()
        if not q:
            return []
        hits = []
        for r in self.market_rows():
            base, name = r["base"].upper(), r["name"].upper()
            if base == q:
                tier = 0
            elif base.startswith(q):
                tier = 1
            elif q in name:
                tier = 2
            elif q in base:
                tier = 3
            else:
                continue
            hits.append((tier, -r["quote_volume"], r))
        hits.sort(key=lambda h: (h[0], h[1]))
        return [h[2] for h in hits[:limit]]

    def symbol_info(self, symbol: str) -> SymbolInfo | None:
        """A SymbolInfo for any tradable pair, scanned universe or not."""
        for r in self.market_rows():
            if r["symbol"] == symbol:
                bucket = "large" if r["rank"] <= self.cfg.large_cap_top else "small"
                return SymbolInfo(bucket=bucket, **r)
        return None

    # ---------------------------------------------------------------- OHLCV
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 400,
                    ttl: int | None = None) -> pd.DataFrame:
        """Return a DataFrame indexed by UTC timestamp with OHLCV columns.

        Cached for a fraction of the bar duration so a 60s scan loop doesn't
        re-download 4h candles that cannot possibly have changed.
        """
        key = (self.source, symbol, timeframe)
        if ttl is None:
            ttl = max(15, min(tf_seconds(timeframe) // 3, 900))
        now = time.time()
        with self._lock:
            hit = self._ohlcv_cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]

        raw = self._call("fetch_ohlcv", symbol, timeframe=timeframe, limit=limit)
        if not raw:
            raise RuntimeError(f"no OHLCV returned for {symbol} {timeframe}")

        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df.set_index("ts").astype(float)
        df = df[~df.index.duplicated(keep="last")].sort_index()

        # The final candle is still forming. Keep it (we want live price) but
        # flag it so the strategy can decide whether to trust it.
        df.attrs["last_bar_open"] = True
        df.attrs["symbol"] = symbol
        df.attrs["timeframe"] = timeframe

        with self._lock:
            self._ohlcv_cache[key] = (now, df)
        return df

    def fetch_ticker(self, symbol: str) -> dict:
        return self._call("fetch_ticker", symbol)

    def clear_cache(self):
        with self._lock:
            self._ohlcv_cache.clear()
            self._tickers_cache = None
