"""Find the best trading window and frequency for 6E.
Tests: which hours produce the best trades, targeting ~1/day or 3/2 days.
"""

import sys
sys.path.insert(0, ".")

import logging
from datetime import timedelta, timezone, time
from typing import Dict, List

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
from src.backtest_combo import (
    dxy_momentum_confirms, dxy_rsi_extreme, dxy_rsi_confirms,
    exit_trail, exit_atr_rr, get_session_levels, near_session,
)

logging.basicConfig(level=logging.WARNING)
console = Console()

IST = timezone(timedelta(hours=5, minutes=30))


def bar_ist_hour(ts):
    """Get the IST hour of a timestamp (tvdatafeed returns local IST times)."""
    return ts.hour


def run_windowed_backtest(
    e_df, e_rsi, e_atr, d_df, d_rsi,
    daily_df, session_levels, pivots,
    trade_start_hour, trade_end_hour,  # IST hours
    dxy_mode,  # "momentum", "rsi_extreme", "any"
    exit_mode,  # "trail", "atr_rr3"
    entry_filter,  # "next_candle", "ll_hh", None
    level_filter,  # "any_level", "pivot", None
    window=200,
):
    trades = []
    last_sig = -999
    i = window

    while i < len(e_df):
        # Check trading window
        hour = bar_ist_hour(e_df.index[i])
        if not (trade_start_hour <= hour < trade_end_hour):
            i += 1
            continue

        # 6E divergence
        w = e_df.iloc[i - window: i + 1]
        wr = e_rsi.iloc[i - window: i + 1]
        divs = detect_divergences(w, wr, lookback=3, recent_only=5)
        if not divs:
            i += 1
            continue

        div = divs[-1]
        ab = i - window + div.swing_b.index
        if ab <= last_sig + 5:
            i += 1
            continue

        is_bull = "bullish" in div.type.value
        direction = "long" if is_bull else "short"
        price = e_df["close"].iloc[i]
        atr_val = e_atr.iloc[i]
        if np.isnan(atr_val) or atr_val <= 0:
            i += 1
            continue

        # DXY confirmation
        di = min(i, len(d_df) - 1)
        confirmed = False
        if dxy_mode == "momentum":
            confirmed = dxy_momentum_confirms(d_df, di, direction)
        elif dxy_mode == "rsi_extreme":
            confirmed = dxy_rsi_extreme(d_rsi, di, direction)
        elif dxy_mode == "any":
            confirmed = (dxy_momentum_confirms(d_df, di, direction) or
                        dxy_rsi_extreme(d_rsi, di, direction) or
                        dxy_rsi_confirms(d_rsi, di, direction))
        if not confirmed:
            i += 1
            continue

        # Level filter
        at_pivot = False
        if pivots and atr_val > 0:
            prox = check_pivot_proximity(price, pivots, atr_val, 0.5)
            at_pivot = any(p.is_near for p in prox)
        at_sess = near_session(price, e_df.index[i], session_levels, atr_val)

        if level_filter == "any_level" and not (at_pivot or at_sess):
            i += 1
            continue
        elif level_filter == "pivot" and not at_pivot:
            i += 1
            continue

        # Entry filter
        entry_idx = i
        if entry_filter == "next_candle":
            if i + 1 >= len(e_df):
                break
            nxt = e_df.iloc[i + 1]
            if direction == "long" and nxt["close"] <= nxt["open"]:
                i += 1
                continue
            if direction == "short" and nxt["close"] >= nxt["open"]:
                i += 1
                continue
            entry_idx = i + 1
        elif entry_filter == "ll_hh":
            if i >= 3:
                rec = e_df.iloc[i - 3: i + 1]
                if direction == "long" and rec["low"].iloc[-1] > rec["low"].min():
                    i += 1
                    continue
                if direction == "short" and rec["high"].iloc[-1] < rec["high"].max():
                    i += 1
                    continue

        if entry_idx >= len(e_df):
            break
        last_sig = ab

        # Exit
        if exit_mode == "trail":
            xp, xi, pnl, mfe, mae, bh, reason = exit_trail(e_df, entry_idx, direction, atr_val)
        else:
            xp, xi, pnl, mfe, mae, bh, reason = exit_atr_rr(e_df, entry_idx, direction, atr_val, sl_mult=1.0, rr=3.0)

        trades.append({
            "entry_time": e_df.index[entry_idx],
            "direction": direction,
            "div_type": div.type.value,
            "pnl": pnl,
            "bars": bh,
            "hour": bar_ist_hour(e_df.index[entry_idx]),
            "reason": reason,
        })
        i = xi + 1

    return trades


def main():
    settings = Settings()
    fetcher = DataFetcher(settings)

    console.print(Panel(
        "[bold]Trading Window & Frequency Optimizer[/bold]\n"
        "Goal: ~1 trade/day during your active hours\n"
        "Testing various time windows and DXY confirmation modes",
        border_style="blue",
    ))

    e_inst = INSTRUMENTS["6E"]
    d_inst = INSTRUMENTS["DXY"]
    e_daily = fetcher.fetch_daily_ohlcv(e_inst, bars=90)
    bt_tf = TimeframeConfig("5m", "5m", "5d", swing_lookback=3, candles_to_fetch=5000)

    e_df = fetcher.fetch_ohlcv(e_inst, bt_tf)
    d_df = fetcher.fetch_ohlcv(d_inst, bt_tf)
    min_len = min(len(e_df), len(d_df))
    e_df = e_df.iloc[-min_len:]
    d_df = d_df.iloc[-min_len:]

    e_rsi = calculate_rsi(e_df)
    e_atr = calculate_atr(e_df)
    d_rsi = calculate_rsi(d_df)
    session_levels = get_session_levels(e_df)
    pivots = calculate_pivot_levels(e_daily)

    # Count trading days
    trading_days = len(set(e_df.index.strftime("%Y-%m-%d")))
    console.print(f"Data: {len(e_df)} candles, ~{trading_days} days\n")

    # ─── Step 1: Hourly heatmap — which hours produce best trades? ───
    console.print("[bold underline]Step 1: Hourly Performance Heatmap (IST)[/bold underline]\n")

    # Run with loose filters to get enough trades per hour
    all_trades = run_windowed_backtest(
        e_df, e_rsi, e_atr, d_df, d_rsi,
        e_daily, session_levels, pivots,
        trade_start_hour=0, trade_end_hour=24,
        dxy_mode="any", exit_mode="trail",
        entry_filter=None, level_filter=None,
    )

    hour_stats = {}
    for t in all_trades:
        h = t["hour"]
        if h not in hour_stats:
            hour_stats[h] = {"trades": 0, "wins": 0, "pnl": 0.0}
        hour_stats[h]["trades"] += 1
        hour_stats[h]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            hour_stats[h]["wins"] += 1

    ht = Table(show_header=True, border_style="dim")
    ht.add_column("Hour IST", width=10)
    ht.add_column("Session", width=16)
    ht.add_column("Trades", width=7, justify="right")
    ht.add_column("WR%", width=6, justify="right")
    ht.add_column("PnL%", width=10, justify="right")
    ht.add_column("Quality", width=8)

    sessions = {
        range(0, 3): "Late Night",
        range(3, 6): "CME Open",
        range(6, 8): "Asia Close",
        range(8, 12): "Asia/EU",
        range(12, 15): "London",
        range(15, 18): "London/NY",
        range(18, 21): "New York",
        range(21, 24): "NY Close",
    }

    for h in range(24):
        s = hour_stats.get(h, {"trades": 0, "wins": 0, "pnl": 0.0})
        sess = ""
        for r, sn in sessions.items():
            if h in r:
                sess = sn
                break
        wr = s["wins"] / s["trades"] * 100 if s["trades"] > 0 else 0
        pc = "green" if s["pnl"] > 0 else "red" if s["pnl"] < 0 else "dim"
        q = ""
        if s["trades"] >= 3 and wr >= 55 and s["pnl"] > 0:
            q = "[green]GOOD[/green]"
        elif s["trades"] >= 2 and wr >= 50 and s["pnl"] > 0:
            q = "[yellow]OK[/yellow]"
        elif s["trades"] > 0 and s["pnl"] < 0:
            q = "[red]AVOID[/red]"

        ht.add_row(
            f"{h:02d}:00",
            sess,
            str(s["trades"]),
            f"{wr:.0f}%" if s["trades"] > 0 else "-",
            f"[{pc}]{s['pnl']:+.3f}%[/{pc}]" if s["trades"] > 0 else "[dim]-[/dim]",
            q,
        )

    console.print(ht)

    # ─── Step 2: Test specific windows ───
    console.print("\n[bold underline]Step 2: Trading Windows — Targeting ~1 trade/day[/bold underline]\n")

    windows = [
        ("10-21 IST (your pref)", 10, 21),
        ("10-18 IST (Asia+London)", 10, 18),
        ("12-21 IST (London+NY)", 12, 21),
        ("14-21 IST (London/NY overlap)", 14, 21),
        ("15-21 IST (NY session)", 15, 21),
        ("8-14 IST (Asia/EU)", 8, 14),
        ("8-21 IST (full day)", 8, 21),
        ("3-9 IST (CME open+Asia)", 3, 9),
    ]

    configs = [
        ("DXY:any + Trail", "any", "trail", None, None),
        ("DXY:any + Trail + AnyLvl", "any", "trail", None, "any_level"),
        ("DXY:momentum + Trail + LL/HH", "momentum", "trail", "ll_hh", None),
        ("DXY:momentum + Trail + LL/HH + AnyLvl", "momentum", "trail", "ll_hh", "any_level"),
        ("DXY:any + ATR 1:3", "any", "atr_rr3", None, None),
        ("DXY:any + ATR 1:3 + NextCndl", "any", "atr_rr3", "next_candle", None),
        ("DXY:any + Trail + NextCndl", "any", "trail", "next_candle", None),
        ("DXY:any + Trail + NextCndl + AnyLvl", "any", "trail", "next_candle", "any_level"),
    ]

    results = []
    for wname, ws, we in windows:
        for cname, dm, em, ef, lf in configs:
            trades = run_windowed_backtest(
                e_df, e_rsi, e_atr, d_df, d_rsi,
                e_daily, session_levels, pivots,
                trade_start_hour=ws, trade_end_hour=we,
                dxy_mode=dm, exit_mode=em,
                entry_filter=ef, level_filter=lf,
            )
            if len(trades) < 3:
                continue

            wins = [t for t in trades if t["pnl"] > 0]
            gp = sum(t["pnl"] for t in wins)
            gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
            freq = len(trades) / trading_days

            results.append({
                "window": wname,
                "config": cname,
                "trades": len(trades),
                "freq": freq,
                "wr": len(wins) / len(trades) * 100,
                "pnl": sum(t["pnl"] for t in trades),
                "pf": gp / gl if gl > 0 else 99.0,
                "avg": sum(t["pnl"] for t in trades) / len(trades),
            })

    # Sort by: close to 0.5-1.5 trades/day AND profitable
    # Score = profit_factor * min(1, trades_in_range_penalty)
    for r in results:
        freq_score = 1.0
        if r["freq"] < 0.3:
            freq_score = 0.3  # Too few
        elif r["freq"] > 2.0:
            freq_score = 0.7  # Too many
        elif 0.5 <= r["freq"] <= 1.5:
            freq_score = 1.0  # Sweet spot
        r["score"] = r["pf"] * freq_score * (1 if r["pnl"] > 0 else 0.1)

    results.sort(key=lambda r: r["score"], reverse=True)

    rt = Table(show_header=True, border_style="blue", expand=True)
    rt.add_column("#", width=3)
    rt.add_column("Window", width=24)
    rt.add_column("Strategy", width=32)
    rt.add_column("Trd", width=4, justify="right")
    rt.add_column("/day", width=5, justify="right")
    rt.add_column("WR%", width=5, justify="right")
    rt.add_column("PnL%", width=9, justify="right")
    rt.add_column("PF", width=5, justify="right")

    for i, r in enumerate(results[:20], 1):
        pc = "green" if r["pnl"] > 0 else "red"
        fc = "green" if 0.5 <= r["freq"] <= 1.5 else "yellow"
        wrc = "green" if r["wr"] > 50 else "yellow" if r["wr"] > 40 else "red"
        rt.add_row(
            str(i), r["window"], r["config"],
            str(r["trades"]),
            f"[{fc}]{r['freq']:.1f}[/{fc}]",
            f"[{wrc}]{r['wr']:.0f}[/{wrc}]",
            f"[{pc}]{r['pnl']:+.3f}[/{pc}]",
            f"{r['pf']:.2f}",
        )

    console.print(rt)

    # Best for user's goals
    ideal = [r for r in results if 0.4 <= r["freq"] <= 1.8 and r["pnl"] > 0]
    if ideal:
        best = ideal[0]
        console.print(Panel(
            f"[bold]RECOMMENDED FOR YOU[/bold]\n\n"
            f"[bold]Window:[/bold] {best['window']}\n"
            f"[bold]Strategy:[/bold] {best['config']}\n"
            f"[bold]Frequency:[/bold] {best['freq']:.1f} trades/day ({best['trades']} trades in {trading_days} days)\n"
            f"[bold]Win Rate:[/bold] {best['wr']:.0f}%\n"
            f"[bold]Profit Factor:[/bold] {best['pf']:.2f}\n"
            f"[bold]Total PnL:[/bold] {best['pnl']:+.3f}%",
            border_style="green",
            title="[bold green]YOUR STRATEGY[/bold green]",
        ))


if __name__ == "__main__":
    main()
