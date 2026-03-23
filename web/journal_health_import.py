"""Parse Apple Health export.xml and backfill biometric data into trading sessions."""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from .journal_models import get_db


# HealthKit type identifiers
HR_TYPE = "HKQuantityTypeIdentifierHeartRate"
HRV_TYPE = "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"
SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"

# Sleep values that count as "asleep"
ASLEEP_VALUES = {"AsleepCore", "AsleepDeep", "AsleepREM", "Asleep", "HKCategoryValueSleepAnalysisAsleep"}

DATE_FMT = "%Y-%m-%d %H:%M:%S %z"


def parse_date(s):
    """Parse HealthKit date string like '2026-04-10 14:55:06 +0530'."""
    try:
        return datetime.strptime(s, DATE_FMT)
    except ValueError:
        # Try without timezone
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


def import_health_export(export_path: str, days_back: int = 30):
    """Import health data from Apple Health export.xml.

    Args:
        export_path: Path to export.xml (or the unzipped folder containing it)
        days_back: Only import records from the last N days (default 30)

    Returns:
        dict with counts of imported records
    """
    path = Path(export_path)
    if path.is_dir():
        # Look for export.xml inside
        xml_path = path / "apple_health_export" / "export.xml"
        if not xml_path.exists():
            xml_path = path / "export.xml"
    else:
        xml_path = path

    if not xml_path.exists():
        raise FileNotFoundError(f"export.xml not found at {xml_path}")

    cutoff = datetime.now().astimezone() - timedelta(days=days_back)

    # Get all sessions to match against
    conn = get_db()
    sessions = conn.execute(
        "SELECT id, start_time, end_time FROM sessions WHERE status='completed'"
    ).fetchall()
    sessions = [dict(s) for s in sessions]

    # Parse session times (add 5-min buffer on each side for matching)
    BUFFER = timedelta(minutes=5)
    for s in sessions:
        s["start_dt"] = datetime.fromisoformat(s["start_time"]) - BUFFER
        s["end_dt"] = (datetime.fromisoformat(s["end_time"]) if s["end_time"] else datetime.fromisoformat(s["start_time"]) + timedelta(hours=4)) + BUFFER

    counts = {"hr": 0, "hrv": 0, "sleep_sessions": 0, "skipped": 0}

    # Stream-parse the XML (can be 100MB+)
    print(f"Parsing {xml_path}...")
    for event, elem in ET.iterparse(str(xml_path), events=("end",)):
        if elem.tag != "Record":
            continue

        rec_type = elem.get("type")
        if rec_type not in (HR_TYPE, HRV_TYPE, SLEEP_TYPE):
            elem.clear()
            continue

        start_str = elem.get("startDate")
        if not start_str:
            elem.clear()
            continue

        try:
            start_dt = parse_date(start_str)
        except (ValueError, TypeError):
            elem.clear()
            continue

        # Skip old records
        if start_dt.tzinfo and start_dt < cutoff:
            elem.clear()
            continue

        if rec_type == HR_TYPE:
            value = float(elem.get("value", 0))
            timestamp = start_str
            # Find matching session
            for s in sessions:
                if s["start_dt"] <= start_dt.replace(tzinfo=None) <= s["end_dt"]:
                    # Check if already imported
                    existing = conn.execute(
                        "SELECT id FROM health_samples WHERE session_id=? AND timestamp=? AND metric_type='hr'",
                        (s["id"], timestamp)
                    ).fetchone()
                    if not existing:
                        conn.execute(
                            "INSERT INTO health_samples (session_id, timestamp, metric_type, value) "
                            "VALUES (?, ?, 'hr', ?)",
                            (s["id"], timestamp, value)
                        )
                        counts["hr"] += 1
                    break

        elif rec_type == HRV_TYPE:
            value = float(elem.get("value", 0))
            timestamp = start_str
            for s in sessions:
                if s["start_dt"] <= start_dt.replace(tzinfo=None) <= s["end_dt"]:
                    existing = conn.execute(
                        "SELECT id FROM health_samples WHERE session_id=? AND timestamp=? AND metric_type='hrv'",
                        (s["id"], timestamp)
                    ).fetchone()
                    if not existing:
                        conn.execute(
                            "INSERT INTO health_samples (session_id, timestamp, metric_type, value) "
                            "VALUES (?, ?, 'hrv', ?)",
                            (s["id"], timestamp, value)
                        )
                        counts["hrv"] += 1
                    break

        elif rec_type == SLEEP_TYPE:
            sleep_value = elem.get("value", "")
            end_str = elem.get("endDate", "")

            if sleep_value in ASLEEP_VALUES and end_str:
                try:
                    end_dt = parse_date(end_str)
                    duration_mins = (end_dt - start_dt).total_seconds() / 60

                    # Match sleep to the NEXT day's session
                    # (sleep at night → affects next morning's trading)
                    sleep_date = start_dt.date()
                    next_day = sleep_date + timedelta(days=1)

                    for s in sessions:
                        session_date = s["start_dt"].date()
                        if session_date == next_day or session_date == sleep_date:
                            # Update session sleep data
                            existing_sleep = conn.execute(
                                "SELECT id, duration_hours FROM sleep_data WHERE session_id=?",
                                (s["id"],)
                            ).fetchone()

                            if existing_sleep:
                                # Add to existing duration
                                new_hrs = (existing_sleep["duration_hours"] or 0) + (duration_mins / 60)
                                conn.execute(
                                    "UPDATE sleep_data SET duration_hours=? WHERE id=?",
                                    (round(new_hrs, 2), existing_sleep["id"])
                                )
                            else:
                                conn.execute(
                                    "INSERT INTO sleep_data (session_id, sleep_start, sleep_end, duration_hours) "
                                    "VALUES (?, ?, ?, ?)",
                                    (s["id"], start_str, end_str, round(duration_mins / 60, 2))
                                )
                                counts["sleep_sessions"] += 1

                            # Also update the session's sleep_hours
                            total_sleep = conn.execute(
                                "SELECT SUM(duration_hours) as total FROM sleep_data WHERE session_id=?",
                                (s["id"],)
                            ).fetchone()
                            if total_sleep and total_sleep["total"]:
                                sleep_hrs = round(total_sleep["total"], 1)
                                quality = "good" if sleep_hrs >= 7 else ("fair" if sleep_hrs >= 6 else "poor")
                                conn.execute(
                                    "UPDATE sessions SET sleep_hours=?, sleep_quality=? WHERE id=?",
                                    (sleep_hrs, quality, s["id"])
                                )
                            break
                except (ValueError, TypeError):
                    pass

        elem.clear()

    conn.commit()
    conn.close()

    print(f"Imported: {counts['hr']} HR samples, {counts['hrv']} HRV samples, {counts['sleep_sessions']} sleep records")
    return counts


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python health_import.py <path-to-export.xml-or-folder> [days_back]")
        print("  Example: python health_import.py ~/Desktop/export.xml")
        print("  Example: python health_import.py ~/Desktop/apple_health_export/ 14")
        sys.exit(1)

    path = sys.argv[1]
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    result = import_health_export(path, days)
    print(f"\nDone! {result}")
