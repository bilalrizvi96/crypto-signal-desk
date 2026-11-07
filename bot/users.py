"""Each signed-up user's own practice account and settings.

Signals are computed once per scan and shared, because the market is the
same for everyone. What each user does with them is theirs: a play-money
account, sized by their own risk settings and stopped by their own profit
goal. Stored through Accounts (Postgres on a host, files locally).
"""
from __future__ import annotations

import copy
import logging
import threading

from .accounts import Accounts
from .config import Config, RiskCfg
from .paper import PaperBroker
from .settings import goal_status, parse_changes, risk_view

log = logging.getLogger("bot.users")

CURVE_POINTS = 300    # per-user equity history kept; it is saved after every scan


def size_for(sig, risk: RiskCfg) -> float:
    """Units to buy or sell so a stop-out loses risk_per_trade_pct of the
    account, capped at max_notional_per_trade (strategy.build_signal's rule)."""
    per_unit = abs(sig.entry - sig.stop)
    if not per_unit or not sig.entry:
        return 0.0
    units = risk.account_equity * risk.risk_per_trade_pct / 100.0 / per_unit
    return min(units, risk.max_notional_per_trade / sig.entry)


class UserBook:
    def __init__(self, email: str, risk: RiskCfg):
        self.email = email
        self.risk = risk
        self.paper = PaperBroker(risk, persist=False)

    def to_state(self) -> dict:
        state = self.paper.to_state()
        state["equity_curve"] = state["equity_curve"][-CURVE_POINTS:]
        return {"paper": state, "settings": risk_view(self.risk)}


class UserBooks:
    def __init__(self, cfg: Config, accounts: Accounts):
        self.cfg = cfg
        self.accounts = accounts
        self._lock = threading.RLock()
        self.books: dict[str, UserBook] = {}
        try:
            for email, data in accounts.load_users().items():
                self.books[email] = self._restore(email, data)
        except Exception as exc:
            log.warning("could not load user accounts: %s", exc)
        log.info("%d user practice accounts loaded", len(self.books))

    def _new_risk(self) -> RiskCfg:
        return copy.deepcopy(self.cfg.risk)

    def _restore(self, email: str, data: dict) -> UserBook:
        risk = self._new_risk()
        try:
            for target, name, value in parse_changes(data.get("settings") or {}, risk):
                setattr(target, name, value)
        except ValueError as exc:
            log.warning("ignoring bad saved settings for %s: %s", email, exc)
        book = UserBook(email, risk)
        if data.get("paper"):
            book.paper.load_state(data["paper"])
        return book

    # --------------------------------------------------------------- access
    def get(self, email: str) -> UserBook:
        """The user's book, created on first visit."""
        with self._lock:
            book = self.books.get(email)
            if book is None:
                book = self.books[email] = UserBook(email, self._new_risk())
                self._save({email: book})
            return book

    def update_settings(self, email: str, data: dict) -> dict:
        book = self.get(email)
        for target, name, value in parse_changes(data, book.risk):   # raises ValueError
            setattr(target, name, value)
        self._save({email: book})
        return risk_view(book.risk)

    def reset(self, email: str):
        book = self.get(email)
        book.paper.reset()
        self._save({email: book})

    def symbols(self) -> set[str]:
        with self._lock:
            return {p.symbol for b in self.books.values() for p in b.paper.positions.values()}

    def any_can_open(self, sig) -> bool:
        with self._lock:
            return any(b.paper.can_open(sig) and not goal_status(b.risk, b.paper.stats()).get("locked")
                       for b in self.books.values())

    # ----------------------------------------------------------------- scan
    def on_scan(self, prices: dict[str, float], bar_advanced: set, approved: list):
        """Run every user's account against this scan: exits first, then the
        profit-goal check, then new entries sized by the user's settings."""
        with self._lock:
            books = dict(self.books)
        for book in books.values():
            book.paper.mark_to_market(prices, bar_advanced)
            goal = goal_status(book.risk, book.paper.stats(prices))
            if goal.get("locked"):
                if book.paper.positions:
                    book.paper.close_all(prices, "profit goal reached")
                continue
            for sig in approved:
                book.paper.open_from_signal(sig, units=size_for(sig, book.risk))
        self._save(books)

    def _save(self, books: dict[str, UserBook]):
        try:
            self.accounts.save_users({e: b.to_state() for e, b in books.items()})
        except Exception as exc:
            log.warning("could not save user accounts: %s", exc)

    # ---------------------------------------------------------------- views
    def view(self, email: str, prices: dict[str, float]) -> dict:
        """The parts of the dashboard state that belong to this user."""
        book = self.get(email)
        stats = book.paper.stats(prices)
        return {"paper": stats, "goal": goal_status(book.risk, stats),
                "settings": {**risk_view(book.risk), "account_equity": book.risk.account_equity}}

    def admin_rows(self, prices: dict[str, float]) -> list[dict]:
        with self._lock:
            books = list(self.books.values())
        rows = []
        for b in books:
            st = b.paper.stats(prices)
            rows.append({"email": b.email, "equity": st["equity"], "return_pct": st["total_return_pct"],
                         "open": st["open_positions"], "closed": st["closed_trades"], "wins": st["wins"],
                         "settings": risk_view(b.risk)})
        return sorted(rows, key=lambda r: -r["return_pct"])
