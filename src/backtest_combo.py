"""Combo backtester: Use DXY as confirmation for 6E trades.
6E and DXY are inversely correlated — DXY bearish = 6E bullish and vice versa.
All trades are on 6E. DXY is a filter/confirmation only.
"""

import sys
sys.path.insert(0, ".")

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config.instruments import INSTRUMENTS, TimeframeConfig
from config.settings import Settings
from src.analysis.divergence import detect_divergences, find_swing_points
from src.analysis.pivots import calculate_pivot_levels, check_pivot_proximity
from src.analysis.rsi import calculate_atr, calculate_rsi
from src.data.tv_fetcher import DataFetcher

logging.basicConfig(level=logging.WARNING)
console = Console()

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_OPENS_IST = {
    "CME_Open": time(3, 30),
    "LDN_Close": time(0, 45),
}


@dataclass
class Trade:
    entry_time: datetime
    entry_price: float
    direction: str
    div_type_6e: str
    strength_6e: str
    dxy_confirmation: str   # "div", "rsi", "momentum", "none"
    at_pivot: bool
    at_session: bool
    atr: float
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    bars_held: int = 0
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    exit_reason: str = ""


# ─── DXY Confirmation Methods ───


def dxy_has_opposite_divergence(dxy_df, dxy_rsi, bar_idx, direction_6e, window=200, lookback=3):
    """Check if DXY has a divergence opposite to 6E's direction."""
    if bar_idx < window:
        return False
    w_df = dxy_df.iloc[bar_idx - window: bar_idx + 1]
    w_rsi = dxy_rsi.iloc[bar_idx - window: bar_idx + 1]
    divs = detect_divergences(w_df, w_rsi, lookback=lookback, recent_only=10)
    if not divs:
        return False
    for d in divs:
        dxy_bullish = "bullish" in d.type.value
        # 6E long needs DXY bearish, 6E short needs DXY bullish
        if direction_6e == "long" and not dxy_bullish:
            return True
        if direction_6e == "short" and dxy_bullish:
            return True
    return False


def dxy_rsi_confirms(dxy_rsi, bar_idx, direction_6e):
    """Check if DXY RSI direction opposes 6E trade direction.
    6E long → DXY RSI should be falling (below 50 or declining).
    6E short → DXY RSI should be rising (above 50 or inclining).
    """
    if bar_idx < 5:
        return False
    current = dxy_rsi.iloc[bar_idx]
    prev = dxy_rsi.iloc[bar_idx - 5]
    if np.isnan(current) or np.isnan(prev):
        return False

    if direction_6e == "long":
        return current < prev  # DXY RSI falling = dollar weakening = EUR bullish
    return current > prev      # DXY RSI rising = dollar strengthening = EUR bearish


def dxy_momentum_confirms(dxy_df, bar_idx, direction_6e, lookback=10):
    """Check if DXY price momentum opposes 6E direction.
    6E long → DXY should be falling over last N bars.
    6E short → DXY should be rising.
    """
    if bar_idx < lookback:
        return False
    current = dxy_df["close"].iloc[bar_idx]
    past = dxy_df["close"].iloc[bar_idx - lookback]

    if direction_6e == "long":
        return current < past  # DXY dropping
    return current > past      # DXY rising


def dxy_rsi_extreme(dxy_rsi, bar_idx, direction_6e):
    """Check if DXY RSI is at an extreme that supports 6E direction.
    6E long → DXY RSI overbought (>65) = DXY likely to reverse down.
    6E short → DXY RSI oversold (<35) = DXY likely to reverse up.
    """
    if bar_idx < 1:
        return False
    val = dxy_rsi.iloc[bar_idx]
    if np.isnan(val):
        return False
    if direction_6e == "long":
        return val > 65
    return val < 35


# ─── Exit Strategies (from optimizer) ───


def exit_trail(df, idx, direction, atr, sl_mult=1.0, trail_mult=0.75, max_bars=60):
    entry = df["close"].iloc[idx]
    sl_d = atr * sl_mult
    tr_d = atr * trail_mult
    mfe, mae = 0.0, 0.0

    if direction == "long":
        best = entry
        stop = entry - sl_d
        for j in range(1, max_bars + 1):
            if idx + j >= len(df):
                break
            bar = df.iloc[idx + j]
            if bar["low"] <= stop:
                pnl = (stop - entry) / entry * 100
                return stop, idx + j, pnl, mfe, min(mae, pnl), j, "TRAIL"
            if bar["high"] > best:
                best = bar["high"]
                stop = max(stop, best - tr_d)
            pnl = (bar["close"] - entry) / entry * 100
            mfe = max(mfe, pnl)
            mae = min(mae, pnl)
    else:
        best = entry
        stop = entry + sl_d
        for j in range(1, max_bars + 1):
            if idx + j >= len(df):
                break
            bar = df.iloc[idx + j]
            if bar["high"] >= stop:
                pnl = (entry - stop) / entry * 100
                return stop, idx + j, pnl, mfe, min(mae, pnl), j, "TRAIL"
            if bar["low"] < best:
                best = bar["low"]
                stop = min(stop, best + tr_d)
            pnl = (entry - bar["close"]) / entry * 100
            mfe = max(mfe, pnl)
            mae = min(mae, pnl)

    ei = min(idx + max_bars, len(df) - 1)
    ep = df["close"].iloc[ei]
    pnl = ((ep - entry) / entry * 100) if direction == "long" else ((entry - ep) / entry * 100)
    return ep, ei, pnl, mfe, mae, ei - idx, "TIME"


def exit_atr_rr(df, idx, direction, atr, sl_mult=1.5, rr=2.0, max_bars=60):
    entry = df["close"].iloc[idx]
    sl_d = atr * sl_mult
    tp_d = atr * sl_mult * rr
    mfe, mae = 0.0, 0.0

    for j in range(1, max_bars + 1):
        if idx + j >= len(df):
            break
        bar = df.iloc[idx + j]
        if direction == "long":
            if bar["low"] <= entry - sl_d:
                pnl = -sl_d / entry * 100
                return entry - sl_d, idx + j, pnl, mfe, min(mae, pnl), j, "SL"
            if bar["high"] >= entry + tp_d:
                pnl = tp_d / entry * 100
                return entry + tp_d, idx + j, pnl, max(mfe, pnl), mae, j, "TP"
            pnl = (bar["close"] - entry) / entry * 100
        else:
            if bar["high"] >= entry + sl_d:
                pnl = -sl_d / entry * 100
                return entry + sl_d, idx + j, pnl, mfe, min(mae, pnl), j, "SL"
            if bar["low"] <= entry - tp_d:
                pnl = tp_d / entry * 100
                return entry - tp_d, idx + j, pnl, max(mfe, pnl), mae, j, "TP"
            pnl = (entry - bar["close"]) / entry * 100
        mfe = max(mfe, pnl)
        mae = min(mae, pnl)

    ei = min(idx + max_bars, len(df) - 1)
    ep = df["close"].iloc[ei]
    pnl = ((ep - entry) / entry * 100) if direction == "long" else ((entry - ep) / entry * 100)
    return ep, ei, pnl, mfe, mae, ei - idx, "TIME"


# ─── Level Checks ───


def get_session_levels(df):
    levels = {}
    for idx, row in df.iterrows():
        t = idx.time()
        dk = idx.strftime("%Y-%m-%d")
        for name, ot in SESSION_OPENS_IST.items():
            if abs((t.hour * 60 + t.minute) - (ot.hour * 60 + ot.minute)) <= 5:
                if dk not in levels:
                    levels[dk] = {}
                levels[dk][name] = row["close"]
    return levels


def near_session(price, ts, session_levels, atr):
    if atr <= 0:
        return False
    th = atr * 0.5
    for dk in [ts.strftime("%Y-%m-%d"), (ts - timedelta(days=1)).strftime("%Y-%m-%d")]:
        if dk in session_levels:
            for _, lp in session_levels[dk].items():
                if abs(price - lp) <= th:
                    return True
    return False


# ─── Main Backtester ───


def run_combo(
    e_df, e_rsi, e_atr, d_df, d_rsi,
    daily_df, session_levels,
    dxy_confirm_mode,  # "div", "rsi", "momentum", "rsi_extreme", "any2", "any", "none"
    exit_mode,         # "trail", "atr_rr2", "atr_rr3"
    entry_filter,      # "next_candle", "ll_hh", None
    level_filter,      # "pivot", "session", "any_level", None
    swing_lookback=3,
    window=200,
):
    trades = []
    pivots = calculate_pivot_levels(daily_df) if not daily_df.empty else []
    last_sig = -999

    i = window
    while i < len(e_df):
        # Detect 6E divergence
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

        # ─── DXY Confirmation ───
        dxy_idx = min(i, len(d_df) - 1)  # Align by bar index
        confirm = "none"

        checks = {
            "div": lambda: dxy_has_opposite_divergence(d_df, d_rsi, dxy_idx, direction),
            "rsi": lambda: dxy_rsi_confirms(d_rsi, dxy_idx, direction),
            "momentum": lambda: dxy_momentum_confirms(d_df, dxy_idx, direction),
            "rsi_extreme": lambda: dxy_rsi_extreme(d_rsi, dxy_idx, direction),
        }

        if dxy_confirm_mode == "none":
            confirm = "none"
        elif dxy_confirm_mode == "any":
            # Any one DXY signal confirms
            for k, fn in checks.items():
                if fn():
                    confirm = k
                    break
            if confirm == "none":
                i += 1
                continue
        elif dxy_confirm_mode == "any2":
            # At least 2 DXY signals must confirm
            hits = sum(1 for fn in checks.values() if fn())
            if hits >= 2:
                confirm = f"{hits}_signals"
            else:
                i += 1
                continue
        elif dxy_confirm_mode in checks:
            if checks[dxy_confirm_mode]():
                confirm = dxy_confirm_mode
            else:
                i += 1
                continue

        # ─── Level Filter ───
        at_pivot = False
        at_sess = False
        if pivots and atr_val > 0:
            prox = check_pivot_proximity(price, pivots, atr_val, 0.5)
            at_pivot = any(p.is_near for p in prox)
        at_sess = near_session(price, e_df.index[i], session_levels, atr_val)

        if level_filter == "pivot" and not at_pivot:
            i += 1
            continue
        elif level_filter == "session" and not at_sess:
            i += 1
            continue
        elif level_filter == "any_level" and not (at_pivot or at_sess):
            i += 1
            continue

        # ─── Entry Filter ───
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
            entry_idx = i
        else:
            entry_idx = i

        if entry_idx >= len(e_df):
            break

        last_sig = ab
        ep = e_df["close"].iloc[entry_idx]

        # ─── Exit ───
        if exit_mode == "trail":
            xp, xi, pnl, mfe, mae, bh, reason = exit_trail(e_df, entry_idx, direction, atr_val)
        elif exit_mode == "atr_rr2":
            xp, xi, pnl, mfe, mae, bh, reason = exit_atr_rr(e_df, entry_idx, direction, atr_val, sl_mult=1.5, rr=2.0)
        elif exit_mode == "atr_rr3":
            xp, xi, pnl, mfe, mae, bh, reason = exit_atr_rr(e_df, entry_idx, direction, atr_val, sl_mult=1.0, rr=3.0)
        else:
            xp, xi, pnl, mfe, mae, bh, reason = exit_trail(e_df, entry_idx, direction, atr_val)

        trades.append(Trade(
            entry_time=e_df.index[entry_idx], entry_price=ep,
            direction=direction, div_type_6e=div.type.value,
            strength_6e=div.strength.value, dxy_confirmation=confirm,
            at_pivot=at_pivot, at_session=at_sess, atr=atr_val,
            exit_price=xp, pnl_pct=pnl, bars_held=bh,
            max_favorable=mfe, max_adverse=mae, exit_reason=reason,
        ))
        i = xi + 1
        continue

    return trades


def stats(name, trades):
    if not trades:
        return None
    w = [t for t in trades if t.pnl_pct > 0]
    l = [t for t in trades if t.pnl_pct <= 0]
    gp = sum(t.pnl_pct for t in w)
    gl = abs(sum(t.pnl_pct for t in l))
    aw = gp / len(w) if w else 0
    al = gl / len(l) if l else 0
    return {
        "name": name,
        "trades": len(trades),
        "wr": len(w) / len(trades) * 100,
        "pnl": sum(t.pnl_pct for t in trades),
        "pf": gp / gl if gl > 0 else 99.0,
        "rr": aw / al if al > 0 else 99.0,
        "avg_w": aw,
        "avg_l": -al,
        "bars": sum(t.bars_held for t in trades) / len(trades),
        "pivot_trades": len([t for t in trades if t.at_pivot]),
        "session_trades": len([t for t in trades if t.at_session]),
    }


def main():
    settings = Settings()
    fetcher = DataFetcher(settings)

    console.print(Panel(
        "[bold]6E + DXY Combo Strategy Optimizer[/bold]\n"
        "Trade: 6E only | DXY = confirmation filter\n"
        "Logic: 6E bullish div + DXY bearish signal → LONG 6E\n"
        "Timeframe: 5m | Testing all DXY confirmation modes + exits",
        border_style="blue",
    ))

    # Fetch data
    e_inst = INSTRUMENTS["6E"]
    d_inst = INSTRUMENTS["DXY"]

    e_daily = fetcher.fetch_daily_ohlcv(e_inst, bars=90)
    bt_tf = TimeframeConfig("5m", "5m", "5d", swing_lookback=3, candles_to_fetch=5000)

    console.print("Fetching 6E 5m...")
    e_df = fetcher.fetch_ohlcv(e_inst, bt_tf)
    console.print(f"  {len(e_df)} candles")

    console.print("Fetching DXY 5m...")
    d_df = fetcher.fetch_ohlcv(d_inst, bt_tf)
    console.print(f"  {len(d_df)} candles")

    # Align lengths
    min_len = min(len(e_df), len(d_df))
    e_df = e_df.iloc[-min_len:]
    d_df = d_df.iloc[-min_len:]

    e_rsi = calculate_rsi(e_df)
    e_atr = calculate_atr(e_df)
    d_rsi = calculate_rsi(d_df)

    session_levels = get_session_levels(e_df)
    console.print(f"  Session levels: {len(session_levels)} days\n")

    # Strategy grid
    dxy_modes = ["none", "div", "rsi", "momentum", "rsi_extreme", "any", "any2"]
    exit_modes = ["trail", "atr_rr2", "atr_rr3"]
    entry_filters = [None, "next_candle", "ll_hh"]
    level_filters = [None, "pivot", "session", "any_level"]

    results = []
    total = len(dxy_modes) * len(exit_modes) * len(entry_filters) * len(level_filters)
    console.print(f"[bold]Testing {total} combinations...[/bold]")

    count = 0
    for dm in dxy_modes:
        for em in exit_modes:
            for ef in entry_filters:
                for lf in level_filters:
                    count += 1
                    name_parts = [em]
                    if dm != "none":
                        name_parts.append(f"DXY:{dm}")
                    if ef:
                        name_parts.append(ef)
                    if lf:
                        name_parts.append(lf)
                    name = " + ".join(name_parts)

                    trades = run_combo(
                        e_df, e_rsi, e_atr, d_df, d_rsi,
                        e_daily, session_levels,
                        dxy_confirm_mode=dm, exit_mode=em,
                        entry_filter=ef, level_filter=lf,
                    )
                    s = stats(name, trades)
                    if s and s["trades"] >= 3:
                        results.append(s)

    console.print(f"  {count} combos tested, {len(results)} had 3+ trades\n")

    # Sort by profit factor
    results.sort(key=lambda r: r["pf"], reverse=True)

    # ─── Results Table ───
    console.print("[bold underline]Top 25 Strategies (sorted by Profit Factor)[/bold underline]\n")

    table = Table(show_header=True, border_style="blue", expand=True)
    table.add_column("#", width=3)
    table.add_column("Strategy", width=42, no_wrap=False)
    table.add_column("Trd", width=4, justify="right")
    table.add_column("WR%", width=5, justify="right")
    table.add_column("PnL%", width=9, justify="right")
    table.add_column("PF", width=5, justify="right")
    table.add_column("R:R", width=5, justify="right")
    table.add_column("AvgW", width=7, justify="right")
    table.add_column("AvgL", width=7, justify="right")
    table.add_column("Bars", width=4, justify="right")

    for i, r in enumerate(results[:25], 1):
        pc = "green" if r["pnl"] > 0 else "red"
        pfc = "green" if r["pf"] > 1.0 else "red"
        wrc = "green" if r["wr"] > 50 else "yellow" if r["wr"] > 40 else "red"
        table.add_row(
            str(i), r["name"], str(r["trades"]),
            f"[{wrc}]{r['wr']:.0f}[/{wrc}]",
            f"[{pc}]{r['pnl']:+.3f}[/{pc}]",
            f"[{pfc}]{r['pf']:.2f}[/{pfc}]",
            f"{r['rr']:.2f}",
            f"[green]+{r['avg_w']:.3f}[/green]",
            f"[red]{r['avg_l']:.3f}[/red]",
            f"{r['bars']:.0f}",
        )

    console.print(table)

    # ─── Compare: with vs without DXY ───
    console.print("\n[bold underline]DXY Confirmation Impact (best exit per mode)[/bold underline]\n")

    comp = Table(show_header=True, border_style="yellow")
    comp.add_column("DXY Mode", width=20)
    comp.add_column("Best PF", width=8, justify="right")
    comp.add_column("Trades", width=7, justify="right")
    comp.add_column("WR%", width=6, justify="right")
    comp.add_column("PnL%", width=10, justify="right")
    comp.add_column("R:R", width=6, justify="right")

    for dm in dxy_modes:
        mode_results = [r for r in results if (f"DXY:{dm}" in r["name"]) == (dm != "none")]
        if dm == "none":
            mode_results = [r for r in results if "DXY:" not in r["name"]]
        if mode_results:
            best = max(mode_results, key=lambda r: r["pf"])
            pc = "green" if best["pnl"] > 0 else "red"
            label = dm if dm != "none" else "NO DXY (baseline)"
            comp.add_row(
                f"[bold]{label}[/bold]" if dm == "none" else label,
                f"{best['pf']:.2f}",
                str(best["trades"]),
                f"{best['wr']:.0f}%",
                f"[{pc}]{best['pnl']:+.3f}%[/{pc}]",
                f"{best['rr']:.2f}",
            )

    console.print(comp)

    # Top 3 highlight
    console.print()
    for i, r in enumerate(results[:3], 1):
        console.print(Panel(
            f"[bold]#{i}: {r['name']}[/bold]\n"
            f"Trades: {r['trades']} | WR: {r['wr']:.0f}% | PnL: {r['pnl']:+.3f}% | "
            f"PF: {r['pf']:.2f} | R:R: {r['rr']:.2f}",
            border_style="green" if r["pnl"] > 0 else "yellow",
        ))


if __name__ == "__main__":
    main()
