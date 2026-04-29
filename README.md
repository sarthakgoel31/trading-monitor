# Trading Monitor

**6E Delta-Hero scanner with multi-timeframe analysis and 5-source sentiment aggregation.**

Real-time EUR/USD futures scanner that detects high-probability trade setups by combining RSI divergences, confluence-based pivot levels, and market sentiment.

---

## Why

Manual chart scanning across multiple timeframes is slow and error-prone. By the time you spot a divergence, check confluence with pivots, and read the sentiment -- the setup is gone. Existing scanners either lack multi-source sentiment or don't understand futures-specific mechanics like pip hunt detection.

Trading Monitor automates the entire workflow: it fetches data, computes confluence levels, detects divergences, aggregates sentiment from 5 independent sources, and delivers trade-ready alerts through a rich terminal UI and web dashboard. One command, all the analysis.

---

## Features

| Feature | Description |
|---------|-------------|
| RSI Divergence Detection | Scans bullish/bearish divergences across configurable timeframes |
| Confluence Pivot Levels | Classifies levels as important (2 confluent) or ultra-important (3+ confluent) |
| Mega Engine | Unified entry/exit signals, pip hunt detection, and level computation |
| 5-Source Sentiment | Reddit, Google News, Scotia FX Daily (PDF), TradingView ideas, LLM scoring |
| Rich Terminal UI | Color-coded alerts, market status panels, sentiment summaries |
| Backtesting Suite | Standard, combo, optimizer, and scheduled backtest modes |
| ATR Filtering | Volatility-aware alert thresholds |
| Pivot Proximity | Real-time price distance from key levels |
| Practice Mode | Paper trading simulation for strategy testing |

---

## Web Dashboard & Trading Console

The project includes a full **FastAPI web server** (`web/`) that turns the scanner into a live, interactive trading console at `localhost:8420`.

| Feature | Description |
|---------|-------------|
| WebSocket Live Scanning | Real-time market scanning with push updates to the browser |
| HTML Dashboard | Multi-panel trading console with levels, delta signals, sentiment, and trade plan |
| Trading Journal | SQLite-backed session tracking with trades, mood, sleep, caffeine, and rule adherence |
| Apple Health Import | Import heart rate, HRV, and sleep data to correlate biometrics with trading performance |
| Replay Generation | Generate DH|S2 replay HTML from .scid tick data for post-session review |
| Discipline Lessons | 25+ personalized micro-lessons served between scans |
| Windows Data Pusher | Pull Sierra Chart .scid tick data from Windows PC over the network |

```bash
python -m web
# Open http://localhost:8420
```

---

## How It Works

```
DataFetcher (TradingView + Sierra .scid)
    --> Multi-Timeframe Analysis (RSI, Pivots, Divergence, ATR)

Sentiment Feeds (Reddit, News, Scotia FX, TradingView Ideas)
    --> LLM Sentiment Analyzer (composite scoring)

    --> Mega Engine (Confluence + Sentiment + Pip Hunt)
    --> Rich Terminal Alerts / WebSocket Dashboard
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Web Server | FastAPI + Uvicorn + WebSocket |
| Data Feeds | TradingView (tvdatafeed), yfinance, Sierra Chart (.scid) |
| Technical Analysis | pandas, pandas_ta, numpy |
| Sentiment | PRAW (Reddit), PyMuPDF (PDF reports), NLTK |
| LLM Analysis | LLM-powered sentiment scoring |
| Journal | SQLite (trading sessions, trades, biometrics) |
| Scheduling | APScheduler (async) |
| Terminal UI | Rich |
| Config | Pydantic Settings + python-dotenv |

---

## Architecture

```
src/
├── main.py              -- Entry point, scanner loop
├── analysis/            -- RSI, divergence, pivots, confluence
├── data/                -- TradingView data fetching, Sierra .scid parser
├── mega/                -- Unified engine (entries, exits, levels, pip hunt)
├── sentiment/           -- Reddit, news, reports, TradingView, LLM analyzer
├── alerts/              -- Terminal notification rendering
└── models/              -- Pydantic type definitions

web/
├── server.py            -- FastAPI server with WebSocket + background scanner
├── analysis.py          -- Web-specific analysis pipeline
├── sentiment.py         -- Sentiment aggregation for web
├── fetcher.py           -- Data fetcher for web context
├── journal_models.py    -- SQLite models for trading journal
├── journal_analysis.py  -- Session stats, correlations, insights engine
├── journal_health_import.py -- Apple Health XML parser + biometric backfill
├── lessons.py           -- Discipline micro-lesson bank
├── replay_gen.py        -- DH|S2 replay HTML generator from .scid data
├── windows_pusher.py    -- Pull .scid from Windows PC
└── static/              -- Dashboard HTML (index, rules, replay)

config/
├── settings.py          -- Pydantic Settings configuration
└── instruments.py       -- Instrument definitions (6E = $12.50/pip)
```

---

## Getting Started

```bash
git clone https://github.com/sarthakgoel31/trading-monitor.git
cd trading-monitor
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

```env
# .env
REDDIT_CLIENT_ID=your_reddit_client_id
REDDIT_CLIENT_SECRET=your_reddit_client_secret
REDDIT_USER_AGENT=trading-monitor
```

```bash
# Terminal scanner
python -m src.main

# Web dashboard
python -m web
# Open http://localhost:8420
```

---

## Strategy: DH|S2

The core strategy (Delta-Hero with S2 filter) identifies:

1. **RSI divergences** across multiple timeframes
2. **Confluence clusters** where 2+ independent levels overlap (pivot, fib, prior high/low)
3. **Pip hunt signals** detecting stop-run patterns near key levels
4. **Sentiment confirmation** from 5 independent sources

Levels are classified as:
- **Important** -- 2 confluent signals
- **Ultra-Important** -- 3+ confluent signals

---

## Status

| Component | Status |
|-----------|--------|
| Multi-TF RSI divergence detection | Done |
| Confluence-based pivot levels | Done |
| Mega engine (unified signals) | Done |
| Reddit sentiment (PRAW) | Done |
| Google News sentiment | Done |
| Scotia FX Daily PDF extraction | Done |
| TradingView idea parsing | Done |
| LLM sentiment scoring | Done |
| Rich terminal UI | Done |
| FastAPI web dashboard | Done |
| WebSocket live scanning | Done |
| Trading journal + biometrics | Done |
| Apple Health import | Done |
| Replay generation | Done |
| Backtesting suite | Done |
| Practice mode | Done |
| Sierra Chart study (ACSIL C++) | Done (v11) |

---

<p align="center">
  <sub>Built with <a href="https://claude.ai/claude-code">Claude Code</a></sub>
</p>
