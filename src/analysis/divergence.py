import numpy as np
import pandas as pd
from typing import List

from src.models.types import SwingPoint, Divergence, DivergenceType, SignalStrength


def find_swing_points(
    df: pd.DataFrame,
    rsi: pd.Series,
    lookback: int = 5,
    is_high: bool = True,
) -> List[SwingPoint]:
    """Find swing highs or lows using the fractal method.

    A swing high at bar i: high[i] > high[j] for all j in [i-lookback, i+lookback], j != i.
    A swing low at bar i: low[i] < low[j] for all j in [i-lookback, i+lookback], j != i.
    """
    swings: List[SwingPoint] = []
    col = "high" if is_high else "low"
    values = df[col].values
    rsi_values = rsi.values

    for i in range(lookback, len(values) - lookback):
        if np.isnan(rsi_values[i]):
            continue

        window = values[i - lookback : i + lookback + 1]
        center = values[i]

        if is_high:
            if center == np.max(window) and np.sum(window == center) == 1:
                swings.append(
                    SwingPoint(
                        index=i,
                        timestamp=df.index[i],
                        price=center,
                        rsi=rsi_values[i],
                        is_high=True,
                    )
                )
        else:
            if center == np.min(window) and np.sum(window == center) == 1:
                swings.append(
                    SwingPoint(
                        index=i,
                        timestamp=df.index[i],
                        price=center,
                        rsi=rsi_values[i],
                        is_high=False,
                    )
                )

    return swings


def detect_divergences(
    df: pd.DataFrame,
    rsi: pd.Series,
    lookback: int = 5,
    max_bars_apart: int = 80,
    min_bars_apart: int = 5,
    recent_only: int = 30,
) -> List[Divergence]:
    """Detect all four types of RSI divergence.

    Checks consecutive pairs of swing highs/lows for divergence.
    Only reports divergences where the recent swing is within `recent_only` bars of the end.

    Types:
    - REGULAR_BULLISH:  price lower low,  RSI higher low
    - REGULAR_BEARISH:  price higher high, RSI lower high
    - HIDDEN_BULLISH:   price higher low,  RSI lower low
    - HIDDEN_BEARISH:   price lower high,  RSI higher high
    """
    divergences: List[Divergence] = []
    last_bar = len(df) - 1

    swing_highs = find_swing_points(df, rsi, lookback, is_high=True)
    swing_lows = find_swing_points(df, rsi, lookback, is_high=False)

    # Check swing lows for bullish divergences
    for i in range(1, len(swing_lows)):
        a = swing_lows[i - 1]
        b = swing_lows[i]
        bars_apart = b.index - a.index

        if bars_apart < min_bars_apart or bars_apart > max_bars_apart:
            continue
        if (last_bar - b.index) > recent_only:
            continue

        # Regular bullish: price lower low, RSI higher low
        if b.price < a.price and b.rsi > a.rsi:
            divergences.append(
                Divergence(
                    type=DivergenceType.REGULAR_BULLISH,
                    instrument="",
                    timeframe="",
                    swing_a=a,
                    swing_b=b,
                    strength=_assess_strength(a, b, "bullish"),
                    bars_apart=bars_apart,
                )
            )

        # Hidden bullish: price higher low, RSI lower low
        if b.price > a.price and b.rsi < a.rsi:
            divergences.append(
                Divergence(
                    type=DivergenceType.HIDDEN_BULLISH,
                    instrument="",
                    timeframe="",
                    swing_a=a,
                    swing_b=b,
                    strength=_assess_strength(a, b, "bullish"),
                    bars_apart=bars_apart,
                )
            )

    # Check swing highs for bearish divergences
    for i in range(1, len(swing_highs)):
        a = swing_highs[i - 1]
        b = swing_highs[i]
        bars_apart = b.index - a.index

        if bars_apart < min_bars_apart or bars_apart > max_bars_apart:
            continue
        if (last_bar - b.index) > recent_only:
            continue

        # Regular bearish: price higher high, RSI lower high
        if b.price > a.price and b.rsi < a.rsi:
            divergences.append(
                Divergence(
                    type=DivergenceType.REGULAR_BEARISH,
                    instrument="",
                    timeframe="",
                    swing_a=a,
                    swing_b=b,
                    strength=_assess_strength(a, b, "bearish"),
                    bars_apart=bars_apart,
                )
            )

        # Hidden bearish: price lower high, RSI higher high
        if b.price < a.price and b.rsi > a.rsi:
            divergences.append(
                Divergence(
                    type=DivergenceType.HIDDEN_BEARISH,
                    instrument="",
                    timeframe="",
                    swing_a=a,
                    swing_b=b,
                    strength=_assess_strength(a, b, "bearish"),
                    bars_apart=bars_apart,
                )
            )

    return divergences


def _assess_strength(a: SwingPoint, b: SwingPoint, direction: str) -> SignalStrength:
    """Assess divergence strength based on RSI extremity and divergence magnitude."""
    rsi_diff = abs(b.rsi - a.rsi)

    # RSI extremity bonus
    extremity = 0
    if direction == "bullish":
        if b.rsi < 30:
            extremity = 2
        elif b.rsi < 40:
            extremity = 1
    else:
        if b.rsi > 70:
            extremity = 2
        elif b.rsi > 60:
            extremity = 1

    # Divergence magnitude
    magnitude = 0
    if rsi_diff > 15:
        magnitude = 2
    elif rsi_diff > 8:
        magnitude = 1

    score = extremity + magnitude
    if score >= 3:
        return SignalStrength.STRONG
    elif score >= 1:
        return SignalStrength.MODERATE
    return SignalStrength.WEAK
