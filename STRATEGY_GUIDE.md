# Your 6E Trading Strategy — Explained Simply

## First, the Basics

### What is 6E?
6E is the Euro FX futures contract. It tracks EUR/USD — when the Euro gets stronger vs the Dollar, 6E goes up. When the Dollar gets stronger, 6E goes down.

### What is DXY?
DXY is the Dollar Index. It measures how strong the US Dollar is. **6E and DXY move opposite** — when DXY goes up, 6E goes down, and vice versa. Think of them as a seesaw.

### What is RSI?
RSI (Relative Strength Index) is a number from 0 to 100 that tells you if price has been going up too much (overbought, above 70) or down too much (oversold, below 30). It's like a "tiredness meter" — if price has been running up for too long, it's tired and might reverse.

### What is RSI Divergence?
This is when price and RSI **disagree**:
- Price makes a **new low**, but RSI makes a **higher low** → "I know price went lower, but the selling energy is fading" → **Bullish divergence** (price likely to go UP)
- Price makes a **new high**, but RSI makes a **lower high** → "I know price went higher, but the buying energy is fading" → **Bearish divergence** (price likely to go DOWN)

### What is ATR?
ATR (Average True Range) = how much price moves in a typical candle. On 5-min 6E, ATR might be ~0.0015 (15 pips). It auto-adjusts — volatile days have bigger ATR, quiet days have smaller. We use it to set stop losses that adapt to market conditions.

### What is a Pivot Level?
Pivot levels are calculated from yesterday's High, Low, and Close:
- **PP** (Pivot Point) = the "fair price" for the day
- **R1, R2, R3** = resistance levels above PP (price may bounce down here)
- **S1, S2, S3** = support levels below PP (price may bounce up here)

Traders worldwide watch these levels, so price often reacts at them.

### What are Session Levels?
These are the price at specific times you watch:
- **3:30 AM IST** (CME new day open — 6 PM New York time)
- **12:45 AM IST** (near London close)

Price often returns to test these levels during the day.

---

## The Top 3 Strategies Explained

---

### Strategy #1: "The Sniper" (Best quality, fewest trades)
**Trail SL + DXY momentum + Lower Low confirm + Pivot level**
- PF: 25.93 | Win Rate: 67% | R:R: 12.97 | ~3 trades/month

#### When to enter:
You need ALL 4 things to be true at the same time:

1. **6E shows RSI divergence on 5-min chart**
   Example: 6E price drops to 1.1500 (new low), but RSI is at 35 (higher than last time price was this low). That means sellers are getting tired.

2. **DXY is moving in the opposite direction (momentum)**
   You're going LONG 6E → check that DXY has been FALLING over the last 10 candles (50 mins). If DXY is falling, the Dollar is weakening, which supports Euro going up.

3. **Price actually made a lower low (LL/HH confirmation)**
   This confirms the divergence is real, not just noise. The price must have genuinely pushed to a new low before RSI disagreed.

4. **Price is at a pivot level**
   The divergence is happening at S1, S2, R1, R2, or PP — a level where many traders are watching.

#### Where to enter:
At the close of the 5-min candle where all 4 conditions are met.

#### Where to put Stop Loss:
**1 × ATR below your entry** (for longs).
Example: Entry at 1.1500, ATR = 0.0015 → Stop Loss at 1.1485 (15 pips risk).

#### How the Trailing Stop works:
As price moves in your favor, the stop moves up (but never down):
- Price goes to 1.1520 → stop trails to 1.1520 - 0.75×ATR = 1.1509
- Price goes to 1.1540 → stop trails to 1.1540 - 0.0011 = 1.1529
- Price pulls back to 1.1529 → you get stopped out with profit

There is **no fixed take-profit**. You let the winner run and the trailing stop locks in profit automatically.

#### Why it works:
You're only trading when 4 independent signals agree. That's rare (hence few trades), but when it happens, the odds are heavily in your favor.

---

### Strategy #2: "The Daily Driver" (More trades, still high quality)
**Trail SL + DXY momentum + Lower Low confirm + Any level (pivot OR session)**
- PF: 11.73 | Win Rate: 56% | R:R: 9.38 | ~9 trades/month

#### Same as Strategy #1 except:
- Instead of requiring a **pivot level only**, it also accepts **session open levels** (3:30 AM IST / 12:45 AM IST prices)
- This gives 3× more trades because session levels are additional zones where price reacts

#### Entry, SL, Trail: Same as Strategy #1.

---

### Strategy #3: "The Reversal Catcher" (Highest win rate)
**ATR 1:3 R:R + DXY RSI extreme + Next candle confirm + Session level**
- PF: 9.41 | Win Rate: 80% | R:R: 2.35 | ~5 trades/month

#### When to enter:
1. **6E shows RSI divergence**
2. **DXY RSI is at an extreme** — DXY RSI > 65 (overbought, likely to reverse down = good for 6E longs) or DXY RSI < 35 (oversold = good for 6E shorts)
3. **Next candle confirms** — after the divergence candle, the NEXT 5-min candle must be green (for longs) or red (for shorts). This proves momentum is actually shifting.
4. **Price is near a session level** (3:30 AM or 12:45 AM IST open price)

#### Stop Loss & Take Profit:
This one uses a **fixed risk:reward** instead of trailing:
- **Stop Loss**: 1 × ATR from entry
- **Take Profit**: 3 × ATR from entry (3:1 reward-to-risk)
- If neither hits within 60 candles (5 hours), exit at market

Example: Entry 1.1500, ATR = 0.0015
- SL: 1.1485 (risk 15 pips)
- TP: 1.1545 (reward 45 pips)
- Either SL or TP gets hit. With 80% win rate, 4 out of 5 trades hit TP.

---

## Quick Comparison

| | Sniper | Daily Driver | Reversal Catcher |
|---|---|---|---|
| Trades/month | ~3 | ~9 | ~5 |
| Win Rate | 67% | 56% | 80% |
| Risk:Reward | 12.97:1 | 9.38:1 | 2.35:1 |
| Stop Loss | Trailing | Trailing | Fixed 1×ATR |
| Take Profit | Trailing | Trailing | Fixed 3×ATR |
| Best for | Max profit per trade | Regular income | Consistency |
