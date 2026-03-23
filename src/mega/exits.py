"""All exit strategies: trailing, fixed RR, level TP, partial exits, time cutoffs."""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple


def _pnl(entry, exit, direction):
    if direction == "long":
        return (exit - entry) / entry * 100
    return (entry - exit) / entry * 100


# ─── Exit Engines ───


def exit_trail(df, idx, direction, atr, sl_mult=1.0, trail_mult=0.75, max_bars=120,
               time_cutoff_hour=None, vwap_series=None, partial_at_vwap=False):
    """Trailing stop exit with optional time cutoff and VWAP partial.
    Returns dict with full trade details.
    """
    entry = df["close"].iloc[idx]
    sl_d = atr * sl_mult
    tr_d = atr * trail_mult
    mfe, mae = 0.0, 0.0
    partial_booked = False
    partial_pnl = 0.0

    if direction == "long":
        best = entry
        stop = entry - sl_d
    else:
        best = entry
        stop = entry + sl_d

    for j in range(1, max_bars + 1):
        ci = idx + j
        if ci >= len(df):
            break

        bar = df.iloc[ci]
        bar_time = df.index[ci]

        # Time cutoff
        if time_cutoff_hour is not None and bar_time.hour >= time_cutoff_hour:
            pnl = _pnl(entry, bar["close"], direction)
            if partial_booked:
                pnl = (partial_pnl + pnl) / 2
            return _result(entry, bar["close"], ci, pnl, mfe, mae, j, "TIME_CUTOFF", partial_booked)

        # VWAP partial exit
        if partial_at_vwap and not partial_booked and vwap_series is not None:
            vwap = vwap_series.iloc[ci] if ci < len(vwap_series) else np.nan
            if not np.isnan(vwap):
                if direction == "long" and bar["high"] >= vwap and entry < vwap:
                    partial_pnl = _pnl(entry, vwap, direction)
                    partial_booked = True
                elif direction == "short" and bar["low"] <= vwap and entry > vwap:
                    partial_pnl = _pnl(entry, vwap, direction)
                    partial_booked = True

        if direction == "long":
            if bar["low"] <= stop:
                pnl = _pnl(entry, stop, direction)
                if partial_booked:
                    pnl = (partial_pnl + pnl) / 2
                return _result(entry, stop, ci, pnl, mfe, min(mae, pnl), j, "TRAIL_SL", partial_booked)
            if bar["high"] > best:
                best = bar["high"]
                stop = max(stop, best - tr_d)
        else:
            if bar["high"] >= stop:
                pnl = _pnl(entry, stop, direction)
                if partial_booked:
                    pnl = (partial_pnl + pnl) / 2
                return _result(entry, stop, ci, pnl, mfe, min(mae, pnl), j, "TRAIL_SL", partial_booked)
            if bar["low"] < best:
                best = bar["low"]
                stop = min(stop, best + tr_d)

        pnl = _pnl(entry, bar["close"], direction)
        mfe = max(mfe, pnl)
        mae = min(mae, pnl)

    # Max bars exit
    ei = min(idx + max_bars, len(df) - 1)
    pnl = _pnl(entry, df["close"].iloc[ei], direction)
    if partial_booked:
        pnl = (partial_pnl + pnl) / 2
    return _result(entry, df["close"].iloc[ei], ei, pnl, mfe, mae, ei - idx, "MAX_BARS", partial_booked)


def exit_fixed_rr(df, idx, direction, atr, sl_mult=1.0, rr=2.0, max_bars=120,
                  time_cutoff_hour=None, vwap_series=None, partial_at_vwap=False):
    """Fixed risk:reward exit."""
    entry = df["close"].iloc[idx]
    sl_d = atr * sl_mult
    tp_d = atr * sl_mult * rr
    mfe, mae = 0.0, 0.0
    partial_booked = False
    partial_pnl = 0.0

    for j in range(1, max_bars + 1):
        ci = idx + j
        if ci >= len(df):
            break

        bar = df.iloc[ci]
        bar_time = df.index[ci]

        if time_cutoff_hour is not None and bar_time.hour >= time_cutoff_hour:
            pnl = _pnl(entry, bar["close"], direction)
            if partial_booked:
                pnl = (partial_pnl + pnl) / 2
            return _result(entry, bar["close"], ci, pnl, mfe, mae, j, "TIME_CUTOFF", partial_booked)

        if partial_at_vwap and not partial_booked and vwap_series is not None:
            vwap = vwap_series.iloc[ci] if ci < len(vwap_series) else np.nan
            if not np.isnan(vwap):
                if direction == "long" and bar["high"] >= vwap and entry < vwap:
                    partial_pnl = _pnl(entry, vwap, direction)
                    partial_booked = True
                elif direction == "short" and bar["low"] <= vwap and entry > vwap:
                    partial_pnl = _pnl(entry, vwap, direction)
                    partial_booked = True

        if direction == "long":
            if bar["low"] <= entry - sl_d:
                pnl = _pnl(entry, entry - sl_d, direction)
                if partial_booked:
                    pnl = (partial_pnl + pnl) / 2
                return _result(entry, entry - sl_d, ci, pnl, mfe, min(mae, pnl), j, "SL", partial_booked)
            if bar["high"] >= entry + tp_d:
                pnl = _pnl(entry, entry + tp_d, direction)
                if partial_booked:
                    pnl = (partial_pnl + pnl) / 2
                return _result(entry, entry + tp_d, ci, pnl, max(mfe, pnl), mae, j, "TP", partial_booked)
        else:
            if bar["high"] >= entry + sl_d:
                pnl = _pnl(entry, entry + sl_d, direction)
                if partial_booked:
                    pnl = (partial_pnl + pnl) / 2
                return _result(entry, entry + sl_d, ci, pnl, mfe, min(mae, pnl), j, "SL", partial_booked)
            if bar["low"] <= entry - tp_d:
                pnl = _pnl(entry, entry - tp_d, direction)
                if partial_booked:
                    pnl = (partial_pnl + pnl) / 2
                return _result(entry, entry - tp_d, ci, pnl, max(mfe, pnl), mae, j, "TP", partial_booked)

        pnl = _pnl(entry, bar["close"], direction)
        mfe = max(mfe, pnl)
        mae = min(mae, pnl)

    ei = min(idx + max_bars, len(df) - 1)
    pnl = _pnl(entry, df["close"].iloc[ei], direction)
    if partial_booked:
        pnl = (partial_pnl + pnl) / 2
    return _result(entry, df["close"].iloc[ei], ei, pnl, mfe, mae, ei - idx, "MAX_BARS", partial_booked)


def exit_level_tp(df, idx, direction, atr, levels_dict, sl_mult=1.0, max_bars=120,
                  time_cutoff_hour=None):
    """Take profit at the next key level in the profit direction."""
    entry = df["close"].iloc[idx]
    sl_d = atr * sl_mult
    mfe, mae = 0.0, 0.0

    # Find nearest TP level
    tp_price = None
    for name, val in levels_dict.items():
        if val is None or np.isnan(val):
            continue
        if direction == "long" and val > entry + atr * 0.5:
            if tp_price is None or val < tp_price:
                tp_price = val
        elif direction == "short" and val < entry - atr * 0.5:
            if tp_price is None or val > tp_price:
                tp_price = val

    if tp_price is None:
        # No level found, fall back to 2:1 RR
        return exit_fixed_rr(df, idx, direction, atr, sl_mult, 2.0, max_bars, time_cutoff_hour)

    for j in range(1, max_bars + 1):
        ci = idx + j
        if ci >= len(df):
            break

        bar = df.iloc[ci]
        bar_time = df.index[ci]

        if time_cutoff_hour is not None and bar_time.hour >= time_cutoff_hour:
            pnl = _pnl(entry, bar["close"], direction)
            return _result(entry, bar["close"], ci, pnl, mfe, mae, j, "TIME_CUTOFF", False)

        if direction == "long":
            if bar["low"] <= entry - sl_d:
                pnl = _pnl(entry, entry - sl_d, direction)
                return _result(entry, entry - sl_d, ci, pnl, mfe, min(mae, pnl), j, "SL", False)
            if bar["high"] >= tp_price:
                pnl = _pnl(entry, tp_price, direction)
                return _result(entry, tp_price, ci, pnl, max(mfe, pnl), mae, j, "LEVEL_TP", False)
        else:
            if bar["high"] >= entry + sl_d:
                pnl = _pnl(entry, entry + sl_d, direction)
                return _result(entry, entry + sl_d, ci, pnl, mfe, min(mae, pnl), j, "SL", False)
            if bar["low"] <= tp_price:
                pnl = _pnl(entry, tp_price, direction)
                return _result(entry, tp_price, ci, pnl, max(mfe, pnl), mae, j, "LEVEL_TP", False)

        pnl = _pnl(entry, bar["close"], direction)
        mfe = max(mfe, pnl)
        mae = min(mae, pnl)

    ei = min(idx + max_bars, len(df) - 1)
    pnl = _pnl(entry, df["close"].iloc[ei], direction)
    return _result(entry, df["close"].iloc[ei], ei, pnl, mfe, mae, ei - idx, "MAX_BARS", False)


def _result(entry, exit, exit_idx, pnl, mfe, mae, bars, reason, partial):
    return {
        "entry_price": entry,
        "exit_price": exit,
        "exit_idx": exit_idx,
        "pnl": pnl,
        "mfe": mfe,
        "mae": mae,
        "bars": bars,
        "reason": reason,
        "partial": partial,
    }
