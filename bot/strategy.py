"""Signal engine: multi-timeframe confluence scoring for scalp and swing modes.

The model is deliberately explainable. Every indicator casts a vote in
[-1, +1] with a weight; votes are combined into a score in [-100, +100].
The weights change with the detected regime (trending vs ranging), because a
mean-reversion read of RSI is right in a range and wrong in a trend.

Nothing here is a prediction. A high score means "several independent
measures currently agree", which is not the same as "this trade will win".
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import indicators as ta
from .config import ModeCfg, RiskCfg

log = logging.getLogger("bot.strategy")


# --------------------------------------------------------------------------
@dataclass
class Vote:
    name: str
    score: float          # -1 .. +1
    weight: float
    note: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "score": round(self.score, 3),
            "weight": round(self.weight, 2),
            "contribution": round(self.score * self.weight, 3),
            "note": self.note,
        }


@dataclass
class Signal:
    symbol: str
    base: str
    bucket: str
    mode: str
    direction: str                 # "long" | "short" | "flat"
    action: str                    # "enter" | "watch" | "none"
    score: float
    confidence: str
    regime: str
    price: float
    entry: float
    stop: float
    targets: list[float]
    rr: float
    atr: float
    atr_pct: float
    risk_pct_move: float
    position_size_units: float
    position_notional: float
    leverage_suggestion: float
    hold_estimate: str
    max_hold_bars: int
    invalidation: str
    votes: list[dict] = field(default_factory=list)
    confirm: dict = field(default_factory=dict)
    bias: dict = field(default_factory=dict)
    news: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    levels: dict = field(default_factory=dict)
    spark: list[float] = field(default_factory=list)
    timeframes: dict = field(default_factory=dict)
    generated_at: str = ""
    change_24h: float = 0.0
    quote_volume: float = 0.0
    success: dict = field(default_factory=dict)   # estimated chance to close in profit
    name: str = ""
    asset_class: str = "crypto"
    scalp_fit: dict = field(default_factory=dict)  # scalp mode only, see scalp_fit()
    ai: dict = field(default_factory=dict)         # AI review state/verdict, see ai_review.py

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# regime detection
# --------------------------------------------------------------------------
def detect_regime(df: pd.DataFrame) -> tuple[str, dict]:
    last = df.iloc[-1]
    adx_val = float(last.get("adx", np.nan))
    width = df["bb_width"].dropna()
    width_pct = float(
        (width.rank(pct=True).iloc[-1] * 100.0) if len(width) > 30 else 50.0
    )
    squeeze = bool(last.get("squeeze", False))

    if np.isnan(adx_val):
        regime = "unknown"
    elif adx_val >= 25 and width_pct >= 40:
        regime = "trending"
    elif squeeze or width_pct < 25:
        regime = "compression"
    elif adx_val < 18:
        regime = "ranging"
    else:
        regime = "mixed"

    return regime, {
        "adx": None if np.isnan(adx_val) else round(adx_val, 1),
        "bb_width_pct": round(width_pct, 1),
        "squeeze": squeeze,
    }


REGIME_WEIGHTS = {
    #                 trend-following   mean-reversion   volume   structure
    "trending":     {"trend": 1.25, "revert": 0.45, "volume": 1.0, "structure": 1.15},
    "mixed":        {"trend": 1.0,  "revert": 0.85, "volume": 1.0, "structure": 1.0},
    "ranging":      {"trend": 0.6,  "revert": 1.35, "volume": 0.9, "structure": 0.85},
    "compression":  {"trend": 0.8,  "revert": 0.7,  "volume": 1.25, "structure": 1.0},
    "unknown":      {"trend": 1.0,  "revert": 1.0,  "volume": 1.0, "structure": 1.0},
}


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _safe(value, default=np.nan) -> float:
    try:
        f = float(value)
        return default if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# entry-timeframe votes
# --------------------------------------------------------------------------
def entry_votes(df: pd.DataFrame, regime: str) -> list[Vote]:
    w = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["unknown"])
    last, prev = df.iloc[-1], df.iloc[-2]
    close = _safe(last["close"])
    votes: list[Vote] = []

    # --- EMA stack -------------------------------------------------------
    e9, e21, e50 = _safe(last["ema9"]), _safe(last["ema21"]), _safe(last["ema50"])
    if not any(math.isnan(x) for x in (e9, e21, e50)):
        if e9 > e21 > e50:
            s, note = 1.0, "EMA 9>21>50 — stacked bullish"
        elif e9 < e21 < e50:
            s, note = -1.0, "EMA 9<21<50 — stacked bearish"
        elif e9 > e21:
            s, note = 0.4, "short EMAs turning up, 50 not aligned"
        elif e9 < e21:
            s, note = -0.4, "short EMAs turning down, 50 not aligned"
        else:
            s, note = 0.0, "EMAs flat"
        votes.append(Vote("EMA stack", s, 1.6 * w["trend"], note))

    # --- price vs EMA200 -------------------------------------------------
    e200 = _safe(last["ema200"])
    if not math.isnan(e200) and e200 > 0:
        dist = (close - e200) / e200 * 100.0
        s = _clamp(dist / 4.0)
        votes.append(Vote(
            "Price vs EMA200", s, 1.0 * w["trend"],
            f"{dist:+.2f}% {'above' if dist >= 0 else 'below'} the 200 EMA",
        ))

    # --- MACD ------------------------------------------------------------
    hist, hist_prev = _safe(last["macd_hist"]), _safe(prev["macd_hist"])
    macd_v, macd_sig = _safe(last["macd"]), _safe(last["macd_signal"])
    if not math.isnan(hist) and close > 0:
        norm = _clamp(hist / (close * 0.004))
        rising = hist > hist_prev
        s = _clamp(norm * (1.15 if (norm > 0) == rising else 0.6))
        cross = ""
        if not math.isnan(hist_prev):
            if hist > 0 >= hist_prev:
                cross, s = " (fresh bull cross)", _clamp(s + 0.35)
            elif hist < 0 <= hist_prev:
                cross, s = " (fresh bear cross)", _clamp(s - 0.35)
        votes.append(Vote(
            "MACD", s, 1.3 * w["trend"],
            f"histogram {hist:+.6g}, {'expanding' if rising else 'contracting'}"
            f"{cross}; line {'above' if macd_v > macd_sig else 'below'} signal",
        ))

    # --- RSI: regime-dependent read --------------------------------------
    rsi_v = _safe(last["rsi"])
    if not math.isnan(rsi_v):
        if regime in ("ranging", "compression"):
            # fade the extremes
            s = _clamp((50.0 - rsi_v) / 25.0)
            note = f"RSI {rsi_v:.1f} — mean-reversion read in a {regime} market"
            weight = 1.4 * w["revert"]
        else:
            # in a trend, strength is confirmation, but cap the euphoria
            s = _clamp((rsi_v - 50.0) / 22.0)
            if rsi_v > 78:
                s *= 0.55
                note = f"RSI {rsi_v:.1f} — strong but overbought, chase risk"
            elif rsi_v < 22:
                s *= 0.55
                note = f"RSI {rsi_v:.1f} — weak but oversold, bounce risk"
            else:
                note = f"RSI {rsi_v:.1f} — momentum confirms trend"
            weight = 1.2 * w["trend"]
        votes.append(Vote("RSI", s, weight, note))

    # --- Stochastic RSI --------------------------------------------------
    k, d = _safe(last["stoch_k"]), _safe(last["stoch_d"])
    kp, dp = _safe(prev["stoch_k"]), _safe(prev["stoch_d"])
    if not any(math.isnan(x) for x in (k, d, kp, dp)):
        s, note = 0.0, f"StochRSI K {k:.0f} / D {d:.0f}"
        if k > d and kp <= dp and k < 45:
            s, note = 0.85, f"StochRSI bull cross from oversold (K {k:.0f})"
        elif k < d and kp >= dp and k > 55:
            s, note = -0.85, f"StochRSI bear cross from overbought (K {k:.0f})"
        elif k > 85:
            s, note = -0.35, f"StochRSI pinned overbought ({k:.0f})"
        elif k < 15:
            s, note = 0.35, f"StochRSI pinned oversold ({k:.0f})"
        votes.append(Vote("StochRSI", s, 1.0 * w["revert"], note))

    # --- Supertrend ------------------------------------------------------
    st_dir, st_line = _safe(last["st_dir"]), _safe(last["supertrend"])
    if not math.isnan(st_dir):
        flipped = _safe(prev["st_dir"]) != st_dir
        s = 1.0 if st_dir > 0 else -1.0
        note = f"supertrend {'long' if st_dir > 0 else 'short'} @ {st_line:.6g}"
        if flipped:
            note += " (just flipped)"
        votes.append(Vote("Supertrend", s, 1.3 * w["trend"], note))

    # --- Bollinger %B ----------------------------------------------------
    pct_b = _safe(last["bb_pct_b"])
    if not math.isnan(pct_b):
        if regime in ("ranging", "compression"):
            s = _clamp((0.5 - pct_b) * 2.4)
            note = f"%B {pct_b:.2f} — fading band edge"
            weight = 1.2 * w["revert"]
        else:
            s = _clamp((pct_b - 0.5) * 1.7)
            if pct_b > 1.0:
                note = f"%B {pct_b:.2f} — riding above the upper band"
            elif pct_b < 0.0:
                note = f"%B {pct_b:.2f} — riding below the lower band"
            else:
                note = f"%B {pct_b:.2f} — inside the bands"
            weight = 0.9 * w["trend"]
        votes.append(Vote("Bollinger %B", s, weight, note))

    # --- VWAP ------------------------------------------------------------
    vwap = _safe(last["vwap"])
    if not math.isnan(vwap) and vwap > 0:
        dist = (close - vwap) / vwap * 100.0
        s = _clamp(dist / 1.5)
        votes.append(Vote(
            "VWAP", s, 1.1 * w["trend"],
            f"{dist:+.2f}% vs rolling VWAP — "
            f"{'buyers in control' if dist >= 0 else 'sellers in control'}",
        ))

    # --- volume confirmation ---------------------------------------------
    vz = _safe(last["vol_z"])
    if not math.isnan(vz):
        body = close - _safe(last["open"])
        direction = 1.0 if body > 0 else (-1.0 if body < 0 else 0.0)
        magnitude = _clamp(vz / 2.5, 0.0, 1.0)
        s = direction * magnitude
        if vz > 1.5:
            note = f"volume {vz:+.1f}σ — {'buying' if direction > 0 else 'selling'} conviction"
        elif vz < -0.8:
            note = f"volume {vz:+.1f}σ — thin participation, move is unconvincing"
            s *= 0.4
        else:
            note = f"volume {vz:+.1f}σ — average participation"
        votes.append(Vote("Volume", s, 1.1 * w["volume"], note))

    # --- OBV slope -------------------------------------------------------
    obv_v, obv_e = _safe(last["obv"]), _safe(last["obv_ema"])
    if not math.isnan(obv_v) and not math.isnan(obv_e):
        recent = df["obv"].iloc[-20:]
        span = float(recent.max() - recent.min())
        s = _clamp((obv_v - obv_e) / span * 2.0) if span > 0 else 0.0
        votes.append(Vote(
            "OBV", s, 0.9 * w["volume"],
            f"OBV {'above' if obv_v >= obv_e else 'below'} its 21 EMA — "
            f"{'accumulation' if s >= 0 else 'distribution'}",
        ))

    # --- Money Flow Index ------------------------------------------------
    mfi = _safe(last["mfi"])
    if not math.isnan(mfi):
        if regime in ("ranging", "compression"):
            s = _clamp((50.0 - mfi) / 28.0)
        else:
            s = _clamp((mfi - 50.0) / 25.0)
        votes.append(Vote("MFI", s, 0.8 * w["volume"], f"MFI {mfi:.1f}"))

    # --- market structure ------------------------------------------------
    struct = ta.structure_bias(df)
    votes.append(Vote(
        "Market structure", struct["bias"], 1.4 * w["structure"], struct["note"],
    ))

    # --- RSI divergence --------------------------------------------------
    div_score, div_note = _rsi_divergence(df)
    if div_score != 0.0:
        votes.append(Vote("RSI divergence", div_score, 1.2 * w["revert"], div_note))

    # --- candle momentum -------------------------------------------------
    rng = _safe(last["high"]) - _safe(last["low"])
    if rng > 0:
        pos = (close - _safe(last["low"])) / rng
        s = _clamp((pos - 0.5) * 1.6)
        votes.append(Vote(
            "Candle close", s, 0.6,
            f"closing in the {'upper' if pos >= 0.5 else 'lower'} "
            f"{abs(pos - 0.5) * 200:.0f}% of the bar range",
        ))

    return votes


def _rsi_divergence(df: pd.DataFrame, lookback: int = 60) -> tuple[float, str]:
    """Compare the last two confirmed swings in price against RSI."""
    sub = df.iloc[-lookback:]
    if len(sub) < 25 or "rsi" not in sub:
        return 0.0, ""
    sp = ta.swing_points(sub, 3, 3)

    lows = sub.index[sp["swing_low"]]
    if len(lows) >= 2:
        a, b = lows[-2], lows[-1]
        pa, pb = sub.loc[a, "low"], sub.loc[b, "low"]
        ra, rb = sub.loc[a, "rsi"], sub.loc[b, "rsi"]
        if not (pd.isna(ra) or pd.isna(rb)) and pb < pa and rb > ra + 1.5:
            return 0.9, f"bullish divergence — lower low in price, higher low in RSI ({ra:.0f}→{rb:.0f})"

    highs = sub.index[sp["swing_high"]]
    if len(highs) >= 2:
        a, b = highs[-2], highs[-1]
        pa, pb = sub.loc[a, "high"], sub.loc[b, "high"]
        ra, rb = sub.loc[a, "rsi"], sub.loc[b, "rsi"]
        if not (pd.isna(ra) or pd.isna(rb)) and pb > pa and rb < ra - 1.5:
            return -0.9, f"bearish divergence — higher high in price, lower high in RSI ({ra:.0f}→{rb:.0f})"

    return 0.0, ""


# --------------------------------------------------------------------------
# confirmation and bias timeframes (lighter reads)
# --------------------------------------------------------------------------
def timeframe_read(df: pd.DataFrame, label: str) -> dict:
    """Compact directional read of a higher timeframe."""
    last = df.iloc[-1]
    close = _safe(last["close"])
    parts, score, total_w = [], 0.0, 0.0

    def add(value: float, weight: float, note: str):
        nonlocal score, total_w
        score += value * weight
        total_w += weight
        parts.append(note)

    e21, e50, e200 = _safe(last["ema21"]), _safe(last["ema50"]), _safe(last["ema200"])
    if not math.isnan(e21) and not math.isnan(e50):
        v = 1.0 if e21 > e50 else -1.0
        add(v, 1.4, f"EMA21 {'>' if v > 0 else '<'} EMA50")
    if not math.isnan(e200) and e200 > 0:
        v = _clamp((close - e200) / e200 / 0.05)
        add(v, 1.2, f"price {(close - e200) / e200 * 100:+.1f}% vs EMA200")

    st = _safe(last["st_dir"])
    if not math.isnan(st):
        add(1.0 if st > 0 else -1.0, 1.3, f"supertrend {'up' if st > 0 else 'down'}")

    hist = _safe(last["macd_hist"])
    if not math.isnan(hist) and close > 0:
        add(_clamp(hist / (close * 0.006)), 1.0,
            f"MACD hist {'positive' if hist > 0 else 'negative'}")

    rsi_v = _safe(last["rsi"])
    if not math.isnan(rsi_v):
        add(_clamp((rsi_v - 50.0) / 25.0), 0.9, f"RSI {rsi_v:.0f}")

    struct = ta.structure_bias(df)
    add(struct["bias"], 1.2, struct["note"])

    norm = (score / total_w) if total_w else 0.0
    if norm > 0.45:
        label_txt = "bullish"
    elif norm > 0.15:
        label_txt = "mildly bullish"
    elif norm < -0.45:
        label_txt = "bearish"
    elif norm < -0.15:
        label_txt = "mildly bearish"
    else:
        label_txt = "neutral"

    return {
        "timeframe": label,
        "score": round(norm * 100, 1),
        "label": label_txt,
        "notes": parts,
        "rsi": None if math.isnan(rsi_v) else round(rsi_v, 1),
        "adx": None if math.isnan(_safe(last.get("adx"))) else round(_safe(last["adx"]), 1),
    }


# --------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------
def build_signal(
    symbol: str,
    base: str,
    bucket: str,
    mode: str,
    mode_cfg: ModeCfg,
    risk_cfg: RiskCfg,
    frames: dict[str, pd.DataFrame],
    coin_news: dict | None = None,
    news_weight: float = 0.12,
    block_on_critical: bool = True,
    change_24h: float = 0.0,
    quote_volume: float = 0.0,
) -> Signal:
    entry_df = frames["entry"]
    confirm_df = frames.get("confirm")
    bias_df = frames.get("bias")

    regime, regime_meta = detect_regime(entry_df)
    votes = entry_votes(entry_df, regime)

    total_w = sum(v.weight for v in votes) or 1.0
    raw = sum(v.score * v.weight for v in votes) / total_w      # -1 .. +1
    score = raw * 100.0
    warnings: list[str] = []

    # ---- confirmation timeframe -----------------------------------------
    confirm = timeframe_read(confirm_df, mode_cfg.confirm_tf) if confirm_df is not None else {}
    if confirm:
        c = confirm["score"] / 100.0
        if (c > 0) == (raw > 0) and abs(c) > 0.15:
            score *= 1.12                                   # agreement bonus
        elif (c > 0) != (raw > 0) and abs(c) > 0.30:
            score *= 0.72                                   # disagreement haircut
            warnings.append(
                f"{mode_cfg.confirm_tf} disagrees with the entry timeframe"
            )

    # ---- bias timeframe acts as a gate ----------------------------------
    bias = timeframe_read(bias_df, mode_cfg.bias_tf) if bias_df is not None else {}
    if bias:
        b = bias["score"] / 100.0
        if (b > 0) != (raw > 0) and abs(b) > 0.45:
            score *= 0.55
            warnings.append(
                f"counter-trend to the {mode_cfg.bias_tf} bias ({bias['label']}) "
                f"— size down or skip"
            )
        elif (b > 0) == (raw > 0) and abs(b) > 0.45:
            score *= 1.08

    # ---- regime / volatility filters ------------------------------------
    last = entry_df.iloc[-1]
    atr_val = _safe(last["atr"])
    price = _safe(last["close"])
    atr_pct = _safe(last["atr_pct"])
    adx_val = _safe(last.get("adx"))

    if not math.isnan(adx_val) and adx_val < mode_cfg.min_adx:
        score *= 0.75
        warnings.append(f"ADX {adx_val:.0f} below {mode_cfg.min_adx:.0f} — choppy, low follow-through")
    if not math.isnan(atr_pct):
        if atr_pct < mode_cfg.min_atr_pct:
            score *= 0.6
            warnings.append(f"ATR only {atr_pct:.2f}% of price — too quiet to pay fees")
        elif atr_pct > mode_cfg.max_atr_pct:
            score *= 0.7
            warnings.append(f"ATR {atr_pct:.2f}% of price — violent, widen stops or skip")
    if regime == "compression":
        warnings.append("volatility compression — breakout pending, direction unproven")

    # ---- news overlay ----------------------------------------------------
    news_info = coin_news or {}
    sentiment = float(news_info.get("sentiment", 0.0) or 0.0)
    if sentiment:
        score += sentiment * news_weight * 100.0
        warnings.append(
            f"news sentiment {sentiment:+.2f} applied "
            f"({news_info.get('item_count', 0)} headlines)"
        ) if abs(sentiment) > 0.3 else None
    critical_news = bool(news_info.get("critical"))
    news_blocked = False
    if critical_news and block_on_critical:
        news_blocked = True
        warnings.append("BLOCKED: critical headline (hack/exploit/delisting/insolvency) — no entry")

    score = max(-100.0, min(100.0, score))

    # ---- direction and action -------------------------------------------
    direction = "long" if score > 0 else ("short" if score < 0 else "flat")
    magnitude = abs(score)
    if news_blocked:
        action = "none"
    elif magnitude >= mode_cfg.min_score:
        action = "enter"
    elif magnitude >= mode_cfg.watch_score:
        action = "watch"
    else:
        action = "none"
        direction = "flat" if magnitude < mode_cfg.watch_score * 0.5 else direction

    if magnitude >= 75:
        confidence = "high"
    elif magnitude >= 55:
        confidence = "moderate"
    elif magnitude >= 40:
        confidence = "low"
    else:
        confidence = "none"

    # ---- levels: stop, targets, sizing -----------------------------------
    levels = ta.support_resistance(entry_df)
    if math.isnan(atr_val) or atr_val <= 0:
        atr_val = price * 0.005
    entry_price = price
    stop_dist = atr_val * mode_cfg.atr_stop_mult

    if direction == "long":
        stop = entry_price - stop_dist
        # tuck the stop under the nearest structural support when it's close by
        sup = [s for s in levels["support"] if s < entry_price]
        if sup:
            struct_stop = sup[0] - atr_val * 0.25
            if entry_price - struct_stop < stop_dist * 2.0:
                stop = min(stop, struct_stop)
    elif direction == "short":
        stop = entry_price + stop_dist
        res = [r for r in levels["resistance"] if r > entry_price]
        if res:
            struct_stop = res[0] + atr_val * 0.25
            if struct_stop - entry_price < stop_dist * 2.0:
                stop = max(stop, struct_stop)
    else:
        stop = entry_price - stop_dist

    risk_per_unit = abs(entry_price - stop)
    if risk_per_unit <= 0:
        risk_per_unit = max(atr_val, entry_price * 0.002)

    sign = 1.0 if direction == "long" else -1.0
    targets = [entry_price + sign * risk_per_unit * r for r in mode_cfg.targets_r]

    # snap the first target toward real structure if it sits closer
    if direction == "long" and levels["resistance"]:
        nearest = levels["resistance"][0]
        if entry_price < nearest < targets[0]:
            targets[0] = nearest
    elif direction == "short" and levels["support"]:
        nearest = levels["support"][0]
        if targets[0] < nearest < entry_price:
            targets[0] = nearest

    rr = abs(targets[-1] - entry_price) / risk_per_unit if risk_per_unit else 0.0
    rr_first = abs(targets[0] - entry_price) / risk_per_unit if risk_per_unit else 0.0
    if action == "enter" and rr_first < 0.8:
        action = "watch"
        warnings.append(f"first target only {rr_first:.2f}R away — not worth the risk yet")

    risk_amount = risk_cfg.account_equity * (risk_cfg.risk_per_trade_pct / 100.0)
    size_units = risk_amount / risk_per_unit if risk_per_unit else 0.0
    notional = size_units * entry_price
    if notional > risk_cfg.max_notional_per_trade:
        scale = risk_cfg.max_notional_per_trade / notional
        size_units *= scale
        notional = risk_cfg.max_notional_per_trade
    leverage = min(
        risk_cfg.max_leverage,
        max(1.0, notional / max(risk_cfg.account_equity, 1e-9)),
    )

    bars = mode_cfg.max_hold_bars
    hold = f"~{bars} × {mode_cfg.entry_tf} bars max"
    invalidation = (
        f"close beyond {stop:.6g} on the {mode_cfg.entry_tf}, "
        f"or a {mode_cfg.confirm_tf} flip against the position"
    )

    spark = entry_df["close"].iloc[-60:].tolist()

    return Signal(
        symbol=symbol,
        base=base,
        bucket=bucket,
        mode=mode,
        direction=direction,
        action=action,
        score=round(score, 1),
        confidence=confidence,
        regime=regime,
        price=price,
        entry=entry_price,
        stop=stop,
        targets=targets,
        rr=round(rr, 2),
        atr=atr_val,
        atr_pct=0.0 if math.isnan(atr_pct) else round(atr_pct, 3),
        risk_pct_move=round(risk_per_unit / entry_price * 100.0, 3) if entry_price else 0.0,
        position_size_units=size_units,
        position_notional=notional,
        leverage_suggestion=round(leverage, 2),
        hold_estimate=hold,
        max_hold_bars=mode_cfg.max_hold_bars,
        invalidation=invalidation,
        votes=[v.to_dict() for v in sorted(votes, key=lambda v: -abs(v.score * v.weight))],
        confirm=confirm,
        bias=bias,
        news=news_info,
        warnings=[w for w in warnings if w],
        levels={
            "support": levels["support"],
            "resistance": levels["resistance"],
            **regime_meta,
        },
        spark=spark,
        timeframes={
            "entry": mode_cfg.entry_tf,
            "confirm": mode_cfg.confirm_tf,
            "bias": mode_cfg.bias_tf,
        },
        generated_at=datetime.now(timezone.utc).isoformat(),
        change_24h=change_24h,
        quote_volume=quote_volume,
    )


# --------------------------------------------------------------------------
# scalp suitability
# --------------------------------------------------------------------------
ROUND_TRIP_COST_PCT = 2 * (0.05 + 0.02)   # taker fee + slippage, both sides (see paper.py)


def _ramp(x: float, lo: float, hi: float) -> float:
    """0 at lo, 1 at hi, linear between (lo > hi flips the direction)."""
    if hi == lo:
        return 1.0 if x >= hi else 0.0
    return _clamp((x - lo) / (hi - lo), 0.0, 1.0)


def scalp_fit(sig: Signal, spread_pct: float) -> dict:
    """How well a pair suits small, fast trades right now, 0–100.

    A strong signal is not enough for scalping: the pair also has to be
    liquid, tight, moving enough to beat fees, and not so wild that stops
    get run. Each factor scores 0–1 and they are weighted equally.
    """
    qv = sig.quote_volume or 0.0
    cost = ROUND_TRIP_COST_PCT + max(spread_pct, 0.0)
    stop_move = sig.risk_pct_move or 0.0
    atr = sig.atr_pct or 0.0

    liquidity = _ramp(math.log10(max(qv, 1.0)), math.log10(3e6), math.log10(5e7))
    spread = _ramp(spread_pct, 0.15, 0.03)
    if atr < 0.15:
        volatility = _ramp(atr, 0.10, 0.15)
    else:
        volatility = _ramp(atr, 3.0, 1.0)
    fees = _ramp(stop_move / cost if cost else 0.0, 1.5, 5.0)
    strength = _clamp(abs(sig.score) / 80.0, 0.0, 1.0) if sig.action != "none" else 0.0

    factors = {"liquidity": liquidity, "spread": spread, "volatility": volatility,
               "fees": fees, "signal": strength}
    score = round(sum(factors.values()) / len(factors) * 100.0, 1)
    verdict = "good" if score >= 70 else ("fair" if score >= 45 else "poor")

    reasons = []
    depth = "deep" if liquidity >= 0.7 else ("moderate" if liquidity >= 0.35 else "thin")
    reasons.append(f"{depth} liquidity (${qv / 1e6:,.1f}M 24h)")
    reasons.append(f"spread {spread_pct:.3f}% — {'tight' if spread >= 0.7 else 'wide'}")
    if atr < 0.15:
        reasons.append(f"moves {atr:.2f}% per bar — too quiet to beat fees")
    elif atr > 1.0:
        reasons.append(f"moves {atr:.2f}% per bar — wild, stops get hit")
    else:
        reasons.append(f"moves {atr:.2f}% per bar — workable for scalps")
    reasons.append(f"fees + spread ≈ {cost:.2f}% vs a {stop_move:.2f}% stop"
                   + (" — fees eat most of the edge" if fees < 0.4 else ""))
    if sig.action == "enter":
        reasons.append(f"signal firing {sig.direction} ({sig.score:+.0f})")
    elif sig.action == "watch":
        reasons.append(f"on watch {sig.direction} ({sig.score:+.0f}), not firing")
    else:
        reasons.append("no signal right now")

    return {"score": score, "verdict": verdict,
            "factors": {k: round(v, 2) for k, v in factors.items()},
            "reasons": reasons}
