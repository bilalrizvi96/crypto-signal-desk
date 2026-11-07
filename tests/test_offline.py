"""Offline test harness — runs the whole pipeline on synthetic candles.

No network required. Verifies indicator maths against independent
reimplementations, then drives a full scan through a fake exchange so the
strategy, paper broker, live gating and dashboard renderer all execute.

    python tests/test_offline.py
"""
from __future__ import annotations

import json
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
import bot.settings as settings_mod                   # noqa: E402
import tempfile as _tempfile                          # noqa: E402

# never read or write the real dashboard settings while testing
settings_mod.STATE_DIR = Path(_tempfile.mkdtemp())

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

    # a position only ages on its own timeframe's bars
    age_path = Path("/tmp/paper_test_aging.json")
    age_path.unlink(missing_ok=True)
    ager = PaperBroker(cfg.risk, path=age_path)
    aged = ager.open_from_signal(s_up)
    check("aging test opened a position", aged is not None)
    if aged:
        ager.mark_to_market({s_up.symbol: aged.entry}, {(s_up.symbol, "other-tf")})
        check("a bar on another timeframe does not age a position", aged.bars_held == 0)
        ager.mark_to_market({s_up.symbol: aged.entry}, {(s_up.symbol, aged.entry_tf)})
        check("a bar on its own timeframe ages it by one", aged.bars_held == 1)
    age_path.unlink(missing_ok=True)

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

    # success estimate: 40% prior worth 10 trades, blended with 1W / 1L
    fresh = PaperBroker(cfg.risk, path=Path("/tmp/paper_test_fresh.json"))
    check("success chance starts at the 40% prior",
          fresh.success_chance("scalp")["pct"] == 40.0
          and fresh.success_chance("scalp")["basis"] == "prior")
    modes = {t.mode for t in broker.closed}
    if len(modes) == 1:
        sc = broker.success_chance(modes.pop())
        check("success chance blends paper results with the prior",
              sc["pct"] == 41.7 and sc["trades"] == 2 and sc["basis"] == "paper",
              f"{sc['pct']}% from {sc['trades']} trades")

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
                "1h": "1h", "4h": "4h", "1d": "1D", "1w": "W-MON"}[timeframe]
        agg = self._base(symbol).resample(rule).agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}
        ).dropna()
        return agg.iloc[-400:]

    def fetch_ohlcv(self, symbol, timeframe, limit=400, ttl=None):
        return self._df(symbol, timeframe)

    def symbol_info(self, symbol):
        return next((s for s in self.build_universe() if s.symbol == symbol), None)

    def search(self, query, limit=12):
        q = query.upper()
        return [{"symbol": s.symbol, "base": s.base, "name": "", "asset_class": "crypto",
                 "last": s.last, "change_24h": s.change_24h, "quote_volume": s.quote_volume}
                for s in self.build_universe() if q in s.base][:limit]

    def clear_cache(self):
        pass


def test_scan():
    print("\n[4] Full scan through a fake exchange")
    import bot.scanner as scanner_mod

    cfg = Config()
    cfg.news.enabled = False       # no network in this harness
    cfg.paper_enabled = True
    cfg.live.enabled = False
    cfg.ai.enabled = False         # the AI gate has its own section below

    real_md = scanner_mod.MarketData
    scanner_mod.MarketData = FakeMarketData
    try:
        state_path = Path("/tmp/paper_scan_state.json")
        state_path.unlink(missing_ok=True)
        sc = scanner_mod.Scanner(cfg)
        # the broker loaded the real state/paper_state.json on construction;
        # point it at a temp file and start empty so live positions can't leak in
        sc.paper.path = state_path
        sc.paper.reset()
        result = sc.scan()
    finally:
        scanner_mod.MarketData = real_md

    check("scan produced a result", bool(result.get("generated_at")))
    check("no fatal scan error", "error" not in result, str(result.get("error"))[:80])
    check("all 7 symbols scanned", result["counts"]["symbols"] == 7,
          f"{result['counts']['symbols']}")
    counts = {m: len(result["signals"].get(m, [])) for m in ("scalp", "swing", "position")}
    check("all three modes produced signals", all(n == 7 for n in counts.values()), str(counts))
    pos_sig = next((s for s in result["signals"].get("position", [])), None)
    check("long-term mode reads daily entries against weekly trend",
          pos_sig is not None and pos_sig["timeframes"] == {"entry": "1d", "confirm": "1w", "bias": "1w"})
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
    check("every signal carries a success chance",
          all(0 < s["success"]["pct"] < 100 for s in every))
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
        expected = getattr(cfg, pos["mode"]).max_hold_bars
        check(f"{pos['mode']} position uses its own max hold ({expected} bars)",
              pos["max_hold_bars"] == expected, f"got {pos['max_hold_bars']}")

    # a second scan should not crash and should keep state
    result2 = sc.scan()
    check("second scan succeeds", result2["scan_number"] == 2)

    # open positions keep their live price, even after leaving the scanned list
    check("open positions carry a live mark and P&L",
          all(p["mark"] is not None and p["unrealised"] is not None
              for p in result2["paper"]["positions"]) and result2["paper"]["positions"])
    held = {p.symbol for p in sc.paper.positions.values()}
    sc._universe = [s for s in sc._universe if s.symbol not in held]
    result3 = sc.scan()
    check("a position whose coin left the scan list is still priced",
          all(p["mark"] is not None for p in result3["paper"]["positions"]),
          f"{len(held)} held")

    # best-for-scalping ranking
    best = result2.get("best_scalp", [])
    check("best-for-scalping list is populated", 1 <= len(best) <= 5, f"{len(best)} picks")
    check("best-for-scalping is ranked by fit",
          all(best[i]["fit"]["score"] >= best[i + 1]["fit"]["score"] for i in range(len(best) - 1)))
    check("scalp fit is 0–100 with a verdict",
          all(0 <= b["fit"]["score"] <= 100 and b["fit"]["verdict"] in ("good", "fair", "poor")
              for b in best))
    check("only scalp signals carry a scalp fit",
          all(not s["scalp_fit"] for s in result2["signals"]["swing"]))

    # search + on-demand analysis
    hits = sc.search("PU")
    check("search finds a pair by ticker prefix", any(h["base"] == "PUMP" for h in hits))
    open_before = len(sc.paper.positions)
    one = sc.analyse_one("PUMP/USDT")
    check("analysing one pair returns every mode",
          {s["mode"] for s in one.get("signals", [])} == {"scalp", "swing", "position"},
          str(one.get("error", ""))[:60])
    check("analysis includes a scalp fit", bool(one.get("scalp_fit")))
    check("analysed signals carry a success chance",
          all(s["success"]["pct"] > 0 for s in one.get("signals", [])))
    check("analysing does not open paper positions", len(sc.paper.positions) == open_before)
    check("unknown pair returns an error, not a crash",
          "error" in sc.analyse_one("NOPE/USDT"))

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
    check("search box, wallet and learn link present",
          'id="searchInput"' in html and 'id="walletPanel"' in html
          and "tradingview.com/education" in html)

    print(f"      → wrote {out}")
    return out


# ---------------------------------------------------------------------------
# 6. config + live gating
# ---------------------------------------------------------------------------
def test_config_and_gating():
    print("\n[6] Config and live-trading gates")
    import os
    env_before = dict(os.environ)
    # read config.yaml without the local .env: tests must never see real keys
    cfg_file = load_config(use_env_file=False)
    check("config.yaml loads", cfg_file.universe.exchange in ("binance", "bybit", "okx",
                                                              "kraken", "kucoin"),
          cfg_file.universe.exchange)
    check("scalp uses fast timeframes", cfg_file.scalp.entry_tf in ("1m", "3m", "5m", "15m"),
          cfg_file.scalp.entry_tf)
    check("swing uses slow timeframes", cfg_file.swing.entry_tf in ("1h", "4h", "1d"),
          cfg_file.swing.entry_tf)
    check("the test suite did not load .env", os.environ.get("LIVE_CONFIRM") == env_before.get("LIVE_CONFIRM"))

    # safety checks run on the shipped code defaults, not on a local config
    # someone has deliberately armed
    cfg = Config()
    from bot.config import LiveCfg
    check("live trading defaults to off", LiveCfg().enabled is False)
    check("dry run defaults to on", cfg.live.dry_run is True)
    check("testnet defaults to on", cfg.live.testnet is True)

    from bot.live import LiveTrader
    real_init = LiveTrader._init_exchange
    LiveTrader._init_exchange = lambda self: None          # no network, no exchange
    try:
        for k in ("EXCHANGE_API_KEY", "EXCHANGE_API_SECRET", "LIVE_CONFIRM"):
            os.environ.pop(k, None)
        trader = LiveTrader(cfg.live, cfg.risk)
        check("trader is not armed by default", trader.is_armed() is False)
        gate = trader.gate_status()
        check("gate reports every lock",
              all(k in gate for k in ("enabled", "dry_run", "has_credentials",
                                      "confirm_phrase_ok", "armed")))

        # even with credentials + confirm phrase, dry_run still blocks
        os.environ["EXCHANGE_API_KEY"] = "test"
        os.environ["EXCHANGE_API_SECRET"] = "test"
        os.environ["LIVE_CONFIRM"] = "I_UNDERSTAND_THE_RISK"
        live_on = LiveCfg(enabled=True)
        trader2 = LiveTrader(live_on, cfg.risk)
        check("credentials alone do not arm it", trader2.is_armed() is False)

        from types import SimpleNamespace
        trader2.log_path = Path("/tmp/csd_test_orders.jsonl")
        short = SimpleNamespace(symbol="SOL/USDT", direction="short", action="enter", mode="swing",
                                position_size_units=0.1, entry=100.0, stop=101.0,
                                targets=[99.0, 98.0])
        rec = trader2.submit(short)
        check("spot never sends a short as a sell",
              trader2.cfg.market_type == "spot" and rec.status == "rejected" and "short" in rec.reason,
              rec.reason[:60])
        scalp_long = SimpleNamespace(symbol="BTC/USDT", direction="long", action="enter",
                                     mode="scalp", position_size_units=0.001, entry=100.0,
                                     stop=99.0, targets=[101.0, 102.0])
        rec2 = trader2.submit(scalp_long)
        check("scalp trades never reach the exchange", rec2.status == "rejected"
              and "paper-only" in rec2.reason and trader2.wants(scalp_long) is False, rec2.reason[:60])
        trader2.log_path.unlink(missing_ok=True)
    finally:
        LiveTrader._init_exchange = real_init
        os.environ.clear()
        os.environ.update(env_before)

    # .env loading: shell wins, blanks and comments ignored, quotes stripped
    from bot.config import load_env_file
    env = Path("/tmp/csd_test.env")
    env.write_text('# comment\nCSD_T_A="quoted"\nexport CSD_T_B=plain\nCSD_T_EMPTY=\nCSD_T_SHELL=file\n')
    os.environ["CSD_T_SHELL"] = "shell"
    loaded = load_env_file(env)
    check(".env values load, quotes stripped",
          os.environ.get("CSD_T_A") == "quoted" and os.environ.get("CSD_T_B") == "plain")
    check(".env skips blanks and never overrides the shell",
          "CSD_T_EMPTY" not in os.environ and os.environ["CSD_T_SHELL"] == "shell"
          and sorted(loaded) == ["CSD_T_A", "CSD_T_B"])
    for k in ("CSD_T_A", "CSD_T_B", "CSD_T_SHELL"):
        os.environ.pop(k, None)
    env.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 7. landing, sign-up gate, feedback, admin
# ---------------------------------------------------------------------------
def test_server(result):
    print("\n[7] Landing, sign-up and feedback")
    import shutil
    import tempfile
    from bot.accounts import Accounts
    from bot.server import create_app

    class StubScanner:
        scan_count = 1
        paper = None
        def last_result(self):
            return result

    tmp = Path(tempfile.mkdtemp())
    try:
        app = create_app(Config(), StubScanner(), Accounts(tmp))
        c = app.test_client()

        r = c.get("/")
        check("landing page is public", r.status_code == 200 and b"Get access" in r.data)
        check("dashboard redirects to landing before sign-up",
              c.get("/app").status_code == 302)
        check("API refuses before sign-up", c.get("/api/state").status_code == 401)
        check("a bad email is rejected",
              c.post("/api/signup", json={"email": "not-an-email"}).status_code == 400)
        check("a form post (non-JSON) is refused",
              c.post("/api/signup", data={"email": "a@b.co"}).status_code == 415)

        r = c.post("/api/signup", json={"email": "Trader@Example.com"})
        check("a valid email signs up", r.status_code == 200 and r.get_json()["new"] is True)
        check("dashboard opens after sign-up", c.get("/app").status_code == 200)
        check("signed-in visitors skip the landing", c.get("/").status_code == 302)
        r = c.post("/api/signup", json={"email": "trader@example.com"})
        check("signing up twice is not a duplicate", r.get_json()["new"] is False
              and len(Accounts(tmp).signups()) == 1)

        check("empty feedback is rejected",
              c.post("/api/feedback", json={}).status_code == 400)
        r = c.post("/api/feedback", json={"rating": 4, "improve": ["wallet", "bogus"],
                                          "liked": "search", "message": "more stocks"})
        check("feedback is accepted", r.status_code == 200)
        fb = Accounts(tmp).all_feedback()
        check("feedback is stored with the email and known areas only",
              len(fb) == 1 and fb[0]["email"] == "trader@example.com"
              and fb[0]["improve"] == ["wallet"])
        check("admin page opens from this machine",
              c.get("/admin/feedback").status_code == 200)
        check("admin page refuses proxied requests",
              c.get("/admin/feedback", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 403)
        s = Accounts(tmp).summary()
        check("admin summary counts it", s["signups"] == 1 and s["avg_rating"] == 4.0)

        c.get("/logout")
        check("signing out locks the dashboard again", c.get("/app").status_code == 302)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 7b. AI trade review (fake Claude client: no network, no cost)
# ---------------------------------------------------------------------------
class FakeClaude:
    """Mimics client.beta.messages.create; scripted replies, records calls."""
    def __init__(self, decision="take", confidence=80, script=None, fail=False):
        from types import SimpleNamespace as NS
        self.NS, self.calls, self.fail = NS, [], fail
        self.script = list(script or [])
        self.decision, self.confidence = decision, confidence
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("simulated API outage")
        stop = self.script.pop(0) if self.script else "end_turn"
        NS = self.NS
        usage = NS(input_tokens=6000, output_tokens=3000, cache_creation_input_tokens=0,
                   cache_read_input_tokens=0, server_tool_use=NS(web_search_requests=2))
        if stop == "pause_turn":
            return NS(stop_reason="pause_turn", content=[NS(type="server_tool_use")], usage=usage)
        if stop == "refusal":
            return NS(stop_reason="refusal", content=[], usage=usage)
        body = {"decision": self.decision, "confidence": self.confidence,
                "summary": "test verdict", "reasons_for": ["trend aligned"],
                "reasons_against": ["fees"], "watch_for": "a close below the stop",
                "key_news": [{"title": "Example", "url": "https://example.com/n", "impact": "neutral"}]}
        return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps(body))], usage=usage)


def _ai_scanner(fake, tmp, **ai_overrides):
    import bot.scanner as scanner_mod
    from bot.ai_review import AIReviewer
    cfg = Config()
    cfg.news.enabled = False
    cfg.live.enabled = False
    for k, v in ai_overrides.items():
        setattr(cfg.ai, k, v)
    real_md = scanner_mod.MarketData
    scanner_mod.MarketData = FakeMarketData
    try:
        sc = scanner_mod.Scanner(cfg)
    finally:
        scanner_mod.MarketData = real_md
    sc.paper.path = tmp / "paper.json"
    sc.paper.reset()
    sc.ai = AIReviewer(cfg.ai, state_dir=tmp, client=fake)
    return sc


def _wait_reviews(sc, timeout=20):
    import time as _t
    end = _t.time() + timeout
    while sc.ai.stats()["pending"] and _t.time() < end:
        _t.sleep(0.05)


def test_ai_review():
    print("\n[7b] AI trade review (fake Claude, no network)")
    import os
    import shutil
    import tempfile
    from bot.ai_review import AIReviewer, _parse_verdict
    saved_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    tmp = Path(tempfile.mkdtemp())
    try:
        # approval: the first scan only queues reviews, the next one trades
        fake = FakeClaude("take", 80)
        sc = _ai_scanner(fake, tmp)
        r1 = sc.scan()
        check("first scan opens nothing while reviews run", r1["paper"]["open_positions"] == 0)
        _wait_reviews(sc)
        check("each tradable entry was reviewed once", len(fake.calls) == r1["counts"]["enter"]
              and len(fake.calls) >= 1, f"{len(fake.calls)} reviews, {r1['counts']['enter']} entries")
        kw = fake.calls[0]
        check("review uses Opus 5.5 with adaptive thinking and fallbacks",
              kw["model"] == "claude-opus-5-5" and kw["thinking"] == {"type": "adaptive"}
              and kw["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in kw["betas"])
        check("review asks for a schema-checked verdict and can search the web",
              kw["output_config"]["format"]["type"] == "json_schema"
              and kw["tools"][0]["type"] == "web_search_20260209")
        check("review sees the desk's track record", "desk_track_record_this_mode"
              in kw["messages"][0]["content"])
        r2 = sc.scan()
        pos = r2["paper"]["positions"]
        check("approved entries open on the next scan", len(pos) >= 1, f"{len(pos)} open")
        check("positions remember the AI's confidence", all(p["ai_confidence"] == 80 for p in pos))
        sigs = [s for m in r2["signals"].values() for s in m if s["action"] == "enter"]
        check("cards carry the approved verdict", any(s["ai"].get("state") == "approved" for s in sigs))
        check("a cached verdict is not paid for twice", len(fake.calls) == r1["counts"]["enter"])
        st = r2["ai"]
        check("spend is estimated and counted", st["reviews_today"] == len(fake.calls)
              and st["spend_today_usd"] > 0, f"${st['spend_today_usd']}")
        check("reviews are logged", (tmp / "ai_reviews.jsonl").exists())
        check("the daily count survives a restart",
              AIReviewer(sc.cfg.ai, state_dir=tmp, client=fake).stats()["reviews_today"] == len(fake.calls))

        # skip and low confidence never trade; with required, failures don't either
        for label, fk, req in (("a skip verdict opens nothing", FakeClaude("skip", 85), False),
                               ("a low-confidence take opens nothing", FakeClaude("take", 40), False),
                               ("required: an API failure opens nothing", FakeClaude(fail=True), True),
                               ("required: a refusal opens nothing", FakeClaude(script=["refusal"] * 50), True)):
            t2 = Path(tempfile.mkdtemp())
            sc2 = _ai_scanner(fk, t2, required=req)
            sc2.scan(); _wait_reviews(sc2)
            check(label, sc2.scan()["paper"]["open_positions"] == 0)
            shutil.rmtree(t2, ignore_errors=True)

        # no key, review required: nothing is reviewed and nothing trades
        t3 = Path(tempfile.mkdtemp())
        sc3 = _ai_scanner(None, t3, required=True)
        r3 = sc3.scan()
        states = {s["ai"].get("state") for m in r3["signals"].values() for s in m if s["action"] == "enter"}
        check("required: without an API key entries wait and nothing opens",
              r3["paper"]["open_positions"] == 0 and states == {"unavailable"}, str(states))
        shutil.rmtree(t3, ignore_errors=True)

        # no key, not required: the desk's own rules trade
        t3b = Path(tempfile.mkdtemp())
        sc3b = _ai_scanner(None, t3b)
        r3b = sc3b.scan()
        entered = [s for m in r3b["signals"].values() for s in m if s["action"] == "enter"]
        check("without an API key entries open on the desk's rules",
              r3b["paper"]["open_positions"] >= 1 and r3b["ai"]["required"] is False)
        check("rule-based entries are marked as fallback, not AI-approved",
              all(p["ai_confidence"] is None for p in r3b["paper"]["positions"])
              and any(s["ai"].get("fallback") for s in entered))
        shutil.rmtree(t3b, ignore_errors=True)

        # an API failure, not required: rules decide on the next scan
        t3c = Path(tempfile.mkdtemp())
        sc3c = _ai_scanner(FakeClaude(fail=True), t3c)
        sc3c.scan(); _wait_reviews(sc3c)
        check("an API failure falls back to the desk's rules", sc3c.scan()["paper"]["open_positions"] >= 1)
        shutil.rmtree(t3c, ignore_errors=True)

        # the daily cap holds
        t4 = Path(tempfile.mkdtemp())
        fk4 = FakeClaude("take", 80)
        sc4 = _ai_scanner(fk4, t4, max_reviews_per_day=1)
        sc4.scan(); _wait_reviews(sc4); sc4.scan(); _wait_reviews(sc4)
        check("the daily review cap is never exceeded", len(fk4.calls) == 1, f"{len(fk4.calls)} calls")
        shutil.rmtree(t4, ignore_errors=True)

        # pause_turn resumes the same turn
        t5 = Path(tempfile.mkdtemp())
        fk5 = FakeClaude("take", 75, script=["pause_turn"])
        rv = AIReviewer(Config().ai, state_dir=t5, client=fk5)
        from types import SimpleNamespace as NS
        from bot.ai_review import CONTEXT_FIELDS
        sig = NS(**{k: None for k in CONTEXT_FIELDS})
        sig.symbol, sig.mode, sig.direction = "PUMP/USDT", "swing", "long"
        sig.entry, sig.stop, sig.targets = 1.0, 0.9, [1.1, 1.2]
        out = rv.review_now(sig, {})
        check("a paused search turn is resumed, not dropped",
              len(fk5.calls) == 2 and out["state"] == "approved"
              and fk5.calls[1]["messages"][-1]["role"] == "assistant", f"{len(fk5.calls)} calls")
        shutil.rmtree(t5, ignore_errors=True)

        check("verdict JSON is read even when wrapped in prose",
              _parse_verdict('Here it is: {"decision": "skip", "confidence": 7}')["decision"] == "skip")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if saved_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved_key


# ---------------------------------------------------------------------------
# 7c. dashboard trading settings and the profit goal
# ---------------------------------------------------------------------------
def test_settings_and_goal():
    print("\n[7c] Trading settings and profit goal")
    import shutil
    import tempfile
    from bot.accounts import Accounts
    from bot.server import create_app
    from bot.settings import DeskSettings, goal_status

    tmp = Path(tempfile.mkdtemp())
    try:
        cfg = Config()
        ds = DeskSettings(cfg, path=tmp / "s.json")
        v = ds.update({"risk_per_trade_pct": 1.5, "max_open_positions": 10,
                       "max_notional_per_trade": 120, "live_modes": ["position", "scalp"],
                       "profit_goal_pct": 25, "lock_at_goal": False})
        check("settings change the running config",
              cfg.risk.risk_per_trade_pct == 1.5 and cfg.risk.max_open_positions == 10
              and cfg.risk.max_notional_per_trade == 120 and cfg.live.modes == ["scalp", "position"]
              and cfg.risk.profit_goal_pct == 25 and cfg.risk.lock_at_goal is False)
        check("settings view reflects them", v["max_open_positions"] == 10 and v["live_modes"] == ["scalp", "position"])
        cfg2 = Config()
        DeskSettings(cfg2, path=tmp / "s.json")
        check("settings survive a restart", cfg2.risk.max_open_positions == 10
              and cfg2.live.modes == ["scalp", "position"])
        for bad in ({"risk_per_trade_pct": 50}, {"max_open_positions": 0},
                    {"live_modes": ["moon"]}, {"max_notional_per_trade": "lots"}):
            try:
                ds.update(bad); ok = False
            except ValueError:
                ok = True
            check(f"rejects {bad}", ok)
        check("a rejected update changes nothing", cfg.risk.max_open_positions == 10)
        try:
            ds.update({"max_open_positions": 3, "risk_per_trade_pct": 99})
        except ValueError:
            pass
        check("a half-bad update is all-or-nothing", cfg.risk.max_open_positions == 10)

        # goal maths
        cfg.risk.profit_goal_pct, cfg.risk.lock_at_goal = 20, True
        g = goal_status(cfg.risk, {"total_return_pct": 5.0})
        check("goal progress is a share of the target", g["progress_pct"] == 25.0 and not g["reached"])
        g = goal_status(cfg.risk, {"total_return_pct": 21.0})
        check("reaching the goal locks when lock is on", g["reached"] and g["locked"])
        cfg.risk.profit_goal_pct = 0
        check("a 0% goal is off", goal_status(cfg.risk, {"total_return_pct": 50})["enabled"] is False)

        # at the goal the scanner banks open trades and opens nothing new
        sc = _ai_scanner(None, tmp)
        r1 = sc.scan()
        check("goal test opened trades first", r1["paper"]["open_positions"] >= 1)
        sc.cfg.risk.profit_goal_pct = 0.0001       # any gain at all counts as reached
        sc.paper.cash = sc.paper.starting_equity * 1.3
        r2 = sc.scan()
        check("at the goal, open practice trades are closed", r2["paper"]["open_positions"] == 0
              and r2["goal"]["locked"], str(r2["goal"]))
        check("they close as 'profit goal reached'",
              any(t["reason"] == "profit goal reached" for t in r2["paper"]["recent_trades"]))
        check("no new trade opens while locked", sc.scan()["paper"]["open_positions"] == 0)
        sc.cfg.risk.profit_goal_pct = 1000
        check("raising the goal unlocks trading", sc.scan()["paper"]["open_positions"] >= 1)

        # the HTTP routes: owner-only writes
        sc.settings = DeskSettings(sc.cfg, path=tmp / "s2.json")
        app = create_app(sc.cfg, sc, Accounts(tmp))
        c = app.test_client()
        c.post("/api/signup", json={"email": "owner@example.com"})
        r = c.get("/api/settings")
        check("settings are readable after sign-up", r.status_code == 200 and r.get_json()["can_edit"])
        r = c.post("/api/settings", json={"max_open_positions": 7})
        check("the owner can save settings", r.status_code == 200 and sc.cfg.risk.max_open_positions == 7)
        check("bad settings get a 400", c.post("/api/settings", json={"max_open_positions": 99}).status_code == 400)
        r = c.post("/api/settings", json={"max_open_positions": 2},
                   headers={"X-Forwarded-For": "8.8.8.8"})
        check("a remote visitor's save changes only their own account, not the desk",
              r.status_code == 200 and r.get_json()["scope"] == "user"
              and sc.cfg.risk.max_open_positions == 7
              and sc.users.get("owner@example.com").risk.max_open_positions == 2)
        html = c.get("/app").data
        check("dashboard has the bot settings panel", b'id="botPanel"' in html)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 7d. every user's own practice account
# ---------------------------------------------------------------------------
def test_user_accounts():
    print("\n[7d] Separate practice accounts per user")
    import shutil
    import tempfile
    from bot.accounts import Accounts
    from bot.server import create_app
    from bot.users import UserBooks, size_for

    tmp = Path(tempfile.mkdtemp())
    try:
        sc = _ai_scanner(None, tmp)
        acc = Accounts(tmp)
        sc.users = UserBooks(sc.cfg, acc)
        a, b = sc.users.get("a@example.com"), sc.users.get("b@example.com")
        sc.users.update_settings("b@example.com", {"risk_per_trade_pct": 2, "max_notional_per_trade": 10000,
                                                   "max_open_positions": 2})
        check("a user's settings don't touch the desk or other users",
              sc.cfg.risk.risk_per_trade_pct == 1.0 and a.risk.risk_per_trade_pct == 1.0
              and b.risk.risk_per_trade_pct == 2.0)
        try:
            sc.users.update_settings("a@example.com", {"live_modes": ["scalp"], "risk_per_trade_pct": 1.5})
            ok = sc.cfg.live.modes == ["swing", "position"]
        except ValueError:
            ok = False
        check("a user cannot change which modes send exchange orders", ok)

        sc.scan()
        check("each user's account opens trades from the shared signals",
              a.paper.positions and b.paper.positions)
        check("each user's trade-count limit applies to them alone", len(b.paper.positions) <= 2
              and len(a.paper.positions) >= len(b.paper.positions))
        pa = next(iter(a.paper.positions.values()))
        pb = b.paper.positions.get(f"{pa.symbol}|{pa.mode}")
        if pb:
            check("sizes follow each user's own risk % (2% vs 1.5%)",
                  abs(pb.size_units / pa.size_units - 2 / 1.5) < 1e-6,
                  f"{pa.size_units:.4g} vs {pb.size_units:.4g}")
        sig_like = type("S", (), {"entry": 100.0, "stop": 95.0})()
        # 1.5% of $1,000 = $15 at $5 a unit = 3 units ($300), capped at $250 = 2.5 units
        check("user sizing risks their % and respects their cap",
              abs(size_for(sig_like, a.risk) - 2.5) < 1e-9, str(size_for(sig_like, a.risk)))

        sc.users.reset("a@example.com")
        check("resetting one user's account leaves the others", not a.paper.positions and b.paper.positions)

        again = UserBooks(sc.cfg, Accounts(tmp))
        check("user accounts and settings survive a restart",
              set(again.books) == {"a@example.com", "b@example.com"}
              and again.books["b@example.com"].risk.risk_per_trade_pct == 2.0
              and len(again.books["b@example.com"].paper.positions) == len(b.paper.positions))

        # one user's goal locks only them
        b.risk.profit_goal_pct = 0.0001
        b.paper.cash = b.paper.starting_equity * 1.5
        sc.scan()
        check("a user at their goal is banked and paused", not b.paper.positions)
        check("other users keep trading", bool(a.paper.positions))

        rows = sc.users.admin_rows({})
        check("admin sees every user's account", {r["email"] for r in rows} == {"a@example.com", "b@example.com"})

        # over HTTP: a visitor sees their own account, the owner the desk
        app = create_app(sc.cfg, sc, acc)
        c = app.test_client()
        c.post("/api/signup", json={"email": "a@example.com"})
        remote = {"X-Forwarded-For": "8.8.8.8"}
        mine = c.get("/api/state", headers=remote).get_json()
        desk = c.get("/api/state").get_json()
        check("a visitor's dashboard shows their own practice account",
              mine.get("viewer") == "user"
              and len(mine["paper"]["positions"]) == len(a.paper.positions)
              and mine["settings"]["risk_per_trade_pct"] == 1.5)
        check("the owner's dashboard shows the main desk", desk.get("viewer") is None
              and desk["paper"]["open_positions"] == len(sc.paper.positions))
        c.post("/api/paper/reset", headers=remote)
        check("a visitor's reset clears only their account",
              not a.paper.positions and len(sc.paper.positions) == desk["paper"]["open_positions"])
        html = c.get("/admin/feedback").data
        check("admin page lists users' practice accounts", b"b@example.com" in html and b"practice accounts" in html)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 8. Postgres storage (only when TEST_DATABASE_URL points at a scratch database)
# ---------------------------------------------------------------------------
def test_postgres():
    import os
    url = os.getenv("TEST_DATABASE_URL")
    print("\n[8] Postgres storage" + ("" if url else " — skipped, TEST_DATABASE_URL not set"))
    if not url:
        return
    from bot.accounts import PgAccounts
    saved_secret = os.environ.pop("SECRET_KEY", None)
    a = PgAccounts(url)
    try:
        for t in ("signups", "feedback", "settings"):
            a._run(f"DELETE FROM {t}")
        check("pg: a new email signs up", a.signup("Pg@Example.com") is True)
        check("pg: the same email is not a duplicate", a.signup("pg@example.com") is False
              and len(a.signups()) == 1)
        a.feedback("pg@example.com", 5, "charts", ["wallet", "bogus"], "alerts please")
        fb = a.all_feedback()
        check("pg: feedback round-trips with known areas only",
              len(fb) == 1 and fb[0]["rating"] == 5 and fb[0]["improve"] == ["wallet"]
              and fb[0]["message"] == "alerts please")
        s = a.summary()
        check("pg: summary counts it", s["signups"] == 1 and s["avg_rating"] == 5.0
              and s["improve_votes"] == {"wallet": 1})
        a._run("DELETE FROM user_state")
        a.save_users({"u@example.com": {"paper": {"cash": 1010.5}, "settings": {"risk_per_trade_pct": 2}}})
        a.save_users({"u@example.com": {"paper": {"cash": 1020.0}, "settings": {"risk_per_trade_pct": 2}}})
        check("pg: a user's account is saved and updated in place",
              a.load_users() == {"u@example.com": {"paper": {"cash": 1020.0}, "settings": {"risk_per_trade_pct": 2}}})
        k1 = a.session_secret()
        check("pg: session secret survives a restart", PgAccounts(url).session_secret() == k1)
    finally:
        for t in ("signups", "feedback", "settings", "user_state"):
            a._run(f"DROP TABLE IF EXISTS {t}")
        if saved_secret is not None:
            os.environ["SECRET_KEY"] = saved_secret


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
    test_server(result)
    test_ai_review()
    test_settings_and_goal()
    test_user_accounts()
    test_postgres()

    print("\n" + "═" * 74)
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"    ✗ {f}")
    print("═" * 74)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
