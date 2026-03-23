import logging
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel

from config.instruments import INSTRUMENTS
from config.settings import Settings
from src.analysis.confluence import assess_confluence
from src.analysis.divergence import detect_divergences
from src.analysis.pivots import calculate_pivot_levels, check_pivot_proximity
from src.analysis.rsi import calculate_atr, calculate_rsi
from src.alerts.terminal import (
    TerminalNotifier,
    render_market_closed,
    render_scan_header,
    render_sentiment_summary,
)
from src.data.tv_analysis import TVAnalysis
from src.data.tv_fetcher import DataFetcher
from src.models.types import Alert, CompositeSentiment
from src.sentiment.llm_analyzer import SentimentAnalyzer
from src.sentiment.news_feed import NewsFeed
from src.sentiment.reddit_feed import RedditFeed
from src.sentiment.report_feed import ReportFeed
from src.sentiment.tv_ideas import TVIdeasFeed

console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("trading-monitor")


class SentimentCache:
    """Cache sentiment results to avoid hitting APIs too often."""

    def __init__(self, ttl_seconds: int = 900):
        self._cache: Dict[str, Tuple[datetime, CompositeSentiment]] = {}
        self._ttl = ttl_seconds

    def get(self, instrument: str) -> Optional[CompositeSentiment]:
        entry = self._cache.get(instrument)
        if entry is None:
            return None
        cached_at, result = entry
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age > self._ttl:
            return None
        return result

    def set(self, instrument: str, result: CompositeSentiment) -> None:
        self._cache[instrument] = (datetime.now(timezone.utc), result)


def fetch_sentiment(
    inst_key: str,
    instrument,
    reddit: RedditFeed,
    news: NewsFeed,
    tv_ideas: TVIdeasFeed,
    report_feed: ReportFeed,
    llm: SentimentAnalyzer,
    cache: SentimentCache,
) -> CompositeSentiment:
    """Fetch and analyze sentiment, using cache when available."""
    cached = cache.get(inst_key)
    if cached is not None:
        logger.info(f"Using cached sentiment for {inst_key}")
        return cached

    logger.info(f"Fetching fresh sentiment for {inst_key}...")

    # Reddit
    reddit_posts = reddit.fetch_posts(instrument.reddit_keywords)
    reddit_result = llm.analyze(
        "reddit",
        inst_key,
        [
            {"text": f"{p['title']} {p.get('body', '')}", "score": p.get("score", 0)}
            for p in reddit_posts
        ],
    )

    # Google News
    news_headlines = news.fetch_headlines(instrument.news_keywords)
    news_result = llm.analyze(
        "news",
        inst_key,
        [{"text": f"{h['title']} {h.get('description', '')}"} for h in news_headlines],
    )

    # Research reports (Scotia FX Daily, etc.)
    reports = report_feed.fetch_reports(inst_key)
    report_result = llm.analyze(
        "reports",
        inst_key,
        [{"text": r["text"], "score": 0} for r in reports],
    )

    # TradingView community sentiment
    tv_sentiment = tv_ideas.get_ta_sentiment(instrument)

    # Composite
    composite = llm.compute_composite(
        [reddit_result, news_result, report_result],
        tv_sentiment=tv_sentiment,
    )

    cache.set(inst_key, composite)
    return composite


def run_scan(
    settings: Settings,
    fetcher: DataFetcher,
    reddit: RedditFeed,
    news: NewsFeed,
    tv_ideas: TVIdeasFeed,
    report_feed: ReportFeed,
    llm: SentimentAnalyzer,
    notifier: TerminalNotifier,
    sentiment_cache: SentimentCache,
) -> None:
    """Single scan cycle — called every 5 minutes."""
    render_scan_header(datetime.now(timezone.utc))
    all_alerts: List[Alert] = []

    for inst_key, instrument in INSTRUMENTS.items():
        console.print(f"[bold]{instrument.name}[/bold]")

        # Daily data for pivots
        try:
            daily_df = fetcher.fetch_daily_ohlcv(instrument, bars=30)
            pivot_levels = calculate_pivot_levels(daily_df)
        except Exception as e:
            logger.error(f"Failed to fetch daily data for {inst_key}: {e}")
            console.print(f"  [red]Daily data error: {e}[/red]")
            continue

        # Sentiment (once per instrument, cached)
        sentiment = fetch_sentiment(
            inst_key, instrument, reddit, news, tv_ideas, report_feed, llm, sentiment_cache
        )
        render_sentiment_summary(sentiment)

        # Per-timeframe analysis
        for tf_config in instrument.timeframes:
            try:
                df = fetcher.fetch_ohlcv(instrument, tf_config)

                # Check for stale data (market closed)
                last_candle_age = (
                    datetime.now(timezone.utc) - df.index[-1].to_pydatetime().replace(tzinfo=timezone.utc)
                ).total_seconds()
                # If last candle is older than 3x the expected interval, market may be closed
                expected_seconds = {"5m": 300, "15m": 900, "1h": 3600}.get(tf_config.name, 300)
                if last_candle_age > expected_seconds * 3:
                    render_market_closed()
                    continue

                rsi = calculate_rsi(df, settings.rsi_period)
                atr_series = calculate_atr(df, settings.atr_period)
                current_atr = atr_series.iloc[-1]
                current_price = df["close"].iloc[-1]

                # Divergence detection
                divergences = detect_divergences(
                    df,
                    rsi,
                    lookback=tf_config.swing_lookback,
                    max_bars_apart=settings.divergence_max_bars_apart,
                    min_bars_apart=settings.divergence_min_bars_apart,
                    recent_only=settings.divergence_recent_only,
                )

                for d in divergences:
                    d.instrument = inst_key
                    d.timeframe = tf_config.name

                # Pivot proximity
                pivot_results = check_pivot_proximity(
                    current_price, pivot_levels, current_atr, settings.pivot_atr_multiplier
                )

                # TV summary
                tv_summary = TVAnalysis.get_summary(instrument, tf_config.name)

                # Confluence assessment for each divergence
                for div in divergences:
                    alert = assess_confluence(
                        inst_key, tf_config.name, div, pivot_results, sentiment, tv_summary
                    )
                    if alert:
                        all_alerts.append(alert)

                if not divergences:
                    console.print(f"  [dim]{tf_config.name}: No divergence[/dim]")

            except Exception as e:
                logger.error(f"Error scanning {inst_key} {tf_config.name}: {e}")
                console.print(f"  [red]{tf_config.name} error: {e}[/red]")

        console.print()

    # Render all alerts
    notifier.send(all_alerts)


def main():
    settings = Settings()

    console.print(
        Panel(
            "[bold green]Trading Monitor[/bold green]\n"
            f"Instruments: {', '.join(INSTRUMENTS.keys())}\n"
            f"Timeframes: 5m, 15m, 1h\n"
            f"Scan interval: {settings.poll_interval_seconds}s\n"
            f"Sentiment cache: {settings.sentiment_cache_seconds}s",
            title="[bold]Config[/bold]",
            border_style="blue",
        )
    )

    # Initialize components
    fetcher = DataFetcher(settings)
    reddit = RedditFeed(settings)
    news_feed = NewsFeed()
    tv_ideas = TVIdeasFeed()
    report_feed = ReportFeed()
    llm = SentimentAnalyzer(settings)
    notifier = TerminalNotifier()
    sentiment_cache = SentimentCache(ttl_seconds=settings.sentiment_cache_seconds)

    # Initial scan
    try:
        run_scan(settings, fetcher, reddit, news_feed, tv_ideas, report_feed, llm, notifier, sentiment_cache)
    except Exception as e:
        logger.error(f"Initial scan failed: {e}")
        console.print(f"[red]Initial scan failed: {e}[/red]")

    # Recurring loop
    while True:
        try:
            console.print(
                f"[dim]Next scan in {settings.poll_interval_seconds}s... (Ctrl+C to stop)[/dim]"
            )
            time.sleep(settings.poll_interval_seconds)
            run_scan(settings, fetcher, reddit, news_feed, tv_ideas, report_feed, llm, notifier, sentiment_cache)
        except KeyboardInterrupt:
            console.print("\n[bold yellow]Monitor stopped.[/bold yellow]")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Scan failed: {e}")
            console.print(f"[red]Scan failed: {e}. Retrying next cycle...[/red]")


if __name__ == "__main__":
    main()
