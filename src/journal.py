"""Trade Journal — Compare your actual trades against the system.
After each session, log your trades. The system shows:
- What you caught (matched system signals)
- What you missed (system signaled, you didn't trade)
- What you shouldn't have taken (you traded, no system signal)
- P&L comparison
"""

import sys
sys.path.insert(0, ".")

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from src.data.scid_parser import load_6e_combined, get_all_timeframes
from src.mega.engine import precompute, execute_exit

logging.basicConfig(level=logging.WARNING)
console = Console()
IST = timedelta(hours=5, minutes=30)
JOURNAL_DIR = Path("data/journal")


def get_system_trades(date_str, window_start=8, window_end=12):
    """Get all DH|strong system trades for a date and window."""

    ticks = load_6e_combined("data")
    tf = get_all_timeframes(ticks)
    df5 = tf["5m"]
    daily = tf["1D"]
    ind = precompute(df5, None, daily)

    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    date_key = date_str

    # Get all levels
    levels_today = {}
    if date_key in ind["std_pivots"]:
        levels_today.update({f"std_{k}": v for k, v in ind["std_pivots"][date_key].items()})
    if date_key in ind["fib_pivots"]:
        levels_today.update({f"fib_{k}": v for k, v in ind["fib_pivots"][date_key].items()})
    if date_key in ind.get("daily_levels", {}):
        levels_today.update(ind["daily_levels"][date_key])
    if date_key in ind.get("vpoc_tpoc", {}):
        levels_today.update(ind["vpoc_tpoc"][date_key])

    # Find system signals
    sys_trades = []
    last_exit = -1

    for i in range(len(df5)):
        if df5.index[i].date() != target_date:
            continue

        ist_h = (df5.index[i] + IST).hour + (df5.index[i] + IST).minute / 60
        if not (window_start <= ist_h < window_end):
            continue

        if i <= last_exit:
            continue

        atr = ind["atr"].iloc[i]
        if np.isnan(atr) or atr <= 0:
            continue

        price = df5["close"].iloc[i]
        vwap = ind["vwap"].iloc[i]
        delta = ind["delta"].iloc[i] if not np.isnan(ind["delta"].iloc[i]) else 0

        # Cum delta
        cd_now = sum(ind["delta"].iloc[max(0, i - 5 + j)]
                     for j in range(6)
                     if not np.isnan(ind["delta"].iloc[max(0, i - 5 + j)]))
        cd_prev = sum(ind["delta"].iloc[max(0, i - 10 + j)]
                      for j in range(5)
                      if not np.isnan(ind["delta"].iloc[max(0, i - 10 + j)]))

        delta_long = delta > 0 and cd_now > cd_prev
        delta_short = delta < 0 and cd_now < cd_prev

        if not (delta_long or delta_short):
            continue

        if np.isnan(vwap):
            continue

        direction = "long" if delta_long else "short"
        vwap_ok = (direction == "long" and price < vwap) or (direction == "short" and price > vwap)
        if not vwap_ok:
            continue

        # Level check
        threshold = atr * 0.5
        hits = [(n, p) for n, p in levels_today.items() if abs(price - p) <= threshold]
        if len(hits) < 2:
            continue

        # Execute trade
        ec = {"mode": "trail", "sl_mult": 1.0, "trail_mult": 0.5,
              "partial": None, "max_bars": 30, "time_cutoff_ist": window_end}
        xp, xi, pnl, mfe, mae, bars, reason, partial = execute_exit(
            df5, i, direction, atr, ec, ind["vwap"]
        )
        last_exit = xi

        ist_time = (df5.index[i] + IST).strftime("%H:%M")
        sl = price - atr if direction == "long" else price + atr

        sys_trades.append({
            "time": ist_time,
            "direction": direction,
            "entry": round(price, 5),
            "sl": round(sl, 5),
            "exit": round(xp, 5),
            "pnl": round(pnl, 4),
            "bars": bars,
            "reason": reason,
            "levels": [h[0] for h in hits[:4]],
            "delta": round(delta),
        })

    return sys_trades


def log_session():
    """Interactive session: user logs their trades, system compares."""

    console.print(Panel(
        "[bold]TRADE JOURNAL[/bold]\n"
        "Log your trades from today's session.\n"
        "I'll compare against what the system would have taken.",
        border_style="blue",
    ))

    # Get date
    today = datetime.now().strftime("%Y-%m-%d")
    date_str = Prompt.ask("Date", default=today)

    # Get window
    ws = int(Prompt.ask("Window start (IST hour)", default="8"))
    we = int(Prompt.ask("Window end (IST hour)", default="12"))

    console.print("\n[dim]Loading system trades...[/dim]")
    sys_trades = get_system_trades(date_str, ws, we)
    console.print(f"System found [bold]{len(sys_trades)}[/bold] DH|strong signals for {date_str} ({ws}:00-{we}:00 IST)\n")

    # Collect user trades
    user_trades = []
    console.print("[bold]Enter your trades (type 'done' when finished):[/bold]\n")

    while True:
        time_str = Prompt.ask("  Trade time (HH:MM IST, or 'done')")
        if time_str.lower() == "done":
            break

        direction = Prompt.ask("  Direction", choices=["long", "short", "l", "s"])
        direction = "long" if direction in ("long", "l") else "short"

        entry = float(Prompt.ask("  Entry price"))

        exit_str = Prompt.ask("  Exit price (or 'open' if still in trade)", default="open")
        exit_price = float(exit_str) if exit_str != "open" else None

        pnl = None
        if exit_price:
            if direction == "long":
                pnl = round((exit_price - entry) / entry * 100, 4)
            else:
                pnl = round((entry - exit_price) / entry * 100, 4)

        user_trades.append({
            "time": time_str,
            "direction": direction,
            "entry": entry,
            "exit": exit_price,
            "pnl": pnl,
        })
        console.print(f"  [green]Logged: {direction.upper()} @ {entry}[/green]\n")

    if not user_trades and not sys_trades:
        console.print("[dim]No trades to compare.[/dim]")
        return

    # ─── COMPARE ───
    console.print(f"\n{'═' * 70}")
    console.print(f"[bold]JOURNAL ANALYSIS — {date_str}[/bold]\n")

    # Show system trades
    console.print("[bold blue]System Trades (DH|strong):[/bold blue]")
    sys_table = Table(show_header=True, border_style="blue")
    sys_table.add_column("Time", width=6)
    sys_table.add_column("Dir", width=6)
    sys_table.add_column("Entry", width=9, justify="right")
    sys_table.add_column("SL", width=9, justify="right")
    sys_table.add_column("Exit", width=9, justify="right")
    sys_table.add_column("P&L", width=8, justify="right")
    sys_table.add_column("Levels", width=25)
    sys_table.add_column("Delta", width=6, justify="right")

    for t in sys_trades:
        pnl_color = "green" if t["pnl"] > 0 else "red"
        dir_color = "green" if t["direction"] == "long" else "red"
        sys_table.add_row(
            t["time"],
            f"[{dir_color}]{t['direction'].upper()}[/{dir_color}]",
            f"{t['entry']:.5f}",
            f"[red]{t['sl']:.5f}[/red]",
            f"{t['exit']:.5f}",
            f"[{pnl_color}]{t['pnl']:+.4f}%[/{pnl_color}]",
            ", ".join(t["levels"][:3]),
            f"{t['delta']:+.0f}",
        )
    console.print(sys_table)

    # Show user trades
    if user_trades:
        console.print("\n[bold green]Your Trades:[/bold green]")
        usr_table = Table(show_header=True, border_style="green")
        usr_table.add_column("Time", width=6)
        usr_table.add_column("Dir", width=6)
        usr_table.add_column("Entry", width=9, justify="right")
        usr_table.add_column("Exit", width=9, justify="right")
        usr_table.add_column("P&L", width=8, justify="right")

        for t in user_trades:
            pnl_str = f"{t['pnl']:+.4f}%" if t["pnl"] is not None else "open"
            pnl_color = "green" if t["pnl"] and t["pnl"] > 0 else "red" if t["pnl"] else "dim"
            dir_color = "green" if t["direction"] == "long" else "red"
            usr_table.add_row(
                t["time"],
                f"[{dir_color}]{t['direction'].upper()}[/{dir_color}]",
                f"{t['entry']:.5f}",
                f"{t['exit']:.5f}" if t["exit"] else "[dim]open[/dim]",
                f"[{pnl_color}]{pnl_str}[/{pnl_color}]",
            )
        console.print(usr_table)

    # ─── Match Analysis ───
    console.print(f"\n[bold]{'─' * 50}[/bold]")
    console.print("[bold]ANALYSIS:[/bold]\n")

    # Match user trades to system trades (within 15 min and same direction)
    matched_sys = set()
    matched_usr = set()

    for ui, ut in enumerate(user_trades):
        for si, st in enumerate(sys_trades):
            if si in matched_sys:
                continue
            # Check time proximity (within 15 min)
            try:
                ut_mins = int(ut["time"].split(":")[0]) * 60 + int(ut["time"].split(":")[1])
                st_mins = int(st["time"].split(":")[0]) * 60 + int(st["time"].split(":")[1])
                if abs(ut_mins - st_mins) <= 15 and ut["direction"] == st["direction"]:
                    matched_sys.add(si)
                    matched_usr.add(ui)
                    console.print(f"  [green]✓ CAUGHT[/green] — Your {ut['direction'].upper()} at {ut['time']} matched system signal at {st['time']}")
                    if ut["pnl"] is not None and st["pnl"]:
                        diff = ut["pnl"] - st["pnl"]
                        console.print(f"    Your P&L: {ut['pnl']:+.4f}% | System P&L: {st['pnl']:+.4f}% | Diff: {diff:+.4f}%")
                    break
            except (ValueError, IndexError):
                pass

    # Missed system signals
    missed = [st for si, st in enumerate(sys_trades) if si not in matched_sys]
    if missed:
        console.print()
        for st in missed:
            console.print(f"  [yellow]⚠ MISSED[/yellow] — System had {st['direction'].upper()} at {st['time']} @ {st['entry']:.5f}")
            console.print(f"    Levels: {', '.join(st['levels'][:3])} | Delta: {st['delta']:+.0f} | Would have been: {st['pnl']:+.4f}%")

    # Overtrading (user trades not matched to system)
    overtrades = [ut for ui, ut in enumerate(user_trades) if ui not in matched_usr]
    if overtrades:
        console.print()
        for ut in overtrades:
            console.print(f"  [red]✗ NO SYSTEM SIGNAL[/red] — Your {ut['direction'].upper()} at {ut['time']} @ {ut['entry']:.5f}")
            console.print(f"    System didn't see this as a valid DH|strong setup. Check: was there 2+ levels? Delta + cum delta confirming? VWAP aligned?")

    # ─── Summary ───
    console.print(f"\n{'═' * 50}")

    summary = Table(title="Session Summary", border_style="blue")
    summary.add_column("Metric", width=30)
    summary.add_column("Value", width=15, justify="right")

    summary.add_row("System signals", str(len(sys_trades)))
    summary.add_row("Your trades", str(len(user_trades)))
    summary.add_row("[green]Caught (matched)[/green]", f"[green]{len(matched_usr)}[/green]")
    summary.add_row("[yellow]Missed[/yellow]", f"[yellow]{len(missed)}[/yellow]")
    summary.add_row("[red]No system signal (overtrade)[/red]", f"[red]{len(overtrades)}[/red]")

    sys_pnl = sum(t["pnl"] for t in sys_trades)
    usr_pnl = sum(t["pnl"] for t in user_trades if t["pnl"] is not None)

    summary.add_row("System total P&L", f"{sys_pnl:+.4f}%")
    summary.add_row("Your total P&L", f"{usr_pnl:+.4f}%")

    if len(sys_trades) > 0:
        catch_rate = len(matched_usr) / len(sys_trades) * 100
        summary.add_row("Catch rate", f"{catch_rate:.0f}%")

    console.print(summary)

    # Save to journal
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    journal_entry = {
        "date": date_str,
        "window": f"{ws}-{we}",
        "system_trades": sys_trades,
        "user_trades": user_trades,
        "caught": len(matched_usr),
        "missed": len(missed),
        "overtrades": len(overtrades),
        "system_pnl": sys_pnl,
        "user_pnl": usr_pnl,
    }

    journal_file = JOURNAL_DIR / f"{date_str}.json"
    with open(journal_file, "w") as f:
        json.dump(journal_entry, f, indent=2)

    console.print(f"\n[dim]Saved to {journal_file}[/dim]")

    # Advice
    if len(missed) > len(matched_usr):
        console.print("\n[yellow]Tip: You're missing more signals than you're catching. Focus on watching delta at strong levels.[/yellow]")
    if len(overtrades) > 0:
        console.print("\n[red]Tip: You took trades the system wouldn't. Before entering, count the levels — need 2+ within 0.5×ATR.[/red]")
    if len(matched_usr) == len(sys_trades) and len(overtrades) == 0:
        console.print("\n[bold green]Perfect session! You matched every system signal with zero overtrades.[/bold green]")


def show_history():
    """Show journal history and progress over time."""
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(JOURNAL_DIR.glob("*.json"))

    if not files:
        console.print("[dim]No journal entries yet. Run: python -m src.journal log[/dim]")
        return

    console.print(Panel("[bold]JOURNAL HISTORY[/bold]", border_style="blue"))

    history = Table(show_header=True, border_style="blue")
    history.add_column("Date", width=12)
    history.add_column("Window", width=6)
    history.add_column("System", width=7, justify="right")
    history.add_column("Yours", width=6, justify="right")
    history.add_column("Caught", width=7, justify="right")
    history.add_column("Missed", width=7, justify="right")
    history.add_column("Over", width=5, justify="right")
    history.add_column("Sys P&L", width=8, justify="right")
    history.add_column("Your P&L", width=8, justify="right")

    for f in files[-20:]:
        with open(f) as fh:
            j = json.load(fh)
        history.add_row(
            j["date"], j["window"],
            str(len(j["system_trades"])), str(len(j["user_trades"])),
            f"[green]{j['caught']}[/green]",
            f"[yellow]{j['missed']}[/yellow]",
            f"[red]{j['overtrades']}[/red]",
            f"{j['system_pnl']:+.4f}%",
            f"{j['user_pnl']:+.4f}%",
        )

    console.print(history)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Trade Journal")
    parser.add_argument("command", choices=["log", "history"], help="'log' to enter trades, 'history' to view past sessions")
    args = parser.parse_args()

    if args.command == "log":
        log_session()
    else:
        show_history()


if __name__ == "__main__":
    main()
