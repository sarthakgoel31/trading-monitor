"""Practice Mode — Replay historical days bar-by-bar.
Presents one candle at a time. You decide: LONG, SHORT, or SKIP.
After your decision, reveals what the system would have done and the outcome.
"""

import sys
sys.path.insert(0, ".")

import json
import logging
import random
from datetime import timedelta
from pathlib import Path

import numpy as np
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt

from src.data.scid_parser import load_6e_combined, get_all_timeframes
from src.mega.engine import precompute, execute_exit

logging.basicConfig(level=logging.WARNING)
console = Console()
IST = timedelta(hours=5, minutes=30)


def run_practice(date_str=None, window_start=8, window_end=12):
    """Run practice mode for a specific date or random date."""

    console.print(Panel(
        "[bold]PRACTICE MODE[/bold]\n"
        "You'll see candles one at a time. Decide: LONG, SHORT, or SKIP.\n"
        "After each decision, I'll show what the system saw and the outcome.\n"
        "Type 'q' to quit anytime.",
        border_style="blue",
    ))

    # Load data
    console.print("Loading data...", style="dim")
    ticks = load_6e_combined("data")
    tf = get_all_timeframes(ticks)
    df5 = tf["5m"]
    daily = tf["1D"]
    ind = precompute(df5, None, daily)
    days = np.unique(df5.index.date)

    # Pick a date
    # Filter to dates with enough bars in the morning window
    valid_dates = []
    for d in days:
        mask = df5.index.date == d
        bars = df5[mask]
        morning_bars = [b for b in bars.index if window_start <= (b + IST).hour < window_end]
        if len(morning_bars) >= 20:
            valid_dates.append(d)

    if date_str:
        from datetime import datetime
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
        if target not in valid_dates:
            console.print(f"[red]Date {date_str} not available. Pick from: {valid_dates[-5:]}[/red]")
            return
        practice_date = target
    else:
        # Pick random date from last 30 valid days
        practice_date = random.choice(valid_dates[-30:])

    console.print(f"\n[bold green]Practice date: {practice_date}[/bold green]")
    console.print(f"Window: {window_start}:00 - {window_end}:00 IST\n")

    # Get bars for this date in the window
    mask = df5.index.date == practice_date
    day_bars = df5[mask]

    practice_bars = []
    for idx in range(len(day_bars)):
        bar_time = day_bars.index[idx]
        ist_h = (bar_time + IST).hour + (bar_time + IST).minute / 60
        if window_start <= ist_h < window_end:
            practice_bars.append((day_bars.index.get_loc(bar_time), bar_time))

    if len(practice_bars) < 10:
        console.print("[red]Not enough bars for this date/window.[/red]")
        return

    # Get levels for the day
    date_key = str(practice_date)
    levels_today = {}
    if date_key in ind["std_pivots"]:
        levels_today.update({f"std_{k}": v for k, v in ind["std_pivots"][date_key].items()})
    if date_key in ind["fib_pivots"]:
        levels_today.update({f"fib_{k}": v for k, v in ind["fib_pivots"][date_key].items()})
    if date_key in ind.get("daily_levels", {}):
        levels_today.update(ind["daily_levels"][date_key])
    if date_key in ind.get("vpoc_tpoc", {}):
        levels_today.update(ind["vpoc_tpoc"][date_key])

    # Show levels
    console.print("[bold]Today's Key Levels:[/bold]")
    level_table = Table(show_header=True, border_style="blue")
    level_table.add_column("Level", width=20)
    level_table.add_column("Price", width=12, justify="right")
    for name, price in sorted(levels_today.items(), key=lambda x: x[1], reverse=True):
        level_table.add_row(name, f"{price:.5f}")
    console.print(level_table)
    console.print()

    # Practice loop
    score = {"correct": 0, "wrong": 0, "skipped": 0, "missed": 0, "total": 0}
    your_trades = []
    system_trades = []

    shown_bars = 5  # Start by showing first 5 bars as context

    for bar_num, (global_idx, bar_time) in enumerate(practice_bars):
        if bar_num < shown_bars:
            continue  # Skip context bars

        # Show the chart so far (last 10 bars as context)
        ist_time = (bar_time + IST).strftime("%I:%M %p")
        ist_date = (bar_time + IST).strftime("%b %d")

        console.print(f"\n{'─' * 60}")
        console.print(f"[bold]Bar {bar_num + 1}/{len(practice_bars)} — {ist_date} {ist_time} IST[/bold]")

        # Show last 8 bars + current as a mini table
        context_start = max(0, bar_num - 7)
        mini = Table(show_header=True, border_style="dim", expand=True)
        mini.add_column("Time", width=8)
        mini.add_column("O", width=8, justify="right")
        mini.add_column("H", width=8, justify="right")
        mini.add_column("L", width=8, justify="right")
        mini.add_column("C", width=8, justify="right")
        mini.add_column("Vol", width=6, justify="right")
        mini.add_column("Delta", width=7, justify="right")
        mini.add_column("Color", width=5)

        for j in range(context_start, bar_num + 1):
            gidx = practice_bars[j][0]
            bt = practice_bars[j][1]
            t = (bt + IST).strftime("%H:%M")
            bar = df5.iloc[gidx]
            d = ind["delta"].iloc[gidx] if not np.isnan(ind["delta"].iloc[gidx]) else 0
            color = "[green]GREEN[/green]" if bar["close"] > bar["open"] else "[red]RED[/red]"
            is_current = j == bar_num
            style = "bold" if is_current else ""

            mini.add_row(
                f"{'→ ' if is_current else '  '}{t}",
                f"{bar['open']:.5f}", f"{bar['high']:.5f}",
                f"{bar['low']:.5f}", f"{bar['close']:.5f}",
                f"{bar['volume']:.0f}", f"{d:+.0f}", color,
                style=style,
            )

        console.print(mini)

        # Show VWAP
        vwap = ind["vwap"].iloc[global_idx]
        price = df5["close"].iloc[global_idx]
        atr = ind["atr"].iloc[global_idx]
        console.print(f"  VWAP: [blue]{vwap:.5f}[/blue] | Price {'[green]below[/green]' if price < vwap else '[red]above[/red]'} VWAP | ATR: {atr:.5f}")

        # Show nearby levels
        if not np.isnan(atr) and atr > 0:
            threshold = atr * 0.5
            nearby = [(n, p) for n, p in levels_today.items() if abs(price - p) <= threshold * 2]
            if nearby:
                lvl_str = ", ".join(f"{n}({p:.5f})" for n, p in nearby[:4])
                console.print(f"  Nearby levels: [purple]{lvl_str}[/purple]")
            else:
                console.print("  Nearby levels: [dim]none[/dim]")

        # Ask user
        console.print()
        choice = Prompt.ask(
            "[bold]Your call[/bold]",
            choices=["long", "short", "skip", "l", "s", "k", "q"],
            default="skip",
        )

        if choice in ("q",):
            break

        user_dir = None
        if choice in ("long", "l"):
            user_dir = "long"
        elif choice in ("short", "s"):
            user_dir = "short"

        # ─── What the SYSTEM would do ───
        delta = ind["delta"].iloc[global_idx] if not np.isnan(ind["delta"].iloc[global_idx]) else 0
        cum_d_now = sum(
            ind["delta"].iloc[max(0, global_idx - 5 + j)]
            for j in range(6)
            if not np.isnan(ind["delta"].iloc[max(0, global_idx - 5 + j)])
        )
        cum_d_prev = sum(
            ind["delta"].iloc[max(0, global_idx - 10 + j)]
            for j in range(5)
            if not np.isnan(ind["delta"].iloc[max(0, global_idx - 10 + j)])
        )

        delta_long = delta > 0 and cum_d_now > cum_d_prev
        delta_short = delta < 0 and cum_d_now < cum_d_prev

        sys_dir = None
        sys_reason = []
        if not np.isnan(atr) and atr > 0:
            threshold = atr * 0.5
            hits = [(n, p) for n, p in levels_today.items() if abs(price - p) <= threshold]
            strong = len(hits) >= 2
            vwap_long = price < vwap if not np.isnan(vwap) else False
            vwap_short = price > vwap if not np.isnan(vwap) else False

            if delta_long and vwap_long and strong:
                sys_dir = "long"
                sys_reason = [f"delta +{delta:.0f}", "cum delta rising", f"{len(hits)} levels", "below VWAP"]
            elif delta_short and vwap_short and strong:
                sys_dir = "short"
                sys_reason = [f"delta {delta:.0f}", "cum delta falling", f"{len(hits)} levels", "above VWAP"]

        # ─── Outcome: simulate the trade ───
        outcome_pnl = None
        if sys_dir and global_idx < len(df5) - 2:
            ec = {"mode": "trail", "sl_mult": 1.0, "trail_mult": 0.5,
                  "partial": None, "max_bars": 30, "time_cutoff_ist": window_end}
            xp, xi, pnl, mfe, mae, bars, reason, partial = execute_exit(
                df5, global_idx, sys_dir, atr, ec, ind["vwap"]
            )
            outcome_pnl = pnl

        # ─── Show result ───
        console.print()
        if sys_dir:
            color = "green" if sys_dir == "long" else "red"
            console.print(f"  [bold {color}]SYSTEM: {sys_dir.upper()}[/bold {color}] — {', '.join(sys_reason)}")
            if outcome_pnl is not None:
                pnl_color = "green" if outcome_pnl > 0 else "red"
                console.print(f"  Outcome: [{pnl_color}]{outcome_pnl:+.4f}%[/{pnl_color}]")
                sl_price = price - atr if sys_dir == "long" else price + atr
                console.print(f"  Entry: {price:.5f} | SL: {sl_price:.5f} | Trail: {atr * 0.5:.5f}")
        else:
            console.print("  [dim]SYSTEM: SKIP — no signal (delta/levels/VWAP not aligned)[/dim]")

        # Score
        score["total"] += 1
        if user_dir == sys_dir and sys_dir is not None:
            console.print("  [bold green]✓ CORRECT — you matched the system![/bold green]")
            score["correct"] += 1
            your_trades.append({"time": ist_time, "dir": user_dir, "pnl": outcome_pnl, "match": True})
        elif user_dir is None and sys_dir is None:
            console.print("  [bold green]✓ CORRECT SKIP — nothing to trade here.[/bold green]")
            score["correct"] += 1
        elif user_dir is None and sys_dir is not None:
            console.print(f"  [yellow]MISSED — system had a {sys_dir.upper()} signal you skipped.[/yellow]")
            score["missed"] += 1
        elif user_dir is not None and sys_dir is None:
            console.print(f"  [red]OVERTRADE — you took a {user_dir.upper()} but system saw no signal.[/red]")
            score["wrong"] += 1
            your_trades.append({"time": ist_time, "dir": user_dir, "pnl": None, "match": False})
        elif user_dir != sys_dir and sys_dir is not None:
            console.print(f"  [red]WRONG DIRECTION — you went {user_dir.upper()}, system said {sys_dir.upper()}.[/red]")
            score["wrong"] += 1
            your_trades.append({"time": ist_time, "dir": user_dir, "pnl": None, "match": False})

    # ─── Final Score ───
    console.print(f"\n{'═' * 60}")
    total = score["total"]
    if total > 0:
        accuracy = score["correct"] / total * 100

        result = Table(title=f"Practice Results — {practice_date}", border_style="blue")
        result.add_column("Metric", width=25)
        result.add_column("Value", width=15, justify="right")

        result.add_row("Bars reviewed", str(total))
        result.add_row("Correct calls", f"[green]{score['correct']}[/green]")
        result.add_row("Wrong direction", f"[red]{score['wrong']}[/red]")
        result.add_row("Missed signals", f"[yellow]{score['missed']}[/yellow]")
        result.add_row("Accuracy", f"{'[green]' if accuracy > 70 else '[yellow]'}{accuracy:.0f}%")

        console.print(result)

        if accuracy >= 80:
            console.print("\n[bold green]Excellent! You're reading the delta + levels well.[/bold green]")
        elif accuracy >= 60:
            console.print("\n[bold yellow]Good start. Focus on: check 2+ levels before entering.[/bold yellow]")
        else:
            console.print("\n[bold red]Needs work. Review: delta direction + VWAP side + strong level.[/bold red]")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="DH Practice Mode")
    parser.add_argument("--date", type=str, default=None, help="Date to practice (YYYY-MM-DD)")
    parser.add_argument("--start", type=int, default=8, help="Window start hour IST (default 8)")
    parser.add_argument("--end", type=int, default=12, help="Window end hour IST (default 12)")
    args = parser.parse_args()

    run_practice(args.date, args.start, args.end)


if __name__ == "__main__":
    main()
