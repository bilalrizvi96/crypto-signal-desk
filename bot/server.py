"""Flask server: serves the dashboard and runs the scan loop in the background."""
from __future__ import annotations

import logging
import threading
import time
import webbrowser

from flask import Flask, Response, jsonify, request

from .config import Config
from .dashboard import render_page
from .scanner import Scanner

log = logging.getLogger("bot.server")


def create_app(cfg: Config, scanner: Scanner) -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False

    @app.get("/")
    def index():
        state = scanner.last_result()
        html = render_page(state, poll_ms=cfg.server.scan_interval_seconds * 1000, live=True)
        return Response(html, mimetype="text/html")

    @app.get("/api/state")
    def api_state():
        return jsonify(scanner.last_result() or {})

    @app.get("/api/scan")
    def api_scan():
        return jsonify(scanner.scan())

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
                    })
        out.sort(key=lambda s: -abs(s["score"]))
        return jsonify({"generated_at": state.get("generated_at"), "signals": out})

    @app.post("/api/paper/reset")
    def api_paper_reset():
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
    log.info("running first scan before serving…")
    scanner.scan()

    stop_event = threading.Event()
    thread = threading.Thread(
        target=scan_loop,
        args=(scanner, cfg.server.scan_interval_seconds, stop_event),
        daemon=True,
        name="scan-loop",
    )
    thread.start()

    app = create_app(cfg, scanner)
    url = f"http://{cfg.server.host}:{cfg.server.port}"
    print(f"\n  Dashboard →  {url}\n")
    if cfg.server.open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    try:
        app.run(host=cfg.server.host, port=cfg.server.port,
                debug=False, use_reloader=False, threaded=True)
    finally:
        stop_event.set()
