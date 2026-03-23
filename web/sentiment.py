"""Market Intel — fetches sentiment from Scotia, News, TradingView. No paid APIs."""

import asyncio
import logging
import re
import ssl
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

try:
    import nltk
    try:
        nltk.data.find("sentiment/vader_lexicon.zip")
    except LookupError:
        try:
            ssl._create_default_https_context = ssl._create_unverified_context
        except AttributeError:
            pass
        nltk.download("vader_lexicon", quiet=True)
    from nltk.sentiment.vader import SentimentIntensityAnalyzer
    vader = SentimentIntensityAnalyzer()
except Exception:
    vader = None

try:
    from tradingview_ta import TA_Handler, Interval
    HAS_TV = True
except Exception:
    HAS_TV = False

REDDIT_SUBS = ["Forex", "FuturesTrading", "Trading"]
REDDIT_QUERIES = ["EURUSD", "EUR USD", "6E futures"]

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except Exception:
    HAS_FITZ = False

logger = logging.getLogger("trading-console.sentiment")

BULLISH_WORDS = {"rally", "surge", "breakout", "bullish", "hawkish", "gain", "strength", "support", "bounce", "recovery", "buy", "buying", "higher", "rise", "positive", "optimism", "advance", "climb"}
BEARISH_WORDS = {"sell", "selloff", "crash", "bearish", "dovish", "loss", "weakness", "decline", "drop", "fall", "negative", "pessimism", "slump", "lower", "slide", "sink", "tumble", "risk-off"}


def _score_text(text: str) -> float:
    """Score text using VADER + financial keywords."""
    if not text.strip():
        return 0.0
    words = set(re.findall(r"[a-z_-]+", text.lower()))
    bull = len(words & BULLISH_WORDS)
    bear = len(words & BEARISH_WORDS)
    fin_score = (bull - bear) / max(bull + bear, 1)
    vader_score = vader.polarity_scores(text)["compound"] if vader else 0.0
    return 0.4 * vader_score + 0.6 * fin_score


async def fetch_scotia() -> dict[str, Any] | None:
    """Fetch Scotia G10 FX Daily PDF and extract EUR section."""
    if not HAS_FITZ:
        return None
    url = "https://scotiaequityresearch.com/FX/G10_FX_Daily.pdf"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return None
            doc = fitz.open(stream=resp.content, filetype="pdf")
            full_text = ""
            for page in doc:
                full_text += page.get_text()
            doc.close()

        lines = full_text.split("\n")

        # Find where disclaimers start (skip everything after)
        disclaimer_start = len(lines)
        for i, line in enumerate(lines):
            if "Trademark" in line or "TM " in line.strip():
                disclaimer_start = i
                break

        # 1) Extract overview paragraph (main market narrative on page 1)
        overview = ""
        # Look for "FX Market Update" or the main paragraph after headers
        for i, line in enumerate(lines[:disclaimer_start]):
            stripped = line.strip()
            if "Market Update" in stripped or "FX Market" in stripped or (len(stripped) > 100 and i > 5):
                # Grab the full overview paragraph
                parts = []
                for j in range(i, min(i + 30, disclaimer_start)):
                    nxt = lines[j].strip()
                    if nxt.startswith("•") or (not nxt and parts):
                        break
                    if nxt and len(nxt) > 20:
                        parts.append(nxt)
                overview = " ".join(parts)
                break

        # 2) Extract all bullet points (EUR, USD, GBP, JPY, etc.)
        bullets = []
        for i, line in enumerate(lines[:disclaimer_start]):
            stripped = line.strip()
            if stripped.startswith("•"):
                parts = [stripped]
                for j in range(i + 1, min(i + 4, len(lines))):
                    nxt = lines[j].strip()
                    if not nxt or nxt.startswith("•"):
                        break
                    parts.append(nxt)
                bullets.append(" ".join(parts))

        # 3) Find dedicated EURUSD section (e.g., "EURUSD (1.1512) The EUR is...")
        eur_section = ""
        stop_headers = ["usdcad", "gbpusd", "usdjpy", "audusd", "nzdusd",
                        "usdmxn", "usdnok", "usdsek", "usdchf",
                        "short-term tech", "daily fx update", "global foreign exchange"]
        for i, line in enumerate(lines[:disclaimer_start]):
            stripped = line.strip()
            if stripped.upper().startswith("EURUSD") or stripped.upper().startswith("EUR/USD"):
                parts = []
                for j in range(i, min(i + 40, disclaimer_start)):
                    nxt = lines[j].strip()
                    if j > i and any(h in nxt.lower() for h in stop_headers):
                        break
                    if nxt and not nxt.startswith("•"):
                        parts.append(nxt)
                if parts:
                    section = " ".join(parts)
                    eur_section = (eur_section + " " + section).strip() if eur_section else section

        # EUR bullet (for headline)
        eur_bullet = next((b for b in bullets if "EUR" in b.upper() and "EUR" in b.split()[1].upper()), "")

        # Full text for scoring: overview + EUR section
        score_text = (overview + " " + eur_section + " " + eur_bullet).strip()
        score = _score_text(score_text) if score_text else 0.0

        return {
            "source": "Scotia FX Daily",
            "overview": overview[:500] if overview else "",
            "bullets": bullets,
            "eur_section": eur_section[:600] if eur_section else "",
            "headline": eur_bullet,
            "text": (eur_bullet + " | " + eur_section)[:600] if eur_section else eur_bullet[:300],
            "score": score,
        }
    except Exception as e:
        logger.error(f"Scotia fetch failed: {e}")
        return None


async def fetch_news() -> list[dict[str, Any]]:
    """Fetch Google News RSS for EUR/USD headlines."""
    headlines = []
    queries = ["EURUSD", "EUR USD ECB", "euro dollar forex"]
    seen = set()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for q in queries[:2]:
                url = f"https://news.google.com/rss/search?q={quote(q)}&hl=en-US&gl=US&ceid=US:en"
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.text)
                for item in root.findall(".//item"):
                    title = item.findtext("title", "")
                    source = item.findtext("source", "")
                    pub_date = item.findtext("pubDate", "")
                    if title in seen:
                        continue
                    seen.add(title)
                    score = _score_text(title)
                    tag = "bullish" if score > 0.1 else "bearish" if score < -0.1 else "neutral"
                    headlines.append({
                        "title": title,
                        "source": source,
                        "date": pub_date,
                        "score": score,
                        "tag": tag,
                    })
                if len(headlines) >= 5:
                    break
    except Exception as e:
        logger.error(f"News fetch failed: {e}")
    return headlines[:5]


def fetch_tradingview() -> dict[str, Any] | None:
    """Get TradingView technical analysis summary for 6E / EURUSD."""
    if not HAS_TV:
        return None
    try:
        handler = TA_Handler(
            symbol="EURUSD",
            screener="forex",
            exchange="FX_IDC",
            interval=Interval.INTERVAL_1_HOUR,
        )
        analysis = handler.get_analysis()
        s = analysis.summary
        buy = s.get("BUY", 0)
        sell = s.get("SELL", 0)
        neutral = s.get("NEUTRAL", 0)
        total = buy + sell + neutral
        score = (buy - sell) / total if total > 0 else 0

        # Oscillators vs MAs
        osc = analysis.oscillators.get("RECOMMENDATION", "NEUTRAL") if analysis.oscillators else "N/A"
        ma = analysis.moving_averages.get("RECOMMENDATION", "NEUTRAL") if analysis.moving_averages else "N/A"

        return {
            "buy": buy, "sell": sell, "neutral": neutral,
            "score": score,
            "recommendation": s.get("RECOMMENDATION", "NEUTRAL"),
            "oscillators": osc,
            "moving_averages": ma,
        }
    except Exception as e:
        logger.error(f"TradingView fetch failed: {e}")
        return None


async def fetch_reddit() -> list[dict[str, Any]]:
    """Fetch Reddit posts via public RSS feed — no API key needed."""
    posts = []
    seen = set()
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    subs = ["Forex", "Forexstrategy", "FuturesTrading"]
    queries = ["EURUSD", "EUR USD"]
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            for sub in subs:
                for q in queries:
                    url = f"https://www.reddit.com/r/{sub}/search.rss?q={quote(q)}&sort=new&t=week&limit=5&restrict_sr=on"
                    try:
                        resp = await client.get(url, headers=headers)
                        if resp.status_code != 200:
                            continue
                        # Parse Atom XML
                        root = ET.fromstring(resp.text)
                        ns = {"atom": "http://www.w3.org/2005/Atom"}
                        for entry in root.findall("atom:entry", ns):
                            entry_id = entry.findtext("atom:id", "", ns)
                            if not entry_id or entry_id in seen or entry_id.startswith("t5_"):
                                continue  # skip subreddit entries
                            seen.add(entry_id)
                            title = entry.findtext("atom:title", "", ns)
                            # Extract body text from HTML content
                            content_el = entry.find("atom:content", ns)
                            body_text = ""
                            if content_el is not None and content_el.text:
                                # Strip HTML tags for sentiment scoring
                                body_text = re.sub(r'<[^>]+>', ' ', content_el.text)
                                body_text = re.sub(r'\s+', ' ', body_text).strip()[:500]
                            link = entry.find("atom:link", ns)
                            post_url = link.get("href", "") if link is not None else ""
                            # Get subreddit from category
                            cat = entry.find("atom:category", ns)
                            post_sub = cat.get("label", f"r/{sub}") if cat is not None else f"r/{sub}"
                            # Score using title + body
                            full_text = f"{title}. {body_text}" if body_text else title
                            score = _score_text(full_text)
                            tag = "bullish" if score > 0.1 else "bearish" if score < -0.1 else "neutral"
                            posts.append({
                                "title": title[:100],
                                "subreddit": post_sub,
                                "upvotes": 0,  # RSS doesn't include upvotes
                                "comments": 0,
                                "score": score,
                                "tag": tag,
                                "url": post_url,
                            })
                    except Exception:
                        continue
                    await asyncio.sleep(0.3)
    except Exception as e:
        logger.error(f"Reddit fetch failed: {e}")
    return posts[:5]


async def fetch_all_sentiment() -> dict[str, Any]:
    """Fetch all sentiment sources in parallel. Returns dashboard-ready dict."""
    # Run all in parallel
    scotia_task = asyncio.create_task(fetch_scotia())
    news_task = asyncio.create_task(fetch_news())
    reddit_task = asyncio.create_task(fetch_reddit())
    tv_result = await asyncio.to_thread(fetch_tradingview)
    scotia_result = await scotia_task
    news_result = await news_task
    reddit_result = await reddit_task

    # Compute composite score
    scores = []
    weights = []

    if scotia_result and scotia_result.get("score"):
        scores.append(scotia_result["score"])
        weights.append(0.35)  # reports carry most weight

    if news_result:
        avg_news = sum(h["score"] for h in news_result) / len(news_result) if news_result else 0
        scores.append(avg_news)
        weights.append(0.25)

    if tv_result and tv_result.get("score") is not None:
        scores.append(tv_result["score"])
        weights.append(0.35)

    if reddit_result:
        # Weight by upvotes for better signal
        total_ups = sum(p.get("upvotes", 1) for p in reddit_result)
        if total_ups > 0:
            avg_reddit = sum(p["score"] * p.get("upvotes", 1) for p in reddit_result) / total_ups
        else:
            avg_reddit = sum(p["score"] for p in reddit_result) / len(reddit_result)
        scores.append(avg_reddit)
        weights.append(0.05)

    if scores and weights:
        composite = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
    else:
        composite = 0.0

    confidence = min(len(scores) / 3, 1.0) * 0.7  # max 70% confidence
    if abs(composite) > 0.15:
        label = "BULLISH" if composite > 0 else "BEARISH"
    else:
        label = "NEUTRAL"

    # Build insight
    parts = []
    if scotia_result and scotia_result.get("text"):
        parts.append(f"Scotia: {scotia_result['text'][:80]}")
    if tv_result:
        parts.append(f"TV {tv_result.get('recommendation', 'N/A')} (Buy:{tv_result.get('buy',0)} Sell:{tv_result.get('sell',0)})")
    if news_result:
        bull_count = sum(1 for h in news_result if h["tag"] == "bullish")
        bear_count = sum(1 for h in news_result if h["tag"] == "bearish")
        parts.append(f"News: {bull_count} bullish, {bear_count} bearish headlines")
    if reddit_result:
        r_bull = sum(1 for p in reddit_result if p["tag"] == "bullish")
        r_bear = sum(1 for p in reddit_result if p["tag"] == "bearish")
        parts.append(f"Reddit: {len(reddit_result)} posts ({r_bull} bull, {r_bear} bear)")
    insight = ". ".join(parts) if parts else "No sentiment data available."

    return {
        "composite": round(composite, 2),
        "label": label,
        "confidence": round(confidence * 100),
        "scotia": scotia_result,
        "news": news_result,
        "tradingview": tv_result,
        "reddit": reddit_result,
        "insight": insight,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
