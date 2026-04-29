# Trading Monitor

**6E Delta-Hero scanner with multi-timeframe analysis and multi-source sentiment.**

---

## Demo

> Screenshots and demo video to be added.

---

## What It Does

Trading Monitor is a real-time scanner for the 6E (EUR/USD Futures) market that detects RSI divergences, computes confluence-based pivot levels, and aggregates sentiment from five sources. It identifies high-probability trade setups by combining technical analysis across multiple timeframes with market sentiment, then delivers alerts through a rich terminal UI.

---

## Key Features

- **Multi-Timeframe RSI Divergence Detection** -- Scans for bullish/bearish divergences across configurable timeframes
- **Confluence-Based Pivot Levels** -- Classifies levels as important (2 confluent) or ultra-important (3+ confluent)
- **Mega Engine** -- Integrated entry/exit signals, pip hunt detection, and level computation in a unified engine
- **5-Source Sentiment Aggregation**:
  - Reddit (r/Forex, r/Trading) via PRAW
  - Google News via feed parsing
  - Scotia FX Daily reports (PDF extraction via PyMuPDF)
  - TradingView community ideas
  - LLM-powered sentiment analysis for nuanced scoring
- **Rich Terminal UI** -- Color-coded alerts, market status panels, and sentiment summaries via Rich
- **Backtesting Suite** -- Multiple backtest modes (standard, combo, optimizer, scheduled) for strategy validation
- **ATR-Based Filtering** -- Volatility-aware alert thresholds
- **Pivot Proximity Detection** -- Real-time price distance from key levels
- **Practice Mode** -- Paper trading simulation for strategy testing
- **Configurable Instruments** -- Pydantic-based settings with dotenv support

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Data Feeds | TradingView (tvdatafeed), yfinance |
| Technical Analysis | pandas, pandas_ta, numpy |
| Sentiment | PRAW (Reddit), PyMuPDF (PDF reports), NLTK |
| LLM Analysis | LLM-powered sentiment scoring |
| Output | Rich (terminal UI) |
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
  data/                -- TradingView data fetching
  mega/                -- Unified engine (entries, exits, levels, pip hunt)
  sentiment/           -- Reddit, news, reports, TradingView, LLM analyzer
  alerts/              -- Terminal notification rendering
  models/              -- Pydantic type definitions
config/
  settings.py          -- Pydantic Settings configuration
  instruments.py       -- Instrument definitions
```

---

<p align="center">
  <sub>Built with <a href="https://claude.ai/claude-code">Claude Code</a></sub>
</p>
