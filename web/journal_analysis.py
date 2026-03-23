"""Analysis engine — computes patterns and insights from trading journal data."""

import statistics
from .journal_models import get_db, dicts_from_rows


def get_overview(conn):
    """Overall stats across all completed sessions."""
    sessions = dicts_from_rows(conn.execute(
        "SELECT * FROM sessions WHERE status='completed' ORDER BY start_time DESC"
    ).fetchall())

    trades = dicts_from_rows(conn.execute(
        "SELECT t.*, s.mood_before, s.sleep_hours, s.readiness_score "
        "FROM trades t JOIN sessions s ON t.session_id = s.id "
        "WHERE s.status='completed'"
    ).fetchall())

    if not sessions:
        return {"total_sessions": 0, "message": "No completed sessions yet. Start trading!"}

    total_trades = len(trades)
    wins = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    breakevens = [t for t in trades if t["outcome"] == "breakeven"]
    per_plan = [t for t in trades if t["per_plan"]]
    rule_breaks = [t for t in trades if not t["per_plan"]]

    total_pips = sum(t["pnl_pips"] or 0 for t in trades)
    win_rate = (len(wins) / total_trades * 100) if total_trades else 0
    plan_adherence = (len(per_plan) / total_trades * 100) if total_trades else 0

    avg_win = statistics.mean([t["pnl_pips"] for t in wins if t["pnl_pips"]]) if wins else 0
    avg_loss = statistics.mean([abs(t["pnl_pips"]) for t in losses if t["pnl_pips"]]) if losses else 0
    risk_reward = (avg_win / avg_loss) if avg_loss > 0 else 0

    return {
        "total_sessions": len(sessions),
        "total_trades": total_trades,
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(breakevens),
        "win_rate": round(win_rate, 1),
        "total_pips": round(total_pips, 1),
        "avg_win_pips": round(avg_win, 1),
        "avg_loss_pips": round(avg_loss, 1),
        "risk_reward": round(risk_reward, 2),
        "plan_adherence": round(plan_adherence, 1),
        "rule_breaks": len(rule_breaks),
    }


def get_correlations(conn):
    """Find correlations between biometrics/context and trade outcomes."""
    trades = dicts_from_rows(conn.execute(
        "SELECT t.*, s.mood_before, s.sleep_hours, s.readiness_score, s.caffeine_cups, s.id as sid "
        "FROM trades t JOIN sessions s ON t.session_id = s.id "
        "WHERE s.status='completed'"
    ).fetchall())

    if len(trades) < 3:
        return {"message": "Need at least 3 trades for correlation analysis."}

    results = {}

    # Win rate by emotion before
    emotion_groups = {}
    for t in trades:
        e = t["emotion_before"] or "unknown"
        emotion_groups.setdefault(e, []).append(t)
    results["by_emotion"] = {
        e: {
            "count": len(ts),
            "win_rate": round(sum(1 for t in ts if t["outcome"] == "win") / len(ts) * 100, 1),
            "avg_pips": round(statistics.mean([t["pnl_pips"] or 0 for t in ts]), 1),
        }
        for e, ts in emotion_groups.items() if len(ts) >= 1
    }

    # Win rate by sleep hours
    sleep_groups = {"<6h": [], "6-7h": [], "7-8h": [], "8h+": []}
    for t in trades:
        sh = t["sleep_hours"]
        if sh is None:
            continue
        if sh < 6:
            sleep_groups["<6h"].append(t)
        elif sh < 7:
            sleep_groups["6-7h"].append(t)
        elif sh < 8:
            sleep_groups["7-8h"].append(t)
        else:
            sleep_groups["8h+"].append(t)
    results["by_sleep"] = {
        k: {
            "count": len(ts),
            "win_rate": round(sum(1 for t in ts if t["outcome"] == "win") / len(ts) * 100, 1) if ts else 0,
            "plan_adherence": round(sum(1 for t in ts if t["per_plan"]) / len(ts) * 100, 1) if ts else 0,
        }
        for k, ts in sleep_groups.items() if ts
    }

    # Win rate by plan adherence
    plan_trades = [t for t in trades if t["per_plan"]]
    break_trades = [t for t in trades if not t["per_plan"]]
    results["plan_vs_break"] = {
        "per_plan": {
            "count": len(plan_trades),
            "win_rate": round(sum(1 for t in plan_trades if t["outcome"] == "win") / len(plan_trades) * 100, 1) if plan_trades else 0,
            "avg_pips": round(statistics.mean([t["pnl_pips"] or 0 for t in plan_trades]), 1) if plan_trades else 0,
        },
        "rule_break": {
            "count": len(break_trades),
            "win_rate": round(sum(1 for t in break_trades if t["outcome"] == "win") / len(break_trades) * 100, 1) if break_trades else 0,
            "avg_pips": round(statistics.mean([t["pnl_pips"] or 0 for t in break_trades]), 1) if break_trades else 0,
        }
    }

    # Win rate by confidence level
    conf_groups = {}
    for t in trades:
        c = t["confidence_before"]
        if c is None:
            continue
        conf_groups.setdefault(c, []).append(t)
    results["by_confidence"] = {
        str(c): {
            "count": len(ts),
            "win_rate": round(sum(1 for t in ts if t["outcome"] == "win") / len(ts) * 100, 1),
        }
        for c, ts in sorted(conf_groups.items()) if len(ts) >= 1
    }

    # Win rate by HR zone (if health data exists)
    hr_by_session = {}
    for row in conn.execute(
        "SELECT session_id, AVG(value) as avg_hr FROM health_samples "
        "WHERE metric_type='hr' GROUP BY session_id"
    ).fetchall():
        hr_by_session[row["session_id"]] = row["avg_hr"]

    if hr_by_session:
        hr_groups = {"calm (<75)": [], "normal (75-90)": [], "elevated (90-100)": [], "stressed (100+)": []}
        for t in trades:
            avg_hr = hr_by_session.get(t["sid"])
            if avg_hr is None:
                continue
            if avg_hr < 75:
                hr_groups["calm (<75)"].append(t)
            elif avg_hr < 90:
                hr_groups["normal (75-90)"].append(t)
            elif avg_hr < 100:
                hr_groups["elevated (90-100)"].append(t)
            else:
                hr_groups["stressed (100+)"].append(t)
        results["by_heart_rate"] = {
            k: {
                "count": len(ts),
                "win_rate": round(sum(1 for t in ts if t["outcome"] == "win") / len(ts) * 100, 1) if ts else 0,
                "avg_pips": round(statistics.mean([t["pnl_pips"] or 0 for t in ts]), 1) if ts else 0,
            }
            for k, ts in hr_groups.items() if ts
        }

    return results


def get_insights(conn):
    """Generate actionable insights from accumulated data."""
    trades = dicts_from_rows(conn.execute(
        "SELECT t.*, s.mood_before, s.sleep_hours, s.readiness_score, s.caffeine_cups "
        "FROM trades t JOIN sessions s ON t.session_id = s.id "
        "WHERE s.status='completed'"
    ).fetchall())

    sessions = dicts_from_rows(conn.execute(
        "SELECT * FROM sessions WHERE status='completed'"
    ).fetchall())

    insights = []

    if len(trades) < 5:
        insights.append({
            "type": "info",
            "icon": "i",
            "title": "Building your profile",
            "text": f"Complete {5 - len(trades)} more trades to unlock pattern detection. "
                    "Every session teaches the system about your trading personality."
        })
        return insights

    # 1. Rule-breaking cost
    plan_trades = [t for t in trades if t["per_plan"]]
    break_trades = [t for t in trades if not t["per_plan"]]
    if break_trades:
        break_pips = sum(t["pnl_pips"] or 0 for t in break_trades)
        plan_pips = sum(t["pnl_pips"] or 0 for t in plan_trades) if plan_trades else 0
        if break_pips < 0:
            insights.append({
                "type": "danger",
                "icon": "!",
                "title": "Rule-breaking is costing you",
                "text": f"Your {len(break_trades)} off-plan trades lost {abs(break_pips):.1f} pips total. "
                        f"Plan trades netted {plan_pips:+.1f} pips. "
                        "Delete rule-breaking and you'd be more profitable."
            })

    # 2. Sleep impact
    well_rested = [t for t in trades if (t["sleep_hours"] or 0) >= 7]
    under_slept = [t for t in trades if t["sleep_hours"] and t["sleep_hours"] < 6]
    if well_rested and under_slept:
        wr_good = sum(1 for t in well_rested if t["outcome"] == "win") / len(well_rested) * 100
        wr_bad = sum(1 for t in under_slept if t["outcome"] == "win") / len(under_slept) * 100
        if wr_good > wr_bad + 10:
            insights.append({
                "type": "warning",
                "icon": "z",
                "title": "Sleep directly affects your P&L",
                "text": f"Win rate with 7h+ sleep: {wr_good:.0f}%. "
                        f"Win rate with <6h sleep: {wr_bad:.0f}%. "
                        "Prioritize sleep before trading days."
            })

    # 3. Worst emotion
    emotion_wr = {}
    for t in trades:
        e = t["emotion_before"]
        if not e:
            continue
        emotion_wr.setdefault(e, {"wins": 0, "total": 0})
        emotion_wr[e]["total"] += 1
        if t["outcome"] == "win":
            emotion_wr[e]["wins"] += 1
    worst_emotion = None
    worst_wr = 100
    best_emotion = None
    best_wr = 0
    for e, data in emotion_wr.items():
        if data["total"] >= 2:
            wr = data["wins"] / data["total"] * 100
            if wr < worst_wr:
                worst_wr = wr
                worst_emotion = e
            if wr > best_wr:
                best_wr = wr
                best_emotion = e
    if worst_emotion and worst_wr < 40:
        insights.append({
            "type": "danger",
            "icon": "!",
            "title": f"Avoid trading when feeling '{worst_emotion}'",
            "text": f"Win rate when {worst_emotion}: {worst_wr:.0f}%. "
                    f"Your best state is '{best_emotion}' at {best_wr:.0f}%. "
                    "If you feel the bad emotion, step away or reduce size."
        })
    elif best_emotion:
        insights.append({
            "type": "success",
            "icon": "+",
            "title": f"You trade best when '{best_emotion}'",
            "text": f"Win rate: {best_wr:.0f}% across {emotion_wr[best_emotion]['total']} trades. "
                    "Try to cultivate this mental state before sessions."
        })

    # 4. Tilt detection — consecutive losses → rule break
    session_ids = sorted(set(t["session_id"] for t in trades))
    tilt_count = 0
    for sid in session_ids:
        st = sorted([t for t in trades if t["session_id"] == sid], key=lambda x: x["entry_time"] or "")
        consecutive_losses = 0
        for t in st:
            if t["outcome"] == "loss":
                consecutive_losses += 1
            else:
                consecutive_losses = 0
            if consecutive_losses >= 2 and not t["per_plan"]:
                tilt_count += 1
    if tilt_count > 0:
        insights.append({
            "type": "warning",
            "icon": "~",
            "title": "Tilt pattern detected",
            "text": f"Found {tilt_count} instances where 2+ consecutive losses led to rule-breaking. "
                    "Rule: After 2 losses in a row, take a 15-min break or stop for the day."
        })

    # 5. Confidence calibration
    high_conf = [t for t in trades if (t["confidence_before"] or 0) >= 4]
    low_conf = [t for t in trades if (t["confidence_before"] or 0) <= 2 and t["confidence_before"] is not None]
    if high_conf and low_conf:
        hc_wr = sum(1 for t in high_conf if t["outcome"] == "win") / len(high_conf) * 100
        lc_wr = sum(1 for t in low_conf if t["outcome"] == "win") / len(low_conf) * 100
        if lc_wr > hc_wr:
            insights.append({
                "type": "warning",
                "icon": "?",
                "title": "Overconfidence is hurting you",
                "text": f"High confidence trades win {hc_wr:.0f}%, but low confidence wins {lc_wr:.0f}%. "
                        "Your gut might be wrong when you feel most sure. Double-check setups on high-confidence entries."
            })

    # 6. Overall discipline score
    if trades:
        plan_pct = len(plan_trades) / len(trades) * 100
        if plan_pct >= 80:
            insights.append({
                "type": "success",
                "icon": "+",
                "title": f"Discipline score: {plan_pct:.0f}%",
                "text": "Strong plan adherence. Keep it up — consistent execution beats occasional brilliance."
            })
        elif plan_pct < 50:
            insights.append({
                "type": "danger",
                "icon": "!",
                "title": f"Discipline score: {plan_pct:.0f}%",
                "text": "More than half your trades are off-plan. This is the #1 thing to fix. "
                        "Consider trading smaller size until discipline improves."
            })

    return insights


def compute_session_health_summary(conn, session_id):
    """Compute health summary for a specific session."""
    samples = dicts_from_rows(conn.execute(
        "SELECT * FROM health_samples WHERE session_id = ? ORDER BY timestamp",
        (session_id,)
    ).fetchall())

    if not samples:
        return None

    hr_samples = [s["value"] for s in samples if s["metric_type"] == "hr"]
    hrv_samples = [s["value"] for s in samples if s["metric_type"] == "hrv"]

    result = {}
    if hr_samples:
        result["hr"] = {
            "avg": round(statistics.mean(hr_samples), 1),
            "max": round(max(hr_samples), 1),
            "min": round(min(hr_samples), 1),
            "std": round(statistics.stdev(hr_samples), 1) if len(hr_samples) > 1 else 0,
            "samples": len(hr_samples),
        }
        # Stress classification
        avg = result["hr"]["avg"]
        if avg < 75:
            result["stress_level"] = "low"
        elif avg < 90:
            result["stress_level"] = "moderate"
        elif avg < 100:
            result["stress_level"] = "elevated"
        else:
            result["stress_level"] = "high"

    if hrv_samples:
        result["hrv"] = {
            "avg": round(statistics.mean(hrv_samples), 1),
            "min": round(min(hrv_samples), 1),
            "max": round(max(hrv_samples), 1),
            "samples": len(hrv_samples),
        }

    return result


def compute_readiness(sleep_hours, mood, caffeine, resting_hr=None, hrv_avg=None):
    """Compute a 0-100 readiness score from pre-session context."""
    score = 50  # baseline

    # Sleep factor (0-30 points)
    if sleep_hours is not None:
        if sleep_hours >= 8:
            score += 30
        elif sleep_hours >= 7:
            score += 25
        elif sleep_hours >= 6:
            score += 15
        elif sleep_hours >= 5:
            score += 5
        else:
            score -= 10

    # Mood factor (0-20 points)
    mood_scores = {
        "calm": 20, "focused": 20, "energized": 15,
        "neutral": 10, "tired": 0, "anxious": -5,
        "frustrated": -10, "fomo": -15, "revenge": -20
    }
    score += mood_scores.get(mood, 5)

    # Caffeine factor
    if caffeine is not None:
        if caffeine == 0:
            score += 0
        elif caffeine <= 2:
            score += 5
        else:
            score -= 5  # too much caffeine = jittery

    # HRV factor (if available)
    if hrv_avg is not None:
        if hrv_avg > 50:
            score += 10
        elif hrv_avg > 35:
            score += 5
        elif hrv_avg < 20:
            score -= 10

    return max(0, min(100, score))
