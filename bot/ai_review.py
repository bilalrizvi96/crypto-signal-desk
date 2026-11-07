"""AI trade review — Claude researches a setup before any trade opens.

The scanner proposes; Claude decides. Each review gets the desk's own
multi-timeframe read, indicator votes, levels, headlines and track record,
searches the live web for fresh news on the asset, and returns a structured
take/skip verdict with its reasons.

Fail-closed by design: no API key, an API error, a refusal, an unparseable
answer or a spent daily budget all mean "skip". Reviews run on a small thread
pool so a slow search never stalls the scan loop; a verdict is cached per
setup (symbol, mode, direction) so the same trade isn't paid for twice.

A review is a second opinion, not a guarantee. The paper record tracks how
AI-approved trades actually perform so that claim can be checked.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .config import AICfg, STATE_DIR

log = logging.getLogger("bot.ai")

# USD per million tokens (input, output) and per web search — for the spend
# estimate shown on the dashboard; the Anthropic console has the real bill
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
WEB_SEARCH_USD = 0.01
MAX_CONTINUATIONS = 4          # pause_turn resumes per review

SYSTEM_PROMPT = """You are the risk desk for a small crypto and tokenized-stock trading account.
A rules-based scanner has proposed one trade. Decide whether the account should take it.

Most proposed setups are not worth taking once fees, slippage and noise are counted, and the
account has been losing on short-term trades, so the bar is high: say "take" only when the
evidence clearly supports the trade at these exact levels. When in doubt, skip.

Work through it:
- Research first. Search the web for news, catalysts and sentiment on this asset from the last
  72 hours (listings, delistings, hacks, unlocks, earnings for tokenized stocks, regulatory
  action, large liquidations), and for scheduled macro events (FOMC, CPI, jobs data) that fall
  inside the planned holding period. Prefer primary and reputable sources; note dates.
- Check the setup against the desk's data: do the entry, confirm and bias timeframes agree; is
  the stop outside normal noise (a stop within about one ATR of entry on the entry timeframe is
  likely to be hit by noise); is the first target worth the risk after round-trip costs; is
  liquidity and spread good enough; do the indicator votes conflict.
- Weigh the desk's own recent track record for this mode. A mode that keeps losing needs
  stronger evidence, not weaker.
- A spot account cannot short: a short can only be acted on with derivatives.

Confidence is how sure you are that taking this trade at these levels is a good decision, from
0 to 100. Keep every text field short and concrete. Cite the news you relied on with its URL."""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["take", "skip"]},
        "confidence": {"type": "integer"},
        "summary": {"type": "string"},
        "reasons_for": {"type": "array", "items": {"type": "string"}},
        "reasons_against": {"type": "array", "items": {"type": "string"}},
        "key_news": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "impact": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                },
                "required": ["title", "url", "impact"],
                "additionalProperties": False,
            },
        },
        "watch_for": {"type": "string"},
    },
    "required": ["decision", "confidence", "summary", "reasons_for", "reasons_against",
                 "key_news", "watch_for"],
    "additionalProperties": False,
}

# signal fields worth the model's attention; sparkline and raw levels are noise to it
CONTEXT_FIELDS = (
    "symbol", "base", "name", "asset_class", "bucket", "mode", "direction", "action", "score",
    "confidence", "regime", "price", "entry", "stop", "targets", "rr", "atr_pct",
    "risk_pct_move", "hold_estimate", "invalidation", "timeframes", "confirm", "bias",
    "votes", "warnings", "news", "change_24h", "quote_volume", "scalp_fit", "success",
)


def setup_key(sig) -> str:
    return f"{sig.symbol}|{sig.mode}|{sig.direction}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_verdict(text: str) -> dict | None:
    """The structured-output answer is plain JSON; fall back to the last JSON
    object in the text in case the model wrapped it."""
    text = (text or "").strip()
    for candidate in (text, *reversed(re.findall(r"\{.*\}", text, re.S))):
        try:
            v = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(v, dict) and v.get("decision") in ("take", "skip"):
            return v
    return None


class AIReviewer:
    def __init__(self, cfg: AICfg, state_dir: Path | None = None, client=None):
        self.cfg = cfg
        self.log_path = (state_dir or STATE_DIR) / "ai_reviews.jsonl"
        self._lock = threading.RLock()
        self._cache: dict[str, dict] = {}          # setup key -> verdict
        self._pending: set[str] = set()
        self._pool = ThreadPoolExecutor(max_workers=max(1, cfg.workers),
                                        thread_name_prefix="ai-review")
        self._client = client
        self._load_today()

    # ------------------------------------------------------------ status
    def available(self) -> tuple[bool, str]:
        if not self.cfg.enabled:
            return False, "AI review is off in config"
        if self._client is None and not os.getenv(self.cfg.api_key_env):
            return False, f"add {self.cfg.api_key_env} to .env to turn on AI review"
        return True, ""

    def _load_today(self):
        """Count today's paid reviews from the log so the cap survives restarts."""
        self._today = _now().date().isoformat()
        self._reviews_today, self._spend_today = 0, 0.0
        if not self.log_path.exists():
            return
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("reviewed_at", "").startswith(self._today) and row.get("billed"):
                self._reviews_today += 1
                self._spend_today += float(row.get("cost_usd") or 0)

    def _roll_day(self):
        if _now().date().isoformat() != self._today:
            self._today = _now().date().isoformat()
            self._reviews_today, self._spend_today = 0, 0.0

    def stats(self) -> dict:
        ok, reason = self.available()
        with self._lock:
            self._roll_day()
            return {
                "enabled": self.cfg.enabled, "available": ok, "reason": reason,
                "model": self.cfg.model, "web_search": self.cfg.web_search,
                "reviews_today": self._reviews_today, "cap": self.cfg.max_reviews_per_day,
                "spend_today_usd": round(self._spend_today, 4),
                "pending": len(self._pending), "min_confidence": self.cfg.min_confidence,
                "required": self.cfg.required,
            }

    # ----------------------------------------------------------- verdicts
    def verdict_for(self, sig) -> dict | None:
        """A fresh cached verdict for this setup, or None."""
        with self._lock:
            v = self._cache.get(setup_key(sig))
            if not v:
                return None
            age_min = (time.time() - v["_ts"]) / 60.0
            if age_min > self.cfg.cache_minutes:
                self._cache.pop(setup_key(sig), None)
                return None
            return v

    def approves(self, verdict: dict | None) -> bool:
        return bool(verdict and verdict.get("decision") == "take"
                    and int(verdict.get("confidence") or 0) >= self.cfg.min_confidence
                    and not verdict.get("error"))

    def status_for(self, sig) -> dict:
        """What the dashboard shows on a card."""
        v = self.verdict_for(sig)
        if v:
            state = "approved" if self.approves(v) else ("error" if v.get("error") else "rejected")
            return {"state": state, **{k: val for k, val in v.items() if not k.startswith("_")}}
        with self._lock:
            if setup_key(sig) in self._pending:
                return {"state": "reviewing"}
        ok, reason = self.available()
        if not ok:
            return {"state": "unavailable", "reason": reason}
        if self._budget_left() <= 0:
            return {"state": "budget", "reason": "daily AI review limit reached"}
        return {"state": "none"}

    def _budget_left(self) -> int:
        with self._lock:
            self._roll_day()
            return self.cfg.max_reviews_per_day - self._reviews_today - len(self._pending)

    def request(self, sig, context: dict) -> bool:
        """Queue a background review unless one is cached, running, or unaffordable."""
        key = setup_key(sig)
        ok, _ = self.available()
        if not ok or self.verdict_for(sig) is not None:
            return False
        with self._lock:
            if key in self._pending or self._budget_left() <= 0:
                return False
            self._pending.add(key)
        self._pool.submit(self._run, sig, context)
        return True

    def review_now(self, sig, context: dict) -> dict:
        """Synchronous review (on-demand research, tests)."""
        ok, reason = self.available()
        if not ok:
            return {"state": "unavailable", "reason": reason}
        if self.verdict_for(sig) is None:
            with self._lock:
                if self._budget_left() <= 0:
                    return {"state": "budget", "reason": "daily AI review limit reached"}
                self._pending.add(setup_key(sig))
            self._run(sig, context)
        return self.status_for(sig)

    # --------------------------------------------------------------- work
    def _run(self, sig, context: dict):
        key = setup_key(sig)
        started = time.time()
        try:
            verdict, usage = self._ask(sig, context)
        except Exception as exc:                     # fail closed
            log.warning("AI review failed for %s: %s", key, exc)
            verdict, usage = {"decision": "skip", "confidence": 0, "error": str(exc)[:300],
                              "summary": "AI review failed, so the trade is skipped."}, None
        verdict["model"] = self.cfg.model
        verdict["reviewed_at"] = _now().isoformat()
        verdict["seconds"] = round(time.time() - started, 1)
        verdict["cost_usd"] = round(self._cost(usage), 4) if usage else 0.0
        verdict["_ts"] = time.time()
        billed = usage is not None
        with self._lock:
            self._roll_day()
            if billed:
                self._reviews_today += 1
                self._spend_today += verdict["cost_usd"]
            self._cache[key] = verdict
            self._pending.discard(key)
        self._record(key, sig, verdict, billed)
        log.info("AI review %s → %s (%s%%) in %.0fs, ~$%.3f",
                 key, verdict.get("decision"), verdict.get("confidence"),
                 verdict["seconds"], verdict["cost_usd"])

    def _record(self, key: str, sig, verdict: dict, billed: bool):
        row = {"key": key, "symbol": sig.symbol, "mode": sig.mode, "direction": sig.direction,
               "entry": sig.entry, "stop": sig.stop, "targets": list(sig.targets),
               "billed": billed, **{k: v for k, v in verdict.items() if not k.startswith("_")}}
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")

    def _cost(self, usage) -> float:
        price_in, price_out = PRICES.get(self.cfg.model, PRICES["claude-opus-5-5"])
        tokens_in = (getattr(usage, "input_tokens", 0) or 0) \
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        cached = getattr(usage, "cache_read_input_tokens", 0) or 0
        tokens_out = getattr(usage, "output_tokens", 0) or 0
        stu = getattr(usage, "server_tool_use", None)
        searches = (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
        return (tokens_in * price_in + cached * price_in * 0.1 + tokens_out * price_out) / 1e6 \
            + searches * WEB_SEARCH_USD

    def _client_or_new(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=os.environ[self.cfg.api_key_env])
        return self._client

    def _ask(self, sig, context: dict) -> tuple[dict, object]:
        client = self._client_or_new()
        payload = {k: getattr(sig, k, None) for k in CONTEXT_FIELDS}
        payload["votes"] = (payload.get("votes") or [])[:13]
        user = (f"Today is {_now():%Y-%m-%d %H:%M} UTC.\n\nProposed trade and desk context "
                f"(JSON):\n{json.dumps({'signal': payload, **context}, default=str)}\n\n"
                "Research it, then give your verdict.")
        tools = ([{"type": "web_search_20260209", "name": "web_search",
                   "max_uses": self.cfg.max_searches}] if self.cfg.web_search else [])
        messages = [{"role": "user", "content": user}]
        usage_total = None
        for _ in range(MAX_CONTINUATIONS + 1):
            response = client.beta.messages.create(
                model=self.cfg.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",                   # a declined review retries on another model
                thinking={"type": "adaptive"},
                output_config={"effort": self.cfg.effort,
                               "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )
            usage_total = _add_usage(usage_total, response.usage)
            if response.stop_reason == "pause_turn":   # long search loop: resume as-is
                messages = [messages[0], {"role": "assistant", "content": response.content}]
                continue
            break
        if response.stop_reason == "refusal":
            return {"decision": "skip", "confidence": 0,
                    "summary": "The AI declined to review this trade, so it is skipped.",
                    "reasons_for": [], "reasons_against": [], "key_news": [],
                    "watch_for": ""}, usage_total
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        verdict = _parse_verdict(text)
        if verdict is None:
            raise ValueError(f"unreadable verdict (stop_reason={response.stop_reason})")
        verdict["confidence"] = max(0, min(100, int(verdict.get("confidence") or 0)))
        return verdict, usage_total


class _Usage:
    """Running total of usage across pause_turn continuations."""
    def __init__(self):
        self.input_tokens = self.output_tokens = 0
        self.cache_creation_input_tokens = self.cache_read_input_tokens = 0
        self.server_tool_use = type("STU", (), {"web_search_requests": 0})()


def _add_usage(total, usage):
    total = total or _Usage()
    for f in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
              "cache_read_input_tokens"):
        setattr(total, f, getattr(total, f) + (getattr(usage, f, 0) or 0))
    stu = getattr(usage, "server_tool_use", None)
    total.server_tool_use.web_search_requests += (getattr(stu, "web_search_requests", 0) or 0) \
        if stu else 0
    return total
