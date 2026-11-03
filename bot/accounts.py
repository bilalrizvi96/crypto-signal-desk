"""Sign-ups and feedback — append-only JSONL files under state/.

Deliberately simple: an email gets you into the dashboard, and feedback is
written where you can read it. This is an access list, not authentication —
anyone can type any address — so do not put anything private behind it.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import STATE_DIR

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$")
IMPROVE_AREAS = ["signals", "search", "wallet", "success chance", "news",
                 "speed", "design", "explanations", "other"]

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(EMAIL_RE.match(email))


def _append(path: Path, row: dict):
    with _lock:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


class Accounts:
    def __init__(self, state_dir: Path | None = None):
        d = state_dir or STATE_DIR
        d.mkdir(exist_ok=True)
        self.signups_path = d / "signups.jsonl"
        self.feedback_path = d / "feedback.jsonl"
        self.secret_path = d / "session_secret.json"

    def session_secret(self) -> str:
        """Stable across restarts so signed-in browsers stay signed in."""
        with _lock:
            if self.secret_path.exists():
                try:
                    return json.loads(self.secret_path.read_text())["key"]
                except (json.JSONDecodeError, KeyError):
                    pass
            key = secrets.token_hex(32)
            self.secret_path.write_text(json.dumps({"key": key}))
            return key

    def signup(self, email: str, source: str = "landing") -> bool:
        """Record an email. Returns False if it was already on the list."""
        email = email.strip().lower()
        if any(r.get("email") == email for r in _read(self.signups_path)):
            return False
        _append(self.signups_path, {"email": email, "at": _now(), "source": source})
        return True

    def feedback(self, email: str, rating: int | None, liked: str,
                 improve: list[str], message: str) -> dict:
        row = {
            "email": email,
            "at": _now(),
            "rating": rating if rating in (1, 2, 3, 4, 5) else None,
            "liked": liked.strip()[:2000],
            "improve": [a for a in improve if a in IMPROVE_AREAS],
            "message": message.strip()[:4000],
        }
        _append(self.feedback_path, row)
        return row

    def signups(self) -> list[dict]:
        return _read(self.signups_path)

    def all_feedback(self) -> list[dict]:
        return _read(self.feedback_path)

    def summary(self) -> dict:
        fb = self.all_feedback()
        ratings = [f["rating"] for f in fb if f.get("rating")]
        areas: dict[str, int] = {}
        for f in fb:
            for a in f.get("improve") or []:
                areas[a] = areas.get(a, 0) + 1
        return {
            "signups": len(self.signups()),
            "feedback": len(fb),
            "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
            "improve_votes": dict(sorted(areas.items(), key=lambda kv: -kv[1])),
        }
