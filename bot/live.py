"""Live execution — deliberately hard to turn on.

Five independent locks must all be released before a real order can leave this
process:

  1. ``live.enabled: true`` in config.yaml
  2. ``live.dry_run: false`` in config.yaml
  3. ``live.testnet: false`` (only for real-money trading; testnet works with
     dry_run off and is the recommended first step)
  4. the ``LIVE_CONFIRM`` environment variable set to the exact confirm phrase
  5. API key + secret present in the environment

With any lock still engaged the trader logs exactly what it *would* have sent
and returns a simulated result. That is the default and it is the right place
to leave it until you have watched paper mode for a long time.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from .config import LiveCfg, RiskCfg, STATE_DIR

log = logging.getLogger("bot.live")


@dataclass
class OrderRecord:
    ts: str
    symbol: str
    side: str
    amount: float
    price: float
    notional: float
    mode: str
    status: str          # "sent" | "simulated" | "rejected"
    reason: str
    exchange_id: str | None = None
    stop: float | None = None
    targets: list[float] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class LiveTrader:
    def __init__(self, cfg: LiveCfg, risk: RiskCfg):
        self.cfg = cfg
        self.risk = risk
        self._lock = threading.RLock()
        self.log_path: Path = STATE_DIR / "orders.jsonl"
        self.exchange = None
        self.open_symbols: set[str] = set()
        self.blocked_reason: str | None = None
        self._init_exchange()

    # ------------------------------------------------------------- unlocks
    def gate_status(self) -> dict:
        key = os.getenv(self.cfg.api_key_env, "")
        secret = os.getenv(self.cfg.api_secret_env, "")
        confirm = os.getenv(self.cfg.confirm_phrase_env, "")
        return {
            "enabled": self.cfg.enabled,
            "dry_run": self.cfg.dry_run,
            "testnet": self.cfg.testnet,
            "has_credentials": bool(key and secret),
            "confirm_phrase_ok": confirm == self.cfg.confirm_phrase,
            "exchange": self.cfg.exchange,
            "armed": self.is_armed(),
            "blocked_reason": self.blocked_reason,
        }

    def is_armed(self) -> bool:
        """True only when a real order would actually be transmitted."""
        if not self.cfg.enabled or self.cfg.dry_run:
            return False
        if os.getenv(self.cfg.confirm_phrase_env, "") != self.cfg.confirm_phrase:
            return False
        if not (os.getenv(self.cfg.api_key_env) and os.getenv(self.cfg.api_secret_env)):
            return False
        return self.exchange is not None

    def _init_exchange(self):
        if not self.cfg.enabled:
            return
        key = os.getenv(self.cfg.api_key_env)
        secret = os.getenv(self.cfg.api_secret_env)
        if not (key and secret):
            self.blocked_reason = (
                f"no credentials in ${self.cfg.api_key_env} / ${self.cfg.api_secret_env}"
            )
            return
        try:
            import ccxt

            klass = getattr(ccxt, self.cfg.exchange.lower())
            opts = {"apiKey": key, "secret": secret, "enableRateLimit": True}
            if self.cfg.market_type in ("swap", "future"):
                opts["options"] = {"defaultType": "swap"}
            ex = klass(opts)
            if self.cfg.testnet:
                if ex.has.get("sandbox") or hasattr(ex, "set_sandbox_mode"):
                    ex.set_sandbox_mode(True)
                else:
                    self.blocked_reason = f"{self.cfg.exchange} has no sandbox in ccxt"
                    return
            ex.load_markets()
            self.exchange = ex
            log.info("live exchange ready: %s (testnet=%s)",
                     self.cfg.exchange, self.cfg.testnet)
        except Exception as exc:
            self.blocked_reason = f"exchange init failed: {exc}"
            log.error(self.blocked_reason)

    # -------------------------------------------------------------- orders
    def _record(self, rec: OrderRecord):
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.to_dict()) + "\n")

    def submit(self, sig, halted: bool = False) -> OrderRecord:
        """Place (or simulate) an entry for a fired signal."""
        now = datetime.now(timezone.utc).isoformat()
        side = "buy" if sig.direction == "long" else "sell"
        amount = sig.position_size_units
        notional = amount * sig.entry

        def reject(reason: str) -> OrderRecord:
            rec = OrderRecord(now, sig.symbol, side, amount, sig.entry, notional,
                              sig.mode, "rejected", reason, stop=sig.stop,
                              targets=list(sig.targets))
            self._record(rec)
            return rec

        if halted:
            return reject("risk kill-switch active")
        if sig.action != "enter":
            return reject(f"signal action is '{sig.action}', not 'enter'")
        if notional > self.risk.max_notional_per_trade:
            return reject(
                f"notional {notional:.2f} exceeds cap {self.risk.max_notional_per_trade:.2f}"
            )
        with self._lock:
            if sig.symbol in self.open_symbols:
                return reject("already holding this symbol")
            if len(self.open_symbols) >= self.risk.max_open_positions:
                return reject(f"max open positions ({self.risk.max_open_positions}) reached")

        if not self.is_armed():
            gate = self.gate_status()
            missing = [k for k in ("enabled", "has_credentials", "confirm_phrase_ok")
                       if not gate[k]]
            if gate["dry_run"]:
                missing.append("dry_run still true")
            rec = OrderRecord(
                now, sig.symbol, side, amount, sig.entry, notional, sig.mode,
                "simulated", "not armed: " + ", ".join(missing) if missing else "dry run",
                stop=sig.stop, targets=list(sig.targets),
            )
            self._record(rec)
            log.info("[DRY RUN] would %s %.8g %s @ %.6g (stop %.6g)",
                     side, amount, sig.symbol, sig.entry, sig.stop)
            return rec

        # ---- real order ---------------------------------------------------
        try:
            market = self.exchange.market(sig.symbol)
            amount = float(self.exchange.amount_to_precision(sig.symbol, amount))
            min_amount = (market.get("limits", {}).get("amount") or {}).get("min")
            if min_amount and amount < min_amount:
                return reject(f"amount {amount} below exchange minimum {min_amount}")

            order = self.exchange.create_order(
                sig.symbol, self.cfg.order_type, side, amount
            )
            with self._lock:
                self.open_symbols.add(sig.symbol)

            rec = OrderRecord(
                now, sig.symbol, side, amount,
                float(order.get("average") or order.get("price") or sig.entry),
                notional, sig.mode, "sent", "entry filled",
                exchange_id=str(order.get("id")), stop=sig.stop,
                targets=list(sig.targets),
            )
            self._record(rec)
            log.warning("LIVE ORDER SENT: %s %.8g %s id=%s",
                        side, amount, sig.symbol, order.get("id"))
            self._attach_exits(sig, amount, side)
            return rec
        except Exception as exc:
            log.error("live order failed for %s: %s", sig.symbol, exc)
            return reject(f"exchange error: {exc}")

    def _attach_exits(self, sig, amount: float, entry_side: str):
        """Best-effort protective stop + take-profit. Support varies by venue,
        so a failure here is logged loudly rather than raised — you must be
        watching the position either way."""
        exit_side = "sell" if entry_side == "buy" else "buy"
        try:
            self.exchange.create_order(
                sig.symbol, "stop_market", exit_side, amount, None,
                {"stopPrice": float(self.exchange.price_to_precision(sig.symbol, sig.stop)),
                 "reduceOnly": True},
            )
            log.info("stop-loss placed for %s @ %.6g", sig.symbol, sig.stop)
        except Exception as exc:
            log.error("COULD NOT PLACE STOP for %s (%s) — manage this manually",
                      sig.symbol, exc)
        try:
            target = sig.targets[-1]
            self.exchange.create_order(
                sig.symbol, "take_profit_market", exit_side, amount, None,
                {"stopPrice": float(self.exchange.price_to_precision(sig.symbol, target)),
                 "reduceOnly": True},
            )
            log.info("take-profit placed for %s @ %.6g", sig.symbol, target)
        except Exception as exc:
            log.warning("could not place take-profit for %s: %s", sig.symbol, exc)

    def sync_positions(self):
        """Refresh the set of symbols we currently hold, when the venue supports it."""
        if not self.exchange:
            return
        try:
            if self.cfg.market_type in ("swap", "future") and self.exchange.has.get("fetchPositions"):
                positions = self.exchange.fetch_positions()
                with self._lock:
                    self.open_symbols = {
                        p["symbol"] for p in positions
                        if abs(float(p.get("contracts") or 0)) > 0
                    }
            elif self.exchange.has.get("fetchOpenOrders"):
                orders = self.exchange.fetch_open_orders()
                with self._lock:
                    self.open_symbols = {o["symbol"] for o in orders}
        except Exception as exc:
            log.warning("position sync failed: %s", exc)

    def recent_orders(self, limit: int = 25) -> list[dict]:
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text(encoding="utf-8").strip().splitlines()
        out = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out[::-1]
