import pandas as pd
from typing import List

from src.models.types import PivotLevel, PivotProximity


def calculate_pivot_levels(daily_df: pd.DataFrame) -> List[PivotLevel]:
    """Calculate standard floor pivot points from the previous daily candle.

    PP = (H + L + C) / 3
    R1 = 2*PP - L,  S1 = 2*PP - H
    R2 = PP + (H-L), S2 = PP - (H-L)
    R3 = H + 2*(PP-L), S3 = L - 2*(H-PP)
    """
    if len(daily_df) < 2:
        return []

    prev = daily_df.iloc[-2]  # Previous completed day
    H, L, C = prev["high"], prev["low"], prev["close"]
    PP = (H + L + C) / 3

    return [
        PivotLevel("PP", PP, "pivot"),
        PivotLevel("R1", 2 * PP - L, "resistance"),
        PivotLevel("S1", 2 * PP - H, "support"),
        PivotLevel("R2", PP + (H - L), "resistance"),
        PivotLevel("S2", PP - (H - L), "support"),
        PivotLevel("R3", H + 2 * (PP - L), "resistance"),
        PivotLevel("S3", L - 2 * (H - PP), "support"),
    ]


def check_pivot_proximity(
    current_price: float,
    levels: List[PivotLevel],
    atr: float,
    multiplier: float = 0.5,
) -> List[PivotProximity]:
    """Check if current price is near any pivot level.
    'Near' = within multiplier * ATR.
    Returns all levels sorted by distance.
    """
    if atr <= 0 or not levels:
        return []

    threshold = atr * multiplier
    results = []

    for level in levels:
        distance = abs(current_price - level.value)
        atr_ratio = distance / atr
        results.append(
            PivotProximity(
                level=level,
                distance=distance,
                distance_atr_ratio=atr_ratio,
                is_near=distance <= threshold,
            )
        )

    results.sort(key=lambda x: x.distance)
    return results
