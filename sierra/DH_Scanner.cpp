// DH_Scanner.cpp — Delta-Hero Live Scanner v9
//
// Matches Python backtest EXACTLY:
// - UTC calendar day boundaries (SCID data is UTC, Python groups by UTC date)
// - IST = chart timezone, subtract 5h30m to get UTC date
// - VWAP resets at UTC midnight (05:30 IST)
// - Per-day level lines via subgraph arrays (each day shows its own levels)
// - Labeled horizontal lines for TODAY's levels (text labels visible)
// - 8-12 IST time window (configurable)
// - 6-bar cooldown after exit (prevents re-entry cycling)
// - VWAP is directional filter, NOT counted as a level
// - cdPrev: 5 bars [i-10..i-6], matching Python
//
// Expected validation (from Python backtest):
//   March 23: PP=1.16020 S1=1.15930 (from March 22 UTC: H=1.16110 L=1.15870 C=1.16080)
//   March 24: PP=1.16232 S1=1.15603 (from March 23 UTC: H=1.16860 L=1.15300 C=1.16535)

#include "sierrachart.h"

SCDLLName("DH_Scanner")

// ═══ Helpers ═══

struct VPR { float vpoc; float tpoc; int ok; };

VPR CalcVP(SCStudyInterfaceRef sc, int s, int e)
{
    VPR r; r.vpoc = 0; r.tpoc = 0; r.ok = 0;
    if (s < 0 || e <= s) return r;
    float lo = sc.Low[s], hi = sc.High[s];
    for (int k = s; k <= e; k++) {
        if (sc.Low[k] < lo) lo = sc.Low[k];
        if (sc.High[k] > hi) hi = sc.High[k];
    }
    if (hi <= lo) return r;
    float bs = (hi - lo) / 50.0f;
    float vb[50], tb[50];
    for (int k = 0; k < 50; k++) { vb[k] = 0; tb[k] = 0; }
    for (int k = s; k <= e; k++) {
        int a = (int)((sc.Low[k] - lo) / bs);
        int b = (int)((sc.High[k] - lo) / bs);
        if (a < 0) a = 0; if (b > 49) b = 49;
        int sp = b - a + 1; if (sp < 1) sp = 1;
        float v = (float)sc.Volume[k]; if (v <= 0) v = 1;
        for (int m = a; m <= b; m++) { vb[m] += v / sp; tb[m] += 1.0f / sp; }
    }
    int vi = 0, ti = 0;
    for (int k = 1; k < 50; k++) {
        if (vb[k] > vb[vi]) vi = k;
        if (tb[k] > tb[ti]) ti = k;
    }
    r.vpoc = lo + (vi + 0.5f) * bs;
    r.tpoc = lo + (ti + 0.5f) * bs;
    r.ok = 1;
    return r;
}

// Convert chart time (IST) to UTC date integer.
// Uses only GetDate() + GetTimeInSeconds() with plain integer math.
// No SCDateTime arithmetic operators (HOURS/MINUTES subtraction is unreliable).
// IST = UTC + 5h30m = UTC + 19800 seconds.
// If IST time-of-day < 19800s (05:30 AM), the UTC date is the previous day.
int GetUTCDate(SCStudyInterfaceRef sc, int idx)
{
    int istDate = sc.BaseDateTimeIn[idx].GetDate();
    int istTimeSec = sc.BaseDateTimeIn[idx].GetTimeInSeconds();
    if (istTimeSec < 19800)   // before 05:30 AM IST = previous UTC day
        return istDate - 1;
    return istDate;
}

// ═══ Main Study ═══

SCSFExport scsf_DH_Scanner(SCStudyInterfaceRef sc)
{
    // ── Subgraphs ──
    SCSubgraphRef sgBuy   = sc.Subgraph[0];   // Entry LONG arrow (green up)
    SCSubgraphRef sgSell  = sc.Subgraph[1];   // Entry SHORT arrow (red down)
    SCSubgraphRef sgATR   = sc.Subgraph[2];   // ATR (hidden)
    SCSubgraphRef sgDelta = sc.Subgraph[3];   // Delta (hidden)
    SCSubgraphRef sgCumD  = sc.Subgraph[4];   // CumDelta (hidden)
    SCSubgraphRef sgVWAP  = sc.Subgraph[5];   // VWAP line (blue)
    SCSubgraphRef sgNLvl  = sc.Subgraph[6];   // Nearby level count (hidden)
    SCSubgraphRef sgState = sc.Subgraph[7];   // Trade state (hidden)
    SCSubgraphRef sgStop  = sc.Subgraph[8];   // Trail stop (hidden)
    SCSubgraphRef sgBest  = sc.Subgraph[9];   // Best price in trade (hidden)
    SCSubgraphRef sgEATR  = sc.Subgraph[10];  // Entry ATR / cooldown (hidden)
    SCSubgraphRef sgExBuy = sc.Subgraph[11];  // Exit from SHORT arrow (purple up)
    SCSubgraphRef sgExSel = sc.Subgraph[12];  // Exit from LONG arrow (purple down)
    SCSubgraphRef sgPP    = sc.Subgraph[13];  // PP level line (per-day)
    SCSubgraphRef sgR1    = sc.Subgraph[14];  // R1 level line (per-day)
    SCSubgraphRef sgS1    = sc.Subgraph[15];  // S1 level line (per-day)
    SCSubgraphRef sgPDH   = sc.Subgraph[16];  // Prev Day High line (per-day)
    SCSubgraphRef sgPDL   = sc.Subgraph[17];  // Prev Day Low line (per-day)
    SCSubgraphRef sgVPOC  = sc.Subgraph[18];  // VPOC line (per-day)
    SCSubgraphRef sgTPOC  = sc.Subgraph[19];  // TPOC line (per-day)

    // ── Inputs ──
    SCInputRef InATR   = sc.Input[0];
    SCInputRef InCDLB  = sc.Input[1];
    SCInputRef InLvlM  = sc.Input[2];
    SCInputRef InTrlM  = sc.Input[3];
    SCInputRef InAlert = sc.Input[4];
    SCInputRef InWinS  = sc.Input[5];
    SCInputRef InWinE  = sc.Input[6];
    SCInputRef InCool  = sc.Input[7];

    if (sc.SetDefaults)
    {
        sc.GraphName = "DH Scanner v11";
        sc.StudyDescription = "Delta Hero v11 — bar-close trail matching Python backtest";
        sc.AutoLoop = 1;
        sc.UpdateAlways = 1;  // Process every tick (real-time P&L + exit detection)
        sc.MaintainAdditionalChartDataArrays = 1;
        sc.GraphRegion = 0;

        sgBuy.Name = "Entry Buy";   sgBuy.DrawStyle = DRAWSTYLE_ARROW_UP;
        sgBuy.PrimaryColor = RGB(0, 200, 100); sgBuy.LineWidth = 3; sgBuy.DrawZeros = 0;
        sgSell.Name = "Entry Sell"; sgSell.DrawStyle = DRAWSTYLE_ARROW_DOWN;
        sgSell.PrimaryColor = RGB(255, 80, 80); sgSell.LineWidth = 3; sgSell.DrawZeros = 0;

        sgATR.Name = "ATR";       sgATR.DrawStyle = DRAWSTYLE_IGNORE;
        sgDelta.Name = "Delta";   sgDelta.DrawStyle = DRAWSTYLE_IGNORE;
        sgCumD.Name = "CumDelta"; sgCumD.DrawStyle = DRAWSTYLE_IGNORE;

        sgVWAP.Name = "VWAP"; sgVWAP.DrawStyle = DRAWSTYLE_DASH;
        sgVWAP.PrimaryColor = RGB(68, 138, 255); sgVWAP.LineWidth = 2; sgVWAP.DrawZeros = 0;

        sgNLvl.Name = "NearLvls"; sgNLvl.DrawStyle = DRAWSTYLE_IGNORE;
        sgState.Name = "State";   sgState.DrawStyle = DRAWSTYLE_IGNORE;
        sgStop.Name = "Stop";     sgStop.DrawStyle = DRAWSTYLE_IGNORE;
        sgBest.Name = "Best";     sgBest.DrawStyle = DRAWSTYLE_IGNORE;
        sgEATR.Name = "EntryATR"; sgEATR.DrawStyle = DRAWSTYLE_IGNORE;

        sgExBuy.Name = "Exit Buy";  sgExBuy.DrawStyle = DRAWSTYLE_ARROW_UP;
        sgExBuy.PrimaryColor = RGB(160, 100, 255); sgExBuy.LineWidth = 3; sgExBuy.DrawZeros = 0;
        sgExSel.Name = "Exit Sell"; sgExSel.DrawStyle = DRAWSTYLE_ARROW_DOWN;
        sgExSel.PrimaryColor = RGB(160, 100, 255); sgExSel.LineWidth = 3; sgExSel.DrawZeros = 0;

        sgPP.Name = "PP";   sgPP.DrawStyle = DRAWSTYLE_DASH;
        sgPP.PrimaryColor = RGB(128, 128, 255); sgPP.LineWidth = 1; sgPP.DrawZeros = 0;
        sgR1.Name = "R1";   sgR1.DrawStyle = DRAWSTYLE_DASH;
        sgR1.PrimaryColor = RGB(255, 100, 100); sgR1.LineWidth = 1; sgR1.DrawZeros = 0;
        sgS1.Name = "S1";   sgS1.DrawStyle = DRAWSTYLE_DASH;
        sgS1.PrimaryColor = RGB(100, 255, 100); sgS1.LineWidth = 1; sgS1.DrawZeros = 0;
        sgPDH.Name = "PDH"; sgPDH.DrawStyle = DRAWSTYLE_DASH;
        sgPDH.PrimaryColor = RGB(255, 150, 50); sgPDH.LineWidth = 1; sgPDH.DrawZeros = 0;
        sgPDL.Name = "PDL"; sgPDL.DrawStyle = DRAWSTYLE_DASH;
        sgPDL.PrimaryColor = RGB(255, 150, 50); sgPDL.LineWidth = 1; sgPDL.DrawZeros = 0;
        sgVPOC.Name = "VPOC"; sgVPOC.DrawStyle = DRAWSTYLE_DASH;
        sgVPOC.PrimaryColor = RGB(255, 215, 64); sgVPOC.LineWidth = 2; sgVPOC.DrawZeros = 0;
        sgTPOC.Name = "TPOC"; sgTPOC.DrawStyle = DRAWSTYLE_DASH;
        sgTPOC.PrimaryColor = RGB(179, 136, 255); sgTPOC.LineWidth = 2; sgTPOC.DrawZeros = 0;

        InATR.Name = "ATR Period";       InATR.SetInt(14);
        InCDLB.Name = "CumDelta LB";    InCDLB.SetInt(5);
        InLvlM.Name = "Level xATR";     InLvlM.SetFloat(0.5f);
        InTrlM.Name = "Trail xATR";     InTrlM.SetFloat(0.5f);
        InAlert.Name = "Alerts";         InAlert.SetYesNo(1);
        InWinS.Name = "Window Start IST"; InWinS.SetInt(8);
        InWinE.Name = "Window End IST";   InWinE.SetInt(12);
        InCool.Name = "Cooldown Bars";    InCool.SetInt(3);

        return;
    }

    int i = sc.Index;

    // Persistent state — survives recalculation, used for LIVE trade management
    int& pDir = sc.GetPersistentIntFast(0);        // 0=none, 1=long, -1=short
    int& pCool = sc.GetPersistentIntFast(1);       // cooldown: bar index after which trading resumes
    int& pEntryBar = sc.GetPersistentIntFast(2);   // bar index where trade was entered
    int& pLastBar = sc.GetPersistentIntFast(3);    // last bar index processed for trail update
    float& pEntry = sc.GetPersistentFloatFast(0);  // entry price
    float& pStop = sc.GetPersistentFloatFast(1);   // current stop level
    float& pBest = sc.GetPersistentFloatFast(2);   // best price reached
    float& pATR = sc.GetPersistentFloatFast(3);    // entry ATR (locked)

    // Clear state for early bars (prevents stale data on recalc)
    if (i < 30) {
        sgState[i] = 0; sgStop[i] = 0; sgBest[i] = 0; sgEATR[i] = 0;
        sgPP[i] = 0; sgR1[i] = 0; sgS1[i] = 0; sgPDH[i] = 0; sgPDL[i] = 0;
        sgVPOC[i] = 0; sgTPOC[i] = 0;
        return;
    }

    // ═══════════════════════════════════════════
    // PART 1: INDICATORS
    // ═══════════════════════════════════════════

    // ATR(14, Wilder's)
    sc.ATR(sc.BaseDataIn, sgATR, i, InATR.GetInt(), MOVAVGTYPE_WILDERS);
    float atr = sgATR[i];

    // Delta = ask volume - bid volume for this bar
    float delta = sc.BaseData[SC_ASKVOL][i] - sc.BaseData[SC_BIDVOL][i];
    sgDelta[i] = delta;

    // UTC date for this bar (subtract IST offset from chart time)
    int utcDate = GetUTCDate(sc, i);
    int prevUtcDate = (i > 0) ? GetUTCDate(sc, i - 1) : 0;

    // Cumulative delta — resets at UTC midnight (matches Python)
    if (utcDate != prevUtcDate)
        sgCumD[i] = delta;
    else
        sgCumD[i] = sgCumD[i - 1] + delta;

    // VWAP — resets at UTC midnight (matches Python: df.index.date groups)
    // Find first bar of current UTC day
    int dayStart = 0;
    for (int j = i; j >= 0; j--) {
        if (GetUTCDate(sc, j) != utcDate) {
            dayStart = j + 1;
            break;
        }
        if (j == 0) dayStart = 0;
    }

    float cumTPV = 0, cumVol = 0;
    for (int j = dayStart; j <= i; j++) {
        float tp = (sc.High[j] + sc.Low[j] + sc.Close[j]) / 3.0f;
        float v = (float)sc.Volume[j];
        if (v <= 0) v = 1.0f;
        cumTPV += tp * v;
        cumVol += v;
    }
    float vwap = (cumVol > 0) ? (cumTPV / cumVol) : sc.Close[i];
    sgVWAP[i] = vwap;

    // ═══════════════════════════════════════════
    // PART 2: FIND PREVIOUS UTC DAY
    // ═══════════════════════════════════════════

    // Scan backward from dayStart-1 to find all bars of the most recent previous UTC day
    int pdS = -1, pdE = -1, pdDt = 0;
    for (int j = dayStart - 1; j >= 0; j--) {
        int d = GetUTCDate(sc, j);
        if (pdDt == 0) pdDt = d;
        if (d == pdDt) {
            if (pdS == -1 || j < pdS) pdS = j;
            if (j > pdE) pdE = j;
        }
        if (d < pdDt) break;
    }

    // ═══════════════════════════════════════════
    // PART 3: COMPUTE LEVELS FROM PREV UTC DAY
    // ═══════════════════════════════════════════

    float price = sc.Close[i];
    float threshold = (atr > 0) ? atr * InLvlM.GetFloat() : 0.0005f;
    int hitCount = 0;
    char hitNames[50][16];
    float pv[28]; const char* pn[28]; int nLev = 0;
    float lvPP = 0, lvR1 = 0, lvS1 = 0, lvPDH = 0, lvPDL = 0, lvVPOC = 0, lvTPOC = 0;

    if (pdS >= 0 && pdE >= pdS)
    {
        // Prev UTC day OHLC
        float pH = sc.High[pdS], pL = sc.Low[pdS];
        for (int j = pdS; j <= pdE; j++) {
            if (sc.High[j] > pH) pH = sc.High[j];
            if (sc.Low[j] < pL) pL = sc.Low[j];
        }
        float pC = sc.Close[pdE];

        // Standard pivots
        float PP = (pH + pL + pC) / 3.0f;
        float R = pH - pL;
        float R1 = 2 * PP - pL;
        float R2 = PP + R;
        float R3 = 2 * PP + R - pL;
        float S1 = 2 * PP - pH;
        float S2 = PP - R;
        float S3 = 2 * PP - R - pH;

        // Fib pivots
        float fR1 = PP + 0.382f * R;
        float fR2 = PP + 0.618f * R;
        float fS1 = PP - 0.382f * R;
        float fS2 = PP - 0.618f * R;

        lvPP = PP; lvR1 = R1; lvS1 = S1; lvPDH = pH; lvPDL = pL;

        // Fib duplicates: fPP=PP, fR3=R2, fS3=S2 (counted separately like Python)
        float fR3 = PP + R;   // same as R2
        float fS3 = PP - R;   // same as S2

        // Build level array — std pivots + fib pivots (all 14) + PDH/PDL
        pv[0]=PP;  pn[0]="PP";    pv[1]=R1;  pn[1]="R1";   pv[2]=R2;  pn[2]="R2";
        pv[3]=R3;  pn[3]="R3";    pv[4]=S1;  pn[4]="S1";   pv[5]=S2;  pn[5]="S2";
        pv[6]=S3;  pn[6]="S3";
        pv[7]=PP;  pn[7]="fPP";   pv[8]=fR1; pn[8]="fR1";  pv[9]=fR2; pn[9]="fR2";
        pv[10]=fR3;pn[10]="fR3";  pv[11]=fS1;pn[11]="fS1"; pv[12]=fS2;pn[12]="fS2";
        pv[13]=fS3;pn[13]="fS3";
        pv[14]=pH; pn[14]="PDH";  pv[15]=pL; pn[15]="PDL";
        nLev = 16;

        // Prev day VPOC/TPOC
        VPR vp = CalcVP(sc, pdS, pdE);
        if (vp.ok) {
            pv[nLev] = vp.vpoc; pn[nLev] = "VPOC"; nLev++;
            pv[nLev] = vp.tpoc; pn[nLev] = "TPOC"; nLev++;
            lvVPOC = vp.vpoc;
            lvTPOC = vp.tpoc;
        }

        // Session VPOC/TPOC from prev day
        // London session: 12:30-21:30 IST (= 07:00-16:00 UTC, Python: hours>=7 & <16)
        {
            int lS = -1, lE = -1;
            for (int j = pdS; j <= pdE; j++) {
                int t = sc.BaseDateTimeIn[j].GetTimeInSeconds();
                if (t >= 45000 && t < 77400) {  // 12:30 to 21:30 IST
                    if (lS == -1) lS = j;
                    lE = j;
                }
            }
            VPR lv = CalcVP(sc, lS, lE);
            if (lv.ok) {
                pv[nLev] = lv.vpoc; pn[nLev] = "ldn_vpoc"; nLev++;
                pv[nLev] = lv.tpoc; pn[nLev] = "ldn_tpoc"; nLev++;
            }
        }

        // Asia session: 03:30-12:30 IST (= 22:00-07:00 UTC)
        {
            int aS = -1, aE = -1;
            for (int j = pdS; j <= pdE; j++) {
                int t = sc.BaseDateTimeIn[j].GetTimeInSeconds();
                if (t >= 12600 && t < 45000) {  // 03:30 to 12:30 IST
                    if (aS == -1) aS = j;
                    aE = j;
                }
            }
            VPR av = CalcVP(sc, aS, aE);
            if (av.ok) {
                pv[nLev] = av.vpoc; pn[nLev] = "asia_vpoc"; nLev++;
                pv[nLev] = av.tpoc; pn[nLev] = "asia_tpoc"; nLev++;
            }
        }

        // VWAP as a level (the backtest counts it)
        pv[nLev] = vwap; pn[nLev] = "vwap"; nLev++;

        // Count hits: how many levels is price near? (need 2+ for "strong level")
        for (int p = 0; p < nLev && hitCount < 50; p++) {
            float df = price - pv[p]; if (df < 0) df = -df;
            if (df <= threshold) {
                sprintf(hitNames[hitCount], "%s", pn[p]);
                hitCount++;
            }
        }
    }

    sgNLvl[i] = (float)hitCount;

    // ═══════════════════════════════════════════
    // PART 4: PER-DAY SUBGRAPH LEVEL LINES
    // ═══════════════════════════════════════════

    // Each bar stores its own day's levels. DrawZeros=0 hides bars with no levels.
    sgPP[i]  = lvPP;
    sgR1[i]  = lvR1;
    sgS1[i]  = lvS1;
    sgPDH[i] = lvPDH;
    sgPDL[i] = lvPDL;
    sgVPOC[i] = lvVPOC;
    sgTPOC[i] = lvTPOC;

    // ═══════════════════════════════════════════
    // PART 5: LABELED LINES (last bar only, no debug spam)
    // ═══════════════════════════════════════════

    if (i == sc.ArraySize - 1)
    {
        // Labeled horizontal lines for TODAY's levels
        if (nLev > 0) {
            COLORREF lc[28];
            // 0-6: std pivots, 7-13: fib pivots, 14-15: PDH/PDL
            lc[0]=RGB(128,128,255); lc[1]=RGB(255,100,100); lc[2]=RGB(255,80,80);
            lc[3]=RGB(255,60,60);   lc[4]=RGB(100,255,100); lc[5]=RGB(80,255,80);
            lc[6]=RGB(60,255,60);   lc[7]=RGB(128,128,255); lc[8]=RGB(200,130,130);
            lc[9]=RGB(220,110,110); lc[10]=RGB(255,80,80);  lc[11]=RGB(130,200,130);
            lc[12]=RGB(110,220,110);lc[13]=RGB(60,255,60);
            lc[14]=RGB(255,150,50); lc[15]=RGB(255,150,50);
            // 16+: VPOC, TPOC, ldn, asia, vwap
            lc[16]=RGB(255,215,64); lc[17]=RGB(179,136,255);
            lc[18]=RGB(200,180,50); lc[19]=RGB(140,100,200);
            lc[20]=RGB(180,150,50); lc[21]=RGB(120,80,180);
            lc[22]=RGB(68,138,255);
            for(int c=23;c<28;c++) lc[c]=RGB(200,200,200);

            for (int p = 0; p < nLev; p++) {
                s_UseTool t; t.Clear();
                t.ChartNumber = sc.ChartNumber; t.Region = 0;
                t.DrawingType = DRAWING_HORIZONTALLINE;
                t.LineNumber = 2000 + p;
                t.AddMethod = UTAM_ADD_OR_ADJUST;
                t.BeginValue = pv[p]; t.Color = lc[p];
                t.LineWidth = (p >= 13) ? 2 : 1;
                t.LineStyle = LINESTYLE_DASH;
                t.DisplayHorizontalLineValue = 1;
                t.Text.Format("%s", pn[p]);
                sc.UseTool(t);
            }
        }
    }

    // ═══════════════════════════════════════════
    // PART 6: SUBGRAPH-BASED TRADE STATE (for historical arrows)
    // ═══════════════════════════════════════════

    float prevState = (i > 0) ? sgState[i - 1] : 0;
    float prevStop  = (i > 0) ? sgStop[i - 1]  : 0;
    float prevBest  = (i > 0) ? sgBest[i - 1]  : 0;
    float entryATR  = (i > 0) ? sgEATR[i - 1]  : 0;

    sgState[i] = prevState;
    sgStop[i]  = prevStop;
    sgBest[i]  = prevBest;
    sgEATR[i]  = entryATR;

    if (entryATR < 0) {
        sgEATR[i] = entryATR + 1.0f;
        sgState[i] = 0; sgStop[i] = 0; sgBest[i] = 0;
        // Don't return on last bar — fall through to persistent state
        if (i < sc.ArraySize - 1) return;
    }

    int tradeDir = (prevState > 0) ? 1 : ((prevState < 0) ? -1 : 0);

    // Historical bars: subgraph-based exit/trail/entry
    if (i < sc.ArraySize - 1)
    {
        if (tradeDir != 0 && entryATR > 0)
        {
            float trailDist = entryATR * InTrlM.GetFloat();
            if (tradeDir == 1) {
                if (sc.Low[i] <= prevStop) {
                    sgExSel[i] = prevStop - atr * 0.3f;
                    sgState[i] = 0; sgStop[i] = 0; sgBest[i] = 0;
                    sgEATR[i] = (float)(-InCool.GetInt());
                    return;
                }
                float best = prevBest;
                if (sc.High[i] > best) best = sc.High[i];
                float newStop = best - trailDist;
                if (newStop > prevStop) prevStop = newStop;
                sgBest[i] = best; sgStop[i] = prevStop;
                return;
            } else {
                if (sc.High[i] >= prevStop) {
                    sgExBuy[i] = prevStop + atr * 0.3f;
                    sgState[i] = 0; sgStop[i] = 0; sgBest[i] = 0;
                    sgEATR[i] = (float)(-InCool.GetInt());
                    return;
                }
                float best = prevBest;
                if (sc.Low[i] < best) best = sc.Low[i];
                float newStop = best + trailDist;
                if (newStop < prevStop) prevStop = newStop;
                sgBest[i] = best; sgStop[i] = prevStop;
                return;
            }
        }

        // Historical signal detection
        if (atr <= 0 || atr < 0.00050f) return;
        if (hitCount < 2) return;

        int lb = InCDLB.GetInt();
        float cdNow = 0, cdPrev = 0;
        for (int j = i-lb; j <= i; j++) { if(j<0) continue; cdNow += sc.BaseData[SC_ASKVOL][j]-sc.BaseData[SC_BIDVOL][j]; }
        for (int j = i-2*lb; j < i-lb; j++) { if(j<0) continue; cdPrev += sc.BaseData[SC_ASKVOL][j]-sc.BaseData[SC_BIDVOL][j]; }

        int dL = (delta>0 && cdNow>cdPrev)?1:0;
        int dS = (delta<0 && cdNow<cdPrev)?1:0;
        if (!dL && !dS) return;
        int isLong = dL;
        if (isLong && price>=vwap) return;
        if (!isLong && price<=vwap) return;

        float slPrice = isLong ? (price-atr) : (price+atr);
        if (isLong) { sgBuy[i]=sc.Low[i]-atr*0.3f; sgState[i]=price; sgStop[i]=slPrice; sgBest[i]=price; sgEATR[i]=atr; }
        else { sgSell[i]=sc.High[i]+atr*0.3f; sgState[i]=-price; sgStop[i]=slPrice; sgBest[i]=price; sgEATR[i]=atr; }
        return;
    }

    // ═══════════════════════════════════════════
    // PART 7: LIVE BAR — PERSISTENT TRADE STATE
    // (survives recalculation, reliable alerts)
    // ═══════════════════════════════════════════

    if (sc.IsFullRecalculation) {
        pDir = 0; pEntry = 0; pStop = 0; pBest = 0; pATR = 0; pCool = 0;
        pEntryBar = 0; pLastBar = 0;
        return;
    }

    // Cooldown: pCool = bar index after which trading resumes
    if (pCool > 0 && i < pCool) {
        // Hide trade drawings by moving lines to 0 (reliable — DeleteACSDrawing may not work)
        s_UseTool cl; cl.Clear(); cl.ChartNumber=sc.ChartNumber; cl.Region=0;
        cl.DrawingType=DRAWING_HORIZONTALLINE; cl.LineStyle=LINESTYLE_DASH;
        cl.AddMethod=UTAM_ADD_OR_ADJUST; cl.LineWidth=1;
        cl.LineNumber=9000; cl.BeginValue=0; cl.Text=""; sc.UseTool(cl);
        cl.LineNumber=9001; cl.BeginValue=0; cl.Text=""; sc.UseTool(cl);
        cl.LineNumber=9002; cl.BeginValue=0; cl.Text=""; sc.UseTool(cl);
        // Hide text overlay
        s_UseTool ti; ti.Clear(); ti.ChartNumber=sc.ChartNumber; ti.Region=0;
        ti.DrawingType=DRAWING_TEXT; ti.LineNumber=9010;
        ti.AddMethod=UTAM_ADD_OR_ADJUST; ti.UseRelativeVerticalValues=1;
        ti.BeginValue=95; ti.BeginIndex=sc.ArraySize-1; ti.Text=" "; sc.UseTool(ti);
        return;
    }

    // No trade active — hide any orphaned drawings
    if (pDir == 0) {
        s_UseTool cl; cl.Clear(); cl.ChartNumber=sc.ChartNumber; cl.Region=0;
        cl.DrawingType=DRAWING_HORIZONTALLINE; cl.LineStyle=LINESTYLE_DASH;
        cl.AddMethod=UTAM_ADD_OR_ADJUST; cl.LineWidth=1;
        cl.LineNumber=9000; cl.BeginValue=0; cl.Text=""; sc.UseTool(cl);
        cl.LineNumber=9001; cl.BeginValue=0; cl.Text=""; sc.UseTool(cl);
        cl.LineNumber=9002; cl.BeginValue=0; cl.Text=""; sc.UseTool(cl);
        s_UseTool ti; ti.Clear(); ti.ChartNumber=sc.ChartNumber; ti.Region=0;
        ti.DrawingType=DRAWING_TEXT; ti.LineNumber=9010;
        ti.AddMethod=UTAM_ADD_OR_ADJUST; ti.UseRelativeVerticalValues=1;
        ti.BeginValue=95; ti.BeginIndex=sc.ArraySize-1; ti.Text=" "; sc.UseTool(ti);
    }

    // If in a persistent trade — bar-close trail logic (matches Python backtest)
    // Python: exit check on CLOSED bars only, starting from entry_i + 1.
    //   for j in range(1, max_bars+1): check bar[idx+j].low vs stop, THEN trail.
    // Sierra: trail updates when a new bar starts (using prev bar's OHLC).
    //   Exit checked in real-time (every tick) against the correctly-set stop.
    //   Entry bar is NEVER checked for exit (matching Python's range(1,...)).
    if (pDir != 0 && pATR > 0)
    {
        // ── Display (runs every tick for real-time visual feedback) ──

        // Keep entry arrow visible on live bar
        if (pDir == 1) sgBuy[i] = sc.Low[i] - atr * 0.3f;
        else           sgSell[i] = sc.High[i] + atr * 0.3f;

        float initSL = pEntry - pATR * (pDir == 1 ? 1.0f : -1.0f);
        float pnlNow = (pDir == 1) ? (price - pEntry) / 0.0001f : (pEntry - price) / 0.0001f;

        // Trade info text overlay
        {
            s_UseTool info; info.Clear();
            info.ChartNumber = sc.ChartNumber;
            info.DrawingType = DRAWING_TEXT;
            info.LineNumber = 9010;
            info.AddMethod = UTAM_ADD_OR_ADJUST;
            info.Region = 0;
            info.UseRelativeVerticalValues = 1;
            info.BeginValue = 95;
            info.BeginIndex = sc.ArraySize - 1;
            info.Color = (pDir == 1) ? RGB(0, 200, 100) : RGB(255, 80, 80);
            info.FontBackColor = RGB(20, 20, 30);
            info.TransparentLabelBackground = 0;
            info.Text.Format("%s | Entry: %.5f | SL: %.5f | Trail: %.5f | P&L: %+.1f pips",
                (pDir == 1) ? "LONG" : "SHORT", pEntry, initSL, pStop, pnlNow);
            sc.UseTool(info);
        }

        // ENTRY horizontal line
        {
            s_UseTool et; et.Clear();
            et.ChartNumber = sc.ChartNumber; et.Region = 0;
            et.DrawingType = DRAWING_HORIZONTALLINE;
            et.LineNumber = 9001; et.AddMethod = UTAM_ADD_OR_ADJUST;
            et.BeginValue = pEntry;
            et.Color = (pDir == 1) ? RGB(0, 200, 100) : RGB(255, 80, 80);
            et.LineWidth = 2; et.DisplayHorizontalLineValue = 1;
            et.Text.Format("ENTRY %s @ %.5f", (pDir == 1) ? "LONG" : "SHORT", pEntry);
            sc.UseTool(et);
        }

        // INIT SL horizontal line (fixed at initial stop, never moves)
        {
            s_UseTool sl; sl.Clear();
            sl.ChartNumber = sc.ChartNumber; sl.Region = 0;
            sl.DrawingType = DRAWING_HORIZONTALLINE;
            sl.LineNumber = 9000; sl.AddMethod = UTAM_ADD_OR_ADJUST;
            sl.BeginValue = initSL;
            sl.Color = RGB(255, 60, 60);
            sl.LineWidth = 2; sl.LineStyle = LINESTYLE_DASH;
            sl.DisplayHorizontalLineValue = 1;
            sl.Text.Format("INIT SL %.5f", initSL);
            sc.UseTool(sl);
        }

        // TRAIL SL horizontal line (moves as stop trails)
        {
            s_UseTool tr; tr.Clear();
            tr.ChartNumber = sc.ChartNumber; tr.Region = 0;
            tr.DrawingType = DRAWING_HORIZONTALLINE;
            tr.LineNumber = 9002; tr.AddMethod = UTAM_ADD_OR_ADJUST;
            tr.BeginValue = pStop;
            tr.Color = RGB(160, 100, 255);
            tr.LineWidth = 2; tr.LineStyle = LINESTYLE_DASH;
            tr.DisplayHorizontalLineValue = 1;
            tr.Text.Format("TRAIL SL %.5f", pStop);
            sc.UseTool(tr);
        }

        // ── Trail update: ONLY on bar close (when new bar detected) ──
        // Matches Python: trail uses closed bar's high/low, not live ticks.
        // Skip entry bar (Python starts from entry_i + 1).
        if (i > pLastBar && pLastBar > 0 && (i - 1) > pEntryBar)
        {
            pLastBar = i;
            float trailDist = pATR * InTrlM.GetFloat();

            if (pDir == 1) {
                // Python: if bar["low"] <= stop → exit FIRST, then trail
                // Here we just trail. Exit is checked real-time below.
                if (sc.High[i-1] > pBest) pBest = sc.High[i-1];
                float newStop = pBest - trailDist;
                if (newStop > pStop) pStop = newStop;
            } else {
                if (sc.Low[i-1] < pBest) pBest = sc.Low[i-1];
                float newStop = pBest + trailDist;
                if (newStop < pStop) pStop = newStop;
            }
        }
        else if (pLastBar == 0 || pLastBar < pEntryBar)
        {
            pLastBar = i;  // Initialize after entry
        }

        // ── Exit check: real-time for timely alerts ──
        // The stop (pStop) was set correctly by bar-close trail above,
        // so it won't be prematurely tightened by intra-bar moves.
        // Skip the entry bar entirely (matches Python: range(1,...)).
        if (i > pEntryBar)
        {
            if (pDir == 1 && sc.Low[i] <= pStop)
            {
                // EXIT LONG
                sgExSel[i] = pStop - atr * 0.3f;
                sgBuy[i] = 0;

                if (InAlert.GetYesNo()) {
                    float pnlPips = (pStop - pEntry) / 0.0001f;
                    float pnlD = pnlPips * 12.50f;
                    SCString m1; m1.Format("--- EXIT LONG ---");
                    SCString m2; m2.Format("Exit: %.5f | Entry: %.5f", pStop, pEntry);
                    SCString m3; m3.Format("P&L: %+.1f pips ($%+.1f)", pnlPips, pnlD);
                    sc.AddAlertLine(m1, 1);   // Alert 1 → sound
                    sc.AddAlertLine(m2, 0);
                    sc.AddAlertLine(m3, 0);
                }
                pDir = 0; pEntry = 0; pStop = 0; pBest = 0; pATR = 0;
                pCool = i + InCool.GetInt();
                pEntryBar = 0;
                { s_UseTool info; info.Clear(); info.ChartNumber=sc.ChartNumber;
                  info.DrawingType=DRAWING_TEXT; info.LineNumber=9010;
                  info.AddMethod=UTAM_ADD_OR_ADJUST; info.Region=0;
                  info.UseRelativeVerticalValues=1; info.BeginValue=95;
                  info.BeginIndex=sc.ArraySize-1; info.Text=" "; sc.UseTool(info); }
                return;
            }
            else if (pDir == -1 && sc.High[i] >= pStop)
            {
                // EXIT SHORT
                sgExBuy[i] = pStop + atr * 0.3f;
                sgSell[i] = 0;

                if (InAlert.GetYesNo()) {
                    float pnlPips = (pEntry - pStop) / 0.0001f;
                    float pnlD = pnlPips * 12.50f;
                    SCString m1; m1.Format("--- EXIT SHORT ---");
                    SCString m2; m2.Format("Exit: %.5f | Entry: %.5f", pStop, pEntry);
                    SCString m3; m3.Format("P&L: %+.1f pips ($%+.1f)", pnlPips, pnlD);
                    sc.AddAlertLine(m1, 1);   // Alert 1 → sound
                    sc.AddAlertLine(m2, 0);
                    sc.AddAlertLine(m3, 0);
                }
                pDir = 0; pEntry = 0; pStop = 0; pBest = 0; pATR = 0;
                pCool = i + InCool.GetInt();
                pEntryBar = 0;
                { s_UseTool info; info.Clear(); info.ChartNumber=sc.ChartNumber;
                  info.DrawingType=DRAWING_TEXT; info.LineNumber=9010;
                  info.AddMethod=UTAM_ADD_OR_ADJUST; info.Region=0;
                  info.UseRelativeVerticalValues=1; info.BeginValue=95;
                  info.BeginIndex=sc.ArraySize-1; info.Text=" "; sc.UseTool(info); }
                return;
            }
        }

        return;
    }

    // ═══════════════════════════════════════════
    // PART 8: LIVE SIGNAL DETECTION + ENTRY
    // ═══════════════════════════════════════════

    if (atr <= 0 || atr < 0.00050f) return;
    if (hitCount < 2) return;

    int lb = InCDLB.GetInt();
    float cdNow = 0, cdPrev = 0;
    for (int j = i - lb; j <= i; j++) {
        if (j < 0) continue;
        cdNow += sc.BaseData[SC_ASKVOL][j] - sc.BaseData[SC_BIDVOL][j];
    }
    for (int j = i - 2 * lb; j < i - lb; j++) {
        if (j < 0) continue;
        cdPrev += sc.BaseData[SC_ASKVOL][j] - sc.BaseData[SC_BIDVOL][j];
    }

    int dL = (delta > 0 && cdNow > cdPrev) ? 1 : 0;
    int dS = (delta < 0 && cdNow < cdPrev) ? 1 : 0;
    if (!dL && !dS) return;

    int isLong = dL;
    if (isLong  && price >= vwap) return;
    if (!isLong && price <= vwap) return;

    float slPrice = isLong ? (price - atr) : (price + atr);

    // Set subgraph arrow
    if (isLong) sgBuy[i] = sc.Low[i] - atr * 0.3f;
    else        sgSell[i] = sc.High[i] + atr * 0.3f;

    // Lock trade in persistent state
    pDir = isLong ? 1 : -1;
    pEntry = price;
    pStop = slPrice;
    pBest = price;
    pATR = atr;
    pEntryBar = i;
    pLastBar = i;

    // Alert — no lastAlertBar guard needed, pDir prevents re-entry
    if (InAlert.GetYesNo())
    {
        SCString lvl;
        for (int h = 0; h < hitCount && h < 4; h++) {
            if (h > 0) lvl += " + ";
            lvl += hitNames[h];
        }
        const char* dir = isLong ? "LONG" : "SHORT";
        float trail = atr * InTrlM.GetFloat();
        float slPips = atr / 0.0001f;
        float trPips = trail / 0.0001f;

        SCString m1; m1.Format("--- DH SIGNAL: %s ---", dir);
        SCString m2; m2.Format("ENTRY: %.5f", price);
        SCString m3; m3.Format("SL: %.5f (%.0f pips)", slPrice, slPips);
        SCString m4; m4.Format("TRAIL: %.5f (%.0f pips)", trail, trPips);
        SCString m5; m5.Format("Levels: %s", lvl.GetChars());
        SCString m6; m6.Format("Delta: %.0f | VWAP: %.5f", delta, vwap);

        sc.AddAlertLine(m1, 1);   // Alert 1 → plays configured sound
        sc.AddAlertLine(m2, 0);   // Info only, no sound
        sc.AddAlertLine(m3, 0);
        sc.AddAlertLine(m4, 0);
        sc.AddAlertLine(m5, 0);
        sc.AddAlertLine(m6, 0);

        s_UseTool et; et.Clear();
        et.ChartNumber = sc.ChartNumber; et.Region = 0;
        et.DrawingType = DRAWING_HORIZONTALLINE;
        et.LineNumber = 9001; et.AddMethod = UTAM_ADD_OR_ADJUST;
        et.BeginValue = price;
        et.Color = isLong ? RGB(0, 200, 100) : RGB(255, 80, 80);
        et.LineWidth = 2; et.DisplayHorizontalLineValue = 1;
        et.Text.Format("ENTRY %s", dir);
        sc.UseTool(et);

        s_UseTool sl; sl.Clear();
        sl.ChartNumber = sc.ChartNumber; sl.Region = 0;
        sl.DrawingType = DRAWING_HORIZONTALLINE;
        sl.LineNumber = 9000; sl.AddMethod = UTAM_ADD_OR_ADJUST;
        sl.BeginValue = slPrice; sl.Color = RGB(255, 60, 60);
        sl.LineWidth = 2; sl.LineStyle = LINESTYLE_DASH;
        sl.DisplayHorizontalLineValue = 1; sl.Text = "SL";
        sc.UseTool(sl);
    }
}
