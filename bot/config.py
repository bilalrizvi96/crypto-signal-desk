"""Configuration loading — YAML file + environment overrides."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"
STATE_DIR = Path(os.getenv("BOT_STATE_DIR") or ROOT / "state")   # paper state, order log


@dataclass
class UniverseCfg:
    exchange: str = "binance"
    fallback_exchange: str = "okx"     # read prices here while `exchange` rate-limits us ("" = none)
    market_type: str = "spot"          # "spot" or "swap" (perps, needed for real shorts)
    quote: str = "USDT"
    large_cap_top: int = 12            # top N by 24h quote volume = "large"
    small_cap_from: int = 25           # rank window for "small" coins
    small_cap_to: int = 90
    small_cap_count: int = 10          # how many small caps to actually scan
    min_quote_volume_usd: float = 3_000_000.0   # liquidity floor, skip illiquid junk
    max_spread_pct: float = 0.15       # skip anything with a wider spread than this
    include: list[str] = field(default_factory=list)   # always scan these, e.g. ["BTC/USDT"]
    exclude: list[str] = field(default_factory=lambda: [
        # leveraged tokens and stable/stable pairs produce garbage signals
        "USDC/USDT", "FDUSD/USDT", "TUSD/USDT", "DAI/USDT", "BUSD/USDT",
    ])


@dataclass
class ModeCfg:
    """One trading mode: which timeframes, how much history, how tight the risk."""
    enabled: bool = True
    entry_tf: str = "5m"
    confirm_tf: str = "15m"
    bias_tf: str = "1h"
    limit: int = 400                   # candles per timeframe
    atr_stop_mult: float = 1.2
    targets_r: list[float] = field(default_factory=lambda: [1.0, 2.0])
    min_score: float = 55.0            # |score| needed to fire a signal
    watch_score: float = 40.0          # |score| needed to show as "watch"
    min_adx: float = 18.0              # below this the market is chopping
    max_hold_bars: int = 24            # paper trades time out after this many entry-TF bars
    min_atr_pct: float = 0.10          # skip dead markets (ATR < x% of price)
    max_atr_pct: float = 8.0           # skip blowoff volatility
    # confirm/bias frames only read the trend, so they can make do with less
    # history than the entry frame — 52 weekly bars is a year, 210 is four
    min_htf_bars: int = 210


@dataclass
class NewsCfg:
    enabled: bool = True
    feeds: list[str] = field(default_factory=lambda: [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
        "https://cryptopanic.com/news/rss/",
        "https://decrypt.co/feed",
        "https://bitcoinmagazine.com/feed",
        "https://www.theblock.co/rss.xml",
        "https://cryptoslate.com/feed/",
        "https://news.bitcoin.com/feed/",
    ])
    max_age_hours: int = 24
    max_items: int = 120
    refresh_seconds: int = 900         # re-pull feeds every 15 min
    sentiment_weight: float = 0.12     # how much news can move a technical score
    block_on_critical: bool = True     # veto signals on hack/exploit/delist headlines
    timeout_seconds: int = 10


@dataclass
class RiskCfg:
    account_equity: float = 1000.0     # used for position sizing maths
    risk_per_trade_pct: float = 1.0    # % of equity risked per trade
    max_open_positions: int = 5
    max_leverage: float = 3.0
    daily_loss_limit_pct: float = 5.0  # kill switch: stop trading after this drawdown
    max_notional_per_trade: float = 250.0
    profit_goal_pct: float = 20.0      # practice return to aim for; 0 turns the goal off
    lock_at_goal: bool = True          # at the goal: bank open practice trades, open no new ones


@dataclass
class LiveCfg:
    """Live trading is off by default and needs several explicit unlocks."""
    enabled: bool = False
    exchange: str = "binance"
    market_type: str = "spot"
    api_key_env: str = "EXCHANGE_API_KEY"
    api_secret_env: str = "EXCHANGE_API_SECRET"
    testnet: bool = True
    dry_run: bool = True               # log the order instead of sending it
    order_type: str = "market"
    confirm_phrase_env: str = "LIVE_CONFIRM"
    confirm_phrase: str = "I_UNDERSTAND_THE_RISK"
    # modes allowed to send exchange orders; scalp stays paper-only until it
    # proves itself (5 wins in its first 24 practice trades)
    modes: list[str] = field(default_factory=lambda: ["swing", "position"])


@dataclass
class AICfg:
    """Claude reviews every trade before it opens. An AI "skip" always blocks
    the trade. When the AI can't review (no key, an error, a spent daily
    budget), `required` decides: true skips the trade, false lets it open on
    the desk's own rules."""
    enabled: bool = True
    required: bool = False
    model: str = "claude-opus-5-5"
    effort: str = "high"
    web_search: bool = True
    max_searches: int = 5              # web searches per review
    max_reviews_per_day: int = 30      # hard cap on paid reviews (UTC day)
    min_confidence: int = 60           # "take" below this is treated as skip
    cache_minutes: int = 45            # reuse a verdict for the same setup
    workers: int = 2                   # reviews run in the background, in parallel
    api_key_env: str = "ANTHROPIC_API_KEY"


@dataclass
class ServerCfg:
    host: str = "127.0.0.1"
    port: int = 8787
    scan_interval_seconds: int = 60
    open_browser: bool = True


@dataclass
class Config:
    universe: UniverseCfg = field(default_factory=UniverseCfg)
    scalp: ModeCfg = field(default_factory=lambda: ModeCfg(
        entry_tf="5m", confirm_tf="15m", bias_tf="1h", limit=400,
        atr_stop_mult=1.2, targets_r=[1.0, 2.0], min_score=55.0,
        watch_score=40.0, min_adx=18.0, max_hold_bars=24,
        min_atr_pct=0.10, max_atr_pct=8.0,
    ))
    swing: ModeCfg = field(default_factory=lambda: ModeCfg(
        entry_tf="4h", confirm_tf="1d", bias_tf="1d", limit=400,
        atr_stop_mult=1.6, targets_r=[1.5, 3.0], min_score=50.0,
        watch_score=35.0, min_adx=20.0, max_hold_bars=30,
        min_atr_pct=0.5, max_atr_pct=25.0,
    ))
    position: ModeCfg = field(default_factory=lambda: ModeCfg(
        entry_tf="1d", confirm_tf="1w", bias_tf="1w", limit=400,
        atr_stop_mult=2.0, targets_r=[2.0, 4.0], min_score=50.0,
        watch_score=35.0, min_adx=18.0, max_hold_bars=60,
        min_atr_pct=1.0, max_atr_pct=10.0, min_htf_bars=52,
    ))
    news: NewsCfg = field(default_factory=NewsCfg)
    risk: RiskCfg = field(default_factory=RiskCfg)
    live: LiveCfg = field(default_factory=LiveCfg)
    ai: AICfg = field(default_factory=AICfg)
    server: ServerCfg = field(default_factory=ServerCfg)
    paper_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _merge(dc, data: dict):
    """Overlay a dict onto a dataclass instance, ignoring unknown keys."""
    if not isinstance(data, dict):
        return dc
    for key, value in data.items():
        if not hasattr(dc, key):
            continue
        current = getattr(dc, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _merge(current, value)
        else:
            setattr(dc, key, value)
    return dc


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def load_env_file(path: Path) -> list[str]:
    """Read KEY=VALUE lines from a .env file into os.environ.

    Variables already set in the shell win, and blank values are skipped, so
    an empty template never overrides anything. Returns the names it set.
    """
    if not path.exists():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def load_config(path: str | Path | None = None, use_env_file: bool = True) -> Config:
    if use_env_file:
        load_env_file(ROOT / ".env")
    cfg = Config()
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    if p.exists():
        with open(p, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        _merge(cfg, data)

    # environment overrides for the things you'd flip per-run
    if os.getenv("BOT_EXCHANGE"):
        cfg.universe.exchange = os.environ["BOT_EXCHANGE"]
    if os.getenv("BOT_PORT"):
        cfg.server.port = int(os.environ["BOT_PORT"])
    if os.getenv("BOT_EQUITY"):
        cfg.risk.account_equity = float(os.environ["BOT_EQUITY"])

    # hosting platforms (Render, Koyeb…) hand the port in $PORT and need the
    # server reachable from outside the container
    if os.getenv("PORT"):
        cfg.server.port = int(os.environ["PORT"])
    if os.getenv("BOT_HOST"):
        cfg.server.host = os.environ["BOT_HOST"]
    if os.getenv("BOT_OPEN_BROWSER"):
        cfg.server.open_browser = _truthy(os.environ["BOT_OPEN_BROWSER"])
    # a public deployment sets this false so it can never trade, whatever config.yaml says
    if os.getenv("BOT_LIVE_ENABLED"):
        cfg.live.enabled = _truthy(os.environ["BOT_LIVE_ENABLED"])

    STATE_DIR.mkdir(exist_ok=True)
    return cfg
