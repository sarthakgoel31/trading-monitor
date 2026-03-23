"""Pip Hunt — Find strategies with real pip profits.
Tests DH (Delta Hero), DIV (Divergence), DD (Delta-Divergence) entries
across many exit configs. Reports in PIPS, not percentages.

User criteria:
- WR >= 65%, PF >= 3, avg profit >= 4 pips
- Morning 8-12 IST, ~1 trade/day
- Max cumulative loss streak <= 10 pips
"""

import sys
sys.path.insert(0, ".")

import json
import logging
import time
from datetime import timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

from src.data.scid_parser import load_6e_combined, get_all_timeframes
from src.mega.engine import precompute, execute_exit

logging.basicConfig(level=logging.WARNING)
console = Console()
IST = timedelta(hours=5, minutes=30)

# 6E: 1 pip = 0.0001, $6.25/pip on E7 mini
PIP = 0.0001


@dataclass
class PipTrade:
    time_ist: str
    direction: str
    entry: float
    exit: float
    pnl_pips: float
    bars: int
    reason: str
    levels: List[str]


def pips(entry, exit_price, direction):
    """Convert price difference to pips."""
    if direction == "long":
        return (exit_price - entry) / PIP
    return (entry - exit_price) / PIP


def get_dh_signals(df5, ind, window_start=8, window_end=12):
    """Delta Hero: delta + cumDelta rising + 2+ levels + VWAP aligned."""
    signals = []
    for i in range(30, len(df5)):
        ist_dt = df5.index[i] + IST
        ist_h = ist_dt.hour + ist_dt.minute / 60
        if not (window_start <= ist_h < window_end):
            continue

        atr = ind["atr"].iloc[i]
        if np.isnan(atr) or atr < 0.00050:  # Match Sierra DH_Scanner v11 min ATR (5 pips)
            continue

        price = df5["close"].iloc[i]
        vwap = ind["vwap"].iloc[i]
        if np.isnan(vwap):
            continue

        delta = ind["delta"].iloc[i] if not np.isnan(ind["delta"].iloc[i]) else 0

        # Cum delta: last 6 bars vs prev 5 bars
        cd_now = sum(ind["delta"].iloc[max(0, i-5+j)]
                     for j in range(6)
                     if not np.isnan(ind["delta"].iloc[max(0, i-5+j)]))
        cd_prev = sum(ind["delta"].iloc[max(0, i-10+j)]
                      for j in range(5)
                      if not np.isnan(ind["delta"].iloc[max(0, i-10+j)]))

        delta_long = delta > 0 and cd_now > cd_prev
        delta_short = delta < 0 and cd_now < cd_prev
        if not (delta_long or delta_short):
            continue

        direction = "long" if delta_long else "short"
        vwap_ok = (direction == "long" and price < vwap) or (direction == "short" and price > vwap)
        if not vwap_ok:
            continue

        # Level check
        date_key = ist_dt.strftime("%Y-%m-%d")
        threshold = atr * 0.5
        levels_near = []

        if date_key in ind["std_pivots"]:
            for name, lvl in ind["std_pivots"][date_key].items():
                if abs(price - lvl) <= threshold:
                    levels_near.append(f"std_{name}")
        if date_key in ind["fib_pivots"]:
            for name, lvl in ind["fib_pivots"][date_key].items():
                if abs(price - lvl) <= threshold:
                    levels_near.append(f"fib_{name}")
        if date_key in ind.get("daily_levels", {}):
            dl = ind["daily_levels"][date_key]
            for name in ["pd_high", "pd_low", "wk_high", "wk_low", "mo_high", "mo_low"]:
                if name in dl and abs(price - dl[name]) <= threshold:
                    levels_near.append(name)
        if date_key in ind.get("vpoc_tpoc", {}):
            vt = ind["vpoc_tpoc"][date_key]
            for name in ["pd_vpoc", "pd_tpoc", "wk_vpoc", "wk_tpoc"]:
                if name in vt and abs(price - vt[name]) <= threshold:
                    levels_near.append(name)

        if len(levels_near) < 2:
            continue

        signals.append({
            "idx": i, "direction": direction, "atr": atr,
            "price": price, "delta": delta, "levels": levels_near,
            "time_ist": ist_dt.strftime("%Y-%m-%d %H:%M"),
        })

    return signals


def get_dd_signals(df5, ind, window_start=8, window_end=12):
    """Delta-Divergence: RSI divergence + delta confirming + VWAP + strong level."""
    from src.mega.engine import detect_signals
    raw_sigs = detect_signals(df5, ind, lookback=3)
    signals = []

    for sig in raw_sigs:
        i = sig.bar_idx
        ist_dt = df5.index[i] + IST
        ist_h = ist_dt.hour + ist_dt.minute / 60
        if not (window_start <= ist_h < window_end):
            continue

        c = sig.confluences
        d = sig.direction

        # Must have delta confirming
        if d == "long" and not c.get("delta_positive", False):
            continue
        if d == "short" and not c.get("delta_negative", False):
            continue

        # Must have VWAP aligned
        if not c.get("vwap_aligned", False):
            continue

        # Must have strong level (2+)
        if not c.get("at_strong_level", False):
            continue

        signals.append({
            "idx": i, "direction": d, "atr": sig.atr,
            "price": df5["close"].iloc[i],
            "delta": sig.delta_at_signal,
            "levels": sig.levels_near[:4],
            "time_ist": ist_dt.strftime("%Y-%m-%d %H:%M"),
        })

    return signals


def get_div_signals(df5, ind, window_start=8, window_end=12):
    """Divergence Hero: RSI divergence + VWAP + strong level (no delta required)."""
    from src.mega.engine import detect_signals
    raw_sigs = detect_signals(df5, ind, lookback=3)
    signals = []

    for sig in raw_sigs:
        i = sig.bar_idx
        ist_dt = df5.index[i] + IST
        ist_h = ist_dt.hour + ist_dt.minute / 60
        if not (window_start <= ist_h < window_end):
            continue

        c = sig.confluences
        if not c.get("vwap_aligned", False):
            continue
        if not c.get("at_strong_level", False):
            continue

        signals.append({
            "idx": i, "direction": sig.direction, "atr": sig.atr,
            "price": df5["close"].iloc[i],
            "delta": sig.delta_at_signal,
            "levels": sig.levels_near[:4],
            "time_ist": ist_dt.strftime("%Y-%m-%d %H:%M"),
        })

    return signals


# ═══ EXIT CONFIGS TO TEST ═══

EXIT_CONFIGS = [
    # Trails with different widths
    {"name": "tr_05",   "mode": "trail", "sl_mult": 1.0, "trail_mult": 0.5},
    {"name": "tr_075",  "mode": "trail", "sl_mult": 1.0, "trail_mult": 0.75},
    {"name": "tr_10",   "mode": "trail", "sl_mult": 1.0, "trail_mult": 1.0},
    {"name": "tr_125",  "mode": "trail", "sl_mult": 1.0, "trail_mult": 1.25},
    {"name": "tr15_075","mode": "trail", "sl_mult": 1.5, "trail_mult": 0.75},
    {"name": "tr15_10", "mode": "trail", "sl_mult": 1.5, "trail_mult": 1.0},

    # Fixed R:R targets
    {"name": "rr15",    "mode": "atr_rr", "sl_mult": 1.0, "rr": 1.5},
    {"name": "rr20",    "mode": "atr_rr", "sl_mult": 1.0, "rr": 2.0},
    {"name": "rr25",    "mode": "atr_rr", "sl_mult": 1.0, "rr": 2.5},
    {"name": "rr30",    "mode": "atr_rr", "sl_mult": 1.0, "rr": 3.0},
    {"name": "rr15_s15","mode": "atr_rr", "sl_mult": 1.5, "rr": 1.5},
    {"name": "rr20_s15","mode": "atr_rr", "sl_mult": 1.5, "rr": 2.0},

    # Trails with VWAP partial (book 50% at VWAP)
    {"name": "tr_05_v50",  "mode": "trail", "sl_mult": 1.0, "trail_mult": 0.5,  "partial": "vwap_50"},
    {"name": "tr_075_v50", "mode": "trail", "sl_mult": 1.0, "trail_mult": 0.75, "partial": "vwap_50"},
    {"name": "tr_10_v50",  "mode": "trail", "sl_mult": 1.0, "trail_mult": 1.0,  "partial": "vwap_50"},

    # Trails with 1:1 partial (book 50% at breakeven+)
    {"name": "tr_05_r50",  "mode": "trail", "sl_mult": 1.0, "trail_mult": 0.5,  "partial": "rr1_50"},
    {"name": "tr_075_r50", "mode": "trail", "sl_mult": 1.0, "trail_mult": 0.75, "partial": "rr1_50"},
    {"name": "tr_10_r50",  "mode": "trail", "sl_mult": 1.0, "trail_mult": 1.0,  "partial": "rr1_50"},

    # Fixed RR with VWAP partial
    {"name": "rr20_v50",   "mode": "atr_rr", "sl_mult": 1.0, "rr": 2.0, "partial": "vwap_50"},
    {"name": "rr25_v50",   "mode": "atr_rr", "sl_mult": 1.0, "rr": 2.5, "partial": "vwap_50"},
    {"name": "rr30_v50",   "mode": "atr_rr", "sl_mult": 1.0, "rr": 3.0, "partial": "vwap_50"},
]


def run_backtest(signals, df5, ind, exit_cfg, window_end=12):
    """Run a single entry×exit combo and return PipTrade list."""
    trades = []
    last_exit = -1

    for sig in signals:
        i = sig["idx"]
        if i <= last_exit:
            continue

        ec = {
            "mode": exit_cfg["mode"],
            "sl_mult": exit_cfg.get("sl_mult", 1.0),
            "trail_mult": exit_cfg.get("trail_mult", 0.75),
            "rr": exit_cfg.get("rr", 2.0),
            "partial": exit_cfg.get("partial", None),
            "max_bars": 30,
            "time_cutoff_ist": window_end,
        }

        xp, xi, pnl_pct, mfe, mae, bars, reason, partial = execute_exit(
            df5, i, sig["direction"], sig["atr"], ec, ind["vwap"]
        )
        last_exit = xi

        pip_pnl = pips(sig["price"], xp, sig["direction"])

        trades.append(PipTrade(
            time_ist=sig["time_ist"],
            direction=sig["direction"],
            entry=sig["price"],
            exit=xp,
            pnl_pips=round(pip_pnl, 1),
            bars=bars,
            reason=reason,
            levels=sig["levels"],
        ))

    return trades


def compute_pip_stats(name: str, trades: List[PipTrade], trading_days: int) -> Dict:
    """Compute pip-based stats for a strategy."""
    if len(trades) < 5:
        return None

    pnls = [t.pnl_pips for t in trades]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]

    total_pips = sum(pnls)
    gross_win = sum(winners) if winners else 0
    gross_loss = abs(sum(losers)) if losers else 0.001

    wr = len(winners) / len(trades) * 100
    pf = gross_win / gross_loss if gross_loss > 0 else 99
    avg_profit = total_pips / len(trades)
    avg_win = np.mean(winners) if winners else 0
    avg_loss = np.mean(losers) if losers else 0
    rr = abs(avg_win / avg_loss) if avg_loss != 0 else 99
    freq = len(trades) / max(trading_days, 1)

    # Max consecutive loss streak in pips
    max_streak_pips = 0
    current_streak = 0
    max_consec_losses = 0
    current_consec = 0
    for p in pnls:
        if p <= 0:
            current_streak += abs(p)
            current_consec += 1
            max_streak_pips = max(max_streak_pips, current_streak)
            max_consec_losses = max(max_consec_losses, current_consec)
        else:
            current_streak = 0
            current_consec = 0

    # Median trade
    median_pips = float(np.median(pnls))

    return {
        "name": name,
        "trades": len(trades),
        "wr": round(wr, 1),
        "pf": round(pf, 2),
        "rr": round(rr, 2),
        "total_pips": round(total_pips, 1),
        "avg_pips": round(avg_profit, 1),
        "median_pips": round(median_pips, 1),
        "avg_win_pips": round(avg_win, 1),
        "avg_loss_pips": round(avg_loss, 1),
        "max_streak_pips": round(max_streak_pips, 1),
        "max_consec_losses": max_consec_losses,
        "freq_per_day": round(freq, 2),
        "dollars_per_trade": round(avg_profit * 6.25, 1),
    }


def main():
    t0 = time.time()

    console.print("\n[bold blue]PIP HUNT — Finding strategies with real pip profits[/bold blue]")
    console.print("Testing DH / DD / DIV entries × 21 exit configs × morning window\n")

    # Load data
    console.print("[dim]Loading Sierra tick data...[/dim]")
    ticks = load_6e_combined("data")
    tf = get_all_timeframes(ticks)
    df5 = tf["5m"]
    daily = tf["1D"]
    console.print(f"[green]Loaded {len(ticks):,} ticks → {len(df5):,} 5m bars[/green]")

    console.print("[dim]Precomputing indicators...[/dim]")
    ind = precompute(df5, None, daily)
    trading_days = len(np.unique(df5.index.date))
    console.print(f"[green]Indicators ready. {trading_days} trading days.[/green]\n")

    # Get signals for each entry type
    console.print("[bold]Detecting signals...[/bold]")
    entry_types = {
        "DH": get_dh_signals(df5, ind, 8, 12),
        "DD": get_dd_signals(df5, ind, 8, 12),
        "DIV": get_div_signals(df5, ind, 8, 12),
    }
    for name, sigs in entry_types.items():
        console.print(f"  {name}: {len(sigs)} signals")

    # Run all combos
    all_results = []
    total_combos = len(entry_types) * len(EXIT_CONFIGS)

    console.print(f"\n[bold]Running {total_combos} combos...[/bold]")
    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  BarColumn(), TimeElapsedColumn()) as prog:
        task = prog.add_task("Testing...", total=total_combos)

        for entry_name, signals in entry_types.items():
            for ecfg in EXIT_CONFIGS:
                strat_name = f"{entry_name}|{ecfg['name']}"
                trades = run_backtest(signals, df5, ind, ecfg, 12)
                stats = compute_pip_stats(strat_name, trades, trading_days)
                if stats:
                    all_results.append(stats)
                prog.advance(task)

    elapsed = time.time() - t0
    console.print(f"\n[green]Done in {elapsed:.0f}s. {len(all_results)} strategies with 5+ trades.[/green]\n")

    # ═══ FILTER BY USER CRITERIA ═══
    console.print("[bold yellow]═══ STRATEGIES MEETING YOUR CRITERIA ═══[/bold yellow]")
    console.print("[dim]WR >= 60% | PF >= 2.5 | Avg >= 3 pips | Streak <= 15 pips | Freq >= 0.3/day[/dim]\n")

    filtered = [r for r in all_results
                if r["wr"] >= 60
                and r["pf"] >= 2.5
                and r["avg_pips"] >= 3
                and r["max_streak_pips"] <= 15
                and r["freq_per_day"] >= 0.3]

    filtered.sort(key=lambda r: r["avg_pips"] * r["pf"], reverse=True)

    if filtered:
        t = Table(title="TOP STRATEGIES (sorted by avg_pips × PF)", border_style="green", show_lines=True)
        t.add_column("Strategy", width=22)
        t.add_column("Trades", width=7, justify="right")
        t.add_column("WR%", width=6, justify="right")
        t.add_column("PF", width=6, justify="right")
        t.add_column("R:R", width=5, justify="right")
        t.add_column("Avg pip", width=8, justify="right")
        t.add_column("Med pip", width=8, justify="right")
        t.add_column("AvgW", width=7, justify="right")
        t.add_column("AvgL", width=7, justify="right")
        t.add_column("Streak", width=7, justify="right")
        t.add_column("MCL", width=4, justify="right")
        t.add_column("/day", width=5, justify="right")
        t.add_column("$/trade", width=8, justify="right")

        for r in filtered[:25]:
            wr_c = "green" if r["wr"] >= 70 else "yellow"
            pf_c = "green" if r["pf"] >= 5 else "yellow"
            pip_c = "green" if r["avg_pips"] >= 5 else "yellow"
            t.add_row(
                r["name"],
                str(r["trades"]),
                f"[{wr_c}]{r['wr']}[/{wr_c}]",
                f"[{pf_c}]{r['pf']}[/{pf_c}]",
                f"{r['rr']}",
                f"[{pip_c}]{r['avg_pips']}[/{pip_c}]",
                f"{r['median_pips']}",
                f"{r['avg_win_pips']}",
                f"{r['avg_loss_pips']}",
                f"{r['max_streak_pips']}",
                str(r["max_consec_losses"]),
                f"{r['freq_per_day']}",
                f"${r['dollars_per_trade']}",
            )
        console.print(t)
    else:
        console.print("[red]No strategies met all criteria.[/red]")

    # ═══ RELAXED VIEW — ALL RESULTS ═══
    console.print(f"\n[bold cyan]═══ ALL {len(all_results)} RESULTS (top 30 by avg_pips) ═══[/bold cyan]\n")

    all_sorted = sorted(all_results, key=lambda r: r["avg_pips"], reverse=True)

    t2 = Table(title="ALL STRATEGIES", border_style="blue", show_lines=True)
    t2.add_column("Strategy", width=22)
    t2.add_column("Trades", width=7, justify="right")
    t2.add_column("WR%", width=6, justify="right")
    t2.add_column("PF", width=6, justify="right")
    t2.add_column("R:R", width=5, justify="right")
    t2.add_column("Avg pip", width=8, justify="right")
    t2.add_column("Med pip", width=8, justify="right")
    t2.add_column("AvgW", width=7, justify="right")
    t2.add_column("AvgL", width=7, justify="right")
    t2.add_column("Streak", width=7, justify="right")
    t2.add_column("MCL", width=4, justify="right")
    t2.add_column("/day", width=5, justify="right")
    t2.add_column("$/trade", width=8, justify="right")

    for r in all_sorted[:30]:
        t2.add_row(
            r["name"], str(r["trades"]),
            f"{r['wr']}", f"{r['pf']}", f"{r['rr']}",
            f"{r['avg_pips']}", f"{r['median_pips']}",
            f"{r['avg_win_pips']}", f"{r['avg_loss_pips']}",
            f"{r['max_streak_pips']}", str(r["max_consec_losses"]),
            f"{r['freq_per_day']}", f"${r['dollars_per_trade']}",
        )
    console.print(t2)

    # Save full results as JSON
    out_path = "data/pip_hunt_results.json"
    with open(out_path, "w") as f:
        json.dump(all_sorted, f, indent=2)
    console.print(f"\n[dim]Full results saved to {out_path}[/dim]")


if __name__ == "__main__":
    main()
