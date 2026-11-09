"""History check — asks a coin's own past before a trade opens.

Two questions, after the "Time Machine" in github.com/Jakeschincariol/day-trader-skill
(MIT), rebuilt for crypto and for this desk's own rules:

1. **Replay.** Walk the coin's recent bars and, at every bar, ask the real
   strategy (build_signal, on data up to that bar only) whether it would have
   fired this same mode and direction. Each time it did, play the trade out
   on the bars that followed with the paper broker's exits: stop, half off at
   target 1 with the stop moved to entry, the rest at target 2, or a timeout.
   Fees included. One trade at a time, as the broker would.
2. **Simulation.** From today's entry, rebuild the next bars many times by
   drawing from the coin's own recent bars (each as open/high/low/close over
   the previous close), and play the same exits. How often the stop comes
   first, and the average result.

The trade is skipped when its own replay lost money on this coin, or the
simulation's average is clearly negative. It is a reality check from a few
weeks of data, not a prediction.
"""
from __future__ import annotations

import logging
import random
import threading

import pandas as pd

from .config import HistoryCfg, ModeCfg, RiskCfg
from .paper import SLIPPAGE_PCT, TAKER_FEE_PCT
from .strategy import build_signal

log = logging.getLogger("bot.history")

WARMUP = 210            # bars the indicators need before the first replayed bar
MIN_HTF = 60            # confirm/bias bars needed at a replayed bar
ROUND_TRIP_COST = 2 * (TAKER_FEE_PCT + SLIPPAGE_PCT) / 100.0


def play_out(bars, direction: str, entry: float, stop: float, targets: list[float],
             max_hold: int) -> tuple[float, str, int]:
    """Result in R (before fees), how it ended, and the bars it took, for one
    trade over the bars after entry with the paper broker's exits.
    bars: iterable of (high, low, close)."""
    long = direction == "long"
    risk = abs(entry - stop) or 1e-12
    t1, t2 = targets[0], targets[-1]
    banked, size, cur_stop = 0.0, 1.0, stop
    r = lambda px: (px - entry) / risk * (1 if long else -1)
    last, n = entry, 0
    for n, (hi, lo, close) in enumerate(bars, start=1):
        last = close
        if (lo <= cur_stop) if long else (hi >= cur_stop):          # stop first: worst case
            return banked + size * r(cur_stop), ("breakeven" if size < 1 else "stop"), n
        if size == 1.0 and ((hi >= t1) if long else (lo <= t1)):
            banked += 0.5 * r(t1)
            size, cur_stop = 0.5, entry
        if (hi >= t2) if long else (lo <= t2):
            return banked + size * r(t2), "target", n
        if n >= max_hold:
            break
    return banked + size * r(last), "timeout", n


class HistoryChecker:
    def __init__(self, cfg: HistoryCfg, risk: RiskCfg):
        self.cfg = cfg
        self.risk = risk
        self._lock = threading.Lock()
        self._cache: dict[tuple, dict] = {}

    def check(self, sig, frames: dict[str, pd.DataFrame], mode_cfg: ModeCfg) -> dict:
        """The check for a fired signal; cached until a new entry bar prints."""
        entry_df = frames["entry"]
        key = (sig.symbol, sig.mode, sig.direction, entry_df.index[-1],
               round(sig.entry, 10), round(sig.stop, 10))
        with self._lock:
            hit = self._cache.get(key)
        if hit:
            return hit
        try:
            out = self._run(sig, frames, mode_cfg)
        except Exception as exc:                     # never let a check crash a scan
            log.warning("history check failed for %s %s: %s", sig.symbol, sig.mode, exc)
            out = {"state": "error", "pass": not self.cfg.required, "reason": str(exc)[:160]}
        with self._lock:
            if len(self._cache) > 500:
                self._cache.clear()
            self._cache[key] = out
        return out

    # ------------------------------------------------------------------ run
    def _run(self, sig, frames, mode_cfg) -> dict:
        fee_r = ROUND_TRIP_COST * sig.entry / (abs(sig.entry - sig.stop) or 1e-12)
        hist = self._replay(sig, frames, mode_cfg)
        mc = self._simulate(sig, frames["entry"], mode_cfg.max_hold_bars, fee_r)

        reasons, ok = [], True
        if hist["trades"] >= self.cfg.min_samples and hist["avg_r"] <= 0:
            ok = False
            reasons.append(f"the same setup lost money on {sig.base} recently "
                           f"({hist['wins']} of {hist['trades']} won, {hist['avg_r']:+.2f}R a trade)")
        if mc["avg_r"] < self.cfg.min_sim_avg_r:
            ok = False
            reasons.append(f"the simulation averages {mc['avg_r']:+.2f}R after fees "
                           f"(stop first {mc['stop_first_pct']:.0f}% of the time)")
        if ok:
            if hist["trades"] < self.cfg.min_samples:
                reasons.append(f"only {hist['trades']} past setup(s) to learn from; simulation "
                               f"{mc['avg_r']:+.2f}R")
            else:
                reasons.append(f"{hist['wins']} of {hist['trades']} past setups won, "
                               f"{hist['avg_r']:+.2f}R a trade after fees")
        return {"state": "passed" if ok else "blocked", "pass": ok,
                "reason": "; ".join(reasons), "history": hist, "sim": mc,
                "bars": len(frames["entry"]), "timeframe": mode_cfg.entry_tf}

    def _replay(self, sig, frames, mode_cfg) -> dict:
        e = frames["entry"]
        c, b = frames.get("confirm"), frames.get("bias")
        highs, lows, closes = e["high"].to_numpy(), e["low"].to_numpy(), e["close"].to_numpy()
        results, i, end = [], WARMUP, len(e) - 1        # the last bar is today's signal
        while i < end:
            ts = e.index[i]
            sub = {"entry": e.iloc[: i + 1]}
            for role, df in (("confirm", c), ("bias", b)):
                if df is not None:
                    part = df[df.index <= ts]
                    if len(part) < MIN_HTF:
                        sub = None
                        break
                    sub[role] = part
            if sub is None:
                i += 1
                continue
            past = build_signal(symbol=sig.symbol, base=sig.base, bucket=sig.bucket,
                                mode=sig.mode, mode_cfg=mode_cfg, risk_cfg=self.risk, frames=sub)
            if past.action != "enter" or past.direction != sig.direction:
                i += 1
                continue
            after = list(zip(highs[i + 1:], lows[i + 1:], closes[i + 1:]))
            r, how, held = play_out(after, past.direction, past.entry, past.stop, past.targets,
                                    mode_cfg.max_hold_bars)
            if how == "timeout" and held < mode_cfg.max_hold_bars:
                break                   # still running at the last bar: the outcome isn't known yet
            fee_r = ROUND_TRIP_COST * past.entry / (abs(past.entry - past.stop) or 1e-12)
            results.append(r - fee_r)
            i += max(1, held)           # one trade at a time: carry on from where it closed
        wins = sum(1 for r in results if r > 0)
        return {"trades": len(results), "wins": wins,
                "avg_r": round(sum(results) / len(results), 3) if results else 0.0,
                "total_r": round(sum(results), 2)}

    def _simulate(self, sig, entry_df, max_hold: int, fee_r: float) -> dict:
        tail = entry_df.iloc[-(self.cfg.sim_pool_bars + 1):]
        prev = tail["close"].shift(1)
        pool = list(zip((tail["high"] / prev).iloc[1:], (tail["low"] / prev).iloc[1:],
                        (tail["close"] / prev).iloc[1:]))
        rng = random.Random(19)                         # same answer for the same bars
        counts = {"stop": 0, "breakeven": 0, "target": 0, "timeout": 0}
        total = 0.0
        for _ in range(self.cfg.replays):
            px, path = sig.entry, []
            for _ in range(max_hold):
                h, l, cl = pool[rng.randrange(len(pool))]
                path.append((px * h, px * l, px * cl))
                px *= cl
            r, how, _ = play_out(path, sig.direction, sig.entry, sig.stop, sig.targets, max_hold)
            counts[how] += 1
            total += r - fee_r
        n = self.cfg.replays
        return {"replays": n, "avg_r": round(total / n, 3),
                "target_first_pct": round(counts["target"] / n * 100, 1),
                "stop_first_pct": round(counts["stop"] / n * 100, 1),
                "breakeven_pct": round(counts["breakeven"] / n * 100, 1),
                "timeout_pct": round(counts["timeout"] / n * 100, 1)}
