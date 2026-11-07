"""Scanner — ties data, indicators, strategy, news and execution together."""
from __future__ import annotations

import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pandas as pd

from . import indicators as ta
from .config import Config
from .data import MarketData, RateLimited, SymbolInfo, tf_seconds
from .ai_review import AIReviewer
from .live import LiveTrader
from .news import NewsEngine
from .paper import PaperBroker, prior_success
from .settings import DeskSettings, goal_status
from .strategy import build_signal, scalp_fit

log = logging.getLogger("bot.scanner")


def _tf_rank(tf: str) -> int:
    try:
        return tf_seconds(tf)
    except ValueError:
        return 10 ** 9


def _candles(df: pd.DataFrame, n: int = 60) -> list[list[float]]:
    """Last n entry-timeframe bars as [time_ms, open, high, low, close] for the chart."""
    tail = df.iloc[-n:]
    return [[int(ts.timestamp() * 1000), float(r.open), float(r.high), float(r.low), float(r.close)]
            for ts, r in zip(tail.index, tail.itertuples())]


def _brief(sig) -> dict:
    return {"symbol": sig.symbol, "base": sig.base, "name": sig.name,
            "asset_class": sig.asset_class, "price": sig.price,
            "action": sig.action, "direction": sig.direction, "score": sig.score,
            "fit": sig.scalp_fit}


class Scanner:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.data = MarketData(cfg.universe)
        self.news = NewsEngine(cfg.news)
        self.paper = PaperBroker(cfg.risk) if cfg.paper_enabled else None
        self.live = LiveTrader(cfg.live, cfg.risk) if cfg.live.enabled else None
        self.ai = AIReviewer(cfg.ai) if cfg.ai.enabled else None
        self.settings = DeskSettings(cfg)
        self.users = None          # UserBooks, attached by the server (it owns the storage)
        self.last_prices: dict[str, float] = {}
        self._lock = threading.RLock()
        self._last_result: dict | None = None
        self._universe: list[SymbolInfo] = []
        self._universe_at: float = 0.0
        self._last_bar_ts: dict[tuple[str, str], pd.Timestamp] = {}
        self.scan_count = 0

    # ------------------------------------------------------------ universe
    def universe(self, ttl: int = 900) -> list[SymbolInfo]:
        now = time.time()
        with self._lock:
            if self._universe and now - self._universe_at < ttl:
                return self._universe
        uni = self.data.build_universe()
        with self._lock:
            self._universe = uni
            self._universe_at = now
        return uni

    # ---------------------------------------------------------- per-symbol
    def _modes(self) -> list[tuple[str, object]]:
        out = []
        if self.cfg.scalp.enabled:
            out.append(("scalp", self.cfg.scalp))
        if self.cfg.swing.enabled:
            out.append(("swing", self.cfg.swing))
        if self.cfg.position.enabled:
            out.append(("position", self.cfg.position))
        return out

    def _analyse_symbol(self, info: SymbolInfo) -> tuple[list, dict]:
        """Returns (signals, meta) for one symbol across all enabled modes."""
        signals, frames_cache, entry_frames = [], {}, {}
        coin_news = (
            self.news.for_coin(info.base).to_dict() if self.cfg.news.enabled else {}
        )

        for mode, mcfg in self._modes():
            try:
                frames = {}
                for role, tf in (("entry", mcfg.entry_tf),
                                 ("confirm", mcfg.confirm_tf),
                                 ("bias", mcfg.bias_tf)):
                    if tf in frames_cache:
                        if role == "entry" and len(frames_cache[tf]) < 210:
                            frames = {}
                            break
                        frames[role] = frames_cache[tf]
                        continue
                    raw = self.data.fetch_ohlcv(info.symbol, tf, mcfg.limit)
                    need = 210 if role == "entry" else min(210, mcfg.min_htf_bars)
                    if len(raw) < need:
                        log.debug("%s %s: only %d candles, skipping mode %s",
                                  info.symbol, tf, len(raw), mode)
                        frames = {}
                        break
                    enriched = ta.enrich(raw)
                    frames_cache[tf] = enriched
                    frames[role] = enriched
                if not frames:
                    continue

                sig = build_signal(
                    symbol=info.symbol,
                    base=info.base,
                    bucket=info.bucket,
                    mode=mode,
                    mode_cfg=mcfg,
                    risk_cfg=self.cfg.risk,
                    frames=frames,
                    coin_news=coin_news,
                    news_weight=self.cfg.news.sentiment_weight,
                    block_on_critical=self.cfg.news.block_on_critical,
                    change_24h=info.change_24h,
                    quote_volume=info.quote_volume,
                )
                sig.name = info.name
                sig.asset_class = info.asset_class
                if mode == "scalp":
                    sig.scalp_fit = scalp_fit(sig, info.spread_pct)
                signals.append(sig)
                entry_frames[mode] = frames["entry"]
            except Exception as exc:
                log.warning("analysis failed for %s (%s): %s", info.symbol, mode, exc)
                log.debug(traceback.format_exc())

        meta = {
            "price": info.last,
            # newest bar per timeframe, so each paper position counts its own bars
            "last_bar_ts": {tf: df.index[-1] for tf, df in frames_cache.items()},
            "news": coin_news,
            "entry_frames": entry_frames,
        }
        return signals, meta

    # --------------------------------------------------------------- scan
    def scan(self) -> dict:
        started = time.time()
        self.scan_count += 1

        try:
            uni = list(self.universe())
        except RateLimited as exc:
            return self._keep_last(str(exc))
        except Exception as exc:
            log.error("universe build failed: %s", exc)
            return self._error_result(f"could not reach {self.cfg.universe.exchange}: {exc}")

        # an open paper position must keep getting prices even after its coin
        # drops out of the volume ranking, or its stop and target never trigger
        held = {p.symbol for p in self.paper.positions.values()} if self.paper else set()
        if self.users:
            held |= self.users.symbols()
        if held:
            scanned = {s.symbol for s in uni}
            for sym in sorted(held - scanned):
                try:
                    info = self.data.symbol_info(sym)
                except Exception as exc:
                    log.warning("could not price open position %s: %s", sym, exc)
                    continue
                if info:
                    uni.append(info)

        if self.cfg.news.enabled:
            try:
                self.news.refresh({s.base for s in uni})
            except Exception as exc:
                log.warning("news refresh failed: %s", exc)

        all_signals, prices, bar_advanced, errors = [], {}, set(), []

        # modest parallelism: ccxt's sync rate limiter is not built for wide fan-out
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(self._analyse_symbol, info): info for info in uni}
            for fut in as_completed(futures):
                info = futures[fut]
                try:
                    signals, meta = fut.result()
                except Exception as exc:
                    errors.append(f"{info.symbol}: {exc}")
                    continue
                all_signals.extend(signals)
                # Mark open positions against the live ticker price, never a
                # per-mode close: a 4h bar's close and a 5m bar's close are
                # different numbers, and mixing them corrupts unrealised P&L.
                mark = info.last
                if not mark and signals:
                    fastest = min(
                        signals,
                        key=lambda s: _tf_rank(s.timeframes.get("entry", "1d")),
                    )
                    mark = fastest.price
                prices[info.symbol] = mark
                for tf, ts in (meta.get("last_bar_ts") or {}).items():
                    prev = self._last_bar_ts.get((info.symbol, tf))
                    if prev is not None and ts > prev:
                        bar_advanced.add((info.symbol, tf))
                    self._last_bar_ts[(info.symbol, tf)] = ts

        # a ban that started mid-scan leaves most symbols unpriced; trading
        # on the few that got through would be acting on a partial picture
        if self.data.banned_until > time.time() and len(prices) < len(uni) / 2:
            return self._keep_last(self.data.ban_message())

        # ---- execution -----------------------------------------------------
        paper_events = []
        if self.paper:
            paper_events = self.paper.mark_to_market(prices, bar_advanced)
        if self.live:
            self.live.sync_positions()
        # success estimate after mark_to_market so trades closed this scan count
        self._attach_success(all_signals)

        # at the profit goal, bank the practice gains and open nothing new
        goal = goal_status(self.cfg.risk, self.paper.stats(prices) if self.paper else None)
        if goal.get("locked") and self.paper and self.paper.positions:
            for t in self.paper.close_all(prices, "profit goal reached"):
                paper_events.append(("close", t))
            goal = goal_status(self.cfg.risk, self.paper.stats(prices))

        # every entry goes through the AI review first; only approved ones trade
        entries = sorted((s for s in all_signals if s.action == "enter"),
                         key=lambda s: -abs(s.score))
        approved = self._ai_gate(entries)

        if self.paper:
            for sig in ([] if goal.get("locked") else approved):
                self.paper.open_from_signal(sig)
            self.paper.save()

        live_records = []
        if self.live:
            halted = bool(self.paper and self.paper.halted_reason)
            for sig in ([] if goal.get("locked") else approved):
                live_records.append(self.live.submit(sig, halted=halted).to_dict())

        # every signed-up user's own practice account, with their own sizing
        if self.users:
            self.users.on_scan(prices, bar_advanced, approved)
        self.last_prices = prices

        best_scalp = sorted((s for s in all_signals if s.mode == "scalp" and s.scalp_fit),
                            key=lambda s: -s.scalp_fit["score"])[:5]

        # ---- assemble ------------------------------------------------------
        by_mode = {}
        for mode, _ in self._modes():
            mode_sigs = [s for s in all_signals if s.mode == mode]
            mode_sigs.sort(key=lambda s: (-abs(s.score), s.symbol))
            by_mode[mode] = [s.to_dict() for s in mode_sigs]

        actionable = [s for s in all_signals if s.action == "enter"]
        watching = [s for s in all_signals if s.action == "watch"]

        result = {
            "warning": None,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scan_number": self.scan_count,
            "duration_seconds": round(time.time() - started, 2),
            "exchange": self.cfg.universe.exchange,
            "market_type": self.cfg.universe.market_type,
            "universe": [
                {
                    "symbol": s.symbol, "base": s.base, "bucket": s.bucket,
                    "rank": s.rank, "quote_volume": s.quote_volume,
                    "change_24h": s.change_24h, "last": s.last,
                    "name": s.name, "asset_class": s.asset_class,
                }
                for s in uni
            ],
            "signals": by_mode,
            "best_scalp": [_brief(s) for s in best_scalp],
            "counts": {
                "symbols": len(uni),
                "signals": len(all_signals),
                "enter": len(actionable),
                "watch": len(watching),
                "long": sum(1 for s in actionable if s.direction == "long"),
                "short": sum(1 for s in actionable if s.direction == "short"),
            },
            "top_signals": [s.to_dict() for s in
                            sorted(actionable + watching,
                                   key=lambda s: -abs(s.score))[:8]],
            "market_mood": self.news.market_mood() if self.cfg.news.enabled else {},
            "news": [i.to_dict() for i in self.news.items()[:30]] if self.cfg.news.enabled else [],
            "news_errors": self.news.errors() if self.cfg.news.enabled else [],
            "paper": self.paper.stats(prices) if self.paper else None,
            "ai": self.ai.stats() if self.ai else {"enabled": False},
            "settings": self.settings.view(),
            "goal": goal,
            "paper_events": [
                {"type": kind, "data": payload.to_dict() if hasattr(payload, "to_dict") else payload}
                for kind, payload in paper_events
            ],
            "live": {
                "gate": self.live.gate_status(),
                "recent_orders": self.live.recent_orders(),
                "this_scan": live_records,
            } if self.live else {"gate": {"enabled": False}, "recent_orders": [], "this_scan": []},
            "errors": errors,
            "config": {
                **{mode: {"entry": m.entry_tf, "confirm": m.confirm_tf,
                          "bias": m.bias_tf, "min_score": m.min_score}
                   for mode, m in self._modes()},
                "risk": {
                    "equity": self.cfg.risk.account_equity,
                    "risk_per_trade_pct": self.cfg.risk.risk_per_trade_pct,
                    "max_open": self.cfg.risk.max_open_positions,
                },
            },
        }

        with self._lock:
            self._last_result = result
        log.info("scan #%d done in %.1fs — %d enter, %d watch",
                 self.scan_count, result["duration_seconds"],
                 result["counts"]["enter"], result["counts"]["watch"])
        return result

    def _ai_gate(self, entries: list) -> list:
        """Queue reviews for entries that could actually trade, and return the
        ones the AI has already approved. A new setup trades on a later scan,
        once its background review lands."""
        if not self.ai:
            return entries
        approved = []
        for sig in entries:
            could_trade = ((self.paper and self.paper.can_open(sig))
                           or (self.live and self.live.wants(sig))
                           or (self.users and self.users.any_can_open(sig)))
            verdict = self.ai.verdict_for(sig)
            if verdict is None and could_trade:
                self.ai.request(sig, self._ai_context(sig))
            sig.ai = self.ai.status_for(sig)
            if self.ai.approves(verdict):
                approved.append(sig)
            # the AI couldn't look at it (no key, error, spent budget): unless
            # a review is required, the desk's own rules decide. A "skip" still blocks.
            elif not self.cfg.ai.required and sig.ai.get("state") in ("unavailable", "budget", "error"):
                sig.ai = {**sig.ai, "fallback": True}
                approved.append(sig)
        return approved

    def _ai_context(self, sig) -> dict:
        """What the reviewer gets beyond the signal itself."""
        record = {}
        if self.paper:
            trades = [t for t in self.paper.closed if t.mode == sig.mode]
            record = {
                "closed_trades": len(trades),
                "wins": sum(1 for t in trades if t.pnl > 0),
                "avg_pnl_usd": round(sum(t.pnl for t in trades) / len(trades), 3) if trades else None,
                "last_10": [{"symbol": t.symbol, "direction": t.direction, "exit": t.reason,
                             "r_multiple": t.r_multiple, "bars_held": t.bars_held}
                            for t in trades[-10:]],
            }
        return {
            "account": {"market_type": self.cfg.universe.market_type,
                        "exchange": self.cfg.universe.exchange,
                        "round_trip_cost_pct": 0.14,
                        "risk_per_trade_pct": self.cfg.risk.risk_per_trade_pct},
            "market_mood": self.news.market_mood() if self.cfg.news.enabled else {},
            "desk_track_record_this_mode": record,
        }

    def ai_research(self, symbol: str, mode: str) -> dict:
        """On-demand review from the detail page. Counts toward the daily cap."""
        if not self.ai:
            return {"state": "unavailable", "reason": "AI review is off in config"}
        info = self.data.symbol_info(symbol)
        if info is None:
            return {"state": "error", "reason": f"{symbol} is not tradable"}
        signals, _ = self._analyse_symbol(info)
        self._attach_success(signals)
        sig = next((s for s in signals if s.mode == mode), None)
        if sig is None:
            return {"state": "error", "reason": f"no {mode} analysis for {symbol}"}
        return self.ai.review_now(sig, self._ai_context(sig))

    def _attach_success(self, signals: list):
        # computed after mark_to_market so trades closed this scan count
        chance = {mode: (self.paper.success_chance(mode) if self.paper else prior_success())
                  for mode, _ in self._modes()}
        for sig in signals:
            sig.success = dict(chance.get(sig.mode) or prior_success())

    # ------------------------------------------------------------- search
    def search(self, query: str, limit: int = 12) -> list[dict]:
        return [
            {"symbol": r["symbol"], "base": r["base"], "name": r["name"],
             "asset_class": r["asset_class"], "last": r["last"],
             "change_24h": r["change_24h"], "quote_volume": r["quote_volume"]}
            for r in self.data.search(query, limit)
        ]

    def analyse_one(self, symbol: str) -> dict:
        """Full analysis for any pair, on demand. Read-only: it never opens a
        paper position, because a search is a question, not a decision."""
        info = self.data.symbol_info(symbol)
        if info is None:
            return {"symbol": symbol, "error": f"{symbol} is not tradable on "
                                               f"{self.cfg.universe.exchange}"}
        signals, meta = self._analyse_symbol(info)
        if not signals:
            return {"symbol": symbol, "error": "not enough price history to analyse"}
        self._attach_success(signals)
        if self.ai:
            for sig in signals:
                sig.ai = self.ai.status_for(sig)
        scalp = next((s for s in signals if s.mode == "scalp"), None)
        return {
            "symbol": info.symbol, "base": info.base, "name": info.name,
            "asset_class": info.asset_class, "exchange": self.cfg.universe.exchange,
            "spread_pct": info.spread_pct, "quote_volume": info.quote_volume,
            "change_24h": info.change_24h, "last": info.last,
            "scalp_fit": scalp.scalp_fit if scalp else None,
            "signals": [s.to_dict() for s in signals],
            "candles": {mode: _candles(df) for mode, df in meta["entry_frames"].items()},
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _keep_last(self, message: str) -> dict:
        """Rate-limited: keep showing the last good scan, marked as old."""
        log.warning("scan skipped: %s", message)
        with self._lock:
            last = self._last_result
            if not last or last.get("error"):
                return self._error_result(message)
            self._last_result = {**last, "warning": message + ". Showing the last scan until then."}
            return self._last_result

    def _error_result(self, message: str) -> dict:
        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scan_number": self.scan_count,
            "duration_seconds": 0.0,
            "error": message,
            "signals": {},
            "counts": {"symbols": 0, "signals": 0, "enter": 0, "watch": 0,
                       "long": 0, "short": 0},
            "top_signals": [],
            "best_scalp": [],
            "universe": [],
            "market_mood": {},
            "news": [],
            "news_errors": [],
            "paper": self.paper.stats({}) if self.paper else None,
            "paper_events": [],
            "live": {"gate": {"enabled": False}, "recent_orders": [], "this_scan": []},
            "errors": [message],
            "config": {},
        }
        with self._lock:
            self._last_result = result
        return result

    def last_result(self) -> dict | None:
        with self._lock:
            return self._last_result
