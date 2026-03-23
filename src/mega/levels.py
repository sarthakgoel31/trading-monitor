"""Compute all price levels: VWAP, prev day/week/month H/L, volume profile approximation."""

import numpy as np
import pandas as pd
from datetime import timedelta
from typing import Dict, List, Tuple


def compute_session_vwap(df: pd.DataFrame) -> pd.Series:
    """Compute intraday VWAP, resetting each session day.
    VWAP = cumulative(typical_price * volume) / cumulative(volume)
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].replace(0, np.nan).fillna(1)  # avoid div by zero

    dates = df.index.date
    vwap = pd.Series(np.nan, index=df.index, dtype=float)

    for day in np.unique(dates):
        mask = dates == day
        day_tp = tp[mask]
        day_vol = vol[mask]
        cum_tpv = (day_tp * day_vol).cumsum()
        cum_vol = day_vol.cumsum()
        vwap[mask] = cum_tpv / cum_vol

    return vwap


def compute_prev_day_levels(daily_df: pd.DataFrame, bar_date) -> Dict[str, float]:
    """Get previous completed day's high, low, close, open."""
    prev_days = daily_df[daily_df.index.date < bar_date]
    if len(prev_days) < 1:
        return {}
    prev = prev_days.iloc[-1]
    return {
        "pd_high": prev["high"],
        "pd_low": prev["low"],
        "pd_close": prev["close"],
        "pd_open": prev["open"],
    }


def compute_weekly_levels(daily_df: pd.DataFrame, bar_date) -> Dict[str, float]:
    """Get previous completed week's high and low."""
    daily_df = daily_df.copy()
    daily_df["week"] = daily_df.index.isocalendar().week.values
    daily_df["year"] = daily_df.index.year

    current = pd.Timestamp(bar_date)
    cw = current.isocalendar()[1]
    cy = current.year

    prev_week = daily_df[(daily_df["year"] == cy) & (daily_df["week"] == cw - 1)]
    if prev_week.empty and cw == 1:
        prev_week = daily_df[(daily_df["year"] == cy - 1) & (daily_df["week"] >= 52)]
    if prev_week.empty:
        return {}

    return {
        "wk_high": prev_week["high"].max(),
        "wk_low": prev_week["low"].min(),
    }


def compute_monthly_levels(daily_df: pd.DataFrame, bar_date) -> Dict[str, float]:
    """Get previous completed month's high and low."""
    current = pd.Timestamp(bar_date)
    if current.month == 1:
        pm, py = 12, current.year - 1
    else:
        pm, py = current.month - 1, current.year

    prev_month = daily_df[(daily_df.index.month == pm) & (daily_df.index.year == py)]
    if prev_month.empty:
        return {}

    return {
        "mo_high": prev_month["high"].max(),
        "mo_low": prev_month["low"].min(),
    }


def compute_all_static_levels(daily_df: pd.DataFrame, bar_date) -> Dict[str, float]:
    """Combine all static levels for a given trading day."""
    levels = {}
    levels.update(compute_prev_day_levels(daily_df, bar_date))
    levels.update(compute_weekly_levels(daily_df, bar_date))
    levels.update(compute_monthly_levels(daily_df, bar_date))
    return levels


def approximate_volume_profile(df: pd.DataFrame, start_idx: int, end_idx: int, n_bins: int = 50) -> Dict:
    """Approximate volume profile from bar data.
    Returns VPOC, VAH, VAL, TPOC.
    """
    window = df.iloc[start_idx:end_idx + 1]
    if len(window) < 5:
        return {}

    price_min = window["low"].min()
    price_max = window["high"].max()
    if price_max == price_min:
        return {}

    bin_size = (price_max - price_min) / n_bins
    bins = np.zeros(n_bins)
    time_bins = np.zeros(n_bins)

    vol = window["volume"].values
    highs = window["high"].values
    lows = window["low"].values
    closes = window["close"].values

    for j in range(len(window)):
        # Distribute bar's volume across price bins it touches
        lo_bin = max(0, int((lows[j] - price_min) / bin_size))
        hi_bin = min(n_bins - 1, int((highs[j] - price_min) / bin_size))
        if hi_bin == lo_bin:
            bins[lo_bin] += vol[j] if vol[j] > 0 else 1
            time_bins[lo_bin] += 1
        else:
            spread = hi_bin - lo_bin + 1
            per_bin = (vol[j] if vol[j] > 0 else 1) / spread
            for b in range(lo_bin, hi_bin + 1):
                bins[b] += per_bin
                time_bins[b] += 1.0 / spread

    # VPOC = price level with most volume
    vpoc_bin = np.argmax(bins)
    vpoc = price_min + (vpoc_bin + 0.5) * bin_size

    # TPOC = price level with most time
    tpoc_bin = np.argmax(time_bins)
    tpoc = price_min + (tpoc_bin + 0.5) * bin_size

    # VAH/VAL = expand from VPOC until 70% of total volume captured
    total_vol = bins.sum()
    target = total_vol * 0.70
    captured = bins[vpoc_bin]
    lo, hi = vpoc_bin, vpoc_bin

    while captured < target and (lo > 0 or hi < n_bins - 1):
        expand_lo = bins[lo - 1] if lo > 0 else 0
        expand_hi = bins[hi + 1] if hi < n_bins - 1 else 0
        if expand_lo >= expand_hi and lo > 0:
            lo -= 1
            captured += bins[lo]
        elif hi < n_bins - 1:
            hi += 1
            captured += bins[hi]
        else:
            lo -= 1
            captured += bins[lo]

    val = price_min + lo * bin_size
    vah = price_min + (hi + 1) * bin_size

    return {"vpoc": vpoc, "tpoc": tpoc, "vah": vah, "val": val}


def get_session_profile(df: pd.DataFrame, bar_idx: int) -> Dict:
    """Get volume profile for current session (today's bars up to bar_idx)."""
    bar_date = df.index[bar_idx].date()
    session_start = None
    for j in range(bar_idx, -1, -1):
        if df.index[j].date() != bar_date:
            session_start = j + 1
            break
    if session_start is None:
        session_start = 0
    if bar_idx - session_start < 10:
        return {}
    return approximate_volume_profile(df, session_start, bar_idx)


def get_prev_day_profile(df: pd.DataFrame, bar_idx: int) -> Dict:
    """Get volume profile for previous trading day."""
    bar_date = df.index[bar_idx].date()
    prev_bars = [(j, df.index[j].date()) for j in range(bar_idx) if df.index[j].date() < bar_date]
    if not prev_bars:
        return {}
    prev_date = prev_bars[-1][1]
    indices = [j for j, d in prev_bars if d == prev_date]
    if len(indices) < 10:
        return {}
    return approximate_volume_profile(df, indices[0], indices[-1])
