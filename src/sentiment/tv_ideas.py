import logging
from typing import Dict, Optional

from src.data.tv_analysis import TVAnalysis
from config.instruments import Instrument

logger = logging.getLogger("trading-monitor.sentiment")


class TVIdeasFeed:
    """Get TradingView community sentiment via technical analysis summary.
    Uses the tradingview_ta library's aggregated buy/sell/neutral indicators
    as a proxy for community sentiment.
    """

    @staticmethod
    def get_ta_sentiment(instrument: Instrument) -> Optional[Dict]:
        """Get aggregated TA sentiment across timeframes.
        Returns a dict with overall bias and details.
        """
        try:
            # Use 1h as representative timeframe for sentiment
            summary = TVAnalysis.get_summary(instrument, "1h")
            if not summary:
                return None

            buy = summary.get("buy_count", 0)
            sell = summary.get("sell_count", 0)
            neutral = summary.get("neutral_count", 0)
            total = buy + sell + neutral

            if total == 0:
                return None

            # Convert to -1 to +1 score
            score = (buy - sell) / total

            return {
                "recommendation": summary.get("recommendation", "NEUTRAL"),
                "score": score,
                "buy": buy,
                "sell": sell,
                "neutral": neutral,
                "oscillators": summary.get("oscillators", ""),
                "moving_averages": summary.get("moving_averages", ""),
                "rsi_14": summary.get("rsi_14"),
            }
        except Exception as e:
            logger.warning(f"TV ideas sentiment failed for {instrument.name}: {e}")
            return None
