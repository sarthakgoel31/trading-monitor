import pandas as pd
import pandas_ta as ta


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate RSI. Input df must have 'close' column."""
    return ta.rsi(df["close"], length=period)


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate ATR for pivot proximity threshold."""
    return ta.atr(df["high"], df["low"], df["close"], length=period)
