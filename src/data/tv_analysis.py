import logging
from tradingview_ta import TA_Handler, Interval as TAInterval

from config.instruments import Instrument

logger = logging.getLogger("trading-monitor.data")

TA_INTERVAL_MAP = {
    "5m": TAInterval.INTERVAL_5_MINUTES,
    "15m": TAInterval.INTERVAL_15_MINUTES,
    "1h": TAInterval.INTERVAL_1_HOUR,
}


class TVAnalysis:
    @staticmethod
    def get_summary(instrument: Instrument, timeframe: str) -> dict:
        """Get TradingView technical analysis summary for an instrument/timeframe.
        Returns dict with recommendation, buy/sell/neutral counts, RSI, etc.
        """
        interval = TA_INTERVAL_MAP.get(timeframe)
        if interval is None:
            logger.warning(f"Unknown TA timeframe: {timeframe}")
            return {}

        try:
            handler = TA_Handler(
                symbol=instrument.tradingview_ta_symbol,
                exchange=instrument.tradingview_ta_exchange,
                screener=instrument.tradingview_ta_screener,
                interval=interval,
            )
            analysis = handler.get_analysis()
            return {
                "recommendation": analysis.summary["RECOMMENDATION"],
                "buy_count": analysis.summary["BUY"],
                "sell_count": analysis.summary["SELL"],
                "neutral_count": analysis.summary["NEUTRAL"],
                "rsi_14": analysis.indicators.get("RSI"),
                "oscillators": analysis.oscillators["RECOMMENDATION"],
                "moving_averages": analysis.moving_averages["RECOMMENDATION"],
            }
        except Exception as e:
            logger.warning(f"TV analysis failed for {instrument.name} {timeframe}: {e}")
            return {}
