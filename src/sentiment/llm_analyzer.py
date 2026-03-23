import logging
import re
import ssl
from datetime import datetime, timezone
from typing import Dict, List, Optional

import nltk

# Auto-download VADER lexicon if missing
try:
    nltk.data.find("sentiment/vader_lexicon.zip")
except LookupError:
    try:
        ssl._create_default_https_context = ssl._create_unverified_context
    except AttributeError:
        pass
    nltk.download("vader_lexicon", quiet=True)

from nltk.sentiment.vader import SentimentIntensityAnalyzer

from config.settings import Settings
from src.models.types import CompositeSentiment, SentimentResult

logger = logging.getLogger("trading-monitor.sentiment")

# Financial keywords that VADER misses — boost scores when these appear
BULLISH_WORDS = {
    "rally", "rallies", "rallied", "rallying",
    "surge", "surges", "surged", "surging",
    "breakout", "breaks-out", "broke-out",
    "upside", "bullish", "hawkish", "tighten", "tightening",
    "outperform", "outperforming", "outperforms",
    "gain", "gains", "gained", "gaining",
    "strength", "strengthens", "strengthened", "strengthening", "firm", "firmer",
    "support", "supported",
    "bounce", "bounced", "bouncing", "rebound", "rebounded", "rebounding",
    "recovery", "recovering", "recovers", "recovered",
    "uptick", "bid", "bids",
    "buy", "buying", "buyers", "bought",
    "long", "higher", "highs",
    "rise", "rises", "rising", "rose", "risen",
    "positive", "optimism", "optimistic",
    "accumulate", "upgrade", "upgraded",
    "advance", "advances", "advanced", "advancing",
    "climb", "climbs", "climbed", "climbing",
    "soar", "soars", "soared", "soaring",
}

BEARISH_WORDS = {
    "sell", "selling", "sellers", "sold",
    "selloff", "sell-off",
    "crash", "crashes", "crashed", "crashing",
    "plunge", "plunges", "plunged", "plunging",
    "downside", "bearish", "dovish", "easing", "looser",
    "underperform", "underperforming", "underperforms",
    "loss", "losses", "lost", "losing",
    "weakness", "weakens", "weakened", "weakening", "weak", "weaker", "softer",
    "resistance",
    "decline", "declines", "declined", "declining",
    "drop", "drops", "dropped", "dropping",
    "fall", "falls", "falling", "fell", "fallen",
    "negative", "pessimism", "pessimistic",
    "slump", "slumps", "slumped", "slumping",
    "retreat", "retreats", "retreated", "retreating",
    "lower", "lows",
    "short", "shorting",
    "slide", "slides", "slid", "sliding",
    "sink", "sinks", "sank", "sinking",
    "tumble", "tumbles", "tumbled", "tumbling",
    "downgrade", "downgraded",
    "risk-off", "uncertainty", "turmoil", "fragile",
}


def _financial_boost(text: str) -> float:
    """Score financial keywords that VADER's generic lexicon misses.
    Returns a value between -1 and +1.
    """
    words = set(re.findall(r"[a-z_-]+", text.lower()))
    bull_count = len(words & BULLISH_WORDS)
    bear_count = len(words & BEARISH_WORDS)
    total = bull_count + bear_count
    if total == 0:
        return 0.0
    return (bull_count - bear_count) / total


class SentimentAnalyzer:
    """Local sentiment analyzer using VADER + financial keyword boosting.
    No API keys needed.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._vader = SentimentIntensityAnalyzer()

    def analyze(
        self,
        source: str,
        instrument: str,
        texts: List[Dict],
    ) -> SentimentResult:
        """Analyze a batch of texts from a single source.
        Each text dict should have a 'text' key and optionally 'score' for engagement.
        """
        if not texts:
            return SentimentResult(
                source=source,
                instrument=instrument,
                score=0.0,
                confidence=0.0,
                summary="No data available",
            )

        # Sort by engagement, take top items
        sorted_texts = sorted(texts, key=lambda x: x.get("score", 0), reverse=True)[:20]

        vader_scores = []
        fin_scores = []

        for t in sorted_texts:
            text = t.get("text", "")
            if not text.strip():
                continue

            # VADER compound score (-1 to +1)
            vs = self._vader.polarity_scores(text)
            vader_scores.append(vs["compound"])

            # Financial keyword boost
            fin_scores.append(_financial_boost(text))

        if not vader_scores:
            return SentimentResult(
                source=source, instrument=instrument,
                score=0.0, confidence=0.0, summary="No usable text",
            )

        # Blend: 40% VADER, 60% financial keywords (fin keywords are more
        # accurate for FX/macro text than VADER's generic lexicon)
        avg_vader = sum(vader_scores) / len(vader_scores)
        avg_fin = sum(fin_scores) / len(fin_scores)
        blended = 0.4 * avg_vader + 0.6 * avg_fin

        # Confidence based on agreement between texts
        if len(vader_scores) >= 3:
            # High confidence if most texts agree on direction
            same_sign = sum(1 for s in vader_scores if s * blended > 0)
            confidence = same_sign / len(vader_scores)
        else:
            confidence = 0.4  # Low sample = lower confidence

        # Build summary
        if blended > 0.15:
            tone = "bullish"
        elif blended < -0.15:
            tone = "bearish"
        else:
            tone = "neutral"

        summary = f"{len(sorted_texts)} texts, {tone} ({blended:+.2f})"

        return SentimentResult(
            source=source,
            instrument=instrument,
            score=max(-1.0, min(1.0, blended)),
            confidence=max(0.0, min(1.0, confidence)),
            summary=summary,
            sample_texts=[t["text"][:100] for t in sorted_texts[:3]],
            timestamp=datetime.now(timezone.utc),
        )

    def compute_composite(
        self,
        results: List[SentimentResult],
        tv_sentiment: Optional[Dict] = None,
        weights: Optional[Dict[str, float]] = None,
    ) -> CompositeSentiment:
        """Combine multiple source results into a weighted composite score."""
        if weights is None:
            weights = {
                "reddit": self._settings.sentiment_weight_reddit,
                "news": self._settings.sentiment_weight_news,
                "reports": self._settings.sentiment_weight_reports,
                "tradingview": self._settings.sentiment_weight_tv,
            }

        all_results = list(results)

        # Add TradingView as a SentimentResult if available
        if tv_sentiment and tv_sentiment.get("score") is not None:
            all_results.append(
                SentimentResult(
                    source="tradingview",
                    instrument=results[0].instrument if results else "",
                    score=tv_sentiment["score"],
                    confidence=0.7,
                    summary=f"TV: {tv_sentiment.get('recommendation', 'N/A')} "
                    f"(Buy:{tv_sentiment.get('buy',0)} Sell:{tv_sentiment.get('sell',0)})",
                )
            )

        if not all_results:
            return CompositeSentiment(
                instrument="",
                overall_score=0.0,
                overall_confidence=0.0,
                sources=[],
                summary="No sentiment data",
            )

        # Weighted average
        weighted_sum = 0.0
        weight_total = 0.0
        confidence_sum = 0.0

        for r in all_results:
            w = weights.get(r.source, 0.1)
            weighted_sum += r.score * w * r.confidence
            weight_total += w * r.confidence
            confidence_sum += r.confidence

        overall_score = weighted_sum / weight_total if weight_total > 0 else 0.0
        overall_confidence = confidence_sum / len(all_results)

        summaries = [f"{r.source}: {r.summary}" for r in all_results if r.summary]
        combined_summary = " | ".join(summaries) if summaries else "Mixed signals"

        return CompositeSentiment(
            instrument=all_results[0].instrument,
            overall_score=max(-1.0, min(1.0, overall_score)),
            overall_confidence=max(0.0, min(1.0, overall_confidence)),
            sources=all_results,
            summary=combined_summary,
        )
