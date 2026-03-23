# 6E Trading System — Complete Scope

## Instrument
- **Trade:** 6E (Euro FX Futures) only
- **Confirm:** DXY (US Dollar Index) as inverse confirmation
- **Timeframes:** 5m, 15m (and 1m for delta/volume analysis)

---

## DATA SOURCES

### Tonight (TradingView — already working)
- 6E + DXY OHLCV on 5m, 15m, 1h, daily
- Volume per bar
- TradingView TA summaries

### Tomorrow (Sierra Chart via DTC)
- Raw tick data for 6E and DXY
- Used to compute: VPOC, TPOC, VAH, VAL, delta, cumulative delta, volume per trade

### External
- Forex Factory calendar (news events, impact rating, forecast vs actual)
- Scotia FX Daily PDF (sentiment)
- Google News RSS (sentiment)

---

## ENTRY CONFLUENCES (all toggleable, test every combination)

### A. Core Signal
| # | Factor | Description |
|---|--------|-------------|
| 1 | **RSI Divergence** | Regular bullish, regular bearish, hidden bullish, hidden bearish. 14-period RSI. Fractal swing detection (lookback 3 for 5m, 5 for 15m) |
| 2 | **DXY Confirmation** | Modes: momentum (10-bar), RSI direction, RSI extreme (>65 / <35), any single, any 2+ |

### B. Candle Confirmation
| # | Factor | Description |
|---|--------|-------------|
| 3 | **Next candle confirms** | Green for long, red for short. Doji rule: wait 1 more candle, if 3rd confirms = enter, if 3rd fails = skip. Never wait past 3rd candle |
| 4 | **Wick rejection** | Long lower wicks (bullish) or long upper wicks (bearish) in last 3 candles. Wick ratio > 55% of total range |
| 5 | **Lower Low / Higher High** | Price actually made a new LL (for bullish div) or HH (for bearish div) — confirms the divergence is real |

### C. Volume Confirmation
| # | Factor | Description |
|---|--------|-------------|
| 6 | **Volume spike** | Divergence candle volume >= 1.3x average of last 20 bars |
| 7 | **Delta divergence** | Price makes lower low but delta (buy vol - sell vol) is positive = buyers absorbing. Computed from 1m bars within the 5m candle. *Tomorrow: tick-level from Sierra* |
| 8 | **Cumulative delta direction** | Cum delta trending up = underlying buying pressure (supports long). 5m, 15m, and 1m windows |
| 9 | **Volume per trade (whale detection)** | Avg qty per trade in a 1m bar. Flag bars where per-trade volume is >2x normal = institutional/whale activity at that level. That price level becomes a key support/resistance |

### D. Price Levels (test: at any level, at specific types, no level filter)
| # | Level | Timeframes |
|---|-------|------------|
| 10 | **Standard Pivots** | Daily (PP, S1-S3, R1-R3) |
| 11 | **Fibonacci Pivots** | Daily (fPP, fS1-fS3, fR1-fR3) |
| 12 | **Fib Retracement** | Dynamic from prev swing. Levels: 23.6%, 38.2%, 50%, 61.8%, 70%, 78.6%, 81% |
| 13 | **Session Opens** | 3:30 AM IST (CME open), 12:45 AM IST (London close) |
| 14 | **Prev Day High / Low** | From daily data |
| 15 | **Weekly High / Low** | From daily data |
| 16 | **Monthly High / Low** | From daily data |
| 17 | **VWAP** | Session VWAP. Also used as direction filter (long below = mean reversion) and partial exit target |
| 18 | **VPOC** | Session, prev day, weekly, monthly. Price level with highest volume. *Tomorrow: tick-level from Sierra* |
| 19 | **TPOC** | Session, prev day. Price level where most time was spent. *Tomorrow: tick-level from Sierra* |
| 20 | **VAH / VAL** | Session, prev day. Value area boundaries (70% volume range). *Tomorrow: tick-level from Sierra* |
| 21 | **Whale levels** | Price levels where volume-per-trade was abnormally high (from factor #9). *Tomorrow: tick-level from Sierra* |

**Level proximity:** within 0.5 × ATR(14) of the level

---

## EXIT STRATEGIES (test every combination)

### A. Stop Loss Methods
| # | Method | Variants |
|---|--------|----------|
| 1 | **Trailing stop** | Initial: 1×ATR. Trail: 0.5, 0.75, 1.0 × ATR |
| 2 | **Fixed stop** | 1×ATR, 1.5×ATR |

### B. Take Profit Methods
| # | Method | Variants |
|---|--------|----------|
| 3 | **Trailing (no TP)** | Let trail stop decide exit |
| 4 | **Fixed R:R** | 1:1, 1:1.5, 1:2, 1:3 |
| 5 | **Next key level** | TP at the nearest pivot / VPOC / session level / prev day H-L in the profit direction |
| 6 | **VWAP target** | If entry is away from VWAP, TP at VWAP touch |

### C. Partial Exit (test with and without)
| # | Method | Description |
|---|--------|-------------|
| 7 | **50% at VWAP** | Book half position when price reaches VWAP, trail the rest. Test: does this improve overall P&L vs full trail? |
| 8 | **50% at 1:1 R:R** | Book half at 1:1, trail rest to bigger target |
| 9 | **50% at next level** | Book half at nearest key level, trail rest |

### D. Time Management
| # | Rule | Variants |
|---|------|----------|
| 10 | **Max trade duration** | Force close after N bars: 30, 60, 90, 120 bars (2.5h, 5h, 7.5h, 10h on 5m) |
| 11 | **No overnight** | Force close at: 9 PM, 10 PM, 11 PM IST. Test which cutoff is best |
| 12 | **Trade window** | Only enter during: 8-14, 8-21, 10-18, 10-21, 12-21, 14-21 IST |

---

## NEWS FILTER

### Forex Factory Calendar
| # | Rule | Description |
|---|------|-------------|
| 1 | **High-impact news blackout** | No new entries 15 min before and 15 min after high-impact events (red flag on FF). Test: 15min, 30min, 60min windows |
| 2 | **Medium-impact caution** | Flag but don't block. Test: does filtering medium-impact improve results? |
| 3 | **News direction** | If USD news is positive (actual > forecast) and you're short 6E = aligned. If conflicting = extra caution. Map NFP, CPI, FOMC, ECB to 6E direction |
| 4 | **Post-news trades** | Some of the best setups happen 15-30 min AFTER news when the dust settles. Test: are post-news divergences higher quality? |

---

## SENTIMENT (already built)

| Source | Method | Weight |
|--------|--------|--------|
| Scotia FX Daily PDF | VADER + financial keywords | 30% |
| Google News RSS | VADER + financial keywords | 25% |
| TradingView TA summary | Buy/sell/neutral count | 20% |
| Reddit (if keys added) | VADER + financial keywords | 15% |

---

## STATISTICAL RIGOR

### Outlier Removal
- **IQR method:** For each strategy, calculate Q1 and Q3 of trade P&L. Remove trades where P&L > Q3 + 2×IQR or < Q1 - 2×IQR
- **Flag news trades:** Cross-reference outlier trades with Forex Factory calendar. If an outlier coincides with high-impact news, mark it as "news-driven" and exclude from core stats
- **Report both:** Show results WITH and WITHOUT outliers so we can see the real edge

### Metrics (for each strategy)
- Total trades, win rate
- Profit factor (gross profit / gross loss)
- Average R:R realized
- Average winner %, average loser %
- Max win, max loss
- Max consecutive losses (for psychology)
- Average bars held (trade duration)
- Trades per day (frequency)
- **Sharpe-like ratio:** avg P&L / stdev of P&L (consistency measure)
- Results split by: long vs short, by hour, by day of week

### Ranking
- Primary: **Profit Factor** (after outlier removal)
- Weighted by frequency (boost strategies near 1-2 trades/day target)
- Must have minimum 10 trades to qualify
- Show top 30 overall + top 10 per category (daily driver, high conviction, sniper)

---

## IMPLEMENTATION PHASES

### Phase 1: Tonight (TradingView data)
Build and run with all factors calculable from OHLCV + volume:
- All entry confluences except #7, #8, #9, #18, #19, #20, #21 (need tick data)
- VWAP calculated from bar data (99% accurate)
- Prev day/weekly/monthly H/L from daily data
- All exit strategies including partial exits and time cutoffs
- Forex Factory news calendar integration
- Outlier removal
- Full unbiased matrix: every combo of every factor
- Both 5m and 15m
- Estimated combinations: 2000+
- Run overnight via caffeinate

### Phase 2: Tomorrow (Sierra DTC tick data)
Connect to Sierra, pull tick data, add:
- Tick-level VPOC, TPOC, VAH, VAL (session, prev day, weekly, monthly)
- Delta and cumulative delta from tick data (1m, 5m, 15m)
- Volume per trade / whale detection
- Re-run full matrix with new factors added
- Compare: which Sierra-exclusive factors actually improve results?

### Phase 3: Live Scanner
Wire the winning strategy into the real-time scanner with alerts.

---

## WHAT I AM NOT DOING
- No bias from previous backtest results. Every factor starts equal.
- No cherry-picking strategies. The data decides.
- No ignoring losing strategies. Report everything honestly.
- No overfitting to this specific 24-day sample. Flag strategies with <10 trades as "insufficient data."
