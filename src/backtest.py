"""Backtest RSI divergence + pivot level strategy on historical data."""

import sys
sys.path.insert(0, ".")

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import numpy as np
import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config.instruments import INSTRUMENTS, Instrument, TimeframeConfig
from config.settings import Settings
from src.analysis.divergence import detect_divergences
from src.analysis.pivots import calculate_pivot_levels, check_pivot_proximity
from src.analysis.rsi import calculate_atr, calculate_rsi
from src.data.tv_fetcher import DataFetcher

logging.basicConfig(level=logging.WARNING)
console = Console()


@dataclass
class Trade:
    entry_time: datetime
    entry_price: float
    direction: str          # "long" or "short"
    div_type: str
    strength: str
    timeframe: str
    instrument: str
    at_pivot: bool
    pivot_name: str
    confluence_score: float
    # Filled after exit
    exit_price: float = 0.0
    exit_time: Optional[datetime] = None
    pnl_pct: float = 0.0
    bars_held: int = 0
    max_favorable: float = 0.0   # Best pnl during trade
    max_adverse: float = 0.0     # Worst pnl during trade


@dataclass
class BacktestResult:
    instrument: str
    timeframe: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_pnl_pct: float
    total_pnl_pct: float
    avg_winner_pct: float
    avg_loser_pct: float
    max_win_pct: float
    max_loss_pct: float
    avg_bars_held: float
    profit_factor: float
    trades_at_pivot: int
    pivot_win_rate: float
    trades: List[Trade] = field(default_factory=list)


def run_backtest(
    df: pd.DataFrame,
    daily_df: pd.DataFrame,
    instrument: str,
    timeframe: str,
    swing_lookback: int = 5,
    hold_bars: int = 20,
    window_size: int = 200,
) -> BacktestResult:
    """Walk-forward backtest of divergence signals.

    For each bar from `window_size` onward:
    1. Take the last `window_size` bars as the analysis window
    2. Run divergence detection
    3. If a NEW divergence is found (swing_b at the edge of the window),
       enter a trade at the close
    4. Exit after `hold_bars` bars
    5. Track P&L
    """
    rsi_full = calculate_rsi(df, 14)
    atr_full = calculate_atr(df, 14)

    trades: List[Trade] = []
    active_trade: Optional[Trade] = None
    last_signal_bar = -999  # Prevent duplicate entries on same divergence

    for i in range(window_size, len(df)):
        # Check if active trade should exit
        if active_trade is not None:
            current_price = df["close"].iloc[i]
            if active_trade.direction == "long":
                current_pnl = (current_price - active_trade.entry_price) / active_trade.entry_price * 100
            else:
                current_pnl = (active_trade.entry_price - current_price) / active_trade.entry_price * 100

            active_trade.max_favorable = max(active_trade.max_favorable, current_pnl)
            active_trade.max_adverse = min(active_trade.max_adverse, current_pnl)
            active_trade.bars_held += 1

            if active_trade.bars_held >= hold_bars:
                active_trade.exit_price = current_price
                active_trade.exit_time = df.index[i]
                active_trade.pnl_pct = current_pnl
                trades.append(active_trade)
                active_trade = None

            continue  # Don't enter new trade while one is active

        # Analysis window
        window_df = df.iloc[i - window_size : i + 1]
        window_rsi = rsi_full.iloc[i - window_size : i + 1]

        # Detect divergences
        divs = detect_divergences(
            window_df, window_rsi,
            lookback=swing_lookback,
            max_bars_apart=80,
            min_bars_apart=5,
            recent_only=5,  # Only very recent divergences (forming NOW)
        )

        if not divs:
            continue

        # Take the strongest/most recent divergence
        div = divs[-1]

        # Skip if we already traded this divergence
        actual_bar = i - window_size + div.swing_b.index
        if actual_bar <= last_signal_bar + 5:
            continue
        last_signal_bar = actual_bar

        # Direction
        is_bullish = "bullish" in div.type.value
        direction = "long" if is_bullish else "short"

        # Pivot check
        current_price = df["close"].iloc[i]
        current_atr = atr_full.iloc[i]
        at_pivot = False
        pivot_name = ""

        if not daily_df.empty and current_atr > 0:
            # Find the daily bar before current time for pivots
            pivots = calculate_pivot_levels(daily_df)
            proximity = check_pivot_proximity(current_price, pivots, current_atr, 0.5)
            near = [p for p in proximity if p.is_near]
            if near:
                at_pivot = True
                pivot_name = near[0].level.name

        # Confluence score (simplified)
        score = {"strong": 30, "moderate": 20, "weak": 10}.get(div.strength.value, 10)
        if at_pivot:
            score += 25

        active_trade = Trade(
            entry_time=df.index[i],
            entry_price=current_price,
            direction=direction,
            div_type=div.type.value,
            strength=div.strength.value,
            timeframe=timeframe,
            instrument=instrument,
            at_pivot=at_pivot,
            pivot_name=pivot_name,
            confluence_score=score,
        )

    # Close any remaining active trade at last bar
    if active_trade is not None:
        current_price = df["close"].iloc[-1]
        if active_trade.direction == "long":
            current_pnl = (current_price - active_trade.entry_price) / active_trade.entry_price * 100
        else:
            current_pnl = (active_trade.entry_price - current_price) / active_trade.entry_price * 100
        active_trade.exit_price = current_price
        active_trade.exit_time = df.index[-1]
        active_trade.pnl_pct = current_pnl
        trades.append(active_trade)

    # Calculate stats
    return _compute_stats(trades, instrument, timeframe)


def _compute_stats(trades: List[Trade], instrument: str, timeframe: str) -> BacktestResult:
    if not trades:
        return BacktestResult(
            instrument=instrument, timeframe=timeframe,
            total_trades=0, wins=0, losses=0, win_rate=0, avg_pnl_pct=0,
            total_pnl_pct=0, avg_winner_pct=0, avg_loser_pct=0,
            max_win_pct=0, max_loss_pct=0, avg_bars_held=0,
            profit_factor=0, trades_at_pivot=0, pivot_win_rate=0,
        )

    winners = [t for t in trades if t.pnl_pct > 0]
    losers = [t for t in trades if t.pnl_pct <= 0]
    pivot_trades = [t for t in trades if t.at_pivot]
    pivot_winners = [t for t in pivot_trades if t.pnl_pct > 0]

    gross_profit = sum(t.pnl_pct for t in winners) if winners else 0
    gross_loss = abs(sum(t.pnl_pct for t in losers)) if losers else 0

    return BacktestResult(
        instrument=instrument,
        timeframe=timeframe,
        total_trades=len(trades),
        wins=len(winners),
        losses=len(losers),
        win_rate=len(winners) / len(trades) * 100,
        avg_pnl_pct=sum(t.pnl_pct for t in trades) / len(trades),
        total_pnl_pct=sum(t.pnl_pct for t in trades),
        avg_winner_pct=gross_profit / len(winners) if winners else 0,
        avg_loser_pct=-gross_loss / len(losers) if losers else 0,
        max_win_pct=max(t.pnl_pct for t in trades),
        max_loss_pct=min(t.pnl_pct for t in trades),
        avg_bars_held=sum(t.bars_held for t in trades) / len(trades),
        profit_factor=gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        trades_at_pivot=len(pivot_trades),
        pivot_win_rate=len(pivot_winners) / len(pivot_trades) * 100 if pivot_trades else 0,
        trades=trades,
    )


def print_results(results: List[BacktestResult]) -> None:
    """Pretty-print backtest results."""

    for r in results:
        if r.total_trades == 0:
            console.print(f"[dim]{r.instrument} {r.timeframe}: No trades[/dim]")
            continue

        color = "green" if r.total_pnl_pct > 0 else "red"

        table = Table(
            title=f"{r.instrument} {r.timeframe} — {r.total_trades} trades",
            show_header=True, border_style="blue",
        )
        table.add_column("Metric", style="bold", width=22)
        table.add_column("Value", width=18, justify="right")

        table.add_row("Win Rate", f"{r.win_rate:.1f}%")
        table.add_row("Total P&L", f"[{color}]{r.total_pnl_pct:+.3f}%[/{color}]")
        table.add_row("Avg P&L / trade", f"{r.avg_pnl_pct:+.4f}%")
        table.add_row("Avg Winner", f"[green]+{r.avg_winner_pct:.4f}%[/green]")
        table.add_row("Avg Loser", f"[red]{r.avg_loser_pct:.4f}%[/red]")
        table.add_row("Max Win", f"[green]+{r.max_win_pct:.4f}%[/green]")
        table.add_row("Max Loss", f"[red]{r.max_loss_pct:.4f}%[/red]")
        table.add_row("Profit Factor", f"{r.profit_factor:.2f}")
        table.add_row("Avg Bars Held", f"{r.avg_bars_held:.1f}")
        table.add_row("─" * 22, "─" * 18)
        table.add_row("Trades at Pivot", f"{r.trades_at_pivot} / {r.total_trades}")
        table.add_row("Pivot Win Rate", f"{r.pivot_win_rate:.1f}%")

        console.print(table)
        console.print()

    # Trade-by-trade log for the last result with trades
    for r in results:
        if not r.trades:
            continue

        console.print(f"[bold]Trade Log — {r.instrument} {r.timeframe}[/bold]")
        log = Table(show_header=True, border_style="dim", expand=True)
        log.add_column("#", width=3)
        log.add_column("Entry", width=18)
        log.add_column("Dir", width=5)
        log.add_column("Type", width=18)
        log.add_column("Strength", width=9)
        log.add_column("Pivot", width=6)
        log.add_column("P&L", width=10, justify="right")
        log.add_column("MFE", width=10, justify="right")
        log.add_column("MAE", width=10, justify="right")

        for i, t in enumerate(r.trades, 1):
            pnl_color = "green" if t.pnl_pct > 0 else "red"
            div_label = t.div_type.replace("_", " ").title()
            log.add_row(
                str(i),
                str(t.entry_time)[:16],
                f"[green]LONG[/green]" if t.direction == "long" else f"[red]SHORT[/red]",
                div_label,
                t.strength,
                t.pivot_name or "-",
                f"[{pnl_color}]{t.pnl_pct:+.4f}%[/{pnl_color}]",
                f"[green]+{t.max_favorable:.4f}%[/green]",
                f"[red]{t.max_adverse:.4f}%[/red]",
            )

        console.print(log)
        console.print()


def main():
    settings = Settings()
    fetcher = DataFetcher(settings)

    console.print(Panel(
        "[bold]RSI Divergence Backtest[/bold]\n"
        "Strategy: Enter on divergence, exit after N bars\n"
        "Instruments: 6E, DXY | Timeframes: 15m, 1h",
        border_style="blue",
    ))

    all_results: List[BacktestResult] = []

    for inst_key, instrument in INSTRUMENTS.items():
        console.print(f"\n[bold]{instrument.name}[/bold]")

        # Fetch max history
        daily_df = fetcher.fetch_daily_ohlcv(instrument, bars=90)

        # Test on 15m and 1h (5m has limited history)
        for tf in instrument.timeframes[1:]:  # skip 5m
            console.print(f"  Fetching {tf.name} data...")

            # For backtest, fetch as much data as possible
            from config.instruments import TimeframeConfig
            bt_tf = TimeframeConfig(
                name=tf.name,
                yf_interval=tf.yf_interval,
                yf_period="1mo" if tf.name == "15m" else "3mo",
                swing_lookback=tf.swing_lookback,
                candles_to_fetch=5000,  # Get maximum
            )

            try:
                df = fetcher.fetch_ohlcv(instrument, bt_tf)
                console.print(f"  Got {len(df)} candles ({df.index[0]} to {df.index[-1]})")

                result = run_backtest(
                    df, daily_df,
                    instrument=inst_key,
                    timeframe=tf.name,
                    swing_lookback=tf.swing_lookback,
                    hold_bars=20,
                    window_size=200,
                )
                all_results.append(result)

            except Exception as e:
                console.print(f"  [red]Error: {e}[/red]")

    console.print("\n")
    print_results(all_results)

    # Summary
    total_trades = sum(r.total_trades for r in all_results)
    total_wins = sum(r.wins for r in all_results)
    if total_trades > 0:
        console.print(Panel(
            f"[bold]Overall: {total_trades} trades, "
            f"{total_wins / total_trades * 100:.1f}% win rate, "
            f"{sum(r.total_pnl_pct for r in all_results):+.3f}% total P&L[/bold]",
            border_style="green" if sum(r.total_pnl_pct for r in all_results) > 0 else "red",
        ))


if __name__ == "__main__":
    main()
