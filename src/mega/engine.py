"""Mega backtest engine — all confluences, all exits, no bias.
Designed to test thousands of strategy permutations on Sierra tick data.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger("trading-monitor.mega")

IST_OFFSET = timedelta(hours=5, minutes=30)
SESSION_OPENS_IST = {"CME_Open": time(3, 30), "LDN_Close": time(0, 45)}


# ─── Data Structures ───

@dataclass
class Signal:
    bar_idx: int
    direction: str          # "long" / "short"
    div_type: str
    strength: str
    confluences: Dict[str, bool] = field(default_factory=dict)
    levels_near: List[str] = field(default_factory=list)
    atr: float = 0.0
    delta_at_signal: float = 0.0
    cum_delta_at_signal: float = 0.0
    vol_per_trade_at_signal: float = 0.0
    volume_ratio: float = 0.0


@dataclass
class Trade:
    entry_idx: int
    entry_price: float
    entry_time: datetime
    direction: str
    signal: Signal
    exit_price: float = 0.0
    exit_idx: int = 0
    exit_time: Optional[datetime] = None
    pnl_pct: float = 0.0
    bars_held: int = 0
    mfe: float = 0.0  # max favorable excursion
    mae: float = 0.0  # max adverse excursion
    exit_reason: str = ""
    partial_pnl: float = 0.0  # pnl from partial exit
    is_news_trade: bool = False


# ─── Precompute All Indicators ───

def precompute(df: pd.DataFrame, df_1m: Optional[pd.DataFrame] = None,
               daily_df: Optional[pd.DataFrame] = None) -> Dict:
    """Precompute all indicators once. Returns dict of arrays/series."""
    import pandas_ta as ta

    p = {}
    n = len(df)

    # RSI
    p["rsi"] = ta.rsi(df["close"], length=14)

    # ATR
    p["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    # VWAP (session reset)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].replace(0, np.nan).fillna(1)
    dates = df.index.date
    vwap = pd.Series(np.nan, index=df.index)
    for day in np.unique(dates):
        mask = dates == day
        cum_tpv = (tp[mask] * vol[mask]).cumsum()
        cum_vol = vol[mask].cumsum()
        vwap[mask] = cum_tpv / cum_vol
    p["vwap"] = vwap

    # Swing points for divergence (precompute highs/lows)
    p["swing_highs_3"] = _find_swings(df["high"].values, 3, "high")
    p["swing_lows_3"] = _find_swings(df["low"].values, 3, "low")
    p["swing_highs_5"] = _find_swings(df["high"].values, 5, "high")
    p["swing_lows_5"] = _find_swings(df["low"].values, 5, "low")

    p["rsi_swing_highs_3"] = _find_swings(p["rsi"].values, 3, "high") if p["rsi"] is not None else np.zeros(n, dtype=bool)
    p["rsi_swing_lows_3"] = _find_swings(p["rsi"].values, 3, "low") if p["rsi"] is not None else np.zeros(n, dtype=bool)
    p["rsi_swing_highs_5"] = _find_swings(p["rsi"].values, 5, "high") if p["rsi"] is not None else np.zeros(n, dtype=bool)
    p["rsi_swing_lows_5"] = _find_swings(p["rsi"].values, 5, "low") if p["rsi"] is not None else np.zeros(n, dtype=bool)

    # Volume average (20-bar rolling)
    p["vol_avg"] = df["volume"].rolling(20).mean()

    # Delta and cumulative delta (from main df if available)
    if "delta" in df.columns:
        p["delta"] = df["delta"]
        p["cum_delta"] = df["cum_delta"] if "cum_delta" in df.columns else df["delta"].cumsum()
    else:
        p["delta"] = pd.Series(0.0, index=df.index)
        p["cum_delta"] = pd.Series(0.0, index=df.index)

    # Volume per trade
    if "vol_per_trade" in df.columns:
        p["vpt"] = df["vol_per_trade"]
        p["vpt_avg"] = df["vol_per_trade"].rolling(20).mean()
    else:
        p["vpt"] = pd.Series(0.0, index=df.index)
        p["vpt_avg"] = pd.Series(0.0, index=df.index)

    # Wick ratios
    total_range = df["high"] - df["low"]
    body_top = df[["open", "close"]].max(axis=1)
    body_bot = df[["open", "close"]].min(axis=1)
    safe_range = total_range.replace(0, np.nan)
    p["upper_wick_ratio"] = (df["high"] - body_top) / safe_range
    p["lower_wick_ratio"] = (body_bot - df["low"]) / safe_range

    # Session open prices
    p["session_levels"] = _compute_session_levels(df)

    # Levels from daily data
    if daily_df is not None and len(daily_df) > 2:
        p["daily_levels"] = _compute_daily_levels(daily_df)
        p["std_pivots"] = _compute_pivots(daily_df, "std")
        p["fib_pivots"] = _compute_pivots(daily_df, "fib")
    else:
        p["daily_levels"] = {}
        p["std_pivots"] = {}
        p["fib_pivots"] = {}

    # Fib retracement levels (precomputed per bar would be too expensive,
    # we'll compute on-the-fly in signal detection)

    # VPOC / TPOC levels per session and prev day/week
    p["vpoc_tpoc"] = _compute_vpoc_tpoc_levels(df)

    return p


def _compute_vpoc_tpoc_levels(df, n_bins=50):
    """Compute VPOC and TPOC for prev day, prev week, prev month, and sessions (NY, London, Asia).
    Returns {date_str: {level_name: price}}.

    Sessions in UTC:
      Asia:   22:00 - 07:00 UTC (3:30 AM - 12:30 PM IST)
      London: 07:00 - 15:30 UTC (12:30 PM - 9:00 PM IST)
      NY:     12:00 - 21:00 UTC (5:30 PM - 2:30 AM IST next day)
    """
    IST_OFF = timedelta(hours=5, minutes=30)
    levels = {}
    dates = df.index.date
    unique_dates = np.unique(dates)

    def _profile(window_df):
        if len(window_df) < 10:
            return {}
        lo = window_df["low"].min()
        hi = window_df["high"].max()
        if hi == lo:
            return {}
        bs = (hi - lo) / n_bins
        vol_bins = np.zeros(n_bins)
        time_bins = np.zeros(n_bins)
        for _, row in window_df.iterrows():
            lb = max(0, int((row["low"] - lo) / bs))
            hb = min(n_bins - 1, int((row["high"] - lo) / bs))
            spread = max(1, hb - lb + 1)
            v = row["volume"] if row["volume"] > 0 else 1
            for b in range(lb, hb + 1):
                vol_bins[b] += v / spread
                time_bins[b] += 1.0 / spread
        vpoc = lo + (np.argmax(vol_bins) + 0.5) * bs
        tpoc = lo + (np.argmax(time_bins) + 0.5) * bs
        return {"vpoc": vpoc, "tpoc": tpoc}

    for i, date in enumerate(unique_dates):
        dk = str(date)
        levels[dk] = {}

        # Prev day profile
        if i > 0:
            prev_date = unique_dates[i - 1]
            prev_bars = df[dates == prev_date]
            prof = _profile(prev_bars)
            if prof:
                levels[dk]["pd_vpoc"] = prof["vpoc"]
                levels[dk]["pd_tpoc"] = prof["tpoc"]

        # Prev week profile (last 5 trading days)
        if i >= 5:
            week_dates = unique_dates[max(0, i - 5):i]
            week_mask = np.isin(dates, week_dates)
            prof = _profile(df[week_mask])
            if prof:
                levels[dk]["wk_vpoc"] = prof["vpoc"]
                levels[dk]["wk_tpoc"] = prof["tpoc"]

        # Prev month profile (last 22 trading days)
        if i >= 22:
            month_dates = unique_dates[max(0, i - 22):i]
            month_mask = np.isin(dates, month_dates)
            prof = _profile(df[month_mask])
            if prof:
                levels[dk]["mo_vpoc"] = prof["vpoc"]
                levels[dk]["mo_tpoc"] = prof["tpoc"]

        # Session profiles from PREVIOUS day
        if i > 0:
            prev_date = unique_dates[i - 1]
            prev_bars = df[dates == prev_date]
            if len(prev_bars) > 0:
                hours = prev_bars.index.hour

                # Asia session (22:00-07:00 UTC) = bars from prev day 22:00 + current day 00:00-07:00
                asia = prev_bars[(hours >= 22) | (hours < 7)]
                prof = _profile(asia)
                if prof:
                    levels[dk]["asia_vpoc"] = prof["vpoc"]
                    levels[dk]["asia_tpoc"] = prof["tpoc"]

                # London session (07:00-15:30 UTC)
                london = prev_bars[(hours >= 7) & (hours < 16)]
                prof = _profile(london)
                if prof:
                    levels[dk]["ldn_vpoc"] = prof["vpoc"]
                    levels[dk]["ldn_tpoc"] = prof["tpoc"]

                # NY session (12:00-21:00 UTC)
                ny = prev_bars[(hours >= 12) & (hours < 21)]
                prof = _profile(ny)
                if prof:
                    levels[dk]["ny_vpoc"] = prof["vpoc"]
                    levels[dk]["ny_tpoc"] = prof["tpoc"]

    return levels


def _find_swings(values, lookback, direction):
    """Mark swing highs or lows. Returns boolean array."""
    n = len(values)
    swings = np.zeros(n, dtype=bool)
    for i in range(lookback, n - lookback):
        if np.isnan(values[i]):
            continue
        if direction == "high":
            left = values[max(0, i - lookback):i]
            right = values[i + 1:i + lookback + 1]
            if len(left) > 0 and len(right) > 0:
                if values[i] >= np.nanmax(left) and values[i] >= np.nanmax(right):
                    swings[i] = True
        else:
            left = values[max(0, i - lookback):i]
            right = values[i + 1:i + lookback + 1]
            if len(left) > 0 and len(right) > 0:
                if values[i] <= np.nanmin(left) and values[i] <= np.nanmin(right):
                    swings[i] = True
    return swings


def _compute_session_levels(df):
    """Compute session open prices for each day."""
    levels = {}
    for idx in range(len(df)):
        ts = df.index[idx]
        ist_time = (ts + IST_OFFSET).time()
        dk = (ts + IST_OFFSET).strftime("%Y-%m-%d")
        for name, ot in SESSION_OPENS_IST.items():
            if abs((ist_time.hour * 60 + ist_time.minute) - (ot.hour * 60 + ot.minute)) <= 5:
                if dk not in levels:
                    levels[dk] = {}
                levels[dk][name] = df["close"].iloc[idx]
    return levels


def _compute_daily_levels(daily_df):
    """Compute prev day/week/month high/low per date."""
    levels = {}
    for i in range(1, len(daily_df)):
        date = daily_df.index[i].strftime("%Y-%m-%d")
        prev = daily_df.iloc[i - 1]
        levels[date] = {
            "pd_high": prev["high"], "pd_low": prev["low"],
            "pd_open": prev["open"], "pd_close": prev["close"],
        }
        # Weekly
        week_start = max(0, i - 5)
        week_data = daily_df.iloc[week_start:i]
        levels[date]["wk_high"] = week_data["high"].max()
        levels[date]["wk_low"] = week_data["low"].min()
        # Monthly
        month_start = max(0, i - 22)
        month_data = daily_df.iloc[month_start:i]
        levels[date]["mo_high"] = month_data["high"].max()
        levels[date]["mo_low"] = month_data["low"].min()
    return levels


def _compute_pivots(daily_df, mode="std"):
    """Compute pivot levels per date. Returns {date_str: {name: price}}."""
    pivots = {}
    for i in range(1, len(daily_df)):
        date = daily_df.index[i].strftime("%Y-%m-%d")
        prev = daily_df.iloc[i - 1]
        H, L, C = prev["high"], prev["low"], prev["close"]
        PP = (H + L + C) / 3
        R = H - L

        if mode == "std":
            pivots[date] = {
                "PP": PP, "R1": 2 * PP - L, "R2": PP + R, "R3": 2 * PP + R - L,
                "S1": 2 * PP - H, "S2": PP - R, "S3": 2 * PP - R - H,
            }
        else:  # fib
            pivots[date] = {
                "fPP": PP, "fR1": PP + 0.382 * R, "fR2": PP + 0.618 * R, "fR3": PP + R,
                "fS1": PP - 0.382 * R, "fS2": PP - 0.618 * R, "fS3": PP - R,
            }
    return pivots


# ─── Signal Detection ───

def detect_signals(df: pd.DataFrame, p: Dict, lookback: int = 3,
                   window: int = 200) -> List[Signal]:
    """Detect all RSI divergence signals with full confluence data.
    Does NOT filter — returns every divergence with its confluence flags.
    Filtering is done at strategy level.
    """
    signals = []
    n = len(df)
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    rsi = p["rsi"].values if p["rsi"] is not None else np.full(n, np.nan)

    sw_key = f"swing_lows_{lookback}"
    sh_key = f"swing_highs_{lookback}"
    rsl_key = f"rsi_swing_lows_{lookback}"
    rsh_key = f"rsi_swing_highs_{lookback}"

    price_lows = p[sw_key]
    price_highs = p[sh_key]
    rsi_lows = p[rsl_key]
    rsi_highs = p[rsh_key]

    last_signal_idx = -20

    for i in range(window, n):
        # Find most recent swing low (for bullish div) in window
        bull_sig = _check_bullish_div(i, lows, rsi, price_lows, rsi_lows, window)
        bear_sig = _check_bearish_div(i, highs, rsi, price_highs, rsi_highs, window)

        for div_type, div_strength, is_hidden in [bull_sig, bear_sig]:
            if div_type is None:
                continue
            if i - last_signal_idx < 5:
                continue

            direction = "long" if "bullish" in div_type else "short"
            atr = p["atr"].iloc[i] if not np.isnan(p["atr"].iloc[i]) else 0
            if atr < 0.00050:  # Match Sierra DH_Scanner v11 min ATR (5 pips)
                continue

            last_signal_idx = i

            # Compute ALL confluences for this signal
            conf = {}
            price = closes[i]
            date_key = (df.index[i] + IST_OFFSET).strftime("%Y-%m-%d")
            ist_time = (df.index[i] + IST_OFFSET).time()
            ist_hour = ist_time.hour + ist_time.minute / 60

            # Next candle confirms
            if i + 1 < n:
                nxt_green = closes[i + 1] > df["open"].iloc[i + 1]
                nxt_red = closes[i + 1] < df["open"].iloc[i + 1]
                nxt_doji = abs(closes[i + 1] - df["open"].iloc[i + 1]) / max(highs[i + 1] - lows[i + 1], 1e-10) < 0.1
                conf["next_candle"] = (nxt_green if direction == "long" else nxt_red)
                # Doji rule: check 3rd candle
                if nxt_doji and i + 2 < n:
                    c3_green = closes[i + 2] > df["open"].iloc[i + 2]
                    c3_red = closes[i + 2] < df["open"].iloc[i + 2]
                    conf["next_candle_doji"] = (c3_green if direction == "long" else c3_red)
                else:
                    conf["next_candle_doji"] = False
            else:
                conf["next_candle"] = False
                conf["next_candle_doji"] = False

            # Wick rejection (last 3 bars)
            if direction == "long":
                conf["wick_rejection"] = bool(p["lower_wick_ratio"].iloc[max(0, i - 4):i + 1].max() > 0.55)
            else:
                conf["wick_rejection"] = bool(p["upper_wick_ratio"].iloc[max(0, i - 4):i + 1].max() > 0.55)

            # LL/HH confirmation
            if i >= 3:
                recent = df.iloc[i - 3:i + 1]
                if direction == "long":
                    conf["ll_hh"] = bool(lows[i] <= recent["low"].min())
                else:
                    conf["ll_hh"] = bool(highs[i] >= recent["high"].max())
            else:
                conf["ll_hh"] = True

            # Volume spike
            vol_avg = p["vol_avg"].iloc[i]
            if not np.isnan(vol_avg) and vol_avg > 0:
                vol_ratio = df["volume"].iloc[i] / vol_avg
                conf["volume_spike_1_3"] = vol_ratio >= 1.3
                conf["volume_spike_1_5"] = vol_ratio >= 1.5
            else:
                vol_ratio = 1.0
                conf["volume_spike_1_3"] = False
                conf["volume_spike_1_5"] = False

            # Delta divergence
            delta = p["delta"].iloc[i]
            cum_d = p["cum_delta"].iloc[i]
            if direction == "long":
                conf["delta_positive"] = delta > 0  # Buying absorbing on down move
                conf["cum_delta_up"] = cum_d > p["cum_delta"].iloc[max(0, i - 10)]
            else:
                conf["delta_negative"] = delta < 0
                conf["cum_delta_down"] = cum_d < p["cum_delta"].iloc[max(0, i - 10)]

            # Volume per trade (whale detection)
            vpt = p["vpt"].iloc[i]
            vpt_avg = p["vpt_avg"].iloc[i]
            if not np.isnan(vpt_avg) and vpt_avg > 0:
                conf["whale_activity"] = vpt > 2.0 * vpt_avg
            else:
                conf["whale_activity"] = False

            # VWAP direction
            vwap = p["vwap"].iloc[i]
            if not np.isnan(vwap):
                conf["vwap_long_below"] = price < vwap and direction == "long"
                conf["vwap_short_above"] = price > vwap and direction == "short"
                conf["vwap_aligned"] = conf.get("vwap_long_below", False) or conf.get("vwap_short_above", False)
            else:
                conf["vwap_aligned"] = False

            # Level checks
            threshold = atr * 0.5
            levels_near = []

            # Standard pivots
            if date_key in p["std_pivots"]:
                for name, lvl in p["std_pivots"][date_key].items():
                    if abs(price - lvl) <= threshold:
                        levels_near.append(f"std_{name}")

            # Fib pivots
            if date_key in p["fib_pivots"]:
                for name, lvl in p["fib_pivots"][date_key].items():
                    if abs(price - lvl) <= threshold:
                        levels_near.append(f"fib_{name}")

            # Session levels
            for dk in [date_key, (df.index[i] + IST_OFFSET - timedelta(days=1)).strftime("%Y-%m-%d")]:
                if dk in p["session_levels"]:
                    for name, lvl in p["session_levels"][dk].items():
                        if abs(price - lvl) <= threshold:
                            levels_near.append(f"sess_{name}")

            # Daily levels
            if date_key in p["daily_levels"]:
                dl = p["daily_levels"][date_key]
                for name in ["pd_high", "pd_low", "wk_high", "wk_low", "mo_high", "mo_low"]:
                    if name in dl and abs(price - dl[name]) <= threshold:
                        levels_near.append(name)

            # VWAP as level
            if not np.isnan(vwap) and abs(price - vwap) <= threshold:
                levels_near.append("vwap")

            # VPOC / TPOC levels (prev day, week, month, sessions)
            if date_key in p.get("vpoc_tpoc", {}):
                vt = p["vpoc_tpoc"][date_key]
                for name in ["pd_vpoc", "pd_tpoc", "wk_vpoc", "wk_tpoc", "mo_vpoc", "mo_tpoc",
                             "asia_vpoc", "asia_tpoc", "ldn_vpoc", "ldn_tpoc", "ny_vpoc", "ny_tpoc"]:
                    if name in vt and abs(price - vt[name]) <= threshold:
                        levels_near.append(name)

            # Fib retracement check
            fib_hit = _check_fib_retracement(df, i, atr, direction)
            if fib_hit:
                conf["at_fib_retracement"] = True
                levels_near.append(fib_hit)
            else:
                conf["at_fib_retracement"] = False

            conf["at_any_pivot"] = any("std_" in l or "fib_" in l for l in levels_near)
            conf["at_session_level"] = any("sess_" in l for l in levels_near)
            conf["at_daily_level"] = any(l in ["pd_high", "pd_low", "wk_high", "wk_low", "mo_high", "mo_low"] for l in levels_near)
            conf["at_any_level"] = len(levels_near) > 0
            conf["at_strong_level"] = len(levels_near) >= 2  # Multiple levels = strong confluence

            # Trading window (IST hours)
            conf["window_8_14"] = 8 <= ist_hour < 14
            conf["window_8_21"] = 8 <= ist_hour < 21
            conf["window_10_18"] = 10 <= ist_hour < 18
            conf["window_10_21"] = 10 <= ist_hour < 21

            signals.append(Signal(
                bar_idx=i, direction=direction,
                div_type=div_type, strength=div_strength,
                confluences=conf, levels_near=levels_near,
                atr=atr, delta_at_signal=delta,
                cum_delta_at_signal=cum_d,
                vol_per_trade_at_signal=vpt,
                volume_ratio=vol_ratio,
            ))

    return signals


def _check_bullish_div(i, lows, rsi, price_low_flags, rsi_low_flags, window):
    """Check for bullish divergence (regular + hidden) at bar i."""
    # Find two most recent swing lows in price
    start = max(0, i - window)
    p_lows = [(j, lows[j]) for j in range(start, i + 1) if price_low_flags[j]]
    r_lows = [(j, rsi[j]) for j in range(start, i + 1) if rsi_low_flags[j] and not np.isnan(rsi[j])]

    if len(p_lows) < 2 or len(r_lows) < 2:
        return (None, None, False)

    # Check if recent (within 30 bars of current)
    if i - p_lows[-1][0] > 30:
        return (None, None, False)

    p_a, p_b = p_lows[-2], p_lows[-1]
    # Find RSI values at those price swing points (closest RSI swing)
    r_a = _closest_rsi_swing(p_a[0], r_lows)
    r_b = _closest_rsi_swing(p_b[0], r_lows)

    if r_a is None or r_b is None:
        return (None, None, False)

    # Bars apart check
    bars_apart = p_b[0] - p_a[0]
    if bars_apart < 5 or bars_apart > 80:
        return (None, None, False)

    # Regular bullish: price lower low, RSI higher low
    if p_b[1] < p_a[1] and r_b[1] > r_a[1]:
        strength = "strong" if rsi[p_b[0]] < 35 else "moderate" if rsi[p_b[0]] < 45 else "weak"
        return ("regular_bullish", strength, False)

    # Hidden bullish: price higher low, RSI lower low
    if p_b[1] > p_a[1] and r_b[1] < r_a[1]:
        return ("hidden_bullish", "moderate", True)

    return (None, None, False)


def _check_bearish_div(i, highs, rsi, price_high_flags, rsi_high_flags, window):
    """Check for bearish divergence (regular + hidden) at bar i."""
    start = max(0, i - window)
    p_highs = [(j, highs[j]) for j in range(start, i + 1) if price_high_flags[j]]
    r_highs = [(j, rsi[j]) for j in range(start, i + 1) if rsi_high_flags[j] and not np.isnan(rsi[j])]

    if len(p_highs) < 2 or len(r_highs) < 2:
        return (None, None, False)

    if i - p_highs[-1][0] > 30:
        return (None, None, False)

    p_a, p_b = p_highs[-2], p_highs[-1]
    r_a = _closest_rsi_swing(p_a[0], r_highs)
    r_b = _closest_rsi_swing(p_b[0], r_highs)

    if r_a is None or r_b is None:
        return (None, None, False)

    bars_apart = p_b[0] - p_a[0]
    if bars_apart < 5 or bars_apart > 80:
        return (None, None, False)

    # Regular bearish: price higher high, RSI lower high
    if p_b[1] > p_a[1] and r_b[1] < r_a[1]:
        strength = "strong" if rsi[p_b[0]] > 65 else "moderate" if rsi[p_b[0]] > 55 else "weak"
        return ("regular_bearish", strength, False)

    # Hidden bearish: price lower high, RSI higher high
    if p_b[1] < p_a[1] and r_b[1] > r_a[1]:
        return ("hidden_bearish", "moderate", True)

    return (None, None, False)


def _closest_rsi_swing(price_idx, rsi_swings, max_dist=10):
    """Find the RSI swing point closest to a price swing point."""
    best = None
    best_dist = max_dist + 1
    for idx, val in rsi_swings:
        dist = abs(idx - price_idx)
        if dist < best_dist:
            best = (idx, val)
            best_dist = dist
    return best if best_dist <= max_dist else None


def _check_fib_retracement(df, bar_idx, atr, direction, lookback=100):
    """Check if price is at a fib retracement level."""
    if bar_idx < 20:
        return None
    start = max(0, bar_idx - lookback)
    window = df.iloc[start:bar_idx + 1]

    hh_pos = window["high"].idxmax()
    ll_pos = window["low"].idxmin()

    hh_iloc = df.index.get_loc(hh_pos)
    ll_iloc = df.index.get_loc(ll_pos)

    if hh_iloc < ll_iloc:
        swing_dir = "down"
        high_val = df["high"].iloc[hh_iloc]
        low_val = df["low"].iloc[ll_iloc]
    else:
        swing_dir = "up"
        high_val = df["high"].iloc[hh_iloc]
        low_val = df["low"].iloc[ll_iloc]

    rng = high_val - low_val
    if rng < atr * 3:  # Swing must be meaningful
        return None

    price = df["close"].iloc[bar_idx]
    threshold = atr * 0.5

    fib_levels = [0.236, 0.382, 0.500, 0.618, 0.700, 0.786, 0.810]

    for fib in fib_levels:
        if swing_dir == "down":
            level = low_val + fib * rng
            # For down swing, retracement UP to fib = sell zone
            if abs(price - level) <= threshold:
                if direction == "short":
                    return f"fib_{fib:.1%}"
        else:
            level = high_val - fib * rng
            # For up swing, retracement DOWN to fib = buy zone
            if abs(price - level) <= threshold:
                if direction == "long":
                    return f"fib_{fib:.1%}"

    return None


# ─── DXY Confirmation (precomputed) ───

def compute_dxy_signals(dxy_df: pd.DataFrame, dxy_rsi: pd.Series) -> Dict:
    """Precompute DXY confirmation signals."""
    n = len(dxy_df)
    d = {}

    # Momentum (10-bar)
    d["momentum_down"] = dxy_df["close"].values < dxy_df["close"].shift(10).values
    d["momentum_up"] = dxy_df["close"].values > dxy_df["close"].shift(10).values

    # RSI direction (5-bar)
    rsi_vals = dxy_rsi.values
    d["rsi_falling"] = np.zeros(n, dtype=bool)
    d["rsi_rising"] = np.zeros(n, dtype=bool)
    for i in range(5, n):
        if not np.isnan(rsi_vals[i]) and not np.isnan(rsi_vals[i - 5]):
            d["rsi_falling"][i] = rsi_vals[i] < rsi_vals[i - 5]
            d["rsi_rising"][i] = rsi_vals[i] > rsi_vals[i - 5]

    # RSI extreme
    d["rsi_overbought"] = np.array([v > 65 if not np.isnan(v) else False for v in rsi_vals])
    d["rsi_oversold"] = np.array([v < 35 if not np.isnan(v) else False for v in rsi_vals])

    return d


def dxy_confirms(dxy_signals: Dict, bar_idx: int, direction: str, mode: str) -> bool:
    """Check if DXY confirms the 6E trade direction."""
    if bar_idx >= len(dxy_signals.get("momentum_down", [])):
        return False

    if mode == "none":
        return True

    checks = {
        "momentum": dxy_signals["momentum_down"][bar_idx] if direction == "long" else dxy_signals["momentum_up"][bar_idx],
        "rsi": dxy_signals["rsi_falling"][bar_idx] if direction == "long" else dxy_signals["rsi_rising"][bar_idx],
        "rsi_extreme": dxy_signals["rsi_overbought"][bar_idx] if direction == "long" else dxy_signals["rsi_oversold"][bar_idx],
    }

    if mode in checks:
        return bool(checks[mode])
    elif mode == "any":
        return any(checks.values())
    elif mode == "any2":
        return sum(checks.values()) >= 2
    return False


# ─── Exit Strategies ───

def execute_exit(df, entry_idx, direction, atr, exit_config, vwap=None):
    """Execute an exit strategy. Returns (exit_price, exit_idx, pnl, mfe, mae, bars, reason, partial_pnl)."""
    mode = exit_config["mode"]
    max_bars = exit_config.get("max_bars", 120)
    time_cutoff_ist = exit_config.get("time_cutoff_ist", None)  # e.g., 21 for 9PM IST

    entry_price = df["close"].iloc[entry_idx]
    n = len(df)
    mfe, mae = 0.0, 0.0
    partial_pnl = 0.0
    partial_done = False
    partial_mode = exit_config.get("partial", None)  # "vwap_50", "rr1_50", "level_50"
    remaining_qty = 1.0

    if mode == "trail":
        sl_mult = exit_config.get("sl_mult", 1.0)
        trail_mult = exit_config.get("trail_mult", 0.75)
        return _exit_trail(df, entry_idx, direction, atr, sl_mult, trail_mult,
                          max_bars, time_cutoff_ist, partial_mode, vwap)

    elif mode == "atr_rr":
        sl_mult = exit_config.get("sl_mult", 1.5)
        rr = exit_config.get("rr", 2.0)
        return _exit_atr_rr(df, entry_idx, direction, atr, sl_mult, rr,
                           max_bars, time_cutoff_ist, partial_mode, vwap)

    elif mode == "next_level":
        sl_mult = exit_config.get("sl_mult", 1.0)
        target_level = exit_config.get("target_level", 0)
        return _exit_next_level(df, entry_idx, direction, atr, sl_mult, target_level,
                               max_bars, time_cutoff_ist)

    # Fallback: fixed bars
    hold = exit_config.get("hold_bars", 20)
    return _exit_fixed(df, entry_idx, direction, hold, time_cutoff_ist)


def _pnl(entry, exit, direction):
    if direction == "long":
        return (exit - entry) / entry * 100
    return (entry - exit) / entry * 100


def _check_time_cutoff(df, idx, cutoff_ist):
    """Check if we've hit the IST time cutoff."""
    if cutoff_ist is None:
        return False
    ist_hour = (df.index[idx] + IST_OFFSET).hour
    return ist_hour >= cutoff_ist


def _exit_trail(df, idx, direction, atr, sl_m, tr_m, max_bars, cutoff, partial_mode, vwap):
    entry = df["close"].iloc[idx]
    sl_d = atr * sl_m
    tr_d = atr * tr_m
    mfe, mae = 0.0, 0.0
    partial_pnl = 0.0
    partial_done = False
    qty = 1.0

    best = entry
    stop = entry - sl_d if direction == "long" else entry + sl_d

    for j in range(1, max_bars + 1):
        ci = idx + j
        if ci >= len(df):
            break

        if _check_time_cutoff(df, ci, cutoff):
            p = _pnl(entry, df["close"].iloc[ci], direction)
            return df["close"].iloc[ci], ci, p * qty + partial_pnl, mfe, mae, j, "TIME_CUTOFF", partial_pnl

        bar = df.iloc[ci]

        if direction == "long":
            if bar["low"] <= stop:
                p = _pnl(entry, stop, direction)
                return stop, ci, p * qty + partial_pnl, mfe, min(mae, p), j, "TRAIL_SL", partial_pnl
            if bar["high"] > best:
                best = bar["high"]
                stop = max(stop, best - tr_d)
            # Partial at VWAP
            if partial_mode == "vwap_50" and not partial_done and vwap is not None and ci < len(vwap):
                v = vwap.iloc[ci]
                if not np.isnan(v) and entry < v and bar["high"] >= v:
                    partial_pnl = _pnl(entry, v, direction) * 0.5
                    qty = 0.5
                    partial_done = True
            # Partial at 1:1
            if partial_mode == "rr1_50" and not partial_done:
                if bar["high"] >= entry + sl_d:
                    partial_pnl = _pnl(entry, entry + sl_d, direction) * 0.5
                    qty = 0.5
                    partial_done = True
                    stop = max(stop, entry)  # Move to breakeven
        else:
            if bar["high"] >= stop:
                p = _pnl(entry, stop, direction)
                return stop, ci, p * qty + partial_pnl, mfe, min(mae, p), j, "TRAIL_SL", partial_pnl
            if bar["low"] < best:
                best = bar["low"]
                stop = min(stop, best + tr_d)
            if partial_mode == "vwap_50" and not partial_done and vwap is not None and ci < len(vwap):
                v = vwap.iloc[ci]
                if not np.isnan(v) and entry > v and bar["low"] <= v:
                    partial_pnl = _pnl(entry, v, direction) * 0.5
                    qty = 0.5
                    partial_done = True
            if partial_mode == "rr1_50" and not partial_done:
                if bar["low"] <= entry - sl_d:
                    partial_pnl = _pnl(entry, entry - sl_d, direction) * 0.5
                    qty = 0.5
                    partial_done = True
                    stop = min(stop, entry)

        p = _pnl(entry, bar["close"], direction)
        mfe = max(mfe, p)
        mae = min(mae, p)

    ei = min(idx + max_bars, len(df) - 1)
    p = _pnl(entry, df["close"].iloc[ei], direction)
    return df["close"].iloc[ei], ei, p * qty + partial_pnl, mfe, mae, ei - idx, "TIME", partial_pnl


def _exit_atr_rr(df, idx, direction, atr, sl_m, rr, max_bars, cutoff, partial_mode, vwap):
    entry = df["close"].iloc[idx]
    sl_d = atr * sl_m
    tp_d = atr * sl_m * rr
    mfe, mae = 0.0, 0.0
    partial_pnl = 0.0
    partial_done = False
    qty = 1.0

    for j in range(1, max_bars + 1):
        ci = idx + j
        if ci >= len(df):
            break

        if _check_time_cutoff(df, ci, cutoff):
            p = _pnl(entry, df["close"].iloc[ci], direction)
            return df["close"].iloc[ci], ci, p * qty + partial_pnl, mfe, mae, j, "TIME_CUTOFF", partial_pnl

        bar = df.iloc[ci]

        if direction == "long":
            if bar["low"] <= entry - sl_d:
                p = -sl_d / entry * 100
                return entry - sl_d, ci, p * qty + partial_pnl, mfe, min(mae, p), j, "SL", partial_pnl
            if bar["high"] >= entry + tp_d:
                p = tp_d / entry * 100
                return entry + tp_d, ci, p * qty + partial_pnl, max(mfe, p), mae, j, "TP", partial_pnl
            if partial_mode == "vwap_50" and not partial_done and vwap is not None and ci < len(vwap):
                v = vwap.iloc[ci]
                if not np.isnan(v) and entry < v and bar["high"] >= v:
                    partial_pnl = _pnl(entry, v, direction) * 0.5
                    qty = 0.5
                    partial_done = True
            if partial_mode == "rr1_50" and not partial_done:
                if bar["high"] >= entry + sl_d:
                    partial_pnl = _pnl(entry, entry + sl_d, direction) * 0.5
                    qty = 0.5
                    partial_done = True
        else:
            if bar["high"] >= entry + sl_d:
                p = -sl_d / entry * 100
                return entry + sl_d, ci, p * qty + partial_pnl, mfe, min(mae, p), j, "SL", partial_pnl
            if bar["low"] <= entry - tp_d:
                p = tp_d / entry * 100
                return entry - tp_d, ci, p * qty + partial_pnl, max(mfe, p), mae, j, "TP", partial_pnl
            if partial_mode == "vwap_50" and not partial_done and vwap is not None and ci < len(vwap):
                v = vwap.iloc[ci]
                if not np.isnan(v) and entry > v and bar["low"] <= v:
                    partial_pnl = _pnl(entry, v, direction) * 0.5
                    qty = 0.5
                    partial_done = True
            if partial_mode == "rr1_50" and not partial_done:
                if bar["low"] <= entry - sl_d:
                    partial_pnl = _pnl(entry, entry - sl_d, direction) * 0.5
                    qty = 0.5
                    partial_done = True

        p = _pnl(entry, bar["close"], direction)
        mfe = max(mfe, p)
        mae = min(mae, p)

    ei = min(idx + max_bars, len(df) - 1)
    p = _pnl(entry, df["close"].iloc[ei], direction)
    return df["close"].iloc[ei], ei, p * qty + partial_pnl, mfe, mae, ei - idx, "TIME", partial_pnl


def _exit_next_level(df, idx, direction, atr, sl_m, target, max_bars, cutoff):
    """Exit at the next key level (passed as target price)."""
    entry = df["close"].iloc[idx]
    sl_d = atr * sl_m
    mfe, mae = 0.0, 0.0

    if target == 0:
        return _exit_trail(df, idx, direction, atr, sl_m, 0.75, max_bars, cutoff, None, None)

    for j in range(1, max_bars + 1):
        ci = idx + j
        if ci >= len(df):
            break
        if _check_time_cutoff(df, ci, cutoff):
            p = _pnl(entry, df["close"].iloc[ci], direction)
            return df["close"].iloc[ci], ci, p, mfe, mae, j, "TIME_CUTOFF", 0

        bar = df.iloc[ci]
        if direction == "long":
            if bar["low"] <= entry - sl_d:
                return entry - sl_d, ci, -sl_d / entry * 100, mfe, mae, j, "SL", 0
            if bar["high"] >= target:
                p = _pnl(entry, target, direction)
                return target, ci, p, max(mfe, p), mae, j, "LEVEL_TP", 0
        else:
            if bar["high"] >= entry + sl_d:
                return entry + sl_d, ci, -sl_d / entry * 100, mfe, mae, j, "SL", 0
            if bar["low"] <= target:
                p = _pnl(entry, target, direction)
                return target, ci, p, max(mfe, p), mae, j, "LEVEL_TP", 0

        p = _pnl(entry, bar["close"], direction)
        mfe = max(mfe, p)
        mae = min(mae, p)

    ei = min(idx + max_bars, len(df) - 1)
    p = _pnl(entry, df["close"].iloc[ei], direction)
    return df["close"].iloc[ei], ei, p, mfe, mae, ei - idx, "TIME", 0


def _exit_fixed(df, idx, direction, hold, cutoff):
    entry = df["close"].iloc[idx]
    mfe, mae = 0.0, 0.0
    for j in range(1, hold + 1):
        ci = idx + j
        if ci >= len(df):
            break
        if _check_time_cutoff(df, ci, cutoff):
            p = _pnl(entry, df["close"].iloc[ci], direction)
            return df["close"].iloc[ci], ci, p, mfe, mae, j, "TIME_CUTOFF", 0
        p = _pnl(entry, df["close"].iloc[ci], direction)
        mfe = max(mfe, p)
        mae = min(mae, p)
    ei = min(idx + hold, len(df) - 1)
    p = _pnl(entry, df["close"].iloc[ei], direction)
    return df["close"].iloc[ei], ei, p, mfe, mae, ei - idx, "FIXED", 0
