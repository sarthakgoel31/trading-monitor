# Sierra Chart ACSIL Study Agent

Build Sierra Chart C++ studies for live 6E trading matching Python backtest EXACTLY.

## Critical ACSIL Rules (hard-won, DO NOT deviate)

### UTC Date: INTEGER MATH ONLY
```cpp
int GetUTCDate(SCStudyInterfaceRef sc, int idx) {
    int istDate = sc.BaseDateTimeIn[idx].GetDate();
    int istTimeSec = sc.BaseDateTimeIn[idx].GetTimeInSeconds();
    if (istTimeSec < 19800) return istDate - 1;
    return istDate;
}
```
NEVER use SCDateTime::HOURS()/MINUTES() subtraction — compiles but wrong results.

### Two-Engine Architecture
1. **Historical** (Part 6): subgraph arrays (sgState/sgStop/sgBest/sgEATR), bars 0 to ArraySize-2, draws entry+exit arrows on closed bars
2. **Persistent** (Part 7+8): GetPersistentIntFast/FloatFast, ONLY last bar, live alerts + horizontal lines + text overlay

### Bar-Close Trail (MUST match Python _exit_trail)
Python: `for j in range(1, max_bars+1): check low<=stop FIRST, then trail high>best`
Sierra persistent engine must:
- NEVER check exit on entry bar — store pEntryBar=i, skip when i==pEntryBar
- Trail ONLY on bar close — detect new bar via i>pLastBar, trail from sc.High[i-1]/sc.Low[i-1]
- Real-time exit check OK — stop is correctly set by bar-close trail, won't prematurely tighten

### Drawing Management
- sc.DeleteACSDrawing() is UNRELIABLE — drawings persist after call
- Instead: move lines to BeginValue=0 with Text="" to hide (price 0 is off-screen for 6E)
- Clean up during cooldown AND when pDir==0
- Use s_UseTool with UTAM_ADD_OR_ADJUST and unique LineNumber (9000=SL, 9001=Entry, 9002=Trail, 9010=Text)

### Alert Sound
- sc.AddAlertLine(msg, 1) — alert 1 triggers configured sound (first line only)
- sc.AddAlertLine(msg, 0) — info lines, no sound
- Do NOT use sc.PlaySound() — adds empty alert log entries
- User has Alert 1 mapped to AlertSound.wav in Global Settings > Alerts

### Text Overlay (confirmed valid ACSIL fields)
- UseRelativeVerticalValues=1, FontBackColor=RGB(20,20,30), TransparentLabelBackground=0

### Required SetDefaults
```cpp
sc.AutoLoop = 1;
sc.UpdateAlways = 1;
sc.MaintainAdditionalChartDataArrays = 1;
sc.GraphRegion = 0;
```

### Persistent Variable Slots (v11)
```
IntFast(0)=pDir  IntFast(1)=pCool  IntFast(2)=pEntryBar  IntFast(3)=pLastBar
FloatFast(0)=pEntry  FloatFast(1)=pStop  FloatFast(2)=pBest  FloatFast(3)=pATR
```

### Full Recalculation Reset
Reset ALL persistent vars to 0 and return. No trade state survives full recalc.

### Cooldown
Bar-index based: pCool = i + InCool.GetInt() on exit. Check: if (pCool > 0 && i < pCool).
NOT sc.IsNewBar (may not exist).

## DH|S2 Strategy Rules

### Entry (ALL must be true)
1. delta > 0 (long) or delta < 0 (short)
2. cdNow > cdPrev (long) or cdNow < cdPrev (short) — 6 bars vs prev 5 bars
3. price < vwap (long) or price > vwap (short)
4. 2+ levels within 0.5 x ATR of price
5. ATR >= 0.00050 (5 pips minimum)

### 23 Levels
- Standard pivots: PP, R1, R2, R3, S1, S2, S3 (7)
- Fib pivots: fPP(=PP), fR1, fR2, fR3(=R2), fS1, fS2, fS3(=S2) (7, counted separately)
- PDH, PDL (2)
- VPOC, TPOC from previous UTC day (2)
- London VPOC/TPOC: 12:30-21:30 IST = 07:00-16:00 UTC (2)
- Asia VPOC/TPOC: 03:30-12:30 IST = 22:00-07:00 UTC (2)
- VWAP (1) — counted as level AND directional filter
- Weekly/monthly NOT included (only 1.3% of trades need them)

### Exit
- Initial SL: 1 x ATR
- Trail: 0.5 x ATR (bar-close updates only)
- Cooldown: 3 bars after exit

### VWAP
Resets at UTC midnight (05:30 IST). TP = (H+L+C)/3, volume-weighted.

### Volume Profile (VPOC/TPOC)
50-bin histogram over session bars. VPOC = max volume bin. TPOC = max time bin.

## Reference Implementation
- Sierra study: personal/trading-monitor/sierra/DH_Scanner.cpp (v11)
- Python engine: personal/trading-monitor/src/mega/engine.py
- Python pip hunt: personal/trading-monitor/src/mega/pip_hunt.py
- Replay data: personal/trading-monitor/data/dh_s2_replay.json

## Fetching Live Data
- Windows PC: 192.168.1.26 (DESKTOP-DMO5G99), Rithmic feed
- Ask user to run: python -m http.server 8080 --directory "C:\SierraChart\Data"
- Download: curl -o data/6EM6_live.scid http://192.168.1.26:8080/6EM6.CME.scid
- Parse: read_scid("data/6EM6_live.scid") from src/data/scid_parser.py
- DTC server enabled but Rithmic blocks data redistribution — use file transfer instead

## Sarthak's Setup
- Sierra Chart on Windows, Rithmic data feed, 6EM6.CME symbol
- Chart timezone: IST (UTC+5:30)
- 6E standard contract: $12.50 per pip
- Morning session: 8-12 IST, 1 trade/day target
- Alert 1 configured with AlertSound.wav

## When Building a New Study
1. Start from DH_Scanner.cpp v11 as template
2. Modify signal detection (Part 6 + Part 8) for new strategy
3. Keep two-engine architecture, bar-close trail, persistent vars, drawing management
4. Fetch live SCID, run Python signals, compare with study arrows
5. Validate levels on at least 2 dates before shipping
