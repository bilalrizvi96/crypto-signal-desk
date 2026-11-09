"""Flask server: serves the dashboard and runs the scan loop in the background."""
from __future__ import annotations

import hmac
import logging
import os
import threading
import time
import webbrowser
from datetime import timedelta

from flask import Flask, Response, jsonify, redirect, request, session

from .accounts import IMPROVE_AREAS, Accounts, make_accounts, valid_email
from .config import Config
from .dashboard import render_page
from .landing import render_admin, render_landing
from .scanner import Scanner
from .users import UserBooks

log = logging.getLogger("bot.server")

# reachable without signing up; everything else needs an email on file
PUBLIC_PATHS = {"/", "/api/signup", "/healthz", "/logout"}


def create_app(cfg: Config, scanner: Scanner, accounts: Accounts | None = None) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False          # Flask 3 ignores the old JSON_SORT_KEYS setting
    accounts = accounts or make_accounts()
    app.secret_key = accounts.session_secret()
    app.config.update(SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=timedelta(days=90))

    def admin_allowed() -> bool:
        """ADMIN_TOKEN, if set, is required. Without it, only a browser on this
        machine talking to it directly (no proxy in between) gets in."""
        token = os.getenv("ADMIN_TOKEN")
        if token:
            given = request.args.get("token") or session.get("admin_token") or ""
            if hmac.compare_digest(given, token):
                session["admin_token"] = given
                return True
            return False
        return (request.remote_addr in ("127.0.0.1", "::1")
                and "X-Forwarded-For" not in request.headers)

    if getattr(scanner, "users", False) is None:
        scanner.users = UserBooks(cfg, accounts)
    users = getattr(scanner, "users", None)

    def own_user() -> str | None:
        """The signed-in user whose own practice account this request is
        about; None for the owner, who sees and runs the main desk."""
        email = session.get("email")
        return email if users and email and not admin_allowed() else None

    def personal(state: dict | None) -> dict | None:
        email = own_user()
        if not state or not email:
            return state
        return {**state, **users.view(email, scanner.last_prices), "viewer": "user"}

    @app.before_request
    def require_signup():
        path = request.path
        if path in PUBLIC_PATHS or path.startswith("/admin"):
            return None
        if session.get("email"):
            return None
        if path.startswith("/api/"):
            return jsonify({"error": "sign up with your email first"}), 401
        return redirect("/")

    @app.get("/")
    def landing():
        if session.get("email"):
            return redirect("/app")
        return Response(render_landing(), mimetype="text/html")

    @app.post("/api/signup")
    def api_signup():
        if not request.is_json:          # JSON only, so other sites can't post forms here
            return jsonify({"ok": False, "error": "expected JSON"}), 415
        email = str((request.get_json(silent=True) or {}).get("email", "")).strip().lower()
        if not valid_email(email):
            return jsonify({"ok": False, "error": "Please enter a valid email address."}), 400
        is_new = accounts.signup(email)
        session.permanent = True
        session["email"] = email
        log.info("sign-up: %s (%s)", email, "new" if is_new else "returning")
        return jsonify({"ok": True, "new": is_new})

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect("/")

    @app.get("/app")
    def index():
        state = personal(scanner.last_result())
        html = render_page(state, poll_ms=cfg.server.scan_interval_seconds * 1000, live=True)
        return Response(html, mimetype="text/html")

    @app.post("/api/feedback")
    def api_feedback():
        if not request.is_json:
            return jsonify({"ok": False, "error": "expected JSON"}), 415
        data = request.get_json(silent=True) or {}
        try:
            rating = int(data.get("rating")) if data.get("rating") not in (None, "") else None
        except (TypeError, ValueError):
            rating = None
        improve = data.get("improve") if isinstance(data.get("improve"), list) else []
        liked, message = str(data.get("liked") or ""), str(data.get("message") or "")
        if not (rating or improve or liked.strip() or message.strip()):
            return jsonify({"ok": False, "error": "Add a rating or a few words first."}), 400
        accounts.feedback(session["email"], rating, liked, [str(a) for a in improve], message)
        return jsonify({"ok": True})

    @app.get("/admin/feedback")
    def admin_feedback():
        if not admin_allowed():
            return Response("Not allowed. Open this from the machine running the desk, "
                            "or pass ?token=… when ADMIN_TOKEN is set.", status=403)
        html = render_admin(accounts.summary(), accounts.signups(), accounts.all_feedback(),
                            users.admin_rows(scanner.last_prices) if users else [])
        return Response(html, mimetype="text/html")

    @app.get("/admin/feedback.json")
    def admin_feedback_json():
        if not admin_allowed():
            return jsonify({"error": "not allowed"}), 403
        return jsonify({"summary": accounts.summary(), "signups": accounts.signups(),
                        "feedback": accounts.all_feedback(), "areas": IMPROVE_AREAS,
                        "users": users.admin_rows(scanner.last_prices) if users else []})

    @app.get("/api/state")
    def api_state():
        return jsonify(personal(scanner.last_result()) or {})

    @app.get("/api/scan")
    def api_scan():
        return jsonify(personal(scanner.scan()))

    @app.get("/api/signals")
    def api_signals():
        """Flat list of currently firing signals — handy for your own scripts."""
        state = scanner.last_result() or {}
        out = []
        for mode, sigs in (state.get("signals") or {}).items():
            for s in sigs:
                if s["action"] in ("enter", "watch"):
                    out.append({
                        "symbol": s["symbol"], "mode": mode, "action": s["action"],
                        "direction": s["direction"], "score": s["score"],
                        "confidence": s["confidence"], "entry": s["entry"],
                        "stop": s["stop"], "targets": s["targets"], "rr": s["rr"],
                        "success": s.get("success"),
                    })
        out.sort(key=lambda s: -abs(s["score"]))
        return jsonify({"generated_at": state.get("generated_at"), "signals": out})

    @app.get("/api/search")
    def api_search():
        """Coins and tokenized stocks matching ?q= by ticker or name."""
        q = (request.args.get("q") or "").strip()[:40]
        if not q:
            return jsonify({"results": []})
        try:
            return jsonify({"results": scanner.search(q)})
        except Exception as exc:
            log.warning("search failed: %s", exc)
            return jsonify({"results": [], "error": str(exc)}), 502

    @app.get("/api/analyze")
    def api_analyze():
        """Full scalp + swing analysis for one pair, e.g. ?symbol=NVDAB/USDT."""
        symbol = (request.args.get("symbol") or "").strip().upper()[:30]
        if not symbol:
            return jsonify({"error": "symbol is required"}), 400
        try:
            result = scanner.analyse_one(symbol)
        except Exception as exc:
            log.warning("analyze failed for %s: %s", symbol, exc)
            return jsonify({"symbol": symbol, "error": str(exc)}), 502
        return jsonify(result), (404 if result.get("error") else 200)

    @app.get("/api/ai/status")
    def api_ai_status():
        return jsonify(scanner.ai.stats() if scanner.ai else {"enabled": False})

    @app.post("/api/ai/research")
    def api_ai_research():
        """Run an AI review of one pair now (detail page). Takes up to a couple of
        minutes with web search, and counts toward the daily review cap."""
        if not request.is_json:
            return jsonify({"state": "error", "reason": "expected JSON"}), 415
        data = request.get_json(silent=True) or {}
        symbol = str(data.get("symbol") or "").strip().upper()[:30]
        mode = str(data.get("mode") or "").strip().lower()
        if not symbol or mode not in ("scalp", "swing", "position"):
            return jsonify({"state": "error", "reason": "symbol and mode are required"}), 400
        try:
            return jsonify(scanner.ai_research(symbol, mode))
        except Exception as exc:
            log.warning("AI research failed for %s: %s", symbol, exc)
            return jsonify({"state": "error", "reason": str(exc)[:200]}), 502

    @app.get("/api/settings")
    def api_settings():
        """A user gets their own account's settings; the owner gets the main
        desk's, which also decide which modes send exchange orders."""
        email = own_user()
        if email:
            return jsonify({**users.view(email, {})["settings"], "scope": "user", "can_edit": True})
        return jsonify({**scanner.settings.view(), "scope": "desk", "can_edit": admin_allowed()})

    @app.post("/api/settings")
    def api_settings_update():
        if not request.is_json:
            return jsonify({"ok": False, "error": "expected JSON"}), 415
        data = request.get_json(silent=True) or {}
        email = own_user()
        try:
            if email:
                view = {**users.update_settings(email, data), "scope": "user"}
            elif admin_allowed():
                view = {**scanner.settings.update(data), "scope": "desk"}
            else:
                return jsonify({"ok": False, "error": "only the desk owner can change trading settings"}), 403
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, **view})

    @app.post("/api/paper/reset")
    def api_paper_reset():
        email = own_user()
        if email:
            users.reset(email)
            return jsonify({"ok": True})
        if not admin_allowed():
            return jsonify({"ok": False, "error": "only the desk owner can reset the desk"}), 403
        if not scanner.paper:
            return jsonify({"ok": False, "error": "paper trading is disabled"}), 400
        scanner.paper.reset()
        return jsonify({"ok": True})

    @app.get("/healthz")
    def healthz():
        state = scanner.last_result()
        return jsonify({
            "ok": bool(state and not state.get("error")),
            "scans": scanner.scan_count,
            "last": state.get("generated_at") if state else None,
        })

    @app.get("/export")
    def export():
        """A frozen snapshot you can save or email — no polling."""
        html = render_page(scanner.last_result(), live=False)
        return Response(
            html, mimetype="text/html",
            headers={"Content-Disposition": 'attachment; filename="signal-snapshot.html"'},
        )

    return app


def scan_loop(scanner: Scanner, interval: int, stop_event: threading.Event):
    while not stop_event.is_set():
        started = time.time()
        try:
            scanner.scan()
        except Exception as exc:
            log.error("scan loop error: %s", exc, exc_info=True)
        elapsed = time.time() - started
        stop_event.wait(max(5.0, interval - elapsed))


def serve(cfg: Config):
    scanner = Scanner(cfg)
    app = create_app(cfg, scanner)      # attaches the users' accounts before the first scan
    # the loop runs the first scan straight away; serving starts at once so a
    # host's port check passes while that scan is still downloading candles
    stop_event = threading.Event()
    thread = threading.Thread(
        target=scan_loop,
        args=(scanner, cfg.server.scan_interval_seconds, stop_event),
        daemon=True,
        name="scan-loop",
    )
    thread.start()

    url = f"http://{cfg.server.host}:{cfg.server.port}"
    print(f"\n  Dashboard →  {url}\n")
    if cfg.server.open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    try:
        app.run(host=cfg.server.host, port=cfg.server.port,
                debug=False, use_reloader=False, threaded=True)
    finally:
        stop_event.set()
