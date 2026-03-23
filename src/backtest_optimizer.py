"""Comprehensive backtest optimizer for RSI divergence strategy on 5m data.
Tests multiple exit strategies, filters, and R:R combinations.
"""

import sys
sys.path.insert(0, ".")

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from itertools import product
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config.instruments import INSTRUMENTS, TimeframeConfig
from config.settings import Settings
from src.analysis.divergence import detect_divergences
from src.analysis.pivots import calculate_pivot_levels, check_pivot_proximity
from src.analysis.rsi import calculate_atr, calculate_rsi
from src.data.tv_fetcher import DataFetcher

logging.basicConfig(level=logging.WARNING)
console = Console()

# IST offset
IST = timezone(timedelta(hours=5, minutes=30))

# Session open times in IST (user-specified key levels)
SESSION_OPENS_IST = {
    "CME_Open": time(3, 30),    # 3:30 AM IST = 6:00 PM ET = CME futures new day
    "LDN_Close": time(0, 45),   # 12:45 AM IST = 7:15 PM UTC prev day
}


@dataclass
class Trade:
    entry_time: datetime
    entry_price: float
    direction: str
    div_type: str
    strength: str
    at_pivot: bool
    at_session_level: bool
    session_level_name: str
    atr_at_entry: float
    # Exit info
    exit_price: float = 0.0
    exit_time: Optional[datetime] = None
    pnl_pct: float = 0.0
    bars_held: int = 0
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    exit_reason: str = ""


@dataclass
class StratResult:
    name: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl_pct: float
    avg_pnl_pct: float
    profit_factor: float
    avg_winner: float
    avg_loser: float
    max_win: float
    max_loss: float
    avg_rr: float
    avg_bars: float
    pivot_trades: int
    pivot_wr: float
    session_trades: int
    session_wr: float


# ─── Key Level Detection ───


def get_session_open_prices(df: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    """For each trading day, find the price at session open times.
    Returns {date_str: {level_name: price}}.
    """
    levels = {}
    for idx, row in df.iterrows():
        ts = idx
        if hasattr(ts, 'tz_localize') and ts.tzinfo is None:
            # Assume timestamps are local IST (from tvdatafeed)
            ist_time = ts.time()
        else:
            ist_time = ts.time()

        date_key = ts.strftime("%Y-%m-%d")

        for level_name, open_time in SESSION_OPENS_IST.items():
            # Match within 5-min window
            t_min = (open_time.hour * 60 + open_time.minute)
            ts_min = (ist_time.hour * 60 + ist_time.minute)
            if abs(ts_min - t_min) <= 5:
                if date_key not in levels:
                    levels[date_key] = {}
                levels[date_key][level_name] = row["close"]

    return levels


def check_session_level_proximity(
    price: float, timestamp, session_levels: Dict, atr: float, multiplier: float = 0.5
) -> Tuple[bool, str]:
    """Check if price is near a session open level."""
    date_key = timestamp.strftime("%Y-%m-%d")
    # Also check previous day
    prev_key = (timestamp - timedelta(days=1)).strftime("%Y-%m-%d")

    threshold = atr * multiplier

    for dk in [date_key, prev_key]:
        if dk in session_levels:
            for name, level_price in session_levels[dk].items():
                if abs(price - level_price) <= threshold:
                    return True, f"{name}({level_price:.5f})"

    return False, ""


# ─── Exit Strategies ───


def exit_fixed_bars(df, entry_idx, direction, hold_bars, **kw):
    """Exit after fixed number of bars."""
    trades_data = []
    max_fav = 0.0
    max_adv = 0.0
    entry_price = df["close"].iloc[entry_idx]

    for j in range(1, hold_bars + 1):
        if entry_idx + j >= len(df):
            break
        p = df["close"].iloc[entry_idx + j]
        pnl = _calc_pnl(entry_price, p, direction)
        max_fav = max(max_fav, pnl)
        max_adv = min(max_adv, pnl)

    exit_idx = min(entry_idx + hold_bars, len(df) - 1)
    exit_price = df["close"].iloc[exit_idx]
    pnl = _calc_pnl(entry_price, exit_price, direction)
    return exit_price, exit_idx, pnl, max_fav, max_adv, f"bars={hold_bars}"


def exit_atr_sltp(df, entry_idx, direction, atr, sl_mult, tp_mult, max_bars=60, **kw):
    """Exit on ATR-based SL or TP, whichever hits first."""
    entry_price = df["close"].iloc[entry_idx]
    sl_dist = atr * sl_mult
    tp_dist = atr * tp_mult
    max_fav = 0.0
    max_adv = 0.0

    for j in range(1, max_bars + 1):
        if entry_idx + j >= len(df):
            break
        bar = df.iloc[entry_idx + j]

        if direction == "long":
            # Check SL (low touches)
            if bar["low"] <= entry_price - sl_dist:
                pnl = -sl_mult * atr / entry_price * 100
                return entry_price - sl_dist, entry_idx + j, pnl, max_fav, min(max_adv, pnl), "SL"
            # Check TP (high touches)
            if bar["high"] >= entry_price + tp_dist:
                pnl = tp_mult * atr / entry_price * 100
                return entry_price + tp_dist, entry_idx + j, pnl, max(max_fav, pnl), max_adv, "TP"
            pnl = _calc_pnl(entry_price, bar["close"], direction)
        else:
            if bar["high"] >= entry_price + sl_dist:
                pnl = -sl_mult * atr / entry_price * 100
                return entry_price + sl_dist, entry_idx + j, pnl, max_fav, min(max_adv, pnl), "SL"
            if bar["low"] <= entry_price - tp_dist:
                pnl = tp_mult * atr / entry_price * 100
                return entry_price - tp_dist, entry_idx + j, pnl, max(max_fav, pnl), max_adv, "TP"
            pnl = _calc_pnl(entry_price, bar["close"], direction)

        max_fav = max(max_fav, pnl)
        max_adv = min(max_adv, pnl)

    # Time exit
    exit_idx = min(entry_idx + max_bars, len(df) - 1)
    exit_price = df["close"].iloc[exit_idx]
    pnl = _calc_pnl(entry_price, exit_price, direction)
    return exit_price, exit_idx, pnl, max_fav, max_adv, "TIME"


def exit_trail_sl(df, entry_idx, direction, atr, sl_mult, trail_mult, max_bars=60, **kw):
    """Trailing stop loss. Initial SL = sl_mult*ATR, trail by trail_mult*ATR."""
    entry_price = df["close"].iloc[entry_idx]
    sl_dist = atr * sl_mult
    trail_dist = atr * trail_mult
    max_fav = 0.0
    max_adv = 0.0

    if direction == "long":
        best_price = entry_price
        stop = entry_price - sl_dist
        for j in range(1, max_bars + 1):
            if entry_idx + j >= len(df):
                break
            bar = df.iloc[entry_idx + j]
            if bar["low"] <= stop:
                pnl = _calc_pnl(entry_price, stop, direction)
                return stop, entry_idx + j, pnl, max_fav, min(max_adv, pnl), "TRAIL_SL"
            if bar["high"] > best_price:
                best_price = bar["high"]
                stop = max(stop, best_price - trail_dist)
            pnl = _calc_pnl(entry_price, bar["close"], direction)
            max_fav = max(max_fav, pnl)
            max_adv = min(max_adv, pnl)
    else:
        best_price = entry_price
        stop = entry_price + sl_dist
        for j in range(1, max_bars + 1):
            if entry_idx + j >= len(df):
                break
            bar = df.iloc[entry_idx + j]
            if bar["high"] >= stop:
                pnl = _calc_pnl(entry_price, stop, direction)
                return stop, entry_idx + j, pnl, max_fav, min(max_adv, pnl), "TRAIL_SL"
            if bar["low"] < best_price:
                best_price = bar["low"]
                stop = min(stop, best_price + trail_dist)
            pnl = _calc_pnl(entry_price, bar["close"], direction)
            max_fav = max(max_fav, pnl)
            max_adv = min(max_adv, pnl)

    exit_idx = min(entry_idx + max_bars, len(df) - 1)
    exit_price = df["close"].iloc[exit_idx]
    pnl = _calc_pnl(entry_price, exit_price, direction)
    return exit_price, exit_idx, pnl, max_fav, max_adv, "TIME"


def _calc_pnl(entry, exit, direction):
    if direction == "long":
        return (exit - entry) / entry * 100
    return (entry - exit) / entry * 100


# ─── Entry Filters ───


def filter_next_candle_confirms(df, entry_idx, direction):
    """Next candle must confirm: green for bullish, red for bearish."""
    if entry_idx + 1 >= len(df):
        return False
    nxt = df.iloc[entry_idx + 1]
    if direction == "long":
        return nxt["close"] > nxt["open"]  # Green candle
    return nxt["close"] < nxt["open"]  # Red candle


def filter_lower_low_higher_high(df, entry_idx, direction, lookback=3):
    """For bullish: price made a lower low in last N bars (confirming divergence).
    For bearish: price made a higher high.
    """
    if entry_idx < lookback:
        return True  # Can't check, allow
    recent = df.iloc[entry_idx - lookback : entry_idx + 1]
    if direction == "long":
        return recent["low"].iloc[-1] <= recent["low"].min()
    return recent["high"].iloc[-1] >= recent["high"].max()


# ─── Core Backtest Engine ───


def run_strategy(
    df: pd.DataFrame,
    rsi: pd.Series,
    atr: pd.Series,
    daily_df: pd.DataFrame,
    session_levels: Dict,
    instrument: str,
    swing_lookback: int,
    exit_fn,
    exit_params: dict,
    entry_filter: Optional[str] = None,
    level_filter: Optional[str] = None,  # "pivot", "session", "any_level", None
    window_size: int = 200,
) -> List[Trade]:
    """Run a single strategy configuration and return trades."""
    trades = []
    last_signal_bar = -999

    pivots = calculate_pivot_levels(daily_df) if not daily_df.empty else []

    i = window_size
    while i < len(df):
        window_df = df.iloc[i - window_size : i + 1]
        window_rsi = rsi.iloc[i - window_size : i + 1]

        divs = detect_divergences(
            window_df, window_rsi,
            lookback=swing_lookback,
            max_bars_apart=80,
            min_bars_apart=5,
            recent_only=5,
        )

        if not divs:
            i += 1
            continue

        div = divs[-1]
        actual_bar = i - window_size + div.swing_b.index
        if actual_bar <= last_signal_bar + 5:
            i += 1
            continue

        is_bullish = "bullish" in div.type.value
        direction = "long" if is_bullish else "short"
        current_price = df["close"].iloc[i]
        current_atr = atr.iloc[i] if not np.isnan(atr.iloc[i]) else 0

        # Level checks
        at_pivot = False
        at_session = False
        session_name = ""
        if pivots and current_atr > 0:
            prox = check_pivot_proximity(current_price, pivots, current_atr, 0.5)
            at_pivot = any(p.is_near for p in prox)

        if current_atr > 0:
            at_session, session_name = check_session_level_proximity(
                current_price, df.index[i], session_levels, current_atr, 0.5
            )

        # Apply level filter
        if level_filter == "pivot" and not at_pivot:
            i += 1
            continue
        elif level_filter == "session" and not at_session:
            i += 1
            continue
        elif level_filter == "any_level" and not (at_pivot or at_session):
            i += 1
            continue

        # Apply entry filter
        if entry_filter == "next_candle" and not filter_next_candle_confirms(df, i, direction):
            i += 1
            continue
        if entry_filter == "ll_hh" and not filter_lower_low_higher_high(df, i, direction):
            i += 1
            continue

        # If next_candle filter, entry is on NEXT bar's close
        entry_idx = i + 1 if entry_filter == "next_candle" else i
        if entry_idx >= len(df):
            break

        entry_price = df["close"].iloc[entry_idx]
        last_signal_bar = actual_bar

        # Execute exit strategy
        exit_price, exit_idx, pnl, mfe, mae, reason = exit_fn(
            df, entry_idx, direction, atr=current_atr, **exit_params
        )

        trades.append(Trade(
            entry_time=df.index[entry_idx],
            entry_price=entry_price,
            direction=direction,
            div_type=div.type.value,
            strength=div.strength.value,
            at_pivot=at_pivot,
            at_session_level=at_session,
            session_level_name=session_name,
            atr_at_entry=current_atr,
            exit_price=exit_price,
            exit_time=df.index[min(exit_idx, len(df) - 1)],
            pnl_pct=pnl,
            bars_held=exit_idx - entry_idx,
            max_favorable=mfe,
            max_adverse=mae,
            exit_reason=reason,
        ))

        # Jump past this trade
        i = exit_idx + 1
        continue

    return trades


def compute_result(name: str, trades: List[Trade]) -> StratResult:
    if not trades:
        return StratResult(name=name, total_trades=0, wins=0, losses=0,
                           win_rate=0, total_pnl_pct=0, avg_pnl_pct=0,
                           profit_factor=0, avg_winner=0, avg_loser=0,
                           max_win=0, max_loss=0, avg_rr=0, avg_bars=0,
                           pivot_trades=0, pivot_wr=0, session_trades=0, session_wr=0)

    winners = [t for t in trades if t.pnl_pct > 0]
    losers = [t for t in trades if t.pnl_pct <= 0]
    gp = sum(t.pnl_pct for t in winners)
    gl = abs(sum(t.pnl_pct for t in losers))
    pivot_t = [t for t in trades if t.at_pivot]
    sess_t = [t for t in trades if t.at_session_level]
    avg_w = gp / len(winners) if winners else 0
    avg_l = gl / len(losers) if losers else 0

    return StratResult(
        name=name,
        total_trades=len(trades),
        wins=len(winners),
        losses=len(losers),
        win_rate=len(winners) / len(trades) * 100,
        total_pnl_pct=sum(t.pnl_pct for t in trades),
        avg_pnl_pct=sum(t.pnl_pct for t in trades) / len(trades),
        profit_factor=gp / gl if gl > 0 else 99.0,
        avg_winner=avg_w,
        avg_loser=-avg_l if losers else 0,
        max_win=max(t.pnl_pct for t in trades),
        max_loss=min(t.pnl_pct for t in trades),
        avg_rr=avg_w / avg_l if avg_l > 0 else 99.0,
        avg_bars=sum(t.bars_held for t in trades) / len(trades),
        pivot_trades=len(pivot_t),
        pivot_wr=len([t for t in pivot_t if t.pnl_pct > 0]) / len(pivot_t) * 100 if pivot_t else 0,
        session_trades=len(sess_t),
        session_wr=len([t for t in sess_t if t.pnl_pct > 0]) / len(sess_t) * 100 if sess_t else 0,
    )


# ─── Strategy Configurations ───


def get_all_strategies():
    """Generate all strategy permutations to test."""
    strategies = []

    # 1. Fixed bar exits
    for bars in [5, 10, 15, 20, 30]:
        strategies.append({
            "name": f"FixedBars_{bars}",
            "exit_fn": exit_fixed_bars,
            "exit_params": {"hold_bars": bars},
            "entry_filter": None,
            "level_filter": None,
        })

    # 2. ATR SL/TP with various R:R
    for sl in [1.0, 1.5]:
        for rr in [1.0, 1.5, 2.0, 3.0]:
            tp = sl * rr
            strategies.append({
                "name": f"ATR_SL{sl}_TP{tp:.1f}_RR{rr}",
                "exit_fn": exit_atr_sltp,
                "exit_params": {"sl_mult": sl, "tp_mult": tp},
                "entry_filter": None,
                "level_filter": None,
            })

    # 3. Trailing SL
    for sl in [1.0, 1.5]:
        for trail in [0.5, 0.75, 1.0]:
            strategies.append({
                "name": f"Trail_SL{sl}_T{trail}",
                "exit_fn": exit_trail_sl,
                "exit_params": {"sl_mult": sl, "trail_mult": trail},
                "entry_filter": None,
                "level_filter": None,
            })

    # 4. Best exits + level filters
    best_exits = [
        ("ATR_1.0_RR2", exit_atr_sltp, {"sl_mult": 1.0, "tp_mult": 2.0}),
        ("ATR_1.5_RR2", exit_atr_sltp, {"sl_mult": 1.5, "tp_mult": 3.0}),
        ("Trail_1.0_0.75", exit_trail_sl, {"sl_mult": 1.0, "trail_mult": 0.75}),
        ("Fixed_10", exit_fixed_bars, {"hold_bars": 10}),
    ]

    for exit_name, exit_fn, exit_params in best_exits:
        for lf, lf_name in [("pivot", "Pivot"), ("session", "Session"), ("any_level", "AnyLvl")]:
            strategies.append({
                "name": f"{exit_name}+{lf_name}",
                "exit_fn": exit_fn,
                "exit_params": exit_params,
                "entry_filter": None,
                "level_filter": lf,
            })

    # 5. Entry filters + best exits
    for exit_name, exit_fn, exit_params in best_exits:
        for ef, ef_name in [("next_candle", "NextCndl"), ("ll_hh", "LL_HH")]:
            strategies.append({
                "name": f"{exit_name}+{ef_name}",
                "exit_fn": exit_fn,
                "exit_params": exit_params,
                "entry_filter": ef,
                "level_filter": None,
            })

    # 6. Combined: level filter + entry filter + best exits
    for exit_name, exit_fn, exit_params in best_exits[:2]:
        for lf in ["pivot", "any_level"]:
            for ef in ["next_candle", "ll_hh"]:
                lf_name = "Pivot" if lf == "pivot" else "AnyLvl"
                ef_name = "NextCndl" if ef == "next_candle" else "LL_HH"
                strategies.append({
                    "name": f"{exit_name}+{lf_name}+{ef_name}",
                    "exit_fn": exit_fn,
                    "exit_params": exit_params,
                    "entry_filter": ef,
                    "level_filter": lf,
                })

    return strategies


# ─── Main ───


def main():
    settings = Settings()
    fetcher = DataFetcher(settings)

    console.print(Panel(
        "[bold]RSI Divergence Strategy Optimizer[/bold]\n"
        "Timeframe: 5m | Instruments: 6E, DXY\n"
        "Levels: Daily Pivots + Session Opens (3:30 AM IST, 12:45 AM IST)\n"
        "Testing: Fixed bars, ATR SL/TP, Trailing SL, Next candle, LL/HH, Level filters",
        border_style="blue",
    ))

    strategies = get_all_strategies()
    console.print(f"[bold]Testing {len(strategies)} strategy combinations...[/bold]\n")

    all_results: Dict[str, List[StratResult]] = {}

    for inst_key, instrument in INSTRUMENTS.items():
        console.print(f"[bold]{instrument.name}[/bold]")

        daily_df = fetcher.fetch_daily_ohlcv(instrument, bars=90)

        bt_tf = TimeframeConfig(
            name="5m", yf_interval="5m", yf_period="5d",
            swing_lookback=3, candles_to_fetch=5000,
        )
        df = fetcher.fetch_ohlcv(instrument, bt_tf)
        console.print(f"  {len(df)} candles ({df.index[0]} → {df.index[-1]})")

        rsi = calculate_rsi(df, 14)
        atr = calculate_atr(df, 14)
        session_levels = get_session_open_prices(df)
        console.print(f"  Session levels found for {len(session_levels)} days")

        for strat in strategies:
            trades = run_strategy(
                df, rsi, atr, daily_df, session_levels,
                instrument=inst_key,
                swing_lookback=3,
                exit_fn=strat["exit_fn"],
                exit_params=strat["exit_params"],
                entry_filter=strat.get("entry_filter"),
                level_filter=strat.get("level_filter"),
            )
            result = compute_result(strat["name"], trades)
            key = f"{inst_key}_{strat['name']}"
            if inst_key not in all_results:
                all_results[inst_key] = []
            all_results[inst_key].append(result)

        console.print(f"  Done — {len(strategies)} strategies tested\n")

    # ─── Print Results ───

    for inst_key in INSTRUMENTS:
        results = all_results.get(inst_key, [])
        if not results:
            continue

        # Filter to strategies with trades
        results = [r for r in results if r.total_trades >= 3]

        # Sort by profit factor (most reliable metric)
        results.sort(key=lambda r: r.profit_factor, reverse=True)

        console.print(f"\n[bold underline]{inst_key} — Top 20 Strategies (by Profit Factor)[/bold underline]\n")

        table = Table(show_header=True, border_style="blue", expand=True)
        table.add_column("#", width=3)
        table.add_column("Strategy", width=32)
        table.add_column("Trades", width=6, justify="right")
        table.add_column("WR%", width=6, justify="right")
        table.add_column("PnL%", width=9, justify="right")
        table.add_column("PF", width=5, justify="right")
        table.add_column("R:R", width=5, justify="right")
        table.add_column("AvgW%", width=8, justify="right")
        table.add_column("AvgL%", width=8, justify="right")
        table.add_column("Bars", width=5, justify="right")

        for i, r in enumerate(results[:20], 1):
            pnl_c = "green" if r.total_pnl_pct > 0 else "red"
            pf_c = "green" if r.profit_factor > 1.0 else "red"
            wr_c = "green" if r.win_rate > 50 else "yellow" if r.win_rate > 40 else "red"

            table.add_row(
                str(i),
                r.name,
                str(r.total_trades),
                f"[{wr_c}]{r.win_rate:.0f}[/{wr_c}]",
                f"[{pnl_c}]{r.total_pnl_pct:+.3f}[/{pnl_c}]",
                f"[{pf_c}]{r.profit_factor:.2f}[/{pf_c}]",
                f"{r.avg_rr:.2f}",
                f"[green]+{r.avg_winner:.3f}[/green]",
                f"[red]{r.avg_loser:.3f}[/red]",
                f"{r.avg_bars:.0f}",
            )

        console.print(table)

        # Best overall
        best = results[0]
        console.print(Panel(
            f"[bold]Best: {best.name}[/bold]\n"
            f"Trades: {best.total_trades} | WR: {best.win_rate:.0f}% | "
            f"PnL: {best.total_pnl_pct:+.3f}% | PF: {best.profit_factor:.2f} | R:R: {best.avg_rr:.2f}\n"
            f"Pivot trades: {best.pivot_trades} (WR {best.pivot_wr:.0f}%) | "
            f"Session trades: {best.session_trades} (WR {best.session_wr:.0f}%)",
            border_style="green" if best.total_pnl_pct > 0 else "yellow",
        ))


if __name__ == "__main__":
    main()
