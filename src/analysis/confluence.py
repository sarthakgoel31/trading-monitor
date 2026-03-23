from datetime import datetime
from typing import List, Optional

from src.models.types import (
    Alert,
    CompositeSentiment,
    Divergence,
    DivergenceType,
    PivotProximity,
    SignalStrength,
)


def assess_confluence(
    instrument: str,
    timeframe: str,
    divergence: Divergence,
    pivot_results: List[PivotProximity],
    sentiment: Optional[CompositeSentiment],
    tv_summary: Optional[dict],
) -> Optional[Alert]:
    """Combine signals into a confluence score (0-100) and generate an Alert.

    Scoring:
    - Divergence strength:      +30 (STRONG), +20 (MODERATE), +10 (WEAK)
    - At pivot level:           +25 (at S/R), +15 (at PP)
    - Sentiment alignment:      +20 (strong), +10 (mild)
    - TV summary alignment:     +10
    Minimum score to generate alert: 10 (any divergence)
    """
    score = 0.0
    parts: List[str] = []

    # 1. Divergence score
    strength_scores = {
        SignalStrength.STRONG: 30,
        SignalStrength.MODERATE: 20,
        SignalStrength.WEAK: 10,
    }
    score += strength_scores.get(divergence.strength, 10)
    div_label = divergence.type.value.replace("_", " ").title()
    parts.append(f"{div_label} ({divergence.strength.value})")

    # 2. Pivot proximity
    near_pivots = [p for p in pivot_results if p.is_near]
    if near_pivots:
        best = near_pivots[0]  # Already sorted by distance
        if best.level.level_type in ("support", "resistance"):
            score += 25
            parts.append(f"at {best.level.name} ({best.level.value:.5f})")
        else:
            score += 15
            parts.append(f"near PP ({best.level.value:.5f})")

    # 3. Sentiment alignment
    if sentiment and sentiment.overall_confidence > 0.3:
        is_bullish_div = divergence.type in (
            DivergenceType.REGULAR_BULLISH,
            DivergenceType.HIDDEN_BULLISH,
        )
        sentiment_bullish = sentiment.overall_score > 0

        if is_bullish_div == sentiment_bullish:
            if abs(sentiment.overall_score) > 0.5:
                score += 20
                parts.append(f"sentiment aligned (strong, {sentiment.overall_score:+.2f})")
            else:
                score += 10
                parts.append(f"sentiment aligned (mild, {sentiment.overall_score:+.2f})")
        else:
            # Opposing sentiment — note but don't subtract
            parts.append(f"sentiment opposing ({sentiment.overall_score:+.2f})")

    # 4. TradingView summary alignment
    if tv_summary and tv_summary.get("recommendation"):
        rec = tv_summary["recommendation"]
        is_bullish_div = divergence.type in (
            DivergenceType.REGULAR_BULLISH,
            DivergenceType.HIDDEN_BULLISH,
        )
        tv_bullish = rec in ("BUY", "STRONG_BUY")
        tv_bearish = rec in ("SELL", "STRONG_SELL")

        if (is_bullish_div and tv_bullish) or (not is_bullish_div and tv_bearish):
            score += 10
            parts.append(f"TV: {rec}")

    headline = f"{instrument} {timeframe}: {' | '.join(parts)}"

    return Alert(
        timestamp=datetime.utcnow(),
        instrument=instrument,
        timeframe=timeframe,
        divergence=divergence,
        pivot_proximity=near_pivots if near_pivots else None,
        sentiment=sentiment,
        tv_summary=tv_summary,
        confluence_score=score,
        headline=headline,
    )
