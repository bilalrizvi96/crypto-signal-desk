"""
Technical indicators — pure pandas/numpy, no TA-Lib dependency.

Every function takes/returns pandas Series or DataFrames indexed by timestamp.
Wilder's smoothing is used where the classical definition calls for it (RSI,
ATR, ADX) so values match TradingView / standard charting packages.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------
# moving averages
# --------------------------------------------------------------------------
def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=length).mean()


def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's smoothing (a.k.a. RMA / SMMA). alpha = 1/length."""
    return series.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


# --------------------------------------------------------------------------
# momentum
# --------------------------------------------------------------------------
def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # when avg_loss == 0 the market only went up -> RSI 100
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(avg_gain != 0.0, out.where(avg_loss == 0.0, 0.0))
    return out


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame(
        {
            "macd": macd_line,
            "signal": signal_line,
            "hist": macd_line - signal_line,
        }
    )


def stoch_rsi(
    close: pd.Series, length: int = 14, k: int = 3, d: int = 3
) -> pd.DataFrame:
    r = rsi(close, length)
    lo = r.rolling(length, min_periods=length).min()
    hi = r.rolling(length, min_periods=length).max()
    raw = (r - lo) / (hi - lo).replace(0.0, np.nan)
    k_line = (raw * 100.0).rolling(k, min_periods=k).mean()
    d_line = k_line.rolling(d, min_periods=d).mean()
    return pd.DataFrame({"k": k_line, "d": d_line})


def roc(close: pd.Series, length: int = 10) -> pd.Series:
    """Rate of change, in percent."""
    return (close / close.shift(length) - 1.0) * 100.0


# --------------------------------------------------------------------------
# volatility
# --------------------------------------------------------------------------
def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    return rma(true_range(df), length)


def bollinger(close: pd.Series, length: int = 20, mult: float = 2.0) -> pd.DataFrame:
    mid = sma(close, length)
    sd = close.rolling(length, min_periods=length).std(ddof=0)
    upper = mid + mult * sd
    lower = mid - mult * sd
    width = (upper - lower) / mid.replace(0.0, np.nan)
    # %B: where price sits inside the bands (0 = lower band, 1 = upper band)
    pct_b = (close - lower) / (upper - lower).replace(0.0, np.nan)
    return pd.DataFrame(
        {"mid": mid, "upper": upper, "lower": lower, "width": width, "pct_b": pct_b}
    )


def keltner(df: pd.DataFrame, length: int = 20, mult: float = 1.5) -> pd.DataFrame:
    mid = ema(df["close"], length)
    rng = atr(df, length)
    return pd.DataFrame(
        {"mid": mid, "upper": mid + mult * rng, "lower": mid - mult * rng}
    )


def squeeze_on(df: pd.DataFrame, length: int = 20) -> pd.Series:
    """TTM-style squeeze: Bollinger bands inside Keltner channels."""
    bb = bollinger(df["close"], length, 2.0)
    kc = keltner(df, length, 1.5)
    return (bb["upper"] < kc["upper"]) & (bb["lower"] > kc["lower"])


# --------------------------------------------------------------------------
# trend
# --------------------------------------------------------------------------
def adx(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)

    tr = rma(true_range(df), length)
    plus_di = 100.0 * rma(plus_dm, length) / tr.replace(0.0, np.nan)
    minus_di = 100.0 * rma(minus_dm, length) / tr.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return pd.DataFrame({"adx": rma(dx, length), "plus_di": plus_di, "minus_di": minus_di})


def supertrend(df: pd.DataFrame, length: int = 10, mult: float = 3.0) -> pd.DataFrame:
    """Returns the supertrend line and direction (+1 uptrend, -1 downtrend)."""
    hl2 = (df["high"] + df["low"]) / 2.0
    rng = atr(df, length)
    upper_basic = hl2 + mult * rng
    lower_basic = hl2 - mult * rng

    close = df["close"].to_numpy(dtype=float)
    ub = upper_basic.to_numpy(dtype=float)
    lb = lower_basic.to_numpy(dtype=float)
    n = len(df)

    final_ub = np.full(n, np.nan)
    final_lb = np.full(n, np.nan)
    direction = np.full(n, np.nan)
    line = np.full(n, np.nan)

    started = False
    for i in range(n):
        if np.isnan(ub[i]) or np.isnan(lb[i]):
            continue
        if not started:
            final_ub[i], final_lb[i] = ub[i], lb[i]
            direction[i] = 1.0 if close[i] >= lb[i] else -1.0
            line[i] = final_lb[i] if direction[i] > 0 else final_ub[i]
            started = True
            continue

        prev_ub = final_ub[i - 1]
        prev_lb = final_lb[i - 1]
        final_ub[i] = ub[i] if (ub[i] < prev_ub or close[i - 1] > prev_ub) else prev_ub
        final_lb[i] = lb[i] if (lb[i] > prev_lb or close[i - 1] < prev_lb) else prev_lb

        prev_dir = direction[i - 1]
        if prev_dir > 0:
            direction[i] = -1.0 if close[i] < final_lb[i] else 1.0
        else:
            direction[i] = 1.0 if close[i] > final_ub[i] else -1.0
        line[i] = final_lb[i] if direction[i] > 0 else final_ub[i]

    return pd.DataFrame(
        {"line": line, "direction": direction}, index=df.index
    )


# --------------------------------------------------------------------------
# volume
# --------------------------------------------------------------------------
def obv(df: pd.DataFrame) -> pd.Series:
    sign = np.sign(df["close"].diff().fillna(0.0))
    return (sign * df["volume"]).cumsum()


def vwap_rolling(df: pd.DataFrame, length: int = 96) -> pd.Series:
    """Rolling VWAP — a session-independent stand-in for anchored VWAP."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = (tp * df["volume"]).rolling(length, min_periods=max(2, length // 4)).sum()
    vol = df["volume"].rolling(length, min_periods=max(2, length // 4)).sum()
    return pv / vol.replace(0.0, np.nan)


def volume_zscore(df: pd.DataFrame, length: int = 50) -> pd.Series:
    v = df["volume"]
    mean = v.rolling(length, min_periods=length // 2).mean()
    sd = v.rolling(length, min_periods=length // 2).std(ddof=0)
    return (v - mean) / sd.replace(0.0, np.nan)


def money_flow_index(df: pd.DataFrame, length: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    raw = tp * df["volume"]
    delta = tp.diff()
    pos = raw.where(delta > 0, 0.0).rolling(length, min_periods=length).sum()
    neg = raw.where(delta < 0, 0.0).rolling(length, min_periods=length).sum()
    ratio = pos / neg.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + ratio))


# --------------------------------------------------------------------------
# market structure
# --------------------------------------------------------------------------
def swing_points(df: pd.DataFrame, left: int = 3, right: int = 3) -> pd.DataFrame:
    """Fractal swing highs/lows confirmed by `right` bars — no lookahead leak
    because a point only becomes valid `right` bars after it printed."""
    high, low = df["high"], df["low"]
    win = left + right + 1
    roll_max = high.rolling(win, center=False).max().shift(-right)
    roll_min = low.rolling(win, center=False).min().shift(-right)
    is_high = (high == roll_max) & high.notna()
    is_low = (low == roll_min) & low.notna()
    # invalidate the last `right` bars: not yet confirmed
    if right > 0:
        is_high.iloc[-right:] = False
        is_low.iloc[-right:] = False
    return pd.DataFrame({"swing_high": is_high, "swing_low": is_low})


def structure_bias(df: pd.DataFrame, left: int = 3, right: int = 3, lookback: int = 60) -> dict:
    """Higher-highs/higher-lows vs lower-highs/lower-lows over recent swings."""
    sp = swing_points(df, left, right)
    recent = df.iloc[-lookback:]
    rs = sp.iloc[-lookback:]
    highs = recent["high"][rs["swing_high"]].tolist()
    lows = recent["low"][rs["swing_low"]].tolist()

    bias, note = 0.0, "no clear structure"
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1] > highs[-2]
        hl = lows[-1] > lows[-2]
        lh = highs[-1] < highs[-2]
        ll = lows[-1] < lows[-2]
        if hh and hl:
            bias, note = 1.0, "higher highs + higher lows"
        elif lh and ll:
            bias, note = -1.0, "lower highs + lower lows"
        elif hh or hl:
            bias, note = 0.4, "partial bullish structure"
        elif lh or ll:
            bias, note = -0.4, "partial bearish structure"
        else:
            bias, note = 0.0, "range-bound structure"
    return {
        "bias": bias,
        "note": note,
        "last_swing_high": highs[-1] if highs else None,
        "last_swing_low": lows[-1] if lows else None,
    }


def support_resistance(df: pd.DataFrame, lookback: int = 120, left: int = 3, right: int = 3,
                       max_levels: int = 4) -> dict:
    """Nearest confirmed swing levels above and below current price."""
    sub = df.iloc[-lookback:]
    sp = swing_points(sub, left, right)
    price = float(sub["close"].iloc[-1])
    highs = sorted(sub["high"][sp["swing_high"]].tolist())
    lows = sorted(sub["low"][sp["swing_low"]].tolist())
    resistances = [h for h in highs if h > price][:max_levels]
    supports = [l for l in reversed(lows) if l < price][:max_levels]
    return {"support": supports, "resistance": resistances, "price": price}


# --------------------------------------------------------------------------
# convenience: attach the whole indicator set to an OHLCV frame
# --------------------------------------------------------------------------
def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the standard indicator set. Input needs open/high/low/close/volume."""
    out = df.copy()
    out["ema9"] = ema(out["close"], 9)
    out["ema21"] = ema(out["close"], 21)
    out["ema50"] = ema(out["close"], 50)
    out["ema200"] = ema(out["close"], 200)
    out["rsi"] = rsi(out["close"], 14)

    m = macd(out["close"])
    out["macd"], out["macd_signal"], out["macd_hist"] = m["macd"], m["signal"], m["hist"]

    out["atr"] = atr(out, 14)
    out["atr_pct"] = out["atr"] / out["close"] * 100.0

    bb = bollinger(out["close"])
    out["bb_upper"], out["bb_lower"] = bb["upper"], bb["lower"]
    out["bb_mid"], out["bb_width"], out["bb_pct_b"] = bb["mid"], bb["width"], bb["pct_b"]

    sr = stoch_rsi(out["close"])
    out["stoch_k"], out["stoch_d"] = sr["k"], sr["d"]

    a = adx(out)
    out["adx"], out["plus_di"], out["minus_di"] = a["adx"], a["plus_di"], a["minus_di"]

    st = supertrend(out)
    out["supertrend"], out["st_dir"] = st["line"], st["direction"]

    out["obv"] = obv(out)
    out["obv_ema"] = ema(out["obv"], 21)
    out["vwap"] = vwap_rolling(out)
    out["vol_z"] = volume_zscore(out)
    out["mfi"] = money_flow_index(out)
    out["squeeze"] = squeeze_on(out)
    return out
