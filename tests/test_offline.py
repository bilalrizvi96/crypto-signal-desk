"""Offline test harness — runs the whole pipeline on synthetic candles.

No network required. Verifies indicator maths against independent
reimplementations, then drives a full scan through a fake exchange so the
strategy, paper broker, live gating and dashboard renderer all execute.

    python tests/test_offline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import indicators as ta                      # noqa: E402
from bot.config import Config, load_config            # noqa: E402
from bot.dashboard import render_page                 # noqa: E402
from bot.data import SymbolInfo                       # noqa: E402
from bot.paper import PaperBroker                     # noqa: E402
from bot.strategy import build_signal, detect_regime  # noqa: E402

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = ""):
    (PASS if condition else FAIL).append(name)
    mark = "✓" if condition else "✗"
    print(f"  {mark} {name}" + (f"  — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# synthetic market generator
# ---------------------------------------------------------------------------
def synth(n: int = 500, start: float = 100.0, drift: float = 0.0004,
          vol: float = 0.012, seed: int = 7, tf_minutes: int = 5,
          late_drift: float | None = None, late_frac: float = 0.06) -> pd.DataFrame:
    """Geometric random walk. `late_drift` applies a stronger drift only over
    the final `late_frac` of the series — a market that has just started
    trending — which keeps long histories from compounding to absurd prices."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift, vol, n)
    if late_drift is not None:
        tail = max(1, int(n * late_frac))
        steps[-tail:] = rng.normal(late_drift, vol, tail)
    close = start * np.exp(np.cumsum(steps))
    spread = np.abs(rng.normal(0, vol * 0.6, n)) * close
    high = close + spread
    low = close - spread
    open_ = np.concatenate([[start], close[:-1]])
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])
    volume = np.abs(rng.normal(1000, 320, n)) * (1 + np.abs(steps) * 22)
    idx = pd.date_range("2026-01-01", periods=n, freq=f"{tf_minutes}min", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


# ---------------------------------------------------------------------------
# 1. indicator maths
# ---------------------------------------------------------------------------
def test_indicators():
    print("\n[1] Indicator maths")
    df = synth(400)

    # EMA against a hand-rolled recurrence
    e = ta.ema(df["close"], 20)
    manual, alpha = df["close"].iloc[:20].mean(), 2 / 21
    # pandas ewm(adjust=False) seeds on the first value, so replicate that instead
    manual = df["close"].iloc[0]
    for v in df["close"].iloc[1:]:
        manual = alpha * v + (1 - alpha) * manual
    check("EMA(20) matches the recurrence", abs(e.iloc[-1] - manual) < 1e-6,
          f"{e.iloc[-1]:.6f} vs {manual:.6f}")

    # RSI bounds and a known monotonic case
    r = ta.rsi(df["close"], 14).dropna()
    check("RSI stays in [0, 100]", bool((r >= 0).all() and (r <= 100).all()),
          f"range {r.min():.1f}–{r.max():.1f}")
    rising = pd.Series(np.arange(1, 60, dtype=float))
    check("RSI of a monotonic rise is 100", abs(ta.rsi(rising, 14).iloc[-1] - 100.0) < 1e-9)
    falling = pd.Series(np.arange(60, 1, -1, dtype=float))
    check("RSI of a monotonic fall is 0", abs(ta.rsi(falling, 14).iloc[-1]) < 1e-9)

    # ATR must be positive and bounded by the largest true range
    a = ta.atr(df, 14).dropna()
    tr = ta.true_range(df).dropna()
    check("ATR positive and ≤ max true range",
          bool((a > 0).all() and a.max() <= tr.max() + 1e-9),
          f"ATR last {a.iloc[-1]:.4f}")

    # MACD histogram identity
    m = ta.macd(df["close"])
    check("MACD hist == macd − signal",
          bool(((m["macd"] - m["signal"] - m["hist"]).abs().dropna() < 1e-9).all()))

    # Bollinger ordering
    bb = ta.bollinger(df["close"]).dropna()
    check("Bollinger upper > mid > lower",
          bool((bb["upper"] > bb["mid"]).all() and (bb["mid"] > bb["lower"]).all()))
    inside = ((bb["pct_b"] >= -0.5) & (bb["pct_b"] <= 1.5)).mean()
    check("%B mostly inside [-0.5, 1.5]", inside > 0.95, f"{inside:.1%}")

    # ADX components
    adx_df = ta.adx(df).dropna()
    check("ADX in [0, 100]",
          bool((adx_df["adx"] >= 0).all() and (adx_df["adx"] <= 100).all()),
          f"last {adx_df['adx'].iloc[-1]:.1f}")

    # Supertrend direction is only ±1
    st = ta.supertrend(df).dropna()
    check("Supertrend direction is ±1", set(np.unique(st["direction"])) <= {-1.0, 1.0})

    # Supertrend should be mostly long in a strong uptrend
    up = synth(400, drift=0.004, vol=0.006, seed=3)
    st_up = ta.supertrend(up).dropna()
    frac = (st_up["direction"] > 0).mean()
    check("Supertrend is long most of a strong uptrend", frac > 0.75, f"{frac:.0%} long")

    # StochRSI bounds
    sr = ta.stoch_rsi(df["close"]).dropna()
    check("StochRSI K in [0, 100]",
          bool((sr["k"] >= -1e-9).all() and (sr["k"] <= 100 + 1e-9).all()))

    # swing points must not look ahead
    sp = ta.swing_points(df, 3, 3)
    check("swing points leave the last 3 bars unconfirmed",
          not sp["swing_high"].iloc[-3:].any() and not sp["swing_low"].iloc[-3:].any())

    # enrich attaches everything without exploding
    en = ta.enrich(df)
    needed = ["ema9", "ema200", "rsi", "macd_hist", "atr", "bb_pct_b",
              "stoch_k", "adx", "st_dir", "obv", "vwap", "vol_z", "mfi", "squeeze"]
    missing = [c for c in needed if c not in en.columns]
    check("enrich() attaches the full indicator set", not missing, f"missing {missing}")
    tail_nans = en[needed].iloc[-1].isna().sum()
    check("no NaNs on the last enriched row", tail_nans == 0, f"{tail_nans} NaN")


# ---------------------------------------------------------------------------
# 2. regime + strategy behaviour
# ---------------------------------------------------------------------------
def test_strategy():
    print("\n[2] Strategy behaviour")
    cfg = Config()

    up = ta.enrich(synth(400, drift=0.005, vol=0.007, seed=11))
    down = ta.enrich(synth(400, drift=-0.005, vol=0.007, seed=12))
    flat = ta.enrich(synth(400, drift=0.0, vol=0.003, seed=13))

    regime_up, meta = detect_regime(up)
    check("uptrend is detected as trending/mixed", regime_up in ("trending", "mixed"),
          f"{regime_up}, ADX {meta['adx']}")

    def sig(frames, name):
        return build_signal(
            symbol="TEST/USDT", base="TEST", bucket="large", mode="scalp",
            mode_cfg=cfg.scalp, risk_cfg=cfg.risk, frames=frames,
            coin_news={}, news_weight=0.12, block_on_critical=True,
        )

    s_up = sig({"entry": up, "confirm": up, "bias": up}, "up")
    check("strong uptrend scores long", s_up.score > 30, f"score {s_up.score:+.1f}")
    check("uptrend signal direction is long", s_up.direction == "long")
    check("long stop sits below entry", s_up.stop < s_up.entry,
          f"entry {s_up.entry:.2f} stop {s_up.stop:.2f}")
    check("long targets sit above entry", all(t > s_up.entry for t in s_up.targets))

    s_dn = sig({"entry": down, "confirm": down, "bias": down}, "down")
    check("strong downtrend scores short", s_dn.score < -30, f"score {s_dn.score:+.1f}")
    check("short stop sits above entry", s_dn.stop > s_dn.entry)
    check("short targets sit below entry", all(t < s_dn.entry for t in s_dn.targets))

    s_flat = sig({"entry": flat, "confirm": flat, "bias": flat}, "flat")
    check("flat market scores near zero", abs(s_flat.score) < 45, f"score {s_flat.score:+.1f}")

    # conflicting bias must cut the score
    s_conflict = sig({"entry": up, "confirm": up, "bias": down}, "conflict")
    check("opposing higher-timeframe bias cuts the score",
          abs(s_conflict.score) < abs(s_up.score),
          f"{s_conflict.score:+.1f} vs {s_up.score:+.1f}")
    check("conflict raises a warning", any("counter-trend" in w for w in s_conflict.warnings))

    # critical news must veto
    s_news = build_signal(
        symbol="TEST/USDT", base="TEST", bucket="large", mode="scalp",
        mode_cfg=cfg.scalp, risk_cfg=cfg.risk,
        frames={"entry": up, "confirm": up, "bias": up},
        coin_news={"sentiment": -0.2, "critical": True, "item_count": 3},
        news_weight=0.12, block_on_critical=True,
    )
    check("critical news blocks the entry", s_news.action == "none")
    check("block reason is surfaced", any(w.startswith("BLOCKED") for w in s_news.warnings))

    # positive news should lift a long score
    s_good = build_signal(
        symbol="TEST/USDT", base="TEST", bucket="large", mode="scalp",
        mode_cfg=cfg.scalp, risk_cfg=cfg.risk,
        frames={"entry": up, "confirm": up, "bias": up},
        coin_news={"sentiment": 0.8, "critical": False, "item_count": 5},
        news_weight=0.12, block_on_critical=True,
    )
    check("bullish news lifts a long score", s_good.score > s_up.score,
          f"{s_good.score:+.1f} vs {s_up.score:+.1f}")

    # sizing sanity
    risk_amount = cfg.risk.account_equity * cfg.risk.risk_per_trade_pct / 100.0
    implied = s_up.position_size_units * abs(s_up.entry - s_up.stop)
    check("position size risks the configured amount (or hits the notional cap)",
          abs(implied - risk_amount) < 0.01 or
          abs(s_up.position_notional - cfg.risk.max_notional_per_trade) < 0.01,
          f"risking {implied:.2f} of {risk_amount:.2f}")
    check("score is clamped to ±100", -100 <= s_up.score <= 100)
    check("votes are populated", len(s_up.votes) >= 8, f"{len(s_up.votes)} votes")
    check("every vote carries a human-readable note",
          all(v["note"] for v in s_up.votes))

    return s_up, s_dn, s_flat


# ---------------------------------------------------------------------------
# 3. paper broker
# ---------------------------------------------------------------------------
def test_paper(signals):
    print("\n[3] Paper broker")
    s_up = signals[0]
    cfg = Config()
    path = Path("/tmp/paper_test_state.json")
    if path.exists():
        path.unlink()
    broker = PaperBroker(cfg.risk, path=path)

    s_up.action = "enter"
    pos = broker.open_from_signal(s_up)
    check("opens a position from a firing signal", pos is not None)
    check("no duplicate position for the same symbol+mode",
          broker.open_from_signal(s_up) is None)

    # walk price to target 1 → partial exit + breakeven stop
    broker.mark_to_market({s_up.symbol: s_up.targets[0] * 1.0001})
    live_pos = list(broker.positions.values())
    check("target 1 scales out half", live_pos and live_pos[0].tp1_hit)
    check("stop moves to breakeven after target 1",
          live_pos and abs(live_pos[0].stop - live_pos[0].entry) < 1e-9)

    # walk to the final target → full close
    broker.mark_to_market({s_up.symbol: s_up.targets[-1] * 1.0001})
    check("final target closes the position", not broker.positions)
    check("a closed trade is recorded", len(broker.closed) == 1)
    trade = broker.closed[0]
    check("winning trade has positive P&L", trade.pnl > 0, f"{trade.pnl:+.4f}")
    check("R multiple is sane for a 1R/2R exit", 0.5 < trade.r_multiple < 2.5,
          f"{trade.r_multiple:.2f}R")

    # a losing trade
    s2 = signals[1]
    s2.action = "enter"
    broker.open_from_signal(s2)
    broker.mark_to_market({s2.symbol: s2.stop * 1.0001})
    check("stop-out closes the position", len(broker.closed) == 2)
    check("losing trade has negative P&L", broker.closed[1].pnl < 0,
          f"{broker.closed[1].pnl:+.4f}")

    stats = broker.stats({})
    check("stats report both trades", stats["closed_trades"] == 2)
    check("win rate computed", stats["win_rate"] == 50.0, f"{stats['win_rate']}%")
    check("fees were charged", stats["total_fees"] > 0, f"${stats['total_fees']:.4f}")
    check("equity curve has points", len(stats["equity_curve_points"]) > 0)

    # persistence round-trip
    broker.save()
    reloaded = PaperBroker(cfg.risk, path=path)
    check("state survives a reload", len(reloaded.closed) == 2)

    # daily loss kill switch
    broker.day_start_equity = 1000.0
    broker.cash = 900.0
    broker.mark_to_market({})
    check("daily loss limit halts new entries", broker.halted_reason is not None,
          str(broker.halted_reason)[:60])
    check("halted broker refuses new positions", broker.open_from_signal(s_up) is None)

    path.unlink(missing_ok=True)
    return stats


# ---------------------------------------------------------------------------
# 4. full scan through a fake exchange
# ---------------------------------------------------------------------------
class FakeMarketData:
    """Stands in for ccxt: deterministic synthetic candles per symbol."""

    # (symbol, base, bucket, daily drift, daily vol, seed)
    SYMS = [
        ("BTC/USDT", "BTC", "large", 0.004, 0.008, 1),
        ("ETH/USDT", "ETH", "large", -0.004, 0.009, 2),
        ("SOL/USDT", "SOL", "large", 0.0, 0.004, 3),
        ("INJ/USDT", "INJ", "small", 0.006, 0.014, 4),
        ("SEI/USDT", "SEI", "small", -0.003, 0.011, 5),
        # two unambiguous trends so the entry → paper-position path executes
        ("PUMP/USDT", "PUMP", "small", 0.060, 0.010, 6),
        ("DUMP/USDT", "DUMP", "small", -0.055, 0.010, 7),
    ]

    def __init__(self, cfg):
        self.cfg = cfg

    def build_universe(self):
        out = []
        for i, (sym, base, bucket, drift, vol, seed) in enumerate(self.SYMS):
            df = self._df(sym, "1h")
            last = float(df["close"].iloc[-1])
            prev = float(df["close"].iloc[-25])       # 24 hourly bars back
            out.append(SymbolInfo(
                symbol=sym, base=base, quote_volume=5e8 / (i + 1),
                last=last,
                change_24h=round((last / prev - 1.0) * 100.0, 2), rank=i + 1,
                bucket=bucket, spread_pct=0.02,
            ))
        return out

    _base_cache: dict[str, pd.DataFrame] = {}

    def _base(self, symbol: str) -> pd.DataFrame:
        """One 1-minute series per symbol, resampled into every timeframe —
        so a 4h close and a 5m close describe the same market, the way a real
        exchange's data does."""
        if symbol not in self._base_cache:
            _, base, _, drift, vol, seed = {s[0]: s for s in self.SYMS}[symbol]
            # 5-minute base, 400 days deep — enough for 400 daily bars on top.
            # The two trend instruments only turn in the final stretch.
            trending = base in ("PUMP", "DUMP")
            self._base_cache[symbol] = synth(
                400 * 288, start=100 + seed * 37,
                drift=(0.0 if trending else drift) / 288,
                vol=vol / 17.0,
                seed=seed * 100, tf_minutes=5,
                late_drift=(drift / 288) if trending else None,
                late_frac=0.05,
            )
        return self._base_cache[symbol]

    def _df(self, symbol, timeframe):
        rule = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
                "1h": "1h", "4h": "4h", "1d": "1D"}[timeframe]
        agg = self._base(symbol).resample(rule).agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}
        ).dropna()
        return agg.iloc[-400:]

    def fetch_ohlcv(self, symbol, timeframe, limit=400, ttl=None):
        return self._df(symbol, timeframe)

    def clear_cache(self):
        pass


def test_scan():
    print("\n[4] Full scan through a fake exchange")
    import bot.scanner as scanner_mod

    cfg = Config()
    cfg.news.enabled = False       # no network in this harness
    cfg.paper_enabled = True
    cfg.live.enabled = False

    real_md = scanner_mod.MarketData
    scanner_mod.MarketData = FakeMarketData
    try:
        state_path = Path("/tmp/paper_scan_state.json")
        state_path.unlink(missing_ok=True)
        sc = scanner_mod.Scanner(cfg)
        sc.paper.path = state_path
        result = sc.scan()
    finally:
        scanner_mod.MarketData = real_md

    check("scan produced a result", bool(result.get("generated_at")))
    check("no fatal scan error", "error" not in result, str(result.get("error"))[:80])
    check("all 7 symbols scanned", result["counts"]["symbols"] == 7,
          f"{result['counts']['symbols']}")
    check("both modes produced signals",
          len(result["signals"].get("scalp", [])) == 7 and
          len(result["signals"].get("swing", [])) == 7,
          f"scalp {len(result['signals'].get('scalp', []))}, "
          f"swing {len(result['signals'].get('swing', []))}")
    check("no per-symbol errors", not result["errors"], str(result["errors"])[:100])
    check("counts are internally consistent",
          result["counts"]["long"] + result["counts"]["short"] <= result["counts"]["enter"])
    check("paper stats present", result["paper"] is not None)
    check("trending instruments fired at least one entry",
          result["counts"]["enter"] >= 1, f"{result['counts']['enter']} entries")
    check("a long and a short both appeared",
          result["counts"]["long"] >= 1 and result["counts"]["short"] >= 1,
          f"{result['counts']['long']}L / {result['counts']['short']}S")
    check("paper opened positions from those entries",
          result["paper"]["open_positions"] >= 1,
          f"{result['paper']['open_positions']} open")
    check("live gate reports disabled", result["live"]["gate"]["enabled"] is False)

    every = [s for m in result["signals"].values() for s in m]
    check("every signal has entry/stop/targets",
          all(s["entry"] and s["stop"] and s["targets"] for s in every))
    check("every signal has a positive R:R", all(s["rr"] > 0 for s in every))
    check("stops are on the correct side of entry",
          all((s["stop"] < s["entry"]) if s["direction"] == "long"
              else (s["stop"] > s["entry"]) if s["direction"] == "short" else True
              for s in every))
    check("sparkline data attached", all(len(s["spark"]) > 10 for s in every))

    # Regression: positions must be marked against the live ticker price, not a
    # per-mode bar close. Mixing a 4h close with a 5m close once produced a
    # -1500% "equity" reading out of five 1%-risk positions.
    paper = result["paper"]
    check("paper equity stays in a plausible band",
          abs(paper["total_return_pct"]) < 40,
          f"{paper['total_return_pct']:+.2f}%")
    check("unrealised P&L is bounded by open risk",
          abs(paper["unrealised"]) < cfg.risk.account_equity * 0.5,
          f"{paper['unrealised']:+.2f}")

    # swing positions must inherit the swing hold limit, not a hardcoded one
    for pos in paper["positions"]:
        expected = cfg.swing.max_hold_bars if pos["mode"] == "swing" else cfg.scalp.max_hold_bars
        check(f"{pos['mode']} position uses its own max hold ({expected} bars)",
              pos["max_hold_bars"] == expected, f"got {pos['max_hold_bars']}")

    # a second scan should not crash and should keep state
    result2 = sc.scan()
    check("second scan succeeds", result2["scan_number"] == 2)

    Path("/tmp/paper_scan_state.json").unlink(missing_ok=True)
    return result2


# ---------------------------------------------------------------------------
# 5. dashboard render
# ---------------------------------------------------------------------------
def test_dashboard(result):
    print("\n[5] Dashboard render")
    html = render_page(result, live=False)
    out = Path("/tmp/dashboard_test.html")
    out.write_text(html, encoding="utf-8")

    check("html produced", len(html) > 20000, f"{len(html):,} bytes")
    check("no unreplaced placeholders",
          "__INITIAL_DATA__" not in html and "__POLL_MS__" not in html
          and "__LIVE__" not in html)
    check("initial data embedded", '"scan_number"' in html)
    check("doctype and title present",
          html.startswith("<!doctype html>") and "<title>" in html)
    check("no naked closing script tag inside the JSON payload",
          html.count("</script>") == 1, f"{html.count('</script>')} found")

    # the embedded payload must be valid JSON
    import json
    start = html.index("const INITIAL = ") + len("const INITIAL = ")
    end = html.index(";\nconst POLL_MS", start)
    payload = html[start:end].replace("<\\/", "</")
    try:
        parsed = json.loads(payload)
        check("embedded payload is valid JSON", True,
              f"{len(parsed.get('universe', []))} symbols")
    except Exception as exc:
        check("embedded payload is valid JSON", False, str(exc)[:80])

    # live variant
    live_html = render_page(result, poll_ms=60000, live=True)
    check("live variant sets LIVE = true", "const LIVE = true;" in live_html)
    check("static variant sets LIVE = false", "const LIVE = false;" in html)

    print(f"      → wrote {out}")
    return out


# ---------------------------------------------------------------------------
# 6. config + live gating
# ---------------------------------------------------------------------------
def test_config_and_gating():
    print("\n[6] Config and live-trading gates")
    cfg = load_config()
    check("config.yaml loads", cfg.universe.exchange in ("binance", "bybit", "okx",
                                                         "kraken", "kucoin"),
          cfg.universe.exchange)
    check("live trading defaults to off", cfg.live.enabled is False)
    check("dry run defaults to on", cfg.live.dry_run is True)
    check("testnet defaults to on", cfg.live.testnet is True)
    check("scalp uses fast timeframes", cfg.scalp.entry_tf in ("1m", "3m", "5m", "15m"),
          cfg.scalp.entry_tf)
    check("swing uses slow timeframes", cfg.swing.entry_tf in ("1h", "4h", "1d"),
          cfg.swing.entry_tf)

    from bot.live import LiveTrader
    import os
    trader = LiveTrader(cfg.live, cfg.risk)
    check("trader is not armed by default", trader.is_armed() is False)
    gate = trader.gate_status()
    check("gate reports every lock",
          all(k in gate for k in ("enabled", "dry_run", "has_credentials",
                                  "confirm_phrase_ok", "armed")))

    # even with credentials + confirm phrase, config still blocks
    os.environ["EXCHANGE_API_KEY"] = "test"
    os.environ["EXCHANGE_API_SECRET"] = "test"
    os.environ["LIVE_CONFIRM"] = "I_UNDERSTAND_THE_RISK"
    trader2 = LiveTrader(cfg.live, cfg.risk)
    check("credentials alone do not arm it", trader2.is_armed() is False)
    for k in ("EXCHANGE_API_KEY", "EXCHANGE_API_SECRET", "LIVE_CONFIRM"):
        os.environ.pop(k, None)


# ---------------------------------------------------------------------------
def main():
    print("═" * 74)
    print("  Crypto Signal Desk — offline test suite")
    print("═" * 74)

    test_indicators()
    signals = test_strategy()
    test_paper(signals)
    result = test_scan()
    test_dashboard(result)
    test_config_and_gating()

    print("\n" + "═" * 74)
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"    ✗ {f}")
    print("═" * 74)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
