#!/usr/bin/env python3
"""Crypto Signal Desk — entry point.

    python run.py                 # scan loop + live dashboard (default)
    python run.py once            # one scan, write dashboard.html, print a summary
    python run.py once --open     # …and open it in your browser
    python run.py paper-reset     # wipe the paper portfolio and start again
    python run.py check           # verify exchange + feeds are reachable
"""
from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from pathlib import Path

from bot.config import load_config, ROOT
from bot.dashboard import render_page


def setup_logging(verbose: bool):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)-14s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def print_summary(result: dict):
    counts = result.get("counts", {})
    mood = result.get("market_mood", {})
    print("\n" + "═" * 78)
    print(f"  Scan #{result.get('scan_number')} · {result.get('exchange')} · "
          f"{counts.get('symbols', 0)} pairs · {result.get('duration_seconds')}s")
    print(f"  News mood: {mood.get('label', 'n/a')} "
          f"({mood.get('item_count', 0)} headlines)"
          + ("  ⚠ macro event in the last 6h" if mood.get("macro_alert") else ""))
    print("═" * 78)

    fired = [s for m in result.get("signals", {}).values() for s in m
             if s["action"] in ("enter", "watch")]
    fired.sort(key=lambda s: -abs(s["score"]))

    if not fired:
        print("  No signals. Nothing meets the thresholds right now.\n")
        return

    for s in fired[:20]:
        arrow = "▲" if s["direction"] == "long" else "▼"
        tag = "FIRE" if s["action"] == "enter" else "watch"
        print(f"\n  {arrow} {s['base']:<8} {s['mode']:<6} {tag:<5} "
              f"score {s['score']:+6.1f}  {s['confidence']:<8} {s['regime']}")
        print(f"      entry {s['entry']:<14.8g} stop {s['stop']:<14.8g} "
              f"targets {' / '.join(f'{t:.8g}' for t in s['targets'])}  ({s['rr']}R)")
        top = s.get("votes", [])[:3]
        if top:
            print("      " + " | ".join(f"{v['name']}: {v['note'][:44]}" for v in top))
        for w in s.get("warnings", [])[:2]:
            print(f"      ⚠ {w}")

    paper = result.get("paper")
    if paper:
        print(f"\n  Paper: {paper['equity']:.2f} ({paper['total_return_pct']:+.2f}%) · "
              f"{paper['closed_trades']} closed · {paper['win_rate']}% win · "
              f"avg {paper['avg_r']}R · {paper['open_positions']} open")
    print()


def main():
    parser = argparse.ArgumentParser(description="Crypto Signal Desk")
    parser.add_argument("command", nargs="?", default="serve",
                        choices=["serve", "once", "paper-reset", "check"])
    parser.add_argument("--config", default=None, help="path to config.yaml")
    parser.add_argument("--out", default=str(ROOT / "dashboard.html"),
                        help="output file for `once`")
    parser.add_argument("--open", action="store_true", help="open the html after `once`")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)
    cfg = load_config(args.config)

    if args.command == "paper-reset":
        from bot.paper import PaperBroker
        PaperBroker(cfg.risk).reset()
        print("Paper portfolio reset.")
        return

    if args.command == "check":
        ok = True
        print(f"Exchange: {cfg.universe.exchange} ({cfg.universe.market_type})")
        try:
            from bot.data import MarketData
            md = MarketData(cfg.universe)
            uni = md.build_universe()
            print(f"  ✓ {len(uni)} tradable pairs found")
            print("    " + ", ".join(f"{s.base}({s.bucket[0]})" for s in uni[:14]))
            df = md.fetch_ohlcv(uni[0].symbol, cfg.scalp.entry_tf, 300)
            print(f"  ✓ OHLCV works — {len(df)} candles for {uni[0].symbol}")
        except Exception as exc:
            ok = False
            print(f"  ✗ exchange failed: {exc}")
        if cfg.news.enabled:
            try:
                from bot.news import NewsEngine
                ne = NewsEngine(cfg.news)
                ne.refresh({"BTC", "ETH", "SOL"}, force=True)
                n_items, n_errors = len(ne.items()), len(ne.errors())
                if n_items:
                    print(f"  ✓ news: {n_items} headlines, "
                          f"mood {ne.market_mood()['label']}"
                          + (f" ({n_errors} feed(s) unreachable)" if n_errors else ""))
                else:
                    ok = False
                    print(f"  ✗ news: no headlines retrieved "
                          f"({n_errors} of {len(cfg.news.feeds)} feeds failed)")
                for err in ne.errors()[:3]:
                    print(f"    ⚠ {err[:110]}")
            except Exception as exc:
                ok = False
                print(f"  ✗ news failed: {exc}")
        print("\nReady." if ok else "\nSome checks failed — see above.")
        sys.exit(0 if ok else 1)

    if args.command == "once":
        from bot.scanner import Scanner
        scanner = Scanner(cfg)
        result = scanner.scan()
        print_summary(result)
        out = Path(args.out)
        out.write_text(render_page(result, live=False), encoding="utf-8")
        print(f"  Dashboard written to {out}\n")
        if args.open:
            webbrowser.open(out.resolve().as_uri())
        return

    from bot.server import serve
    serve(cfg)


if __name__ == "__main__":
    main()
