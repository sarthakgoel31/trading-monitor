# DH Scanner v8 — Installation Guide

## What This Does
- Draws key levels on your 5m 6E chart — **per-day** (PP, R1, S1, PDH, PDL as subgraph lines + VWAP)
- All levels (pivots, fib, PDH/PDL, VPOC, TPOC) used for signal detection internally
- Only signals during your **morning window** (8-12 IST, configurable)
- When price reaches a STRONG level (2+ confluent) with delta confirming + VWAP aligned:
  - Plays alert sound
  - Opens Alert Log with full trade details
  - Draws entry line and stop loss line on chart
  - Green/red arrow on chart

## What Changed in v8 (fixes from v7)
- **Level lines now show per-day** — PP, R1, S1, PDH, PDL shown as subgraph lines (correct per day)
- **Time window filter** — only signals during 8-12 IST (was 24/7 before = too many arrows)
- **VWAP not counted as a level** — VWAP is the directional filter, not one of the "2+ levels"
- **Fixed cumulative delta range** — cdPrev now uses 5 bars (was 6, off-by-one vs Python)
- **Removed UTC conversion** — uses IST dates directly (chart is IST, simpler and correct)
- **State initialized on recalc** — prevents stale arrows from previous calculations

## Alert Shows You:
```
DH LONG @ 1.16225 | PP+fPP (2 lvls) | D:+38 | VWAP:1.16350 | SL:1.16075 | Trail:0.00075
```

## Installation Steps

### Step 1: Copy the file
Copy `DH_Scanner.cpp` to your Sierra Chart source folder:
```
C:\SierraChart\ACS_Source\DH_Scanner.cpp
```

### Step 2: Compile
In Sierra Chart:
1. Go to **Analysis > Build Custom Studies DLL**
2. Click **Remote Build** (standard, not ARM64)
3. Wait for "Build succeeded" message

### Step 3: Add to your 6E chart
1. Open your **5m 6E** chart (6EM6.CME)
2. Go to **Analysis > Studies**
3. Click **Add Custom Study**
4. Find **"DH Scanner v8"** in the list
5. Click **Add** → **OK**

### Step 4: Configure (optional)
Double-click the study in your study list:
- **ATR Period**: 14 (default)
- **CumDelta LB**: 5 bars
- **Level xATR**: 0.5 (how close price must be to a level)
- **Trail xATR**: 0.5 (your trailing stop distance)
- **Alerts**: Yes
- **Window Start IST**: 8 (morning session start)
- **Window End IST**: 12 (morning session end)

### Step 5: Verify Debug Output
After adding the study, check **Window > Alert Manager > Alert Log**. You should see:
```
v8 | IST 10:15 | date=46105 | pdDate=46104 | pdBars=276 | hits=2
v8 | PD H=1.16860 L=1.15300 C=1.16535 | PP=1.16232 R1=1.17163 S1=1.15603
v8 | PD first bar=3:30 IST | PD last bar=2:25 IST | price=1.16100 vwap=1.16200 atr=0.00150
```
Verify the PD (previous day) values make sense for your chart.

## What You'll See on Your Chart
- **Blue dashed**: VWAP (resets each day)
- **Blue dashed**: PP (pivot point, per day)
- **Red dashed**: R1 (first resistance, per day)
- **Green dashed**: S1 (first support, per day)
- **Orange dashed**: PDH / PDL (previous day high/low, per day)
- **Green arrow up**: BUY signal (only during 8-12 IST)
- **Red arrow down**: SELL signal (only during 8-12 IST)
- **Purple arrow**: EXIT signal (trail stop hit)
- **Green/Red horizontal line**: Entry price (current trade only)
- **Red dashed line**: Stop loss (current trade only)

## When You Hear the Alert
1. Look at the Alert Log (Window > Alert Manager > Alert Log)
2. Read the entry price, SL, trail distance
3. Confirm the setup makes sense visually
4. Enter at market, set your SL
5. Trail stop by the specified distance as price moves in your favor

## Troubleshooting
- **No levels showing**: Make sure chart has at least 2 days of history loaded
- **No alerts/arrows**: Check "Alerts" is Yes AND "Window Start/End IST" includes current hour
- **Build failed**: Save as `.cpp` in ACS_Source folder, use Remote Build (standard)
- **Study not in list**: After building, close and reopen the Studies dialog
- **Levels look wrong**: Check debug output in Alert Log — verify PD H/L/C matches your chart's previous day
- **Too many arrows**: Should be ~1/day. If more, check Window Start/End settings
