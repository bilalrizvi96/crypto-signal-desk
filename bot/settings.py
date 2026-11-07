"""Trading settings the owner changes from the dashboard.

How much each trade may risk and spend, how many run at once, which modes
send testnet/exchange orders, and the profit goal. Saved to
state/desk_settings.json and written straight into the running config, so
the next scan uses them; config.yaml stays the starting point.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from .config import Config, STATE_DIR

log = logging.getLogger("bot.settings")

MODES = ("scalp", "swing", "position")

# name -> (section, type, min, max)
NUMBERS = {
    "risk_per_trade_pct": ("risk", float, 0.1, 5.0),
    "max_notional_per_trade": ("risk", float, 5.0, 1_000_000.0),
    "max_open_positions": ("risk", int, 1, 20),
    "profit_goal_pct": ("risk", float, 0.0, 1000.0),
}
FLAGS = {"lock_at_goal": "risk"}


class DeskSettings:
    def __init__(self, cfg: Config, path: Path | None = None):
        self.cfg = cfg
        self.path = path or (STATE_DIR / "desk_settings.json")
        self._lock = threading.Lock()
        if self.path.exists():
            try:
                self._apply(json.loads(self.path.read_text()))
            except (ValueError, OSError) as exc:
                log.warning("ignoring saved settings: %s", exc)

    def view(self) -> dict:
        risk = self.cfg.risk
        return {
            "risk_per_trade_pct": risk.risk_per_trade_pct,
            "max_notional_per_trade": risk.max_notional_per_trade,
            "max_open_positions": risk.max_open_positions,
            "profit_goal_pct": risk.profit_goal_pct,
            "lock_at_goal": risk.lock_at_goal,
            "live_modes": [m for m in MODES if m in self.cfg.live.modes],
            "account_equity": risk.account_equity,
        }

    def update(self, data: dict) -> dict:
        """Validate and apply a partial update. Raises ValueError on bad input."""
        with self._lock:
            self._apply(data)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.view(), indent=2))
            tmp.replace(self.path)
        log.info("settings changed: %s", self.view())
        return self.view()

    def _apply(self, data: dict):
        for target, name, value in parse_changes(data, self.cfg.risk, self.cfg.live):
            setattr(target, name, value)


def parse_changes(data: dict, risk, live=None) -> list[tuple[object, str, object]]:
    """Validate a settings update into (target, field, value) changes.
    Nothing is applied here, so a half-bad update changes nothing. live is
    None for a user's own account, which never sends exchange orders."""
    if not isinstance(data, dict):
        raise ValueError("settings must be an object")
    changes = []
    for name, (_, kind, lo, hi) in NUMBERS.items():
        if name not in data:
            continue
        try:
            value = kind(float(data[name]))
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a number") from None
        if not lo <= value <= hi:
            raise ValueError(f"{name} must be between {lo:g} and {hi:g}")
        changes.append((risk, name, value))
    for name in FLAGS:
        if name in data:
            changes.append((risk, name, bool(data[name])))
    if "live_modes" in data and live is not None:
        modes = data["live_modes"]
        if not isinstance(modes, list) or any(m not in MODES for m in modes):
            raise ValueError(f"live_modes must be a list drawn from {', '.join(MODES)}")
        changes.append((live, "modes", [m for m in MODES if m in modes]))
    return changes


def risk_view(risk) -> dict:
    return {name: getattr(risk, name) for name in (*NUMBERS, *FLAGS)}


def goal_status(risk, paper_stats: dict | None) -> dict:
    """Progress of a practice account toward its profit goal."""
    target = risk.profit_goal_pct
    if not target or not paper_stats:
        return {"enabled": False}
    ret = paper_stats.get("total_return_pct") or 0.0
    reached = ret >= target
    return {
        "enabled": True, "target_pct": target, "return_pct": ret,
        "progress_pct": round(max(0.0, min(100.0, ret / target * 100.0)), 1),
        "reached": reached, "locked": reached and risk.lock_at_goal,
    }
