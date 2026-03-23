from datetime import datetime, timezone
from typing import List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.alerts.notifier import Notifier
from src.models.types import (
    Alert,
    CompositeSentiment,
    DivergenceType,
    SignalStrength,
)

console = Console()

# Color mapping
DIV_COLORS = {
    DivergenceType.REGULAR_BULLISH: "green",
    DivergenceType.REGULAR_BEARISH: "red",
    DivergenceType.HIDDEN_BULLISH: "cyan",
    DivergenceType.HIDDEN_BEARISH: "magenta",
}

STRENGTH_ICONS = {
    SignalStrength.STRONG: "[bold bright_white on red] STRONG [/]",
    SignalStrength.MODERATE: "[bold yellow] MODERATE [/]",
    SignalStrength.WEAK: "[dim] WEAK [/]",
}


class TerminalNotifier(Notifier):
    def send(self, alerts: List[Alert]) -> None:
        if not alerts:
            console.print("[dim]No divergence signals detected this scan.[/dim]\n")
            return

        # Sort by confluence score descending
        alerts.sort(key=lambda a: a.confluence_score, reverse=True)

        table = Table(
            title="RSI Divergence Signals",
            show_header=True,
            header_style="bold white",
            border_style="blue",
            expand=True,
        )
        table.add_column("Instrument", style="bold", width=6)
        table.add_column("TF", width=4)
        table.add_column("Divergence", width=22)
        table.add_column("Strength", width=12)
        table.add_column("Pivot", width=18)
        table.add_column("Sentiment", width=12)
        table.add_column("Score", width=6, justify="right")

        for alert in alerts:
            div = alert.divergence
            div_type = div.type if div else None
            color = DIV_COLORS.get(div_type, "white")
            div_label = div_type.value.replace("_", " ").title() if div_type else "N/A"

            strength = STRENGTH_ICONS.get(div.strength, "") if div else ""

            # Pivot info
            pivot_str = ""
            if alert.pivot_proximity:
                nearest = alert.pivot_proximity[0]
                if nearest.is_near:
                    pivot_str = f"[bold yellow]{nearest.level.name} ({nearest.level.value:.5f})[/]"
                else:
                    pivot_str = f"[dim]{nearest.level.name} ({nearest.distance_atr_ratio:.1f}x ATR)[/dim]"

            # Sentiment
            sent_str = ""
            if alert.sentiment:
                s = alert.sentiment.overall_score
                if s > 0.3:
                    sent_str = f"[green]+{s:.2f}[/green]"
                elif s < -0.3:
                    sent_str = f"[red]{s:.2f}[/red]"
                else:
                    sent_str = f"[yellow]{s:+.2f}[/yellow]"

            # Score coloring
            score = alert.confluence_score
            if score >= 50:
                score_str = f"[bold bright_white on green] {score:.0f} [/]"
            elif score >= 30:
                score_str = f"[bold yellow]{score:.0f}[/]"
            else:
                score_str = f"[dim]{score:.0f}[/dim]"

            table.add_row(
                alert.instrument,
                alert.timeframe,
                f"[{color}]{div_label}[/{color}]",
                strength,
                pivot_str,
                sent_str,
                score_str,
            )

        console.print(table)
        console.print()

        # Print top alert detail
        top = alerts[0]
        if top.confluence_score >= 30:
            console.print(
                Panel(
                    top.headline,
                    title=f"[bold]Top Signal (Score: {top.confluence_score:.0f})[/bold]",
                    border_style="green" if "bullish" in (top.divergence.type.value if top.divergence else "") else "red",
                )
            )


def render_scan_header(timestamp: datetime) -> None:
    """Print the scan cycle header."""
    ts = timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    console.print(f"\n[bold blue]{'='*60}[/bold blue]")
    console.print(f"[bold blue]  SCAN @ {ts}[/bold blue]")
    console.print(f"[bold blue]{'='*60}[/bold blue]\n")


def render_sentiment_summary(sentiment: Optional[CompositeSentiment]) -> None:
    """Print sentiment summary for an instrument."""
    if not sentiment:
        return

    s = sentiment.overall_score
    if s > 0.3:
        color = "green"
        label = "BULLISH"
    elif s < -0.3:
        color = "red"
        label = "BEARISH"
    else:
        color = "yellow"
        label = "NEUTRAL"

    console.print(
        f"  [{color}]Sentiment [{sentiment.instrument}]: {label} "
        f"({s:+.2f}, confidence: {sentiment.overall_confidence:.0%})[/{color}]"
    )
    for src in sentiment.sources:
        console.print(
            f"    [dim]- {src.source}: {src.score:+.2f} — {src.summary}[/dim]"
        )
    console.print()


def render_market_closed() -> None:
    """Print market closed message."""
    console.print("[dim yellow]  Market appears closed (stale data). Waiting...[/dim yellow]\n")
