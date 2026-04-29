# Trading Monitor

**6E Delta-Hero scanner with multi-timeframe analysis and multi-source sentiment.**

---

## What It Does

Trading Monitor is a real-time scanner for the 6E (EUR/USD Futures) market that detects RSI divergences, computes confluence-based pivot levels, and aggregates sentiment from five sources. It identifies high-probability trade setups by combining technical analysis across multiple timeframes with market sentiment, then delivers alerts through a rich terminal UI.

---

## Key Features

- **Multi-Timeframe RSI Divergence Detection** -- Scans for bullish/bearish divergences across configurable timeframes
- **Confluence-Based Pivot Levels** -- Classifies levels as important (2 confluent) or ultra-important (3+ confluent)
- **Mega Engine** -- Integrated entry/exit signals, pip hunt detection, and level computation in a unified engine
- **4-Source Sentiment Aggregation**:
  - Reddit (r/Forex, r/Trading) via PRAW
  - Google News via feed parsing
  - Scotia FX Daily reports (PDF extraction via PyMuPDF)
  - TradingView community ideas
  - LLM-powered sentiment analysis for composite scoring
- **Rich Terminal UI** -- Color-coded alerts, market status panels, and sentiment summaries via Rich
- **Backtesting Suite** -- Multiple backtest modes (standard, combo, optimizer, scheduled) for strategy validation
- **ATR-Based Filtering** -- Volatility-aware alert thresholds
- **Pivot Proximity Detection** -- Real-time price distance from key levels
- **Practice Mode** -- Paper trading simulation for strategy testing
- **Configurable Instruments** -- Pydantic-based settings with dotenv support

---

## Web Dashboard & Trading Console

The project includes a full **FastAPI web server** (`web/`) with ~3,000 lines of Python that turns the scanner into a live, interactive trading console at `localhost:8420`.

- **WebSocket Live Scanning** -- Real-time market scanning with push updates to the browser
- **HTML Dashboard** -- Multi-panel trading console with levels, delta signals, sentiment, and trade plan
- **Trading Journal** -- SQLite-backed session tracking with trades, mood, sleep, caffeine, and rule adherence
- **Apple Health Import** -- Import heart rate, HRV, and sleep data from Apple Health exports to correlate biometrics with trading performance
- **Health-Performance Correlation** -- Analyze how sleep quality, heart rate, and readiness scores affect trade outcomes
- **Replay Generation** -- Generate DH|S2 replay HTML from .scid tick data for post-session review
- **Discipline Lessons** -- 25+ personalized micro-lessons served between scans to reinforce trading discipline
- **Windows Data Pusher** -- Pull Sierra Chart .scid tick data from Windows PC over the network

### Run the Web Server

```bash
python -m web
```

Open [http://localhost:8420](http://localhost:8420).

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
| HTTP | requests, certifi |

---

## Getting Started

### Prerequisites

- Python 3.11+

### Installation

```bash
git clone https://github.com/sarthakgoel31/trading-monitor.git
cd trading-monitor
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Environment Variables

```env
REDDIT_CLIENT_ID=your_reddit_client_id
REDDIT_CLIENT_SECRET=your_reddit_client_secret
REDDIT_USER_AGENT=trading-monitor
```

### Run

```bash
python -m src.main
```

---

## Architecture

The scanner runs a continuous loop: the `DataFetcher` pulls OHLCV data from TradingView across multiple timeframes, which feeds into the analysis pipeline (RSI, divergences, pivots, ATR, confluence assessment). In parallel, the sentiment module aggregates signals from Reddit, news, reports, and TradingView ideas, then runs them through an LLM analyzer for composite scoring. The mega engine combines technical and sentiment signals to produce trade-ready alerts rendered in the terminal via Rich.

```
DataFetcher (TradingView) --> Analysis Pipeline (RSI, Pivots, Divergence, ATR)
Sentiment Feeds (Reddit, News, Reports, TV Ideas) --> LLM Analyzer
    --> Mega Engine (Confluence + Sentiment) --> Rich Terminal Alerts
```

---

## Project Structure

```
src/
  main.py              -- Entry point, scanner loop
  analysis/            -- RSI, divergence, pivots, confluence
  data/                -- TradingView data fetching, Sierra .scid parser
  mega/                -- Unified engine (entries, exits, levels, pip hunt)
  sentiment/           -- Reddit, news, reports, TradingView, LLM analyzer
  alerts/              -- Terminal notification rendering
  models/              -- Pydantic type definitions
web/
  server.py            -- FastAPI server with WebSocket + background scanner
  analysis.py          -- Web-specific analysis pipeline
  sentiment.py         -- Sentiment aggregation for web
  fetcher.py           -- Data fetcher for web context
  journal_models.py    -- SQLite models for trading journal
  journal_analysis.py  -- Session stats, correlations, insights engine
  journal_health_import.py -- Apple Health XML parser + biometric backfill
  lessons.py           -- Discipline micro-lesson bank
  replay_gen.py        -- DH|S2 replay HTML generator from .scid data
  windows_pusher.py    -- Pull .scid from Windows PC
  static/              -- Dashboard HTML (index, rules, replay)
config/
  settings.py          -- Pydantic Settings configuration
  instruments.py       -- Instrument definitions
```

---

<p align="center">
  <sub>Built with <a href="https://claude.ai/claude-code">Claude Code</a></sub>
</p>
