"""Forex Factory calendar scraper + news filter."""

import logging
import re
from datetime import datetime, timedelta, timezone, time
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("trading-monitor.news")


def fetch_forex_factory_calendar(weeks_back: int = 4) -> List[Dict]:
    """Scrape Forex Factory calendar for recent and upcoming events.
    Returns list of {datetime, currency, impact, event, actual, forecast, previous}.
    """
    events = []
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
    })

    today = datetime.now()
    for w in range(weeks_back):
        week_start = today - timedelta(weeks=w)
        # Forex Factory uses format: jan1.2026
        month_str = week_start.strftime("%b").lower()
        day_str = week_start.strftime("%-d")
        year_str = week_start.strftime("%Y")
        url = f"https://www.forexfactory.com/calendar?week={month_str}{day_str}.{year_str}"

        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            page_events = _parse_ff_page(resp.text, week_start.year)
            events.extend(page_events)
        except Exception as e:
            logger.warning(f"FF calendar fetch failed for week of {week_start.date()}: {e}")

    # Deduplicate by datetime + event name
    seen = set()
    unique = []
    for ev in events:
        key = f"{ev.get('datetime', '')}_{ev.get('event', '')}"
        if key not in seen:
            seen.add(key)
            unique.append(ev)

    logger.info(f"Fetched {len(unique)} Forex Factory events")
    return unique


def _parse_ff_page(html: str, year: int) -> List[Dict]:
    """Parse a Forex Factory calendar HTML page."""
    events = []
    soup = BeautifulSoup(html, "html.parser")

    rows = soup.select("tr.calendar__row")
    current_date = None

    for row in rows:
        # Date cell
        date_cell = row.select_one("td.calendar__date span")
        if date_cell and date_cell.text.strip():
            date_text = date_cell.text.strip()
            try:
                current_date = _parse_ff_date(date_text, year)
            except Exception:
                pass

        if current_date is None:
            continue

        # Time
        time_cell = row.select_one("td.calendar__time")
        time_text = time_cell.text.strip() if time_cell else ""

        # Currency
        curr_cell = row.select_one("td.calendar__currency")
        currency = curr_cell.text.strip() if curr_cell else ""

        # Impact
        impact_cell = row.select_one("td.calendar__impact span")
        impact = "low"
        if impact_cell:
            classes = impact_cell.get("class", [])
            class_str = " ".join(classes)
            if "high" in class_str or "red" in class_str:
                impact = "high"
            elif "medium" in class_str or "ora" in class_str or "orange" in class_str:
                impact = "medium"

        # Event name
        event_cell = row.select_one("td.calendar__event span")
        event_name = event_cell.text.strip() if event_cell else ""

        if not event_name:
            continue

        # Actual, Forecast, Previous
        actual_cell = row.select_one("td.calendar__actual")
        forecast_cell = row.select_one("td.calendar__forecast")
        previous_cell = row.select_one("td.calendar__previous")

        actual = actual_cell.text.strip() if actual_cell else ""
        forecast = forecast_cell.text.strip() if forecast_cell else ""
        previous = previous_cell.text.strip() if previous_cell else ""

        # Parse time
        event_dt = _combine_date_time(current_date, time_text)

        events.append({
            "datetime": event_dt,
            "date": current_date,
            "time_str": time_text,
            "currency": currency,
            "impact": impact,
            "event": event_name,
            "actual": actual,
            "forecast": forecast,
            "previous": previous,
        })

    return events


def _parse_ff_date(text: str, year: int) -> datetime:
    """Parse FF date like 'Mon Mar 3' or 'Wed Mar 19'."""
    # Remove day name
    parts = text.split()
    if len(parts) >= 3:
        month_str = parts[1]
        day_str = parts[2]
    elif len(parts) == 2:
        month_str = parts[0]
        day_str = parts[1]
    else:
        raise ValueError(f"Can't parse FF date: {text}")

    return datetime.strptime(f"{month_str} {day_str} {year}", "%b %d %Y")


def _combine_date_time(date: datetime, time_text: str) -> Optional[datetime]:
    """Combine date + FF time string (e.g., '8:30am', 'Tentative', 'All Day')."""
    if not time_text or time_text in ("", "Tentative", "All Day"):
        return date

    try:
        # FF times are ET (Eastern Time)
        time_text = time_text.replace("am", " AM").replace("pm", " PM")
        t = datetime.strptime(time_text.strip(), "%I:%M %p")
        return date.replace(hour=t.hour, minute=t.minute)
    except Exception:
        return date


# ─── Filters for backtesting ───


def build_news_blackout_map(
    events: List[Dict],
    blackout_minutes_high: int = 30,
    blackout_minutes_medium: int = 15,
    currencies: Tuple[str, ...] = ("USD", "EUR"),
) -> Dict[str, List[Tuple[datetime, datetime, str, str]]]:
    """Build a map of blackout windows per date.
    Returns {date_str: [(start, end, impact, event_name), ...]}
    """
    blackout = {}

    for ev in events:
        if ev["currency"] not in currencies:
            continue
        if ev["impact"] == "low":
            continue

        dt = ev.get("datetime")
        if dt is None:
            continue

        if ev["impact"] == "high":
            delta = timedelta(minutes=blackout_minutes_high)
        else:
            delta = timedelta(minutes=blackout_minutes_medium)

        start = dt - delta
        end = dt + delta
        date_key = dt.strftime("%Y-%m-%d")

        if date_key not in blackout:
            blackout[date_key] = []
        blackout[date_key].append((start, end, ev["impact"], ev["event"]))

    return blackout


def is_in_news_blackout(
    bar_time: datetime,
    blackout_map: Dict,
    ist_offset_hours: float = 5.5,
) -> Tuple[bool, str]:
    """Check if a bar timestamp falls within a news blackout window.
    FF times are ET, bar_time is IST. Convert to compare.
    ET = IST - 10.5 hours (during EDT) or IST - 9.5 (during EST).
    We approximate ET = IST - 10 hours.
    """
    # Convert IST bar_time to approximate ET
    et_approx = bar_time - timedelta(hours=10)
    date_key = et_approx.strftime("%Y-%m-%d")

    windows = blackout_map.get(date_key, [])
    for start, end, impact, event_name in windows:
        if start <= et_approx <= end:
            return True, f"{impact}: {event_name}"

    # Also check previous day (for late-night ET events)
    prev_key = (et_approx - timedelta(days=1)).strftime("%Y-%m-%d")
    for start, end, impact, event_name in blackout_map.get(prev_key, []):
        if start <= et_approx <= end:
            return True, f"{impact}: {event_name}"

    return False, ""


def is_news_blackout(bar_time, events: List[Dict], blackout_minutes: int = 30) -> bool:
    """Simple check: is bar_time within blackout_minutes of any high-impact USD/EUR event?"""
    if not events:
        return False
    for e in events:
        if e.get("impact") != "high":
            continue
        if e.get("currency", "") not in ("USD", "EUR"):
            continue
        evt_dt = e.get("datetime")
        if evt_dt is None:
            continue
        try:
            delta = abs((bar_time - evt_dt).total_seconds()) / 60
            if delta <= blackout_minutes:
                return True
        except (TypeError, AttributeError):
            continue
    return False
