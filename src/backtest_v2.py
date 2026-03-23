"""V2 Backtester: All confluence factors + 5m & 15m + targeting 1-2 trades/day.

New factors: Fib pivots, volume spike, fib retracement, wick confirmation.
"""

import sys
sys.path.insert(0, ".")

import logging
from datetime import timedelta, timezone, time
from typing import Dict, List, Optional

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
from src.analysis.confluence_extra import (
    calculate_fib_pivots, is_volume_spike, volume_ratio,
    at_fib_retracement, fib_supports_direction,
    wick_confirms_direction,
)
from src.data.tv_fetcher import DataFetcher
from src.backtest_combo import (
    dxy_momentum_confirms, dxy_rsi_extreme, dxy_rsi_confirms,
    exit_trail, exit_atr_rr, get_session_levels, near_session,
)

logging.basicConfig(level=logging.WARNING)
console = Console()

TRADE_START = 8   # IST
TRADE_END = 21    # IST


def run_v2(
    e_df, e_rsi, e_atr, d_df, d_rsi,
    daily_df, session_levels, pivots, fib_pivots,
    # Toggles for each confluence factor
    use_dxy: str = "any",          # "none", "momentum", "rsi", "rsi_extreme", "any"
    use_volume: bool = False,       # Require volume spike on divergence bar
    use_fib_retrace: bool = False,  # Price must be at fib retracement level
    use_wicks: bool = False,        # Wick must confirm direction
    use_fib_pivots: bool = False,   # Use fib pivots instead of/alongside standard
    use_std_pivots: bool = False,   # Standard pivot levels
    use_session: bool = False,      # Session open levels
    entry_filter: str = "none",     # "none", "next_candle", "ll_hh"
    exit_mode: str = "trail",       # "trail", "atr_rr2", "atr_rr3"
    window: int = 200,
    swing_lookback: int = 3,
    trade_start: int = TRADE_START,
    trade_end: int = TRADE_END,
):
    trades = []
    all_levels = []
    if use_std_pivots:
        all_levels.extend(pivots)
    if use_fib_pivots:
        all_levels.extend(fib_pivots)

    last_sig = -999
    i = window

    while i < len(e_df):
        hour = e_df.index[i].hour
        if not (trade_start <= hour < trade_end):
            i += 1
            continue

        w = e_df.iloc[i - window: i + 1]
        wr = e_rsi.iloc[i - window: i + 1]
        divs = detect_divergences(w, wr, lookback=swing_lookback, recent_only=5)
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

        # ─── DXY Check ───
        di = min(i, len(d_df) - 1)
        if use_dxy != "none":
            ok = False
            if use_dxy == "momentum":
                ok = dxy_momentum_confirms(d_df, di, direction)
            elif use_dxy == "rsi":
                ok = dxy_rsi_confirms(d_rsi, di, direction)
            elif use_dxy == "rsi_extreme":
                ok = dxy_rsi_extreme(d_rsi, di, direction)
            elif use_dxy == "any":
                ok = (dxy_momentum_confirms(d_df, di, direction) or
                      dxy_rsi_confirms(d_rsi, di, direction) or
                      dxy_rsi_extreme(d_rsi, di, direction))
            if not ok:
                i += 1
                continue

        # ─── Volume Check ───
        if use_volume:
            if not is_volume_spike(e_df, i, lookback=20, threshold=1.3):
                i += 1
                continue

        # ─── Fib Retracement Check ───
        if use_fib_retrace:
            if not fib_supports_direction(e_df, i, direction, atr_val):
                i += 1
                continue

        # ─── Wick Check ───
        if use_wicks:
            if not wick_confirms_direction(e_df, i, direction, lookback=3):
                i += 1
                continue

        # ─── Level Check (pivot / fib pivot / session) ───
        at_level = False
        if all_levels:
            prox = check_pivot_proximity(price, all_levels, atr_val, 0.5)
            at_level = any(p.is_near for p in prox)
        if use_session:
            if near_session(price, e_df.index[i], session_levels, atr_val):
                at_level = True

        if (use_std_pivots or use_fib_pivots or use_session) and not at_level:
            i += 1
            continue

        # ─── Entry Filter ───
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

        # ─── Exit ───
        if exit_mode == "trail":
            xp, xi, pnl, mfe, mae, bh, reason = exit_trail(e_df, entry_idx, direction, atr_val)
        elif exit_mode == "atr_rr2":
            xp, xi, pnl, mfe, mae, bh, reason = exit_atr_rr(e_df, entry_idx, direction, atr_val, 1.5, 2.0)
        else:
            xp, xi, pnl, mfe, mae, bh, reason = exit_atr_rr(e_df, entry_idx, direction, atr_val, 1.0, 3.0)

        trades.append({"pnl": pnl, "bars": bh, "hour": hour, "reason": reason, "dir": direction})
        i = xi + 1

    return trades


def score_trades(trades, trading_days):
    if not trades:
        return None
    w = [t for t in trades if t["pnl"] > 0]
    l = [t for t in trades if t["pnl"] <= 0]
    gp = sum(t["pnl"] for t in w)
    gl = abs(sum(t["pnl"] for t in l))
    freq = len(trades) / trading_days if trading_days > 0 else 0
    return {
        "n": len(trades),
        "freq": freq,
        "wr": len(w) / len(trades) * 100,
        "pnl": sum(t["pnl"] for t in trades),
        "pf": gp / gl if gl > 0 else 99.0,
        "avg": sum(t["pnl"] for t in trades) / len(trades),
        "rr": (gp / len(w)) / (gl / len(l)) if w and l and gl > 0 else 99.0,
    }


def main():
    settings = Settings()
    fetcher = DataFetcher(settings)

    console.print(Panel(
        "[bold]V2 Strategy Optimizer — All Confluences[/bold]\n"
        "New: Fib Pivots, Volume Spike, Fib Retracement, Wick Analysis\n"
        "Timeframes: 5m + 15m | Window: 8-21 IST\n"
        "Target: 1-2 trades/day, best quality",
        border_style="blue",
    ))

    e_inst = INSTRUMENTS["6E"]
    d_inst = INSTRUMENTS["DXY"]
    e_daily = fetcher.fetch_daily_ohlcv(e_inst, bars=90)
    pivots = calculate_pivot_levels(e_daily)
    fib_pivots = calculate_fib_pivots(e_daily)
    session_levels_5m = {}
    session_levels_15m = {}

    # Strategy configs to test — systematic combinations
    configs = []

    # DXY modes
    dxy_opts = ["any", "momentum"]
    # Exit modes
    exit_opts = ["trail", "atr_rr3"]
    # Entry filters
    entry_opts = ["none", "next_candle", "ll_hh"]

    # Confluence combos (the new factors)
    confluence_combos = [
        # (volume, fib_retrace, wicks, fib_piv, std_piv, session, label)
        (False, False, False, False, False, False, "baseline"),
        (True,  False, False, False, False, False, "vol"),
        (False, True,  False, False, False, False, "fib_ret"),
        (False, False, True,  False, False, False, "wicks"),
        (False, False, False, True,  False, False, "fib_piv"),
        (False, False, False, False, True,  False, "std_piv"),
        (False, False, False, True,  True,  True,  "all_lvl"),
        (True,  False, True,  False, False, False, "vol+wick"),
        (True,  True,  False, False, False, False, "vol+fib_ret"),
        (False, True,  True,  False, False, False, "fib_ret+wick"),
        (True,  True,  True,  False, False, False, "vol+fib_ret+wick"),
        (True,  False, True,  True,  True,  True,  "vol+wick+all_lvl"),
        (True,  True,  True,  True,  True,  True,  "ALL"),
        (False, True,  False, True,  True,  True,  "fib_ret+all_lvl"),
        (True,  True,  True,  False, True,  True,  "vol+fib_ret+wick+piv+sess"),
        (False, False, True,  True,  False, True,  "wick+fib_piv+sess"),
        (True,  False, False, True,  False, True,  "vol+fib_piv+sess"),
        (False, True,  True,  True,  False, True,  "fib_ret+wick+fib_piv+sess"),
    ]

    for dm in dxy_opts:
        for em in exit_opts:
            for ef in entry_opts:
                for vol, fib_r, wick, fpiv, spiv, sess, clabel in confluence_combos:
                    parts = [em]
                    if dm != "none":
                        parts.append(f"DXY:{dm}")
                    if ef != "none":
                        parts.append(ef)
                    parts.append(clabel)
                    configs.append({
                        "name": " + ".join(parts),
                        "dxy": dm, "exit": em, "entry": ef,
                        "vol": vol, "fib_r": fib_r, "wick": wick,
                        "fpiv": fpiv, "spiv": spiv, "sess": sess,
                    })

    console.print(f"Testing {len(configs)} configs on 5m and 15m...\n")

    all_results = []

    for tf_name, tf_cfg in [("5m", TimeframeConfig("5m", "5m", "5d", 3, 5000)),
                             ("15m", TimeframeConfig("15m", "15m", "5d", 5, 5000))]:
        console.print(f"[bold]{tf_name}[/bold]")
        e_df = fetcher.fetch_ohlcv(e_inst, tf_cfg)
        d_df = fetcher.fetch_ohlcv(d_inst, tf_cfg)
        min_len = min(len(e_df), len(d_df))
        e_df = e_df.iloc[-min_len:]
        d_df = d_df.iloc[-min_len:]
        e_rsi = calculate_rsi(e_df)
        e_atr = calculate_atr(e_df)
        d_rsi = calculate_rsi(d_df)
        sl = get_session_levels(e_df)
        trading_days = len(set(e_df.index.strftime("%Y-%m-%d")))
        console.print(f"  {len(e_df)} candles, ~{trading_days} days")

        for cfg in configs:
            trades = run_v2(
                e_df, e_rsi, e_atr, d_df, d_rsi,
                e_daily, sl, pivots, fib_pivots,
                use_dxy=cfg["dxy"], use_volume=cfg["vol"],
                use_fib_retrace=cfg["fib_r"], use_wicks=cfg["wick"],
                use_fib_pivots=cfg["fpiv"], use_std_pivots=cfg["spiv"],
                use_session=cfg["sess"], entry_filter=cfg["entry"],
                exit_mode=cfg["exit"],
                swing_lookback=tf_cfg.swing_lookback,
            )
            s = score_trades(trades, trading_days)
            if s and s["n"] >= 3:
                s["name"] = f"[{tf_name}] {cfg['name']}"
                s["tf"] = tf_name
                all_results.append(s)

        console.print(f"  Done\n")

    # ─── Rank: Best profit factor with decent frequency ───
    # Boost strategies near 1-2 trades/day target
    for r in all_results:
        freq_boost = 1.0
        if 0.5 <= r["freq"] <= 2.0:
            freq_boost = 1.5  # Sweet spot
        elif 0.3 <= r["freq"] < 0.5 or 2.0 < r["freq"] <= 3.0:
            freq_boost = 1.0
        else:
            freq_boost = 0.5
        profitable = 1.0 if r["pnl"] > 0 else 0.05
        r["score"] = r["pf"] * freq_boost * profitable

    all_results.sort(key=lambda r: r["score"], reverse=True)

    # ─── Top 30 ───
    console.print("[bold underline]Top 30 Strategies (quality + frequency balanced)[/bold underline]\n")

    t = Table(show_header=True, border_style="blue", expand=True)
    t.add_column("#", width=3)
    t.add_column("Strategy", width=48, no_wrap=False)
    t.add_column("Trd", width=4, justify="right")
    t.add_column("/day", width=5, justify="right")
    t.add_column("WR%", width=5, justify="right")
    t.add_column("PnL%", width=9, justify="right")
    t.add_column("PF", width=5, justify="right")
    t.add_column("R:R", width=5, justify="right")

    for i, r in enumerate(all_results[:30], 1):
        pc = "green" if r["pnl"] > 0 else "red"
        fc = "green" if 0.5 <= r["freq"] <= 2.0 else "yellow"
        wrc = "green" if r["wr"] > 50 else "yellow" if r["wr"] > 40 else "red"
        t.add_row(
            str(i), r["name"], str(r["n"]),
            f"[{fc}]{r['freq']:.1f}[/{fc}]",
            f"[{wrc}]{r['wr']:.0f}[/{wrc}]",
            f"[{pc}]{r['pnl']:+.3f}[/{pc}]",
            f"{r['pf']:.2f}",
            f"{r['rr']:.2f}",
        )
    console.print(t)

    # ─── Best for 1-2/day ───
    daily_strats = [r for r in all_results if 0.5 <= r["freq"] <= 2.5 and r["pnl"] > 0]
    if daily_strats:
        best = daily_strats[0]
        console.print(Panel(
            f"[bold]BEST FOR 1-2 TRADES/DAY[/bold]\n\n"
            f"[bold]Strategy:[/bold] {best['name']}\n"
            f"[bold]Frequency:[/bold] {best['freq']:.1f} trades/day\n"
            f"[bold]Win Rate:[/bold] {best['wr']:.0f}%\n"
            f"[bold]PF:[/bold] {best['pf']:.2f} | [bold]R:R:[/bold] {best['rr']:.2f}\n"
            f"[bold]Total PnL:[/bold] {best['pnl']:+.3f}%",
            border_style="green", title="[bold green]RECOMMENDED[/bold green]",
        ))

    # ─── Impact of each factor ───
    console.print("\n[bold underline]Factor Impact Analysis[/bold underline]\n")

    factors = {
        "vol": "Volume Spike",
        "fib_ret": "Fib Retracement",
        "wicks": "Wick Confirm",
        "fib_piv": "Fib Pivots",
        "std_piv": "Std Pivots",
    }
    baseline = [r for r in all_results if "baseline" in r["name"] and r["pnl"] > 0]
    baseline_pf = max((r["pf"] for r in baseline), default=0)

    ft = Table(show_header=True, border_style="yellow")
    ft.add_column("Factor", width=18)
    ft.add_column("Best PF with", width=10, justify="right")
    ft.add_column("Best WR with", width=10, justify="right")
    ft.add_column("vs Baseline PF", width=14, justify="right")

    for fkey, fname in factors.items():
        with_factor = [r for r in all_results if fkey in r["name"] and r["pnl"] > 0]
        if with_factor:
            bpf = max(r["pf"] for r in with_factor)
            bwr = max(r["wr"] for r in with_factor)
            diff = bpf - baseline_pf
            dc = "green" if diff > 0 else "red"
            ft.add_row(fname, f"{bpf:.2f}", f"{bwr:.0f}%", f"[{dc}]{diff:+.2f}[/{dc}]")
        else:
            ft.add_row(fname, "-", "-", "[dim]no trades[/dim]")

    console.print(ft)


if __name__ == "__main__":
    main()
