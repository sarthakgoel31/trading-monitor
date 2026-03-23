import logging
import os
import time

import certifi
import pandas as pd

from config.instruments import Instrument, TimeframeConfig
from config.settings import Settings

logger = logging.getLogger("trading-monitor.data")

# Fix macOS SSL cert issue for tvdatafeed's websocket
os.environ["SSL_CERT_FILE"] = certifi.where()

# tvdatafeed interval mapping
TV_INTERVAL_MAP = {}
try:
    from tvDatafeed import TvDatafeed, Interval

    TV_INTERVAL_MAP = {
        "5m": Interval.in_5_minute,
        "15m": Interval.in_15_minute,
        "1h": Interval.in_1_hour,
        "1d": Interval.in_daily,
    }
    _TV_AVAILABLE = True
except ImportError:
    _TV_AVAILABLE = False
    logger.warning("tvdatafeed not installed — falling back to yfinance only")

# yfinance as fallback
try:
    import yfinance as yf

    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False


class DataFetcher:
    """Fetch OHLCV data. Uses TradingView Pro (live) with yfinance fallback."""

    def __init__(self, settings: Settings):
        self._tv = None
        self._use_tv = False

        if _TV_AVAILABLE and settings.tv_username:
            try:
                self._tv = TvDatafeed(
                    username=settings.tv_username,
                    password=settings.tv_password,
                )
                self._use_tv = True
                logger.info("TradingView Pro data feed connected (live data)")
            except Exception as e:
                logger.warning(f"TradingView login failed: {e} — using yfinance")
        elif _TV_AVAILABLE:
            try:
                self._tv = TvDatafeed()  # No login = free tier
                self._use_tv = True
                logger.info("TradingView data feed connected (no login, may be limited)")
            except Exception as e:
                logger.warning(f"TradingView connection failed: {e} — using yfinance")

        if not self._use_tv and not _YF_AVAILABLE:
            raise RuntimeError("No data source available. Install tvdatafeed or yfinance.")

    def fetch_ohlcv(
        self, instrument: Instrument, tf_config: TimeframeConfig
    ) -> pd.DataFrame:
        """Fetch OHLCV data. Tries TradingView first, falls back to yfinance."""
        if self._use_tv:
            try:
                return self._fetch_tv(instrument, tf_config)
            except Exception as e:
                logger.warning(
                    f"TV fetch failed for {instrument.tv_symbol} {tf_config.name}: {e}"
                    " — falling back to yfinance"
                )

        return self._fetch_yfinance(instrument, tf_config)

    def fetch_daily_ohlcv(self, instrument: Instrument, bars: int = 30) -> pd.DataFrame:
        """Fetch daily OHLCV for pivot calculation."""
        if self._use_tv:
            try:
                return self._fetch_tv_daily(instrument, bars)
            except Exception as e:
                logger.warning(f"TV daily fetch failed: {e} — falling back to yfinance")

        return self._fetch_yfinance_daily(instrument, bars)

    # --- TradingView methods ---

    def _fetch_tv(self, instrument: Instrument, tf_config: TimeframeConfig) -> pd.DataFrame:
        interval = TV_INTERVAL_MAP.get(tf_config.name)
        if interval is None:
            raise ValueError(f"Unknown TV interval for {tf_config.name}")

        df = self._tv_get_hist_retry(
            instrument.tv_symbol,
            instrument.tv_exchange,
            interval,
            tf_config.candles_to_fetch,
        )
        return self._normalize_df(df, instrument.tv_symbol, tf_config.name)

    def _fetch_tv_daily(self, instrument: Instrument, bars: int) -> pd.DataFrame:
        df = self._tv_get_hist_retry(
            instrument.tv_symbol,
            instrument.tv_exchange,
            Interval.in_daily,
            bars,
        )
        return self._normalize_df(df, instrument.tv_symbol, "1d")

    def _tv_get_hist_retry(
        self, symbol: str, exchange: str, interval, n_bars: int, retries: int = 3
    ) -> pd.DataFrame:
        last_err = None
        for attempt in range(retries):
            try:
                df = self._tv.get_hist(
                    symbol=symbol,
                    exchange=exchange,
                    interval=interval,
                    n_bars=n_bars,
                )
                if df is not None and not df.empty:
                    return df
                raise ValueError("Empty response")
            except Exception as e:
                last_err = e
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)

        raise RuntimeError(f"TV fetch failed after {retries} attempts: {last_err}")

    # --- yfinance fallback methods ---

    def _fetch_yfinance(self, instrument: Instrument, tf_config: TimeframeConfig) -> pd.DataFrame:
        ticker = yf.Ticker(instrument.yf_symbol)
        df = ticker.history(interval=tf_config.yf_interval, period=tf_config.yf_period)

        if df is None or df.empty:
            raise ValueError(f"No yfinance data for {instrument.yf_symbol}")

        return self._normalize_df(df, instrument.yf_symbol, tf_config.name)

    def _fetch_yfinance_daily(self, instrument: Instrument, bars: int) -> pd.DataFrame:
        ticker = yf.Ticker(instrument.yf_symbol)
        df = ticker.history(interval="1d", period="3mo")

        if df is None or df.empty:
            raise ValueError(f"No yfinance daily data for {instrument.yf_symbol}")

        df = self._normalize_df(df, instrument.yf_symbol, "1d")
        if len(df) > bars:
            df = df.iloc[-bars:]
        return df

    # --- Common ---

    def _normalize_df(self, df: pd.DataFrame, symbol: str, tf_name: str) -> pd.DataFrame:
        df = df.sort_index(ascending=True)
        df.columns = [c.lower() for c in df.columns]
        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep]
        logger.info(f"Fetched {len(df)} candles for {symbol} ({tf_name})")
        return df
