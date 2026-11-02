"""News + sentiment: pull public crypto RSS feeds, score headlines, map to coins.

No API keys required. Sentiment is lexicon-based — deliberately simple and
transparent, so you can read why a headline scored the way it did rather than
trusting a black box. It nudges technical scores; it never drives them alone.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import NewsCfg

log = logging.getLogger("bot.news")

# --------------------------------------------------------------------------
# lexicon — weights are deliberately modest; headlines are noisy
# --------------------------------------------------------------------------
BULLISH = {
    "surge": 2.0, "surges": 2.0, "soar": 2.2, "soars": 2.2, "rally": 1.8,
    "rallies": 1.8, "jump": 1.5, "jumps": 1.5, "spike": 1.5, "climbs": 1.3,
    "gains": 1.2, "rises": 1.1, "rebound": 1.4, "recovery": 1.2, "breakout": 1.8,
    "all-time high": 2.5, "record high": 2.4, "ath": 2.0, "bullish": 2.0,
    "adoption": 1.5, "approval": 2.0, "approved": 2.0, "partnership": 1.3,
    "integration": 1.1, "upgrade": 1.3, "launch": 1.0, "listing": 1.8,
    "listed": 1.6, "inflow": 1.6, "inflows": 1.6, "accumulate": 1.4,
    "accumulation": 1.4, "buy": 0.8, "buying": 1.0, "institutional": 1.2,
    "etf approval": 2.6, "halving": 1.2, "burn": 1.0, "staking": 0.8,
    "whale buys": 1.8, "green": 0.7, "outperform": 1.4, "milestone": 1.1,
    "funding round": 1.3, "raises": 1.1, "mainnet": 1.2, "bull run": 2.2,
}

BEARISH = {
    "crash": -2.6, "crashes": -2.6, "plunge": -2.4, "plunges": -2.4,
    "plummet": -2.4, "tumble": -1.9, "tumbles": -1.9, "slump": -1.7,
    "drop": -1.3, "drops": -1.3, "falls": -1.2, "decline": -1.2,
    "sell-off": -2.0, "selloff": -2.0, "dump": -1.9, "dumps": -1.9,
    "bearish": -2.0, "correction": -1.3, "liquidation": -1.8,
    "liquidations": -1.8, "outflow": -1.6, "outflows": -1.6,
    "hack": -3.0, "hacked": -3.0, "exploit": -3.0, "exploited": -3.0,
    "breach": -2.4, "stolen": -2.6, "scam": -2.2, "fraud": -2.4,
    "lawsuit": -1.8, "sues": -1.8, "sued": -1.8, "charges": -1.6,
    "investigation": -1.5, "probe": -1.4, "ban": -2.2, "banned": -2.2,
    "crackdown": -2.0, "delisting": -2.6, "delisted": -2.6,
    "bankruptcy": -2.8, "insolvent": -2.8, "halt": -1.6, "halted": -1.6,
    "warning": -1.0, "risk": -0.6, "fear": -1.2, "panic": -2.0,
    "rug pull": -3.0, "rugpull": -3.0, "downgrade": -1.5, "red": -0.7,
    "underperform": -1.3, "capitulation": -2.0, "rejected": -1.8,
}

# headlines that should veto a trade on the affected coin outright
CRITICAL_PATTERNS = [
    r"\bhack(ed|ing)?\b", r"\bexploit(ed)?\b", r"\bdelist(ed|ing)?\b",
    r"\brug ?pull\b", r"\bbankrupt(cy)?\b", r"\binsolven(t|cy)\b",
    r"\bwithdrawals? (halted|suspended|paused)\b", r"\bfunds (stolen|drained)\b",
    r"\bsecurity breach\b", r"\btrading (halted|suspended)\b",
]

# market-wide events that raise volatility for everything
MACRO_PATTERNS = [
    r"\bfomc\b", r"\bfed(eral reserve)?\b", r"\bcpi\b", r"\binflation\b",
    r"\brate (cut|hike|decision)\b", r"\bsec\b", r"\betf\b", r"\bregulat",
    r"\bjobs report\b", r"\bnonfarm\b", r"\bgeopolit", r"\btariff",
]

# ticker -> extra names to match in headlines
COIN_ALIASES: dict[str, list[str]] = {
    "BTC": ["bitcoin"], "ETH": ["ethereum", "ether"], "SOL": ["solana"],
    "XRP": ["ripple"], "BNB": ["binance coin"], "ADA": ["cardano"],
    "DOGE": ["dogecoin"], "AVAX": ["avalanche"], "DOT": ["polkadot"],
    "MATIC": ["polygon"], "POL": ["polygon"], "LINK": ["chainlink"],
    "LTC": ["litecoin"], "TRX": ["tron"], "SHIB": ["shiba inu", "shiba"],
    "UNI": ["uniswap"], "ATOM": ["cosmos"], "XLM": ["stellar"],
    "NEAR": ["near protocol"], "APT": ["aptos"], "ARB": ["arbitrum"],
    "OP": ["optimism"], "FIL": ["filecoin"], "ICP": ["internet computer"],
    "HBAR": ["hedera"], "VET": ["vechain"], "INJ": ["injective"],
    "SUI": ["sui network"], "SEI": ["sei network"], "TIA": ["celestia"],
    "RUNE": ["thorchain"], "AAVE": ["aave"], "MKR": ["maker", "makerdao"],
    "LDO": ["lido"], "CRV": ["curve"], "SNX": ["synthetix"],
    "PEPE": ["pepe coin"], "WIF": ["dogwifhat"], "BONK": ["bonk"],
    "TON": ["toncoin", "telegram open network"], "ETC": ["ethereum classic"],
    "BCH": ["bitcoin cash"], "ALGO": ["algorand"], "FTM": ["fantom"],
    "SAND": ["the sandbox"], "MANA": ["decentraland"], "AXS": ["axie"],
    "GRT": ["the graph"], "IMX": ["immutable"], "RNDR": ["render"],
    "FET": ["fetch.ai", "artificial superintelligence"], "STX": ["stacks"],
}


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published: datetime
    score: float
    coins: list[str] = field(default_factory=list)
    critical: bool = False
    macro: bool = False
    matched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "link": self.link,
            "source": self.source,
            "published": self.published.isoformat(),
            "age_minutes": int(
                (datetime.now(timezone.utc) - self.published).total_seconds() / 60
            ),
            "score": round(self.score, 2),
            "coins": self.coins,
            "critical": self.critical,
            "macro": self.macro,
            "matched": self.matched,
        }


@dataclass
class CoinNews:
    """Aggregated news view for one coin."""
    sentiment: float = 0.0        # -1 .. +1
    item_count: int = 0
    critical: bool = False
    headlines: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sentiment": round(self.sentiment, 3),
            "item_count": self.item_count,
            "critical": self.critical,
            "headlines": self.headlines[:5],
        }


def _score_text(text: str) -> tuple[float, list[str]]:
    """Lexicon score for one headline. Multi-word phrases are checked first."""
    low = f" {text.lower()} "
    score, matched = 0.0, []
    for lexicon in (BULLISH, BEARISH):
        for phrase, weight in lexicon.items():
            if " " in phrase:
                if phrase in low:
                    score += weight
                    matched.append(phrase)
            else:
                if re.search(rf"\b{re.escape(phrase)}\b", low):
                    score += weight
                    matched.append(phrase)
    return score, matched


def _match_coins(text: str, bases: set[str]) -> list[str]:
    low = text.lower()
    hits = []
    for base in bases:
        if len(base) >= 3 and re.search(rf"\b{re.escape(base.lower())}\b", low):
            hits.append(base)
            continue
        # short tickers only match uppercase to avoid "OP" matching "open"
        if len(base) < 3 and re.search(rf"\b{re.escape(base)}\b", text):
            hits.append(base)
            continue
        for alias in COIN_ALIASES.get(base, []):
            if alias in low:
                hits.append(base)
                break
    return sorted(set(hits))


class NewsEngine:
    def __init__(self, cfg: NewsCfg):
        self.cfg = cfg
        self._lock = threading.RLock()
        self._items: list[NewsItem] = []
        self._fetched_at: float = 0.0
        self._errors: list[str] = []

    # ------------------------------------------------------------ fetching
    def _fetch_feed(self, url: str) -> list[NewsItem]:
        import feedparser
        import requests

        try:
            resp = requests.get(
                url,
                timeout=self.cfg.timeout_seconds,
                headers={"User-Agent": "Mozilla/5.0 (compatible; CryptoSignalBot/1.0)"},
            )
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
        except Exception as exc:  # network hiccups shouldn't kill the scan
            log.warning("feed failed %s: %s", url, exc)
            with self._lock:
                self._errors.append(f"{url}: {exc}")
            return []

        source = (parsed.feed.get("title") or url.split("/")[2]) if parsed.feed else url
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.cfg.max_age_hours)
        out: list[NewsItem] = []

        for entry in parsed.entries[:60]:
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            published = None
            for key in ("published_parsed", "updated_parsed"):
                tm = entry.get(key)
                if tm:
                    published = datetime(*tm[:6], tzinfo=timezone.utc)
                    break
            if published is None:
                published = datetime.now(timezone.utc)
            if published < cutoff:
                continue

            summary = re.sub(r"<[^>]+>", " ", entry.get("summary", ""))[:400]
            blob = f"{title}. {summary}"
            raw_score, matched = _score_text(blob)
            critical = any(re.search(p, blob.lower()) for p in CRITICAL_PATTERNS)
            macro = any(re.search(p, blob.lower()) for p in MACRO_PATTERNS)

            out.append(NewsItem(
                title=title,
                link=entry.get("link", ""),
                source=source,
                published=published,
                score=raw_score,
                critical=critical,
                macro=macro,
                matched=matched[:6],
            ))
        return out

    def refresh(self, bases: set[str], force: bool = False) -> None:
        if not self.cfg.enabled:
            return
        now = time.time()
        with self._lock:
            fresh = now - self._fetched_at < self.cfg.refresh_seconds
        if fresh and not force:
            return

        with self._lock:
            self._errors = []

        items: list[NewsItem] = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(self._fetch_feed, u): u for u in self.cfg.feeds}
            for fut in as_completed(futures):
                try:
                    items.extend(fut.result())
                except Exception as exc:
                    log.warning("feed worker failed: %s", exc)

        for item in items:
            item.coins = _match_coins(f"{item.title}", bases)

        # newest first, de-duplicated by normalised title
        items.sort(key=lambda i: i.published, reverse=True)
        seen, deduped = set(), []
        for item in items:
            key = re.sub(r"[^a-z0-9]", "", item.title.lower())[:70]
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        with self._lock:
            self._items = deduped[: self.cfg.max_items]
            self._fetched_at = now
        log.info("news: %d items from %d feeds", len(self._items), len(self.cfg.feeds))

    # ------------------------------------------------------------ querying
    def items(self) -> list[NewsItem]:
        with self._lock:
            return list(self._items)

    def errors(self) -> list[str]:
        with self._lock:
            return list(self._errors)

    def for_coin(self, base: str) -> CoinNews:
        """Time-decayed sentiment for one coin, normalised to -1..+1."""
        now = datetime.now(timezone.utc)
        weighted, weight_sum, matched_items = 0.0, 0.0, []

        for item in self.items():
            if base not in item.coins:
                continue
            age_h = max(0.0, (now - item.published).total_seconds() / 3600.0)
            # half-life of 8 hours: yesterday's news barely counts
            decay = 0.5 ** (age_h / 8.0)
            weighted += item.score * decay
            weight_sum += decay
            matched_items.append(item)

        if not matched_items:
            return CoinNews()

        avg = weighted / max(weight_sum, 1e-9)
        # squash: a ±3 average headline saturates the -1..+1 range
        sentiment = max(-1.0, min(1.0, avg / 3.0))
        critical = any(i.critical for i in matched_items[:10])
        return CoinNews(
            sentiment=sentiment,
            item_count=len(matched_items),
            critical=critical,
            headlines=[i.to_dict() for i in matched_items[:5]],
        )

    def market_mood(self) -> dict:
        """Overall tape sentiment plus a macro-event flag."""
        items = self.items()
        if not items:
            return {"sentiment": 0.0, "label": "no data", "macro_alert": False,
                    "item_count": 0, "critical_count": 0}

        now = datetime.now(timezone.utc)
        weighted, weight_sum = 0.0, 0.0
        for item in items:
            age_h = max(0.0, (now - item.published).total_seconds() / 3600.0)
            decay = 0.5 ** (age_h / 8.0)
            weighted += item.score * decay
            weight_sum += decay
        avg = weighted / max(weight_sum, 1e-9)
        sentiment = max(-1.0, min(1.0, avg / 3.0))

        if sentiment > 0.35:
            label = "bullish"
        elif sentiment > 0.12:
            label = "mildly bullish"
        elif sentiment < -0.35:
            label = "bearish"
        elif sentiment < -0.12:
            label = "mildly bearish"
        else:
            label = "neutral"

        recent = [i for i in items
                  if (now - i.published).total_seconds() < 6 * 3600]
        return {
            "sentiment": round(sentiment, 3),
            "label": label,
            "macro_alert": any(i.macro for i in recent[:25]),
            "item_count": len(items),
            "critical_count": sum(1 for i in items if i.critical),
        }
