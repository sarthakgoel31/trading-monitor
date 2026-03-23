import logging
import xml.etree.ElementTree as ET
from typing import Dict, List
from urllib.parse import quote

import requests

logger = logging.getLogger("trading-monitor.sentiment")

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


class NewsFeed:
    """Fetch Google News headlines via RSS (no API key needed)."""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (trading-monitor/1.0)"
        })

    def fetch_headlines(self, keywords: List[str], limit: int = 20) -> List[Dict]:
        """Fetch Google News headlines for instrument keywords via RSS."""
        results = []
        seen_titles = set()

        for keyword in keywords[:3]:
            try:
                url = GOOGLE_NEWS_RSS.format(query=quote(keyword))
                resp = self._session.get(url, timeout=10)
                resp.raise_for_status()

                root = ET.fromstring(resp.content)
                for item in root.iter("item"):
                    title = item.findtext("title", "")
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)

                    description = item.findtext("description", "")
                    source = item.findtext("source", "")
                    pub_date = item.findtext("pubDate", "")

                    results.append({
                        "title": title,
                        "description": description[:300] if description else "",
                        "source": source,
                        "published": pub_date,
                    })

            except Exception as e:
                logger.warning(f"News fetch failed for '{keyword}': {e}")

        return results[:limit]
