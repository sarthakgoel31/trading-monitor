from dataclasses import dataclass, field
from typing import List


@dataclass
class TimeframeConfig:
    name: str              # "5m", "15m", "1h"
    yf_interval: str       # yfinance fallback interval
    yf_period: str         # yfinance fallback period
    swing_lookback: int = 5
    candles_to_fetch: int = 200


@dataclass
class Instrument:
    name: str
    tv_symbol: str         # TradingView symbol (primary, live)
    tv_exchange: str       # TradingView exchange
    yf_symbol: str         # yfinance ticker (fallback, delayed)
    tradingview_ta_symbol: str
    tradingview_ta_exchange: str
    tradingview_ta_screener: str
    reddit_keywords: List[str] = field(default_factory=list)
    news_keywords: List[str] = field(default_factory=list)
    timeframes: List[TimeframeConfig] = field(default_factory=list)


INSTRUMENTS = {
    "6E": Instrument(
        name="6E (Euro FX Futures)",
        tv_symbol="6E1!",
        tv_exchange="CME",
        yf_symbol="EURUSD=X",
        tradingview_ta_symbol="EURUSD",
        tradingview_ta_exchange="FX_IDC",
        tradingview_ta_screener="forex",
        reddit_keywords=["EURUSD", "euro dollar", "EUR/USD", "6E futures", "euro fx"],
        news_keywords=["EURUSD", "euro dollar exchange rate", "ECB interest rate"],
        timeframes=[
            TimeframeConfig("5m", "5m", "5d", swing_lookback=3, candles_to_fetch=200),
            TimeframeConfig("15m", "15m", "5d", swing_lookback=5, candles_to_fetch=200),
            TimeframeConfig("1h", "1h", "1mo", swing_lookback=5, candles_to_fetch=200),
        ],
    ),
    "DXY": Instrument(
        name="DXY (US Dollar Index)",
        tv_symbol="DXY",
        tv_exchange="TVC",
        yf_symbol="DX-Y.NYB",
        tradingview_ta_symbol="DXY",
        tradingview_ta_exchange="TVC",
        tradingview_ta_screener="cfd",
        reddit_keywords=["DXY", "dollar index", "US dollar", "USD index"],
        news_keywords=["US dollar index", "DXY", "Fed interest rate", "dollar strength"],
        timeframes=[
            TimeframeConfig("5m", "5m", "5d", swing_lookback=3, candles_to_fetch=200),
            TimeframeConfig("15m", "15m", "5d", swing_lookback=5, candles_to_fetch=200),
            TimeframeConfig("1h", "1h", "1mo", swing_lookback=5, candles_to_fetch=200),
        ],
    ),
}
