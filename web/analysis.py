"""Analysis wrapper — uses mega engine's precompute() as single source of truth."""

import sys
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = str(Path(__file__).parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.data.scid_parser import read_scid, aggregate_to_bars
from src.mega.engine import precompute, _compute_vpoc_tpoc_levels

from . import config
from .lessons import get_lesson

logger = logging.getLogger("trading-console.analysis")

IST_OFFSET = timedelta(hours=5, minutes=30)


def _market_status() -> str:
    now_utc = datetime.now(timezone.utc)
    weekday = now_utc.weekday()
    hour = now_utc.hour
    if weekday == 5:
        return "closed"
    if weekday == 6 and hour < 22:
        return "closed"
    if weekday == 4 and hour >= 22:
        return "closed"
    if hour == 21:
        return "maintenance"
    return "open"


def run_full_analysis(scid_path: str) -> dict[str, Any] | None:
    """Run full analysis using mega engine's precompute(). Returns JSON-serializable dict."""
    if not Path(scid_path).exists():
        return None

    try:
        ticks = read_scid(scid_path)
    except Exception as e:
        logger.error(f"Failed to parse SCID: {e}")
        return None

    if len(ticks) < 100:
        logger.error("Not enough tick data")
        return None

    df5 = aggregate_to_bars(ticks, "5min")
    df15 = aggregate_to_bars(ticks, "15min")
    df1h = aggregate_to_bars(ticks, "1h")
    df_daily = aggregate_to_bars(ticks, "1D")

    if len(df5) < 20 or len(df_daily) < 2:
        logger.error("Not enough bars for analysis")
        return None

    # ═══════════════════════════════════════════
    # USE MEGA ENGINE precompute() — single source of truth
    # ═══════════════════════════════════════════
    p = precompute(df5, daily_df=df_daily)

    # Also compute for 15m and 1h (for multi-TF RSI)
    p15 = precompute(df15)
    p1h = precompute(df1h)

    curr = float(df5["close"].iloc[-1])
    last_tick_time = df5.index[-1]
    prev_day = df_daily.iloc[-2]
    today_str = str(df5.index[-1].date())

    # ═══════════════════════════════════════════
    # LEVELS — from precomputed pivots + VPOC/TPOC
    # ═══════════════════════════════════════════
    all_levels = {}

    # Standard pivots from mega engine (keyed by date — get today's)
    std_pivots_all = p.get("std_pivots", {})
    if std_pivots_all:
        today_pivots = std_pivots_all.get(today_str, {})
        if not today_pivots:
            # Try last available date
            last_key = list(std_pivots_all.keys())[-1] if std_pivots_all else None
            today_pivots = std_pivots_all.get(last_key, {}) if last_key else {}
        for name, price in today_pivots.items():
            all_levels[name] = float(price)

    # Fib pivots from mega engine (same date-keyed structure)
    fib_pivots_all = p.get("fib_pivots", {})
    if fib_pivots_all:
        today_fibs = fib_pivots_all.get(today_str, {})
        if not today_fibs:
            last_key = list(fib_pivots_all.keys())[-1] if fib_pivots_all else None
            today_fibs = fib_pivots_all.get(last_key, {}) if last_key else {}
        for name, price in today_fibs.items():
            all_levels[f"Fib_{name}"] = float(price)

    # Daily levels (prev H/L/C — also date-keyed)
    daily_levels_all = p.get("daily_levels", {})
    if daily_levels_all:
        today_daily = daily_levels_all.get(today_str, {})
        if not today_daily:
            last_key = list(daily_levels_all.keys())[-1] if daily_levels_all else None
            today_daily = daily_levels_all.get(last_key, {}) if last_key else {}
        for name, price in today_daily.items():
            try:
                pf = float(price)
                if not np.isnan(pf):
                    all_levels[name] = pf
            except (TypeError, ValueError):
                pass

    # VWAP
    vwap_val = float(p["vwap"].iloc[-1])
    all_levels["VWAP"] = vwap_val

    # Session levels (may be date-keyed or flat)
    session_lvls = p.get("session_levels", {})
    if session_lvls:
        # Could be date-keyed like other levels
        if today_str in session_lvls:
            for name, price in session_lvls[today_str].items():
                try:
                    pf = float(price)
                    if not np.isnan(pf):
                        all_levels[name] = pf
                except (TypeError, ValueError):
                    pass
        else:
            # Flat dict or last available date
            last_key = list(session_lvls.keys())[-1] if session_lvls else None
            if last_key:
                val = session_lvls[last_key]
                if isinstance(val, dict):
                    for name, price in val.items():
                        try:
                            pf = float(price)
                            if not np.isnan(pf):
                                all_levels[name] = pf
                        except (TypeError, ValueError):
                            pass
                else:
                    try:
                        pf = float(val)
                        if not np.isnan(pf):
                            all_levels[last_key] = pf
                    except (TypeError, ValueError):
                        pass

    # VPOC / TPOC from volume profile
    vpoc_tpoc = p.get("vpoc_tpoc", {})
    if today_str in vpoc_tpoc:
        vt = vpoc_tpoc[today_str]
        level_map = {
            "pd_vpoc": "PD_VPOC", "pd_tpoc": "PD_TPOC",
            "wk_vpoc": "Wk_VPOC", "wk_tpoc": "Wk_TPOC",
            "mo_vpoc": "Mo_VPOC", "mo_tpoc": "Mo_TPOC",
            "asia_vpoc": "Asia_VPOC", "asia_tpoc": "Asia_TPOC",
            "ldn_vpoc": "LDN_VPOC", "ldn_tpoc": "LDN_TPOC",
            "ny_vpoc": "NY_VPOC", "ny_tpoc": "NY_TPOC",
        }
        for key, label in level_map.items():
            if key in vt and vt[key] and not np.isnan(vt[key]):
                all_levels[label] = float(vt[key])

    # Cluster within 5 pips
    sorted_lvls = sorted(all_levels.items(), key=lambda x: x[1])
    clusters = []
    current_cluster = [sorted_lvls[0]]
    for name, price in sorted_lvls[1:]:
        if abs(price - current_cluster[-1][1]) * 10000 <= 5:
            current_cluster.append((name, price))
        else:
            clusters.append(current_cluster)
            current_cluster = [(name, price)]
    clusters.append(current_cluster)

    levels_list = []
    for c in sorted(clusters, key=lambda x: x[0][1], reverse=True):
        center = sum(pr for _, pr in c) / len(c)
        dist = (center - curr) * 10000
        names = " + ".join(n for n, _ in c)
        tag = "ultra" if len(c) >= 3 else "important" if len(c) >= 2 else ""
        is_current = abs(dist) < 3
        levels_list.append({
            "name": names, "price": round(center, 5),
            "distance": round(dist, 1), "tag": tag, "is_current": is_current,
        })

    # ═══════════════════════════════════════════
    # DELTA — from precomputed (last 12 bars)
    # ═══════════════════════════════════════════
    delta_bars = []
    n_bars = min(12, len(df5))
    for i in range(-n_bars, 0):
        row = df5.iloc[i]
        color = "green" if row["close"] > row["open"] else "red"
        delta_val = float(p["delta"].iloc[i])
        cum_delta_val = float(p["cum_delta"].iloc[i])
        # Volume per trade for this bar
        vpt_val = float(p["vpt"].iloc[i]) if "vpt" in p else 0
        vpt_avg = float(p["vpt_avg"].iloc[i]) if "vpt_avg" in p and not np.isnan(p["vpt_avg"].iloc[i]) else 0
        # Wick ratios
        upper_wick = float(p["upper_wick_ratio"].iloc[i]) if not np.isnan(p["upper_wick_ratio"].iloc[i]) else 0
        lower_wick = float(p["lower_wick_ratio"].iloc[i]) if not np.isnan(p["lower_wick_ratio"].iloc[i]) else 0

        hidden = ""
        if color == "red" and delta_val > 0:
            hidden = "BUYING"
        elif color == "green" and delta_val < 0:
            hidden = "SELLING"

        # Institutional activity flag (vol per trade > 1.5x avg)
        institutional = vpt_val > vpt_avg * 1.5 if vpt_avg > 0 else False

        # Convert to IST for display
        bar_time_utc = df5.index[i]
        bar_time_ist = bar_time_utc + IST_OFFSET
        delta_bars.append({
            "time": bar_time_ist.strftime("%H:%M"),
            "color": color,
            "delta": round(delta_val),
            "cum_delta": round(cum_delta_val),
            "hidden": hidden,
            "vpt": round(vpt_val, 1),
            "vpt_avg": round(vpt_avg, 1),
            "institutional": institutional,
            "upper_wick": round(upper_wick, 2),
            "lower_wick": round(lower_wick, 2),
            "volume": round(float(row["volume"])),
        })

    # ═══════════════════════════════════════════
    # CUM DELTA TREND
    # ═══════════════════════════════════════════
    cd_now = float(p["cum_delta"].iloc[-1])
    cd_5 = float(p["cum_delta"].iloc[-5]) if len(df5) >= 5 else cd_now
    cd_10 = float(p["cum_delta"].iloc[-10]) if len(df5) >= 10 else cd_now
    cd_20 = float(p["cum_delta"].iloc[-20]) if len(df5) >= 20 else cd_now
    cum_delta = {
        "value": round(cd_now),
        "trend": "rising" if cd_now > cd_10 else "falling",
        "bar5": round(cd_now - cd_5),
        "bar10": round(cd_now - cd_10),
        "bar20": round(cd_now - cd_20),
    }

    # ═══════════════════════════════════════════
    # RSI — multi-timeframe from precomputed
    # ═══════════════════════════════════════════
    rsi_5m = p["rsi"]
    rsi_15m = p15["rsi"]
    rsi_1h = p1h["rsi"]

    def _safe_rsi(s):
        if s is None or len(s) == 0:
            return None
        v = s.iloc[-1]
        return round(float(v), 1) if not np.isnan(v) else None

    # ═══════════════════════════════════════════
    # ATR — from precomputed
    # ═══════════════════════════════════════════
    atr_5m_val = float(p["atr"].iloc[-1]) * 10000 if p["atr"] is not None and not np.isnan(p["atr"].iloc[-1]) else None
    # Daily ATR — compute separately since precompute doesn't do daily
    from src.analysis.rsi import calculate_atr
    atr_daily_series = calculate_atr(df_daily)
    atr_daily_val = round(float(atr_daily_series.iloc[-1]) * 10000, 1) if atr_daily_series is not None and len(atr_daily_series) > 0 else None

    # ═══════════════════════════════════════════
    # VOLUME — from precomputed
    # ═══════════════════════════════════════════
    vol_avg = float(p["vol_avg"].iloc[-1]) if not np.isnan(p["vol_avg"].iloc[-1]) else 0
    last_vol = float(df5["volume"].iloc[-1])
    vol_ratio = round(last_vol / vol_avg, 1) if vol_avg > 0 else 0

    # Volume per trade current
    vpt_current = float(p["vpt"].iloc[-1]) if "vpt" in p else 0
    vpt_avg_current = float(p["vpt_avg"].iloc[-1]) if "vpt_avg" in p and not np.isnan(p["vpt_avg"].iloc[-1]) else 0

    # ═══════════════════════════════════════════
    # RSI DIVERGENCE — using precomputed swing points
    # ═══════════════════════════════════════════
    rsi_div = {"detected": False, "type": "", "detail": ""}
    if len(df5) >= 30 and rsi_5m is not None:
        # Use precomputed swing lows for more accurate divergence
        swing_lows = p["swing_lows_5"]
        rsi_swing_lows = p["rsi_swing_lows_5"]
        recent = min(30, len(df5))

        # Find last two price swing lows
        price_swing_indices = np.where(swing_lows[-recent:])[0]
        rsi_swing_indices = np.where(rsi_swing_lows[-recent:])[0]

        if len(price_swing_indices) >= 2:
            idx1 = price_swing_indices[-1] + (len(df5) - recent)
            idx2 = price_swing_indices[-2] + (len(df5) - recent)
            p1 = float(df5["low"].iloc[idx1])
            p2 = float(df5["low"].iloc[idx2])
            r1 = float(rsi_5m.iloc[idx1])
            r2 = float(rsi_5m.iloc[idx2])
            if p1 < p2 and r1 > r2:
                rsi_div = {"detected": True, "type": "bullish", "detail": "price LL, RSI HL"}
            elif p1 > p2 and r1 < r2:
                rsi_div = {"detected": True, "type": "bearish", "detail": "price HL, RSI LL"}

    # ═══════════════════════════════════════════
    # DH|S2 CHECKLIST — aligned with replay strategy
    # (delta + cum_delta momentum + VWAP + ATR)
    # ═══════════════════════════════════════════
    last_delta = float(p["delta"].iloc[-1])
    atr_val = float(p["atr"].iloc[-1]) if not np.isnan(p["atr"].iloc[-1]) else 0

    # At confluent level — ATR-based threshold (same as replay)
    level_threshold_pips = round(atr_val * 0.5 * 10000, 1)  # half ATR in pips
    at_level = any(
        abs(l["distance"]) <= level_threshold_pips
        for l in levels_list
    )

    # Cum delta momentum: last 6 bars vs prev 5 bars (same as replay)
    n = len(df5)
    cd_recent = sum(float(p["delta"].iloc[max(0, n - 6 + j)]) for j in range(min(6, n))
                    if not np.isnan(p["delta"].iloc[max(0, n - 6 + j)]))
    cd_prev = sum(float(p["delta"].iloc[max(0, n - 11 + j)]) for j in range(min(5, n))
                  if not np.isnan(p["delta"].iloc[max(0, n - 11 + j)]))

    # Direction detection (both long AND short)
    delta_long = last_delta > 0 and cd_recent > cd_prev
    delta_short = last_delta < 0 and cd_recent < cd_prev
    delta_confirms = delta_long or delta_short
    cum_delta_momentum = (delta_long and cd_recent > cd_prev) or (delta_short and cd_recent < cd_prev)

    # VWAP alignment (direction-aware)
    if delta_long:
        vwap_aligned = curr < vwap_val
        detected_direction = "LONG"
    elif delta_short:
        vwap_aligned = curr > vwap_val
        detected_direction = "SHORT"
    else:
        vwap_aligned = False
        detected_direction = "NONE"

    # ATR minimum (5 pips = 0.00050)
    atr_ok = atr_val >= 0.00020  # 2-pip min on 5-min bars

    checklist = []
    for item in config.CHECKLIST:
        val = {
            "at_level": at_level,
            "delta_confirms": delta_confirms,
            "cum_delta_momentum": cum_delta_momentum,
            "vwap_aligned": vwap_aligned,
            "atr_ok": atr_ok,
        }.get(item["key"], False)
        checklist.append({"name": item["name"], "passed": val})

    all_passed = all(c["passed"] for c in checklist)

    # ═══════════════════════════════════════════
    # WICK ANALYSIS — from precomputed
    # ═══════════════════════════════════════════
    # Check last 3 bars for wick rejection signals
    wick_signal = None
    for i in range(-3, 0):
        uw = float(p["upper_wick_ratio"].iloc[i]) if not np.isnan(p["upper_wick_ratio"].iloc[i]) else 0
        lw = float(p["lower_wick_ratio"].iloc[i]) if not np.isnan(p["lower_wick_ratio"].iloc[i]) else 0
        if lw > 0.6:
            wick_signal = {"type": "bullish_rejection", "bar": df5.index[i].strftime("%H:%M"), "ratio": round(lw, 2)}
        elif uw > 0.6:
            wick_signal = {"type": "bearish_rejection", "bar": df5.index[i].strftime("%H:%M"), "ratio": round(uw, 2)}

    # ═══════════════════════════════════════════
    # STALE DATA DETECTION
    # ═══════════════════════════════════════════
    now_utc = datetime.now(timezone.utc)
    last_tick_utc = last_tick_time.to_pydatetime()
    if last_tick_utc.tzinfo is None:
        last_tick_utc = last_tick_utc.replace(tzinfo=timezone.utc)
    data_age_minutes = (now_utc - last_tick_utc).total_seconds() / 60
    mkt = _market_status()
    stale = data_age_minutes > 15 and mkt == "open"
    very_stale = data_age_minutes > 60 and mkt == "open"

    # ═══════════════════════════════════════════
    # TRADE PLAN
    # ═══════════════════════════════════════════
    support_levels = [l for l in levels_list if l["distance"] < -2 and l["tag"] in ("important", "ultra")]
    resistance_levels = [l for l in levels_list if l["distance"] > 2 and l["tag"] in ("important", "ultra")]
    nearest_support = support_levels[0] if support_levels else None
    nearest_resistance = resistance_levels[-1] if resistance_levels else None

    vwap_dist = round((vwap_val - curr) * 10000, 1)

    if detected_direction == "SHORT":
        bias = "Bearish tape. Cum delta falling, sellers in control."
    elif detected_direction == "LONG":
        bias = "Bullish momentum building. Delta + cum delta aligned."
    else:
        bias = "Mixed signals. Wait for delta and cum delta to align."

    if nearest_support:
        key_zone = f"{nearest_support['name']} at {nearest_support['price']} ({nearest_support['distance']:.0f}p)"
    elif at_level:
        current_lvl = next((l for l in levels_list if l["is_current"]), None)
        key_zone = f"AT {current_lvl['name']} ({current_lvl['price']})" if current_lvl else "At current level"
    else:
        key_zone = "Between levels — no strong zone nearby"

    trade_plan = {"bias": bias, "key_zone": key_zone, "vwap_distance": vwap_dist}

    # Long/short setups
    long_setup = None
    short_setup = None
    if nearest_support:
        entry_price = nearest_support["price"]
        sl = round(entry_price - 0.0010, 5)
        risk = round((entry_price - sl) * 10000, 1)

        # Find targets ABOVE entry — sorted closest first
        targets_above = sorted(
            [l for l in levels_list if l["price"] > entry_price + 0.0005 and l["tag"] in ("important", "ultra")],
            key=lambda l: l["price"]
        )
        # Also consider VWAP as a target
        vwap_as_target = {"name": "VWAP", "price": vwap_val} if vwap_val > entry_price + 0.0005 else None

        # Build sorted target list (closest first)
        all_targets = []
        for t in targets_above:
            all_targets.append(t)
        if vwap_as_target:
            all_targets.append(vwap_as_target)
        all_targets.sort(key=lambda t: t["price"])

        tp1_lvl = all_targets[0] if len(all_targets) >= 1 else None
        tp2_lvl = all_targets[1] if len(all_targets) >= 2 else None

        if tp1_lvl:
            tp1_reward = round((tp1_lvl["price"] - entry_price) * 10000, 1)
            rr = round(tp1_reward / risk, 1) if risk > 0 else 0
            long_setup = {
                "entry": f"{entry_price:.5f}",
                "sl": f"{sl:.5f} ({risk:.0f}p)",
                "tp1": f"{tp1_lvl['price']:.5f} {tp1_lvl['name']} ({tp1_reward:.0f}p)",
                "tp2": f"{tp2_lvl['price']:.5f} {tp2_lvl['name']} ({round((tp2_lvl['price'] - entry_price) * 10000, 1):.0f}p)" if tp2_lvl else None,
                "rr": f"1:{rr}",
                "trigger": f"Delta + for 3+ bars at {nearest_support['name']}",
            }

        # Short setup if support breaks
        s_sl = round(entry_price + 0.0008, 5)
        s_risk = round((s_sl - entry_price) * 10000, 1)
        targets_below = sorted(
            [l for l in levels_list if l["price"] < entry_price - 0.0005 and l["tag"] in ("important", "ultra")],
            key=lambda l: l["price"], reverse=True
        )
        if targets_below:
            s_target = targets_below[0]
            s_reward = round((entry_price - s_target["price"]) * 10000, 1)
            s_rr = round(s_reward / s_risk, 1) if s_risk > 0 else 0
            short_setup = {
                "entry": f"{entry_price - 0.0003:.5f} (break)",
                "sl": f"{s_sl:.5f} ({s_risk:.0f}p)",
                "tp1": f"{s_target['price']:.5f} {s_target['name']} ({s_reward:.0f}p)",
                "tp2": None,
                "rr": f"1:{s_rr}",
                "trigger": f"Break {nearest_support['name']} w/ volume + neg delta",
            }

    # ═══════════════════════════════════════════
    # DH|S2 STRATEGY SIGNAL — matches replay exactly
    # ═══════════════════════════════════════════
    strategy_signal = None
    if all_passed and detected_direction != "NONE":
        sl_price = round(curr - atr_val if detected_direction == "LONG" else curr + atr_val, 5)
        risk_pips = round(atr_val * 10000, 1)
        trail_pips = round(atr_val * 0.5 * 10000, 1)
        strategy_signal = {
            "direction": detected_direction,
            "entry": round(curr, 5),
            "sl": sl_price,
            "sl_pips": risk_pips,
            "trail": trail_pips,
            "exit_rules": f"Trail {trail_pips:.0f}p | SL {risk_pips:.0f}p | Cut by 12:00 IST",
        }

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "last_tick_time": str(last_tick_time),
        "current_price": round(curr, 5),
        "previous_day": {
            "high": round(float(prev_day["high"]), 5),
            "low": round(float(prev_day["low"]), 5),
            "close": round(float(prev_day["close"]), 5),
            "range": round((float(prev_day["high"]) - float(prev_day["low"])) * 10000, 0),
        },
        "vwap": round(vwap_val, 5),
        "vwap_distance": round((curr - vwap_val) * 10000, 1),
        "levels": levels_list,
        "delta_bars": delta_bars,
        "cum_delta": cum_delta,
        "rsi": {
            "m5": _safe_rsi(rsi_5m),
            "m15": _safe_rsi(rsi_15m),
            "h1": _safe_rsi(rsi_1h),
        },
        "atr": {
            "m5": round(atr_5m_val, 1) if atr_5m_val else None,
            "daily": atr_daily_val,
        },
        "volume": {
            "current": round(last_vol),
            "avg_20": round(vol_avg),
            "ratio": vol_ratio,
            "vpt": round(vpt_current, 1),
            "vpt_avg": round(vpt_avg_current, 1),
        },
        "rsi_divergence": rsi_div,
        "wick_signal": wick_signal,
        "checklist": checklist,
        "setup_triggered": all_passed,
        "trade_plan": trade_plan,
        "long_setup": long_setup,
        "short_setup": short_setup,
        "strategy_signal": strategy_signal,
        "stale_data": stale,
        "very_stale_data": very_stale,
        "data_age_minutes": round(data_age_minutes, 1),
        "market_status": mkt,
        "micro_lesson": get_lesson(),
    }
