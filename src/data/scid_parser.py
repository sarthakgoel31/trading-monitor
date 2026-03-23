"""Parse Sierra Chart .scid files into DataFrames with delta and volume-per-trade."""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("trading-monitor.scid")

OLE_EPOCH = datetime(1899, 12, 30, tzinfo=timezone.utc)

# SCID record dtype — DateTime is int64 (microseconds since 1899-12-30)
SCID_DTYPE = np.dtype([
    ("DateTime", "<i8"),
    ("Open", "<f4"),
    ("High", "<f4"),
    ("Low", "<f4"),
    ("Close", "<f4"),
    ("NumTrades", "<i4"),
    ("TotalVolume", "<u4"),
    ("BidVolume", "<u4"),
    ("AskVolume", "<u4"),
])

HEADER_SIZE = 56


def read_scid(filepath: str) -> pd.DataFrame:
    """Read a .scid file and return a tick-level DataFrame.
    Columns: datetime(index), open, high, low, close, volume, num_trades, bid_volume, ask_volume, delta
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"SCID file not found: {filepath}")

    file_size = path.stat().st_size
    n_records = (file_size - HEADER_SIZE) // SCID_DTYPE.itemsize
    logger.info(f"Reading {filepath}: {n_records:,} records ({file_size / 1e6:.1f} MB)")

    with open(filepath, "rb") as f:
        f.seek(HEADER_SIZE)
        data = np.fromfile(f, dtype=SCID_DTYPE)

    # Convert SCDateTime (microseconds since 1899-12-30) to pandas datetime
    microseconds = data["DateTime"].astype("int64")
    timestamps = pd.to_datetime(microseconds, unit="us", origin=datetime(1899, 12, 30))

    df = pd.DataFrame({
        "open": data["Open"].astype("float64"),
        "high": data["High"].astype("float64"),
        "low": data["Low"].astype("float64"),
        "close": data["Close"].astype("float64"),
        "volume": data["TotalVolume"].astype("float64"),
        "num_trades": data["NumTrades"].astype("int32"),
        "bid_volume": data["BidVolume"].astype("float64"),
        "ask_volume": data["AskVolume"].astype("float64"),
    }, index=timestamps)
    df.index.name = "datetime"

    # Sierra stores prices as integers (e.g., 11616 = 1.1616 for 6E)
    # 6E tick size is 0.00005, prices stored * 100000
    # Detect and convert
    if df["close"].median() > 1000:
        divisor = 10000.0
        df["open"] /= divisor
        df["high"] /= divisor
        df["low"] /= divisor
        df["close"] /= divisor
        logger.info(f"Auto-detected integer prices, divided by {divisor}")

    # Delta = ask volume - bid volume (positive = buying pressure)
    df["delta"] = df["ask_volume"] - df["bid_volume"]

    # Volume per trade
    df["vol_per_trade"] = np.where(df["num_trades"] > 0, df["volume"] / df["num_trades"], 0)

    # Filter out zero/invalid records
    df = df[df["close"] > 0].copy()

    # Fix zero open/low/high from sparse ticks — forward fill from close
    df["open"] = df["open"].replace(0, np.nan).ffill().bfill()
    df.loc[df["high"] == 0, "high"] = df.loc[df["high"] == 0, "close"]
    df.loc[df["low"] == 0, "low"] = df.loc[df["low"] == 0, "close"]

    logger.info(f"Parsed {len(df):,} valid ticks from {df.index[0]} to {df.index[-1]}")
    return df


def aggregate_to_bars(tick_df: pd.DataFrame, interval: str = "5min") -> pd.DataFrame:
    """Aggregate tick data into OHLCV bars with delta and volume-per-trade.
    interval: '1min', '5min', '15min', '60min', '1D'
    """
    resampled = tick_df.resample(interval)

    bars = pd.DataFrame({
        "open": resampled["close"].first(),
        "high": resampled["high"].max(),
        "low": resampled["low"].min(),
        "close": resampled["close"].last(),
        "volume": resampled["volume"].sum(),
        "num_trades": resampled["num_trades"].sum(),
        "bid_volume": resampled["bid_volume"].sum(),
        "ask_volume": resampled["ask_volume"].sum(),
        "delta": resampled["delta"].sum(),
        "vol_per_trade": resampled["volume"].sum() / resampled["num_trades"].sum().replace(0, np.nan),
    })

    # Drop empty bars (no trading)
    bars = bars.dropna(subset=["close"]).copy()

    # Cumulative delta (resets each session/day)
    dates = bars.index.date
    cum_delta = pd.Series(0.0, index=bars.index)
    for day in np.unique(dates):
        mask = dates == day
        cum_delta[mask] = bars.loc[mask, "delta"].cumsum()
    bars["cum_delta"] = cum_delta

    logger.info(f"Aggregated to {len(bars):,} bars at {interval}")
    return bars


def load_6e_combined(data_dir: str = "data") -> pd.DataFrame:
    """Load and combine 6EH6 + 6EM6 tick data, aggregate to provide all timeframes."""
    data_path = Path(data_dir)
    dfs = []

    for scid_file in sorted(data_path.glob("6E*.scid")):
        logger.info(f"Loading {scid_file.name}...")
        df = read_scid(str(scid_file))
        dfs.append(df)

    if not dfs:
        raise FileNotFoundError(f"No 6E .scid files found in {data_dir}")

    # Combine and sort — overlapping dates get deduplicated (keep latest contract)
    combined = pd.concat(dfs).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]

    logger.info(f"Combined: {len(combined):,} ticks from {combined.index[0]} to {combined.index[-1]}")
    return combined


def get_all_timeframes(tick_df: pd.DataFrame) -> dict:
    """Generate all timeframes from tick data.
    Returns dict: {'1m': df, '5m': df, '15m': df, '1h': df, '1D': df}
    """
    return {
        "1m": aggregate_to_bars(tick_df, "1min"),
        "5m": aggregate_to_bars(tick_df, "5min"),
        "15m": aggregate_to_bars(tick_df, "15min"),
        "1h": aggregate_to_bars(tick_df, "60min"),
        "1D": aggregate_to_bars(tick_df, "1D"),
    }
