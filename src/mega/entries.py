"""All 21 entry confluence factors as independent boolean functions."""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional

from src.analysis.divergence import detect_divergences
from src.analysis.pivots import calculate_pivot_levels, check_pivot_proximity
from src.analysis.confluence_extra import (
    calculate_fib_pivots, is_volume_spike, volume_ratio,
    at_fib_retracement, fib_supports_direction,
    wick_confirms_direction,
)


# ─── A. Core Signal ───


def check_divergence(df, rsi, i, window=200, lookback=3):
    """Return list of divergences at bar i, or empty list."""
    if i < window:
        return []
    w = df.iloc[i - window: i + 1]
    wr = rsi.iloc[i - window: i + 1]
    return detect_divergences(w, wr, lookback=lookback, recent_only=5)


def check_dxy_momentum(dxy_df, dxy_idx, direction, lookback=10):
    if dxy_idx < lookback:
        return False
    current = dxy_df["close"].iloc[dxy_idx]
    past = dxy_df["close"].iloc[dxy_idx - lookback]
    if direction == "long":
        return current < past
    return current > past


def check_dxy_rsi(dxy_rsi, dxy_idx, direction):
    if dxy_idx < 5:
        return False
    cur = dxy_rsi.iloc[dxy_idx]
    prev = dxy_rsi.iloc[dxy_idx - 5]
    if np.isnan(cur) or np.isnan(prev):
        return False
    if direction == "long":
        return cur < prev
    return cur > prev


def check_dxy_extreme(dxy_rsi, dxy_idx, direction):
    if dxy_idx < 1:
        return False
    val = dxy_rsi.iloc[dxy_idx]
    if np.isnan(val):
        return False
    if direction == "long":
        return val > 65
    return val < 35


def check_dxy(mode, dxy_df, dxy_rsi, dxy_idx, direction):
    """Combined DXY check. mode: 'none','momentum','rsi','rsi_extreme','any','any2'"""
    if mode == "none":
        return True
    checks = {
        "momentum": lambda: check_dxy_momentum(dxy_df, dxy_idx, direction),
        "rsi": lambda: check_dxy_rsi(dxy_rsi, dxy_idx, direction),
        "rsi_extreme": lambda: check_dxy_extreme(dxy_rsi, dxy_idx, direction),
    }
    if mode in checks:
        return checks[mode]()
    if mode == "any":
        return any(fn() for fn in checks.values())
    if mode == "any2":
        return sum(1 for fn in checks.values() if fn()) >= 2
    return True


# ─── B. Candle Confirmation ───


def check_next_candle(df, i, direction):
    """Next candle must be green (long) or red (short).
    Returns (confirmed, entry_idx).
    If doji → check 3rd candle.
    """
    if i + 1 >= len(df):
        return False, i

    nxt = df.iloc[i + 1]
    body = abs(nxt["close"] - nxt["open"])
    rng = nxt["high"] - nxt["low"]

    # Doji check: body < 20% of range
    is_doji = rng > 0 and body / rng < 0.20

    if not is_doji:
        if direction == "long" and nxt["close"] > nxt["open"]:
            return True, i + 1
        if direction == "short" and nxt["close"] < nxt["open"]:
            return True, i + 1
        return False, i

    # Doji — check 3rd candle
    if i + 2 >= len(df):
        return False, i

    third = df.iloc[i + 2]
    if direction == "long" and third["close"] > third["open"]:
        return True, i + 2
    if direction == "short" and third["close"] < third["open"]:
        return True, i + 2
    return False, i


def check_wicks(df, i, direction, lookback=3):
    return wick_confirms_direction(df, i, direction, lookback)


def check_ll_hh(df, i, direction, lookback=3):
    if i < lookback:
        return True
    rec = df.iloc[i - lookback: i + 1]
    if direction == "long":
        return rec["low"].iloc[-1] <= rec["low"].min()
    return rec["high"].iloc[-1] >= rec["high"].max()


# ─── C. Volume Confirmation ───


def check_volume_spike(df, i, threshold=1.3):
    return is_volume_spike(df, i, lookback=20, threshold=threshold)


def check_delta_divergence(df, i, direction, lookback=5):
    """Approximate delta from bar data: green bar = positive delta, red = negative.
    If price makes lower low but recent bars have net positive delta → buying pressure.
    """
    if i < lookback:
        return False
    recent = df.iloc[i - lookback + 1: i + 1]
    deltas = recent["close"] - recent["open"]  # Positive = buyers, negative = sellers
    vol = recent["volume"].replace(0, 1)
    weighted_delta = (deltas * vol).sum()

    if direction == "long":
        return weighted_delta > 0  # Net buying despite lower price
    return weighted_delta < 0  # Net selling despite higher price


def check_cumulative_delta(df, i, direction, lookback=20):
    """Cumulative delta trend over last N bars."""
    if i < lookback:
        return False
    recent = df.iloc[i - lookback + 1: i + 1]
    deltas = recent["close"] - recent["open"]
    vol = recent["volume"].replace(0, 1)
    cum = (deltas * vol).cumsum()

    if len(cum) < 5:
        return False

    # Check if cum delta is trending in our direction
    first_half = cum.iloc[:len(cum)//2].mean()
    second_half = cum.iloc[len(cum)//2:].mean()

    if direction == "long":
        return second_half > first_half  # Rising cum delta
    return second_half < first_half  # Falling cum delta


# ─── D. Price Levels ───


def check_near_level(price, levels_dict, atr, multiplier=0.5):
    """Check if price is within multiplier*ATR of any level in the dict.
    Returns (is_near, level_name, level_value).
    """
    if atr <= 0:
        return False, "", 0.0
    threshold = atr * multiplier
    best_dist = float("inf")
    best_name = ""
    best_val = 0.0

    for name, val in levels_dict.items():
        if val is None or np.isnan(val):
            continue
        dist = abs(price - val)
        if dist < best_dist:
            best_dist = dist
            best_name = name
            best_val = val

    if best_dist <= threshold:
        return True, best_name, best_val
    return False, "", 0.0


def check_vwap_direction(price, vwap, direction):
    """VWAP direction filter: long below VWAP (mean reversion up), short above."""
    if np.isnan(vwap):
        return True  # No VWAP = no filter
    if direction == "long":
        return price < vwap
    return price > vwap


def check_fib_retracement(df, i, direction, atr):
    return fib_supports_direction(df, i, direction, atr)


# ─── Master confluence checker ───


def evaluate_all_confluences(
    df, rsi, atr_series, vwap_series,
    dxy_df, dxy_rsi,
    i, direction, div,
    levels,  # Dict of all level names → prices
    pivots, fib_pivots,
    news_blackout_fn=None,
) -> Dict[str, bool]:
    """Evaluate all 21 confluences at bar i. Returns dict of factor → True/False."""
    price = df["close"].iloc[i]
    atr = atr_series.iloc[i] if not np.isnan(atr_series.iloc[i]) else 0
    vwap = vwap_series.iloc[i] if vwap_series is not None and not np.isnan(vwap_series.iloc[i]) else np.nan
    di = min(i, len(dxy_df) - 1)

    # Pivot proximity
    piv_near = False
    if pivots and atr > 0:
        prox = check_pivot_proximity(price, pivots, atr, 0.5)
        piv_near = any(p.is_near for p in prox)

    fpiv_near = False
    if fib_pivots and atr > 0:
        prox = check_pivot_proximity(price, fib_pivots, atr, 0.5)
        fpiv_near = any(p.is_near for p in prox)

    level_near, level_name, _ = check_near_level(price, levels, atr)

    # News
    in_blackout = False
    if news_blackout_fn:
        in_blackout, _ = news_blackout_fn(df.index[i])

    nc_ok, nc_idx = check_next_candle(df, i, direction)

    return {
        # Core
        "dxy_momentum": check_dxy_momentum(dxy_df, di, direction),
        "dxy_rsi": check_dxy_rsi(dxy_rsi, di, direction),
        "dxy_extreme": check_dxy_extreme(dxy_rsi, di, direction),
        "dxy_any": check_dxy(mode="any", dxy_df=dxy_df, dxy_rsi=dxy_rsi, dxy_idx=di, direction=direction),
        # Candle
        "next_candle": nc_ok,
        "nc_entry_idx": nc_idx,  # Not a bool, but needed for entry
        "wicks": check_wicks(df, i, direction),
        "ll_hh": check_ll_hh(df, i, direction),
        # Volume
        "vol_spike": check_volume_spike(df, i),
        "delta_div": check_delta_divergence(df, i, direction),
        "cum_delta": check_cumulative_delta(df, i, direction),
        # Levels
        "std_pivot": piv_near,
        "fib_pivot": fpiv_near,
        "fib_retrace": check_fib_retracement(df, i, direction, atr) if atr > 0 else False,
        "any_pivot": piv_near or fpiv_near,
        "pd_hl": check_near_level(price, {k: v for k, v in levels.items() if k.startswith("pd_")}, atr)[0],
        "wk_hl": check_near_level(price, {k: v for k, v in levels.items() if k.startswith("wk_")}, atr)[0],
        "mo_hl": check_near_level(price, {k: v for k, v in levels.items() if k.startswith("mo_")}, atr)[0],
        "session_level": check_near_level(price, {k: v for k, v in levels.items() if "sess" in k}, atr)[0],
        "vwap_direction": check_vwap_direction(price, vwap, direction),
        "vpoc_near": check_near_level(price, {k: v for k, v in levels.items() if "vpoc" in k}, atr)[0],
        "any_level": level_near or piv_near or fpiv_near,
        # News
        "no_news_blackout": not in_blackout,
        # Meta
        "price": price,
        "atr": atr,
        "vwap": vwap,
    }
