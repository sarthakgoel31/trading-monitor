"""Extra confluence factors: Fib pivots, volume spike, fib retracement, wick analysis."""

import numpy as np
import pandas as pd
from typing import List, Tuple, Optional
from src.models.types import PivotLevel


# ─── 1. Fibonacci Pivot Levels ───


def calculate_fib_pivots(daily_df: pd.DataFrame) -> List[PivotLevel]:
    """Fibonacci pivot points from previous day's OHLC.
    PP = (H + L + C) / 3
    R1 = PP + 0.382 * (H - L)
    R2 = PP + 0.618 * (H - L)
    R3 = PP + 1.000 * (H - L)
    S1 = PP - 0.382 * (H - L)
    S2 = PP - 0.618 * (H - L)
    S3 = PP - 1.000 * (H - L)
    """
    if len(daily_df) < 2:
        return []
    prev = daily_df.iloc[-2]
    H, L, C = prev["high"], prev["low"], prev["close"]
    PP = (H + L + C) / 3
    R = H - L

    return [
        PivotLevel("fPP", PP, "pivot"),
        PivotLevel("fR1", PP + 0.382 * R, "resistance"),
        PivotLevel("fR2", PP + 0.618 * R, "resistance"),
        PivotLevel("fR3", PP + 1.000 * R, "resistance"),
        PivotLevel("fS1", PP - 0.382 * R, "support"),
        PivotLevel("fS2", PP - 0.618 * R, "support"),
        PivotLevel("fS3", PP - 1.000 * R, "support"),
    ]


# ─── 2. Volume Spike Detection ───


def is_volume_spike(df: pd.DataFrame, bar_idx: int, lookback: int = 20, threshold: float = 1.5) -> bool:
    """Check if the bar's volume is significantly higher than recent average.
    A volume spike on a reversal candle confirms the divergence.
    Returns True if volume >= threshold * average volume.
    """
    if "volume" not in df.columns:
        return False

    vol = df["volume"].iloc[bar_idx]
    if vol == 0 or np.isnan(vol):
        return False

    start = max(0, bar_idx - lookback)
    avg_vol = df["volume"].iloc[start:bar_idx].mean()
    if avg_vol == 0 or np.isnan(avg_vol):
        return False

    return vol >= threshold * avg_vol


def volume_ratio(df: pd.DataFrame, bar_idx: int, lookback: int = 20) -> float:
    """Return volume / avg_volume ratio. Higher = more conviction."""
    if "volume" not in df.columns:
        return 1.0
    vol = df["volume"].iloc[bar_idx]
    start = max(0, bar_idx - lookback)
    avg = df["volume"].iloc[start:bar_idx].mean()
    if avg == 0 or np.isnan(avg) or np.isnan(vol):
        return 1.0
    return vol / avg


# ─── 3. Fibonacci Retracement ───


def find_recent_swing(df: pd.DataFrame, bar_idx: int, lookback: int = 100) -> Optional[Tuple[int, int, str]]:
    """Find the most recent significant swing move.
    Returns (swing_start_idx, swing_end_idx, direction) where direction is 'up' or 'down'.
    The swing is the dominant move before the current retracement.
    """
    start = max(0, bar_idx - lookback)
    window = df.iloc[start:bar_idx + 1]

    if len(window) < 20:
        return None

    # Find highest high and lowest low in the window
    hh_idx = window["high"].idxmax()
    ll_idx = window["low"].idxmin()

    # Get integer positions relative to df
    hh_pos = df.index.get_loc(hh_idx)
    ll_pos = df.index.get_loc(ll_idx)

    # The swing is from the earlier extreme to the later extreme
    if hh_pos < ll_pos:
        # Move was down (high came first, then low)
        return hh_pos, ll_pos, "down"
    else:
        # Move was up (low came first, then high)
        return ll_pos, hh_pos, "up"


def get_fib_retracement_levels(
    df: pd.DataFrame, swing_start: int, swing_end: int, swing_dir: str
) -> List[Tuple[str, float]]:
    """Calculate fibonacci retracement levels of a swing move.
    Fib levels: 23.6%, 38.2%, 50%, 61.8%, 70%, 78.6%, 81%

    For a DOWN swing (selling): retracement goes UP
      - 0% = swing low (bottom), 100% = swing high (top)
      - 38.2% retracement = price bounced up 38.2% of the down move

    For an UP swing (buying): retracement goes DOWN
      - 0% = swing high (top), 100% = swing low (bottom)
    """
    if swing_dir == "down":
        high = df["high"].iloc[swing_start]
        low = df["low"].iloc[swing_end]
        rng = high - low
        return [
            ("fib_23.6", low + 0.236 * rng),
            ("fib_38.2", low + 0.382 * rng),
            ("fib_50.0", low + 0.500 * rng),
            ("fib_61.8", low + 0.618 * rng),
            ("fib_70.0", low + 0.700 * rng),
            ("fib_78.6", low + 0.786 * rng),
            ("fib_81.0", low + 0.810 * rng),
        ]
    else:
        low = df["low"].iloc[swing_start]
        high = df["high"].iloc[swing_end]
        rng = high - low
        return [
            ("fib_23.6", high - 0.236 * rng),
            ("fib_38.2", high - 0.382 * rng),
            ("fib_50.0", high - 0.500 * rng),
            ("fib_61.8", high - 0.618 * rng),
            ("fib_70.0", high - 0.700 * rng),
            ("fib_78.6", high - 0.786 * rng),
            ("fib_81.0", high - 0.810 * rng),
        ]


def at_fib_retracement(
    df: pd.DataFrame, bar_idx: int, atr: float, lookback: int = 100, tolerance_mult: float = 0.5
) -> Tuple[bool, str]:
    """Check if current price is at a fibonacci retracement level.
    Returns (is_at_fib, fib_level_name).

    The key insight: if the trend was DOWN, and price has retraced UP to a fib level,
    that's a potential SELL zone (trend continuation). And vice versa.
    """
    if atr <= 0:
        return False, ""

    swing = find_recent_swing(df, bar_idx, lookback)
    if swing is None:
        return False, ""

    swing_start, swing_end, swing_dir = swing
    fibs = get_fib_retracement_levels(df, swing_start, swing_end, swing_dir)

    price = df["close"].iloc[bar_idx]
    threshold = atr * tolerance_mult

    for name, level in fibs:
        if abs(price - level) <= threshold:
            return True, f"{name}({level:.5f})"

    return False, ""


def fib_supports_direction(
    df: pd.DataFrame, bar_idx: int, direction: str, atr: float, lookback: int = 100
) -> bool:
    """Check if fib retracement supports the trade direction.
    - If previous swing was DOWN and price retraced UP to a fib → supports SHORT (trend continuation)
    - If previous swing was UP and price retraced DOWN to a fib → supports LONG (trend continuation)
    """
    swing = find_recent_swing(df, bar_idx, lookback)
    if swing is None:
        return False

    swing_start, swing_end, swing_dir = swing
    at_fib, _ = at_fib_retracement(df, bar_idx, atr, lookback)

    if not at_fib:
        return False

    # Swing was down, retraced up → sell zone
    if swing_dir == "down" and direction == "short":
        return True
    # Swing was up, retraced down → buy zone
    if swing_dir == "up" and direction == "long":
        return True

    return False


# ─── 4. Wick Analysis ───


def wick_analysis(df: pd.DataFrame, bar_idx: int) -> dict:
    """Analyze candle wicks.
    Upper wick = selling pressure (sellers pushed price down from high)
    Lower wick = buying pressure (buyers pushed price up from low)

    Returns dict with wick ratios and signals.
    """
    bar = df.iloc[bar_idx]
    high = bar["high"]
    low = bar["low"]
    opn = bar["open"]
    close = bar["close"]

    total_range = high - low
    if total_range == 0:
        return {"upper_ratio": 0, "lower_ratio": 0, "body_ratio": 0,
                "bearish_wick": False, "bullish_wick": False}

    body_top = max(opn, close)
    body_bottom = min(opn, close)

    upper_wick = high - body_top
    lower_wick = body_bottom - low
    body = body_top - body_bottom

    upper_ratio = upper_wick / total_range
    lower_ratio = lower_wick / total_range
    body_ratio = body / total_range

    return {
        "upper_ratio": upper_ratio,
        "lower_ratio": lower_ratio,
        "body_ratio": body_ratio,
        "bearish_wick": upper_ratio > 0.55,  # Big upper wick = sellers in control
        "bullish_wick": lower_ratio > 0.55,  # Big lower wick = buyers in control
    }


def wick_confirms_direction(df: pd.DataFrame, bar_idx: int, direction: str, lookback: int = 3) -> bool:
    """Check if recent wicks confirm trade direction.
    For LONG: look for bullish wicks (long lower wicks = buyers absorbing)
    For SHORT: look for bearish wicks (long upper wicks = sellers rejecting)

    Checks the divergence bar and surrounding bars.
    """
    start = max(0, bar_idx - lookback)
    bullish_count = 0
    bearish_count = 0

    for j in range(start, bar_idx + 1):
        w = wick_analysis(df, j)
        if w["bullish_wick"]:
            bullish_count += 1
        if w["bearish_wick"]:
            bearish_count += 1

    if direction == "long":
        return bullish_count >= 1  # At least 1 bullish wick in last few bars
    return bearish_count >= 1  # At least 1 bearish wick
