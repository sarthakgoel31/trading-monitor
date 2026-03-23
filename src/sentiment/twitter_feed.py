import logging
from typing import Dict, List

logger = logging.getLogger("trading-monitor.sentiment")


class TwitterFeed:
    """Stub for X/Twitter feed.
    X API v2 Free tier only supports posting, not reading/searching.
    Basic tier costs $100/mo. This stub maintains the interface for future use.
    """

    def __init__(self, bearer_token: str = ""):
        if bearer_token:
            logger.info("Twitter bearer token provided but feed is not yet implemented")

    def fetch_posts(self, keywords: List[str], hours_back: int = 24, limit: int = 30) -> List[Dict]:
        """Stub — returns empty list."""
        return []
