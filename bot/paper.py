"""Paper trading engine — a virtual portfolio driven by the same signals.

Behaviour mirrors a realistic manual workflow: enter on a fired signal, take
half off at target 1 and move the stop to breakeven, let the rest run to
target 2, and time out positions that go nowhere. Fees and a slippage
allowance are charged so the equity curve is not flattering fiction.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from .config import RiskCfg, STATE_DIR

log = logging.getLogger("bot.paper")

TAKER_FEE_PCT = 0.05      # 0.05% per side — typical retail taker fee
SLIPPAGE_PCT = 0.02       # extra allowance per side
SUCCESS_PRIOR_PCT = 40.0  # starting success estimate before any trades close
SUCCESS_PRIOR_TRADES = 10 # the prior counts as this many trades when blending


def prior_success() -> dict:
    """The success estimate for a signal when there is no trade history."""
    return {"pct": SUCCESS_PRIOR_PCT, "trades": 0, "wins": 0, "basis": "prior"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PaperPosition:
    id: str
    symbol: str
    base: str
    mode: str
    direction: str
    entry: float
    stop: float
    initial_stop: float
    targets: list[float]
    size_units: float
    remaining_units: float
    notional: float
    opened_at: str
    entry_tf: str
    max_hold_bars: int
    score: float
    bars_held: int = 0
    realised_pnl: float = 0.0
    fees_paid: float = 0.0
    tp1_hit: bool = False
    breakeven: bool = False
    note: str = ""
    ai_confidence: int | None = None   # set when an AI review approved the entry

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClosedTrade:
    id: str
    symbol: str
    base: str
    mode: str
    direction: str
    entry: float
    exit: float
    size_units: float
    pnl: float
    pnl_pct: float
    r_multiple: float
    fees: float
    opened_at: str
    closed_at: str
    bars_held: int
    reason: str
    score: float
    ai_confidence: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class PaperBroker:
    def __init__(self, risk: RiskCfg, path: Path | None = None, persist: bool = True):
        """persist=False keeps the broker in memory; its owner stores
        to_state() somewhere else (a user's account lives in the database)."""
        self.risk = risk
        self.persist = persist
        self.path = path or (STATE_DIR / "paper_state.json")
        self._lock = threading.RLock()
        self.starting_equity = risk.account_equity
        self.cash = risk.account_equity
        self.positions: dict[str, PaperPosition] = {}
        self.closed: list[ClosedTrade] = []
        self.equity_curve: list[dict] = []
        self.day_start_equity = risk.account_equity
        self.day_stamp = datetime.now(timezone.utc).date().isoformat()
        self.halted_reason: str | None = None
        self._load()

    # ------------------------------------------------------------ storage
    def _load(self):
        if not self.persist or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except Exception as exc:
            log.warning("could not read paper state: %s", exc)
            return
        self.load_state(data)
        log.info("paper state loaded: %d open, %d closed",
                 len(self.positions), len(self.closed))

    def load_state(self, data: dict):
        with self._lock:
            self.starting_equity = data.get("starting_equity", self.starting_equity)
            self.cash = data.get("cash", self.cash)
            self.positions = {
                k: PaperPosition(**v) for k, v in data.get("positions", {}).items()
            }
            self.closed = [ClosedTrade(**t) for t in data.get("closed", [])]
            self.equity_curve = data.get("equity_curve", [])
            self.day_start_equity = data.get("day_start_equity", self.starting_equity)
            self.day_stamp = data.get("day_stamp", self.day_stamp)
            self.halted_reason = data.get("halted_reason")

    def to_state(self) -> dict:
        with self._lock:
            return {
                "starting_equity": self.starting_equity,
                "cash": self.cash,
                "positions": {k: v.to_dict() for k, v in self.positions.items()},
                "closed": [t.to_dict() for t in self.closed[-500:]],
                "equity_curve": self.equity_curve[-1000:],
                "day_start_equity": self.day_start_equity,
                "day_stamp": self.day_stamp,
                "halted_reason": self.halted_reason,
                "saved_at": _now(),
            }

    def save(self):
        if not self.persist:
            return
        payload = self.to_state()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.path)

    def reset(self):
        with self._lock:
            self.cash = self.starting_equity = self.risk.account_equity
            self.positions.clear()
            self.closed.clear()
            self.equity_curve.clear()
            self.day_start_equity = self.risk.account_equity
            self.day_stamp = datetime.now(timezone.utc).date().isoformat()
            self.halted_reason = None
        self.save()

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _fee(notional: float) -> float:
        return notional * (TAKER_FEE_PCT + SLIPPAGE_PCT) / 100.0

    def _key(self, symbol: str, mode: str) -> str:
        return f"{symbol}|{mode}"

    def _roll_day(self, equity: float):
        today = datetime.now(timezone.utc).date().isoformat()
        if today != self.day_stamp:
            self.day_stamp = today
            self.day_start_equity = equity
            self.halted_reason = None

    # ---------------------------------------------------------- valuation
    def unrealised(self, prices: dict[str, float]) -> float:
        total = 0.0
        for pos in self.positions.values():
            px = prices.get(pos.symbol)
            if px is None:
                continue
            sign = 1.0 if pos.direction == "long" else -1.0
            total += (px - pos.entry) * sign * pos.remaining_units
        return total

    @staticmethod
    def _position_view(pos: PaperPosition, prices: dict[str, float]) -> dict:
        """A position plus its live mark, so the page can show what it is worth now."""
        d = pos.to_dict()
        px = prices.get(pos.symbol)
        sign = 1.0 if pos.direction == "long" else -1.0
        d["mark"] = px
        d["unrealised"] = (round((px - pos.entry) * sign * pos.remaining_units, 4)
                           if px is not None else None)
        return d

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.unrealised(prices)

    # ------------------------------------------------------------- trading
    def can_open(self, sig) -> bool:
        """Would open_from_signal accept this signal right now? Lets the scanner
        skip paying for an AI review of a trade that could not open anyway."""
        with self._lock:
            return (not self.halted_reason
                    and sig.action == "enter" and sig.direction in ("long", "short")
                    # one trade per coin: a swing and a long-term trade on the
                    # same coin would double the bet on one price move
                    and not any(p.symbol == sig.symbol for p in self.positions.values())
                    and len(self.positions) < self.risk.max_open_positions
                    and sig.position_size_units > 0)

    def open_from_signal(self, sig, units: float | None = None) -> PaperPosition | None:
        """sig is a strategy.Signal; units overrides its size (a user's own
        risk settings). Returns the position if one was opened."""
        units = sig.position_size_units if units is None else units
        with self._lock:
            if not self.can_open(sig) or units <= 0:
                return None
            key = self._key(sig.symbol, sig.mode)

            notional = units * sig.entry
            fee = self._fee(notional)
            self.cash -= fee

            pos = PaperPosition(
                id=uuid.uuid4().hex[:10],
                symbol=sig.symbol,
                base=sig.base,
                mode=sig.mode,
                direction=sig.direction,
                entry=sig.entry,
                stop=sig.stop,
                initial_stop=sig.stop,
                targets=list(sig.targets),
                size_units=units,
                remaining_units=units,
                notional=notional,
                opened_at=_now(),
                entry_tf=sig.timeframes.get("entry", "?"),
                max_hold_bars=int(getattr(sig, "max_hold_bars", 0) or 24),
                score=sig.score,
                fees_paid=fee,
                note=f"{sig.confidence} confidence, {sig.regime} regime",
                ai_confidence=(getattr(sig, "ai", None) or {}).get("confidence")
                if (getattr(sig, "ai", None) or {}).get("state") == "approved" else None,
            )
            self.positions[key] = pos
            log.info("paper open %s %s @ %.6g (score %.0f)",
                     sig.direction, sig.symbol, sig.entry, sig.score)
            return pos

    def _close(self, key: str, pos: PaperPosition, price: float, units: float,
               reason: str, partial: bool):
        sign = 1.0 if pos.direction == "long" else -1.0
        gross = (price - pos.entry) * sign * units
        fee = self._fee(units * price)
        net = gross - fee
        self.cash += net
        pos.realised_pnl += net
        pos.fees_paid += fee
        pos.remaining_units -= units

        if partial:
            return None

        risk_per_unit = abs(pos.entry - pos.initial_stop) or 1e-9
        total_pnl = pos.realised_pnl
        r_mult = total_pnl / (risk_per_unit * pos.size_units)
        trade = ClosedTrade(
            id=pos.id,
            symbol=pos.symbol,
            base=pos.base,
            mode=pos.mode,
            direction=pos.direction,
            entry=pos.entry,
            exit=price,
            size_units=pos.size_units,
            pnl=round(total_pnl, 4),
            pnl_pct=round(total_pnl / max(pos.notional, 1e-9) * 100.0, 3),
            r_multiple=round(r_mult, 3),
            fees=round(pos.fees_paid, 4),
            opened_at=pos.opened_at,
            closed_at=_now(),
            bars_held=pos.bars_held,
            reason=reason,
            score=pos.score,
            ai_confidence=pos.ai_confidence,
        )
        self.closed.append(trade)
        self.positions.pop(key, None)
        log.info("paper close %s %s @ %.6g — %s, %.3gR",
                 pos.direction, pos.symbol, price, reason, r_mult)
        return trade

    def mark_to_market(self, prices: dict[str, float],
                       bar_advanced: set[tuple[str, str]] | None = None):
        """Check stops/targets/timeouts against the latest prices.

        bar_advanced holds (symbol, timeframe) pairs whose newest bar changed
        since the last scan; a position only ages when its own entry
        timeframe prints a new bar, so a daily trade isn't timed out in 5m bars.
        """
        events = []
        with self._lock:
            for key in list(self.positions.keys()):
                pos = self.positions[key]
                px = prices.get(pos.symbol)
                if px is None:
                    continue
                if bar_advanced and (pos.symbol, pos.entry_tf) in bar_advanced:
                    pos.bars_held += 1

                long = pos.direction == "long"

                # stop first — assume the worst fill order within the bar
                if (long and px <= pos.stop) or (not long and px >= pos.stop):
                    reason = "stopped out" if not pos.breakeven else "breakeven stop"
                    t = self._close(key, pos, pos.stop, pos.remaining_units, reason, False)
                    if t:
                        events.append(("close", t))
                    continue

                # target 1 → scale out half, stop to breakeven
                if not pos.tp1_hit and pos.targets:
                    t1 = pos.targets[0]
                    if (long and px >= t1) or (not long and px <= t1):
                        half = pos.remaining_units / 2.0
                        self._close(key, pos, t1, half, "target 1 (partial)", True)
                        pos.tp1_hit = True
                        pos.breakeven = True
                        pos.stop = pos.entry
                        events.append(("partial", {"symbol": pos.symbol, "price": t1}))

                # final target
                if len(pos.targets) > 1:
                    t2 = pos.targets[-1]
                    if (long and px >= t2) or (not long and px <= t2):
                        t = self._close(key, pos, t2, pos.remaining_units,
                                        "final target", False)
                        if t:
                            events.append(("close", t))
                        continue

                if pos.bars_held >= pos.max_hold_bars:
                    t = self._close(key, pos, px, pos.remaining_units, "timed out", False)
                    if t:
                        events.append(("close", t))

            equity = self.equity(prices)
            self._roll_day(equity)
            drawdown_pct = (self.day_start_equity - equity) / max(self.day_start_equity, 1e-9) * 100.0
            if drawdown_pct >= self.risk.daily_loss_limit_pct and not self.halted_reason:
                self.halted_reason = (
                    f"daily loss limit hit ({drawdown_pct:.2f}% ≥ "
                    f"{self.risk.daily_loss_limit_pct}%) — no new entries today"
                )
                log.warning(self.halted_reason)

            self.equity_curve.append({"t": _now(), "equity": round(equity, 4)})
        return events

    def close_all(self, prices: dict[str, float], reason: str) -> list[ClosedTrade]:
        """Close every open position at its current price."""
        closed = []
        with self._lock:
            for key in list(self.positions.keys()):
                pos = self.positions[key]
                px = prices.get(pos.symbol)
                if px is None:
                    continue
                t = self._close(key, pos, px, pos.remaining_units, reason, False)
                if t:
                    closed.append(t)
        return closed

    # --------------------------------------------------------------- stats
    def success_chance(self, mode: str) -> dict:
        """Estimated chance a new signal in this mode closes in profit.

        Starts at SUCCESS_PRIOR_PCT and moves toward the real paper win rate
        for the mode as trades close, so one lucky trade cannot read as 100%.
        """
        with self._lock:
            trades = [t for t in self.closed if t.mode == mode]
            wins = sum(1 for t in trades if t.pnl > 0)
            n = len(trades)
            pct = ((wins + SUCCESS_PRIOR_PCT / 100.0 * SUCCESS_PRIOR_TRADES)
                   / (n + SUCCESS_PRIOR_TRADES) * 100.0)
            return {"pct": round(pct, 1), "trades": n, "wins": wins,
                    "basis": "paper" if n else "prior"}

    def stats(self, prices: dict[str, float] | None = None) -> dict:
        with self._lock:
            prices = prices or {}
            equity = self.equity(prices)
            closed = self.closed
            wins = [t for t in closed if t.pnl > 0]
            losses = [t for t in closed if t.pnl <= 0]
            gross_win = sum(t.pnl for t in wins)
            gross_loss = abs(sum(t.pnl for t in losses))
            r_values = [t.r_multiple for t in closed]

            peak, max_dd = self.starting_equity, 0.0
            for point in self.equity_curve:
                peak = max(peak, point["equity"])
                max_dd = max(max_dd, (peak - point["equity"]) / max(peak, 1e-9) * 100.0)

            return {
                "starting_equity": round(self.starting_equity, 2),
                "equity": round(equity, 2),
                "cash": round(self.cash, 2),
                "unrealised": round(self.unrealised(prices), 2),
                "total_return_pct": round(
                    (equity - self.starting_equity) / max(self.starting_equity, 1e-9) * 100.0, 2
                ),
                "open_positions": len(self.positions),
                "closed_trades": len(closed),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(closed) * 100.0, 1) if closed else 0.0,
                "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
                "avg_r": round(sum(r_values) / len(r_values), 3) if r_values else 0.0,
                "avg_pnl": round(sum(t.pnl for t in closed) / len(closed), 4) if closed else None,
                "ai_closed": sum(1 for t in closed if t.ai_confidence is not None),
                "ai_wins": sum(1 for t in closed if t.ai_confidence is not None and t.pnl > 0),
                "best_r": round(max(r_values), 2) if r_values else 0.0,
                "worst_r": round(min(r_values), 2) if r_values else 0.0,
                "total_fees": round(sum(t.fees for t in closed), 2),
                "max_drawdown_pct": round(max_dd, 2),
                "halted_reason": self.halted_reason,
                "positions": [self._position_view(p, prices) for p in self.positions.values()],
                "recent_trades": [t.to_dict() for t in closed[-15:]][::-1],
                "equity_curve_points": self.equity_curve[-180:],
            }
