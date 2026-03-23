from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # TradingView
    tv_username: str = ""
    tv_password: str = ""

    # Reddit
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "trading-monitor/1.0"

    # Twitter (optional, future)
    twitter_bearer_token: Optional[str] = None

    # Analysis params
    rsi_period: int = 14
    swing_lookback_default: int = 5
    atr_period: int = 14
    pivot_atr_multiplier: float = 0.5
    divergence_max_bars_apart: int = 80
    divergence_min_bars_apart: int = 5
    divergence_recent_only: int = 30

    # Scheduler
    poll_interval_seconds: int = 300  # 5 minutes
    sentiment_cache_seconds: int = 900  # 15 minutes

    # Sentiment weights
    sentiment_weight_reddit: float = 0.15
    sentiment_weight_news: float = 0.25
    sentiment_weight_reports: float = 0.30  # Scotia FX Daily + other research
    sentiment_weight_tv: float = 0.20
    sentiment_weight_twitter: float = 0.10

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
