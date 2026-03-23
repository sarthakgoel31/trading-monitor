import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import praw

from config.settings import Settings

logger = logging.getLogger("trading-monitor.sentiment")

SUBREDDITS = ["Forex", "FuturesTrading", "Trading", "wallstreetbets"]


class RedditFeed:
    def __init__(self, settings: Settings):
        if not settings.reddit_client_id:
            self._reddit = None
            logger.warning("Reddit credentials not configured — skipping Reddit feed")
            return

        self._reddit = praw.Reddit(
            client_id=settings.reddit_client_id,
            client_secret=settings.reddit_client_secret,
            user_agent=settings.reddit_user_agent,
        )

    def fetch_posts(
        self, keywords: List[str], hours_back: int = 24, limit: int = 30
    ) -> List[Dict]:
        """Search subreddits for recent posts matching instrument keywords."""
        if not self._reddit:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        results = []
        seen_ids = set()

        for keyword in keywords[:3]:  # Limit keyword queries to avoid rate limits
            try:
                for sub_name in SUBREDDITS:
                    subreddit = self._reddit.subreddit(sub_name)
                    for post in subreddit.search(keyword, sort="new", time_filter="day", limit=limit // len(SUBREDDITS)):
                        if post.id in seen_ids:
                            continue
                        created = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
                        if created < cutoff:
                            continue

                        seen_ids.add(post.id)
                        results.append({
                            "title": post.title,
                            "body": (post.selftext or "")[:500],
                            "score": post.score,
                            "num_comments": post.num_comments,
                            "created_utc": post.created_utc,
                            "subreddit": sub_name,
                        })
            except Exception as e:
                logger.warning(f"Reddit search failed for '{keyword}' in {sub_name}: {e}")

        # Sort by engagement
        results.sort(key=lambda x: x["score"] + x["num_comments"], reverse=True)
        return results[:limit]
