"""Sign-ups and feedback — JSONL files under state/, or Postgres when
DATABASE_URL is set (free hosts wipe their disk on every restart).

Deliberately simple: an email gets you into the dashboard, and feedback is
written where you can read it. This is an access list, not authentication —
anyone can type any address — so do not put anything private behind it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

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
        self.users_dir = d / "users"

    def session_secret(self) -> str:
        """Stable across restarts so signed-in browsers stay signed in."""
        if os.getenv("SECRET_KEY"):
            return os.environ["SECRET_KEY"]
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

    # ---- each user's own practice account and settings ----
    def load_users(self) -> dict[str, dict]:
        out = {}
        if not self.users_dir.exists():
            return out
        for f in self.users_dir.glob("*.json"):
            try:
                row = json.loads(f.read_text())
                out[row["email"]] = row["data"]
            except (OSError, ValueError, KeyError):
                continue
        return out

    def save_users(self, rows: dict[str, dict]):
        self.users_dir.mkdir(exist_ok=True)
        for email, data in rows.items():
            name = hashlib.sha256(email.encode()).hexdigest()[:24]
            tmp = self.users_dir / f"{name}.tmp"
            tmp.write_text(json.dumps({"email": email, "data": data}))
            tmp.replace(self.users_dir / f"{name}.json")

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


class PgAccounts(Accounts):
    """Same interface, stored in Postgres. One short connection per call:
    sign-ups and feedback are rare, and it keeps threads from sharing one."""

    SCHEMA = (
        "CREATE TABLE IF NOT EXISTS signups (email TEXT PRIMARY KEY, "
        "at TIMESTAMPTZ NOT NULL, source TEXT)",
        "CREATE TABLE IF NOT EXISTS feedback (id SERIAL PRIMARY KEY, email TEXT, "
        "at TIMESTAMPTZ NOT NULL, rating INT, liked TEXT, improve TEXT, message TEXT)",
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS user_state (email TEXT PRIMARY KEY, data TEXT NOT NULL, "
        "updated_at TIMESTAMPTZ NOT NULL)",
    )

    def __init__(self, url: str):
        u = urlparse(url)
        q = parse_qs(u.query)
        local = u.hostname in (None, "", "localhost", "127.0.0.1")
        self._params = {
            "user": unquote(u.username or ""), "password": unquote(u.password or "") or None,
            "host": u.hostname or "localhost", "port": u.port or 5432,
            "database": (u.path or "/").lstrip("/") or "postgres",
        }
        # hosted Postgres (Neon, Supabase, Render) requires TLS; local doesn't
        if q.get("sslmode", [""])[0] != "disable" and not local:
            self._params["ssl_context"] = True
        for stmt in self.SCHEMA:
            self._run(stmt)

    def _run(self, sql: str, **params):
        import pg8000.native
        conn = pg8000.native.Connection(**self._params)
        try:
            return conn.run(sql, **params)
        finally:
            conn.close()

    def session_secret(self) -> str:
        if os.getenv("SECRET_KEY"):
            return os.environ["SECRET_KEY"]
        rows = self._run("SELECT value FROM settings WHERE key = 'session_secret'")
        if rows:
            return rows[0][0]
        key = secrets.token_hex(32)
        self._run("INSERT INTO settings (key, value) VALUES ('session_secret', :v) "
                  "ON CONFLICT (key) DO NOTHING", v=key)
        return self._run("SELECT value FROM settings WHERE key = 'session_secret'")[0][0]

    def signup(self, email: str, source: str = "landing") -> bool:
        rows = self._run(
            "INSERT INTO signups (email, at, source) VALUES (:e, now(), :s) "
            "ON CONFLICT (email) DO NOTHING RETURNING email",
            e=email.strip().lower(), s=source)
        return bool(rows)

    def feedback(self, email: str, rating: int | None, liked: str,
                 improve: list[str], message: str) -> dict:
        row = {
            "email": email, "at": _now(),
            "rating": rating if rating in (1, 2, 3, 4, 5) else None,
            "liked": liked.strip()[:2000],
            "improve": [a for a in improve if a in IMPROVE_AREAS],
            "message": message.strip()[:4000],
        }
        self._run("INSERT INTO feedback (email, at, rating, liked, improve, message) "
                  "VALUES (:e, now(), :r, :l, :i, :m)",
                  e=row["email"], r=row["rating"], l=row["liked"],
                  i=json.dumps(row["improve"]), m=row["message"])
        return row

    def signups(self) -> list[dict]:
        return [{"email": e, "at": at.isoformat(), "source": src}
                for e, at, src in self._run("SELECT email, at, source FROM signups ORDER BY at")]

    def all_feedback(self) -> list[dict]:
        out = []
        for e, at, r, l, i, m in self._run(
                "SELECT email, at, rating, liked, improve, message FROM feedback ORDER BY at"):
            try:
                improve = json.loads(i or "[]")
            except json.JSONDecodeError:
                improve = []
            out.append({"email": e, "at": at.isoformat(), "rating": r, "liked": l or "",
                        "improve": improve, "message": m or ""})
        return out


    def load_users(self) -> dict[str, dict]:
        out = {}
        for email, data in self._run("SELECT email, data FROM user_state"):
            try:
                out[email] = json.loads(data)
            except json.JSONDecodeError:
                continue
        return out

    def save_users(self, rows: dict[str, dict]):
        """One connection for the whole batch: this runs after every scan."""
        if not rows:
            return
        import pg8000.native
        conn = pg8000.native.Connection(**self._params)
        try:
            for email, data in rows.items():
                conn.run("INSERT INTO user_state (email, data, updated_at) VALUES (:e, :d, now()) "
                         "ON CONFLICT (email) DO UPDATE SET data = :d, updated_at = now()",
                         e=email, d=json.dumps(data))
        finally:
            conn.close()


def make_accounts() -> Accounts:
    """Postgres when DATABASE_URL is set, local files otherwise."""
    url = os.getenv("DATABASE_URL")
    return PgAccounts(url) if url else Accounts()
