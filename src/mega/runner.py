"""Mega backtester runner with Rich progress tracking.
Tests all strategy permutations on Sierra tick data.
"""

import sys
sys.path.insert(0, ".")

import json
import logging
import time
from datetime import timedelta
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas_ta as ta
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress, SpinnerColumn,
    TaskProgressColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn,
)
from rich.table import Table

from src.data.scid_parser import load_6e_combined, get_all_timeframes
from src.data.tv_fetcher import DataFetcher
from src.mega.engine import (
    Trade, precompute, detect_signals, compute_dxy_signals,
    dxy_confirms, execute_exit,
)
from src.mega.stats import compute_stats, rank_strategies
from src.mega.news import fetch_forex_factory_calendar, is_news_blackout
from config.settings import Settings
from config.instruments import INSTRUMENTS, TimeframeConfig

logging.basicConfig(level=logging.WARNING)
console = Console()
IST_OFFSET = timedelta(hours=5, minutes=30)


# ─── Strategy Grid ───

def build_grid() -> List[Dict]:
    strats = []

    entries = [
        {"n": "base", "r": {}},
        {"n": "cndl", "r": {"next_candle": True}},
        {"n": "wick", "r": {"wick_rejection": True}},
        {"n": "vol", "r": {"volume_spike_1_3": True}},
        {"n": "llhh", "r": {"ll_hh": True}},
        {"n": "vwap", "r": {"vwap_aligned": True}},
        {"n": "fib", "r": {"at_fib_retracement": True}},
        {"n": "cndl+wick", "r": {"next_candle": True, "wick_rejection": True}},
        {"n": "cndl+vol", "r": {"next_candle": True, "volume_spike_1_3": True}},
        {"n": "cndl+wick+vol", "r": {"next_candle": True, "wick_rejection": True, "volume_spike_1_3": True}},
        {"n": "cndl+wick+vwap", "r": {"next_candle": True, "wick_rejection": True, "vwap_aligned": True}},
        {"n": "cndl+wick+fib", "r": {"next_candle": True, "wick_rejection": True, "at_fib_retracement": True}},
        {"n": "full", "r": {"next_candle": True, "wick_rejection": True, "vwap_aligned": True, "volume_spike_1_3": True}},
        {"n": "sniper", "r": {"next_candle": True, "wick_rejection": True, "volume_spike_1_3": True, "at_fib_retracement": True}},
    ]

    dxy_modes = ["none", "momentum", "rsi", "any", "any2"]

    levels = [
        {"n": "nolvl", "k": None},
        {"n": "pivot", "k": "at_any_pivot"},
        {"n": "sess", "k": "at_session_level"},
        {"n": "daily", "k": "at_daily_level"},
        {"n": "anylvl", "k": "at_any_level"},
        {"n": "strong", "k": "at_strong_level"},
    ]

    exits = [
        {"n": "tr1", "mode": "trail", "sl_mult": 1.0, "trail_mult": 0.75},
        {"n": "tr15", "mode": "trail", "sl_mult": 1.5, "trail_mult": 0.75},
        {"n": "tr1_05", "mode": "trail", "sl_mult": 1.0, "trail_mult": 0.5},
        {"n": "rr15", "mode": "atr_rr", "sl_mult": 1.0, "rr": 1.5},
        {"n": "rr2", "mode": "atr_rr", "sl_mult": 1.0, "rr": 2.0},
        {"n": "rr2s15", "mode": "atr_rr", "sl_mult": 1.5, "rr": 2.0},
        {"n": "rr3", "mode": "atr_rr", "sl_mult": 1.0, "rr": 3.0},
    ]

    partials = [None, "vwap_50", "rr1_50"]
    cutoffs = [None, 21, 23]
    windows = ["window_8_21", "window_10_21", "window_8_14"]

    for e in entries:
        for d in dxy_modes:
            for l in levels:
                for x in exits:
                    for p in partials:
                        for c in cutoffs:
                            for w in windows:
                                parts = [e["n"]]
                                if d != "none": parts.append(f"d:{d}")
                                if l["k"]: parts.append(l["n"])
                                parts.append(x["n"])
                                if p: parts.append(p)
                                if c: parts.append(f"c{c}")
                                parts.append(w.replace("window_", ""))

                                strats.append({
                                    "name": "|".join(parts),
                                    "entry_req": e["r"],
                                    "dxy": d,
                                    "level_key": l["k"],
                                    "exit": x,
                                    "partial": p,
                                    "cutoff": c,
                                    "window": w,
                                })

    return strats


def run_strat(signals, df, dxy_sigs, vwap, news, config) -> List[Trade]:
    """Run one strategy on precomputed signals."""
    trades = []
    req = config["entry_req"]
    last_exit = -1

    for sig in signals:
        if sig.bar_idx <= last_exit:
            continue
        if not sig.confluences.get(config["window"], False):
            continue
        if not dxy_confirms(dxy_sigs, sig.bar_idx, sig.direction, config["dxy"]):
            continue
        if config["level_key"] and not sig.confluences.get(config["level_key"], False):
            continue

        skip = False
        for k, v in req.items():
            if k == "delta_positive" and sig.direction == "short":
                if not sig.confluences.get("delta_negative", False):
                    skip = True; break
                continue
            if k == "delta_negative" and sig.direction == "long":
                if not sig.confluences.get("delta_positive", False):
                    skip = True; break
                continue
            if sig.confluences.get(k, False) != v:
                if k == "next_candle" and sig.confluences.get("next_candle_doji", False):
                    continue
                skip = True; break
        if skip:
            continue

        is_news = is_news_blackout(df.index[sig.bar_idx], news) if news else False

        offset = 1 if "next_candle" in req else 0
        if offset == 1 and sig.confluences.get("next_candle_doji", False):
            offset = 2
        ei = sig.bar_idx + offset
        if ei >= len(df):
            continue

        ec = dict(config["exit"])
        ec["partial"] = config["partial"]
        ec["max_bars"] = 120
        ec["time_cutoff_ist"] = config["cutoff"]

        xp, xi, pnl, mfe, mae, bars, reason, partial = execute_exit(
            df, ei, sig.direction, sig.atr, ec, vwap
        )

        trades.append(Trade(
            entry_idx=ei, entry_price=df["close"].iloc[ei],
            entry_time=df.index[ei], direction=sig.direction,
            signal=sig, exit_price=xp, exit_idx=xi,
            exit_time=df.index[min(xi, len(df) - 1)],
            pnl_pct=pnl, bars_held=bars, mfe=mfe, mae=mae,
            exit_reason=reason, partial_pnl=partial,
            is_news_trade=is_news,
        ))
        last_exit = xi

    return trades


def main():
    t0 = time.time()

    console.print(Panel(
        "[bold]MEGA BACKTEST ENGINE v2[/bold]\n"
        "Sierra tick data: 9+ months | 6 million ticks\n"
        "6E (trade) + DXY (confirm) | 5m + 15m\n"
        "All confluences × All exits × All filters\n"
        "Outlier removal | Unbiased | Progress tracked",
        border_style="blue",
    ))

    # ═══ PHASE 1: LOAD DATA ═══
    console.print("\n[bold cyan]PHASE 1: Loading Data[/bold cyan]")
    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  BarColumn(), TimeElapsedColumn()) as prog:

        p1 = prog.add_task("Loading Sierra tick data...", total=None)
        ticks = load_6e_combined("data")
        prog.update(p1, description=f"[green]6E: {len(ticks):,} ticks ✓")
        prog.stop_task(p1)

        p2 = prog.add_task("Building timeframes...", total=None)
        tf = get_all_timeframes(ticks)
        prog.update(p2, description=f"[green]5m:{len(tf['5m']):,} 15m:{len(tf['15m']):,} 1m:{len(tf['1m']):,} ✓")
        prog.stop_task(p2)

        p3 = prog.add_task("Loading DXY...", total=None)
        settings = Settings()
        fetcher = DataFetcher(settings)
        dxy = INSTRUMENTS["DXY"]
        dxy_5m = fetcher.fetch_ohlcv(dxy, TimeframeConfig("5m", "5m", "5d", 3, 5000))
        dxy_15m = fetcher.fetch_ohlcv(dxy, TimeframeConfig("15m", "15m", "1mo", 5, 5000))
        prog.update(p3, description=f"[green]DXY: 5m={len(dxy_5m)} 15m={len(dxy_15m)} ✓")
        prog.stop_task(p3)

        p4 = prog.add_task("Forex Factory calendar...", total=None)
        news = fetch_forex_factory_calendar()
        prog.update(p4, description=f"[green]News: {len(news)} events ✓")
        prog.stop_task(p4)

    # ═══ PHASE 2: PRECOMPUTE ═══
    console.print("\n[bold cyan]PHASE 2: Precomputing Indicators[/bold cyan]")
    daily = tf["1D"]
    tfs = {"5m": tf["5m"], "15m": tf["15m"]}
    precomp = {}
    dxy_sigs = {}

    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  BarColumn(), TimeElapsedColumn()) as prog:
        for name, df in tfs.items():
            p = prog.add_task(f"Indicators for {name}...", total=None)
            lb = 3 if name == "5m" else 5
            precomp[name] = {"df": df, "ind": precompute(df, tf.get("1m"), daily), "lb": lb}
            prog.update(p, description=f"[green]{name} indicators ✓")
            prog.stop_task(p)

        p = prog.add_task("DXY signals...", total=None)
        dxy_sigs["5m"] = compute_dxy_signals(dxy_5m, ta.rsi(dxy_5m["close"], 14))
        dxy_sigs["15m"] = compute_dxy_signals(dxy_15m, ta.rsi(dxy_15m["close"], 14))
        prog.update(p, description="[green]DXY confirm signals ✓")
        prog.stop_task(p)

    # ═══ PHASE 3: DETECT SIGNALS ═══
    console.print("\n[bold cyan]PHASE 3: Detecting Divergences[/bold cyan]")
    all_sigs = {}
    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  BarColumn(), TimeElapsedColumn()) as prog:
        for name in tfs:
            p = prog.add_task(f"Scanning {name}...", total=None)
            pc = precomp[name]
            sigs = detect_signals(pc["df"], pc["ind"], lookback=pc["lb"])
            all_sigs[name] = sigs
            prog.update(p, description=f"[green]{name}: {len(sigs)} signals ✓")
            prog.stop_task(p)

    total_sigs = sum(len(v) for v in all_sigs.values())
    console.print(f"  Total divergence signals: [bold]{total_sigs}[/bold]")

    # ═══ PHASE 4: RUN STRATEGIES ═══
    grid = build_grid()
    total = len(grid) * len(tfs)
    console.print(f"\n[bold cyan]PHASE 4: Running {len(grid):,} strategies × {len(tfs)} TFs = {total:,} combos[/bold cyan]\n")

    all_results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=50),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
    ) as prog:
        task = prog.add_task("Testing...", total=total)
        done = 0
        for tf_name in tfs:
            pc = precomp[tf_name]
            df = pc["df"]
            sigs = all_sigs[tf_name]
            vwap = pc["ind"]["vwap"]
            ds = dxy_sigs[tf_name]
            days = len(np.unique(df.index.date))

            for s in grid:
                trades = run_strat(sigs, df, ds, vwap, news, s)
                r = compute_stats(s["name"], tf_name, trades, days)
                all_results.append(r)
                done += 1
                if done % 500 == 0:
                    prog.update(task, completed=done,
                                description=f"Testing... ({len([r for r in all_results if r.total >= 10 and r.total_pnl > 0])} profitable)")
                else:
                    prog.advance(task)

    elapsed = time.time() - t0
    console.print(f"\n[bold green]Completed {total:,} combos in {elapsed:.0f}s ({elapsed/60:.1f} min)[/bold green]")

    # ═══ PHASE 5: RANK ═══
    console.print("\n[bold cyan]PHASE 5: Ranking[/bold cyan]")
    ranked = rank_strategies(all_results, min_trades=10)
    console.print(f"  {len(ranked)} strategies qualified\n")

    # Category splits
    daily_d = sorted([r for r in ranked if 0.4 <= r.freq_per_day <= 3.0],
                     key=lambda r: r.score, reverse=True)
    weekly = sorted([r for r in ranked if 0.1 <= r.freq_per_day < 0.4],
                    key=lambda r: r.score, reverse=True)
    snipers = sorted([r for r in ranked if r.total >= 10],
                     key=lambda r: r.profit_factor, reverse=True)

    _print_table(daily_d[:15], f"Top 15 Daily Drivers (0.4-3/day) — {len(daily_d)} qualified")
    _print_table(weekly[:15], f"Top 15 Weekly Edge (1-3/week) — {len(weekly)} qualified")
    _print_table(snipers[:15], f"Top 15 Snipers (highest PF) — {len(snipers)} qualified")
    _print_table(ranked[:15], "Top 15 Overall")

    # Save all categories
    _save(ranked, daily_d, weekly, snipers)

    console.print(Panel(
        f"[bold green]DONE[/bold green] in {elapsed:.0f}s\n"
        f"Results: data/mega_results.json\n"
        f"Top picks: data/top3.json",
        border_style="green",
    ))


def _print_table(results, title):
    if not results:
        console.print(f"  [dim]{title}: none qualified[/dim]\n")
        return
    t = Table(title=title, show_header=True, border_style="blue", expand=True)
    t.add_column("#", width=3)
    t.add_column("Strategy", width=42, no_wrap=False)
    t.add_column("TF", width=3)
    t.add_column("N", width=4, justify="right")
    t.add_column("/d", width=4, justify="right")
    t.add_column("WR", width=4, justify="right")
    t.add_column("PnL", width=7, justify="right")
    t.add_column("PF", width=5, justify="right")
    t.add_column("RR", width=4, justify="right")
    t.add_column("Sh", width=5, justify="right")
    t.add_column("CL", width=3, justify="right")
    t.add_column("Sc", width=5, justify="right")

    for i, r in enumerate(results[:15], 1):
        pc = "green" if r.total_pnl > 0 else "red"
        t.add_row(
            str(i), r.name, r.timeframe, str(r.total),
            f"{r.freq_per_day:.1f}", f"{r.win_rate:.0f}",
            f"[{pc}]{r.total_pnl:+.1f}[/{pc}]",
            f"{r.profit_factor:.2f}", f"{r.avg_rr:.1f}",
            f"{r.sharpe:.3f}", str(r.max_consec_loss),
            f"{r.score:.1f}",
        )
    console.print(t)
    console.print()


def _save(ranked, daily, weekly, snipers):
    Path("data").mkdir(exist_ok=True)

    def _to_dict(r):
        return {
            "name": r.name, "tf": r.timeframe,
            "total": r.total, "wr": round(r.win_rate, 1),
            "pnl": round(r.total_pnl, 3), "pf": round(min(r.profit_factor, 99), 2),
            "rr": round(min(r.avg_rr, 99), 2), "freq": round(r.freq_per_day, 2),
            "sharpe": round(r.sharpe, 4), "mcl": r.max_consec_loss,
            "bars": round(r.avg_bars, 0), "score": round(r.score, 1),
            "long_wr": round(r.long_wr, 1), "short_wr": round(r.short_wr, 1),
            "outliers": r.outliers_removed,
        }

    with open("data/mega_results.json", "w") as f:
        json.dump({
            "daily_drivers": [_to_dict(r) for r in daily[:50]],
            "weekly_edge": [_to_dict(r) for r in weekly[:50]],
            "snipers": [_to_dict(r) for r in snipers[:50]],
            "overall": [_to_dict(r) for r in ranked[:100]],
        }, f, indent=2)

    picks = {}
    if daily: picks["daily_driver"] = {"name": daily[0].name, "tf": daily[0].timeframe, "wr": round(daily[0].win_rate,1), "pf": round(min(daily[0].profit_factor,99),2), "freq": round(daily[0].freq_per_day,2), "pnl": round(daily[0].total_pnl,3)}
    if weekly: picks["weekly_edge"] = {"name": weekly[0].name, "tf": weekly[0].timeframe, "wr": round(weekly[0].win_rate,1), "pf": round(min(weekly[0].profit_factor,99),2), "freq": round(weekly[0].freq_per_day,2), "pnl": round(weekly[0].total_pnl,3)}
    if snipers: picks["sniper"] = {"name": snipers[0].name, "tf": snipers[0].timeframe, "wr": round(snipers[0].win_rate,1), "pf": round(min(snipers[0].profit_factor,99),2), "freq": round(snipers[0].freq_per_day,2), "pnl": round(snipers[0].total_pnl,3)}
    with open("data/top3.json", "w") as f:
        json.dump(picks, f, indent=2)


if __name__ == "__main__":
    main()
