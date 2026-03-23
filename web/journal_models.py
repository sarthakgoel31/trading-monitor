import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / "data" / "trading_journal.db"


def get_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time TEXT,
            status TEXT DEFAULT 'active',
            mood_before TEXT,
            sleep_hours REAL,
            sleep_quality TEXT,
            caffeine_cups INTEGER DEFAULT 0,
            exercise_today INTEGER DEFAULT 0,
            readiness_score REAL,
            session_rating INTEGER,
            lesson TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS health_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id),
            timestamp TEXT NOT NULL,
            metric_type TEXT NOT NULL,
            value REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sleep_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id),
            sleep_start TEXT,
            sleep_end TEXT,
            duration_hours REAL,
            deep_sleep_mins INTEGER,
            rem_sleep_mins INTEGER,
            awake_mins INTEGER
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id),
            entry_time TEXT,
            exit_time TEXT,
            direction TEXT,
            entry_price REAL,
            exit_price REAL,
            pnl_pips REAL,
            outcome TEXT,
            per_plan INTEGER DEFAULT 1,
            rules_broken TEXT,
            confidence_before INTEGER,
            emotion_before TEXT,
            emotion_during TEXT,
            emotion_after TEXT,
            notes TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_health_session ON health_samples(session_id);
        CREATE INDEX IF NOT EXISTS idx_health_type ON health_samples(metric_type);
        CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_id);
    """)
    conn.commit()
    conn.close()


def dict_from_row(row):
    if row is None:
        return None
    return dict(row)


def dicts_from_rows(rows):
    return [dict(r) for r in rows]
