"""Statistics engine: outlier removal, ranking, comprehensive metrics.
Works with Trade objects from engine.py.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List
from datetime import timedelta


IST_OFFSET = timedelta(hours=5, minutes=30)


@dataclass
class StrategyResult:
    name: str
    timeframe: str
    # Core
    total: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0
    total_pnl: float = 0
    avg_pnl: float = 0
    profit_factor: float = 0
    avg_rr: float = 0
    avg_winner: float = 0
    avg_loser: float = 0
    max_win: float = 0
    max_loss: float = 0
    max_consec_loss: int = 0
    avg_bars: float = 0
    freq_per_day: float = 0
    sharpe: float = 0
    # Splits
    long_wr: float = 0
    short_wr: float = 0
    long_pnl: float = 0
    short_pnl: float = 0
    # Outlier
    outliers_removed: int = 0
    pnl_before_outlier: float = 0
    pf_before_outlier: float = 0
    # Level splits
    pivot_trades: int = 0
    pivot_wr: float = 0
    session_trades: int = 0
    session_wr: float = 0
    fib_trades: int = 0
    fib_wr: float = 0
    # Time
    best_hour_ist: int = 0
    best_hour_wr: float = 0
    # Ranking
    score: float = 0


def compute_stats(name: str, timeframe: str, trades: list, trading_days: int) -> StrategyResult:
    """Compute stats from Trade objects with outlier removal."""
    r = StrategyResult(name=name, timeframe=timeframe)
    if not trades:
        return r

    pnls = [t.pnl_pct for t in trades]

    # Pre-outlier stats
    w_pre = [p for p in pnls if p > 0]
    l_pre = [p for p in pnls if p <= 0]
    r.pnl_before_outlier = sum(pnls)
    r.pf_before_outlier = sum(w_pre) / abs(sum(l_pre)) if sum(l_pre) != 0 else 99.0

    # Outlier removal
    clean, n_removed = _remove_outliers(trades)
    r.outliers_removed = n_removed
    if not clean:
        return r

    pnls = [t.pnl_pct for t in clean]
    winners = [t for t in clean if t.pnl_pct > 0]
    losers = [t for t in clean if t.pnl_pct <= 0]
    gp = sum(t.pnl_pct for t in winners)
    gl = abs(sum(t.pnl_pct for t in losers))

    r.total = len(clean)
    r.wins = len(winners)
    r.losses = len(losers)
    r.win_rate = r.wins / r.total * 100
    r.total_pnl = sum(pnls)
    r.avg_pnl = r.total_pnl / r.total
    r.profit_factor = gp / gl if gl > 0 else 99.0
    r.avg_winner = gp / len(winners) if winners else 0
    r.avg_loser = -gl / len(losers) if losers else 0
    r.avg_rr = r.avg_winner / abs(r.avg_loser) if r.avg_loser != 0 else 99.0
    r.max_win = max(pnls)
    r.max_loss = min(pnls)
    r.avg_bars = sum(t.bars_held for t in clean) / r.total
    r.freq_per_day = r.total / trading_days if trading_days > 0 else 0

    # Consecutive losses
    streak = 0
    max_streak = 0
    for p in pnls:
        if p <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    r.max_consec_loss = max_streak

    # Sharpe
    std = np.std(pnls) if len(pnls) > 1 else 1
    r.sharpe = r.avg_pnl / std if std > 0 else 0

    # Long/Short
    longs = [t for t in clean if t.direction == "long"]
    shorts = [t for t in clean if t.direction == "short"]
    r.long_wr = len([t for t in longs if t.pnl_pct > 0]) / len(longs) * 100 if longs else 0
    r.short_wr = len([t for t in shorts if t.pnl_pct > 0]) / len(shorts) * 100 if shorts else 0
    r.long_pnl = sum(t.pnl_pct for t in longs)
    r.short_pnl = sum(t.pnl_pct for t in shorts)

    # Level splits
    pivot_t = [t for t in clean if t.signal.confluences.get("at_any_pivot")]
    session_t = [t for t in clean if t.signal.confluences.get("at_session_level")]
    fib_t = [t for t in clean if t.signal.confluences.get("at_fib_retracement")]
    r.pivot_trades = len(pivot_t)
    r.pivot_wr = len([t for t in pivot_t if t.pnl_pct > 0]) / len(pivot_t) * 100 if pivot_t else 0
    r.session_trades = len(session_t)
    r.session_wr = len([t for t in session_t if t.pnl_pct > 0]) / len(session_t) * 100 if session_t else 0
    r.fib_trades = len(fib_t)
    r.fib_wr = len([t for t in fib_t if t.pnl_pct > 0]) / len(fib_t) * 100 if fib_t else 0

    # Best hour IST
    hour_map = {}
    for t in clean:
        h = (t.entry_time + IST_OFFSET).hour
        hour_map.setdefault(h, {"w": 0, "t": 0})
        hour_map[h]["t"] += 1
        if t.pnl_pct > 0:
            hour_map[h]["w"] += 1
    if hour_map:
        best = max(hour_map, key=lambda h: hour_map[h]["w"] / max(hour_map[h]["t"], 1))
        r.best_hour_ist = best
        r.best_hour_wr = hour_map[best]["w"] / hour_map[best]["t"] * 100

    # Score: PF * freq_boost * consistency
    if 0.5 <= r.freq_per_day <= 2.5:
        freq_boost = 1.5
    elif 0.2 <= r.freq_per_day < 0.5 or 2.5 < r.freq_per_day <= 4.0:
        freq_boost = 1.0
    else:
        freq_boost = 0.5
    profitable = 1.0 if r.total_pnl > 0 else 0.05
    consistency = min(max(r.sharpe * 10, 0.5), 2.0)
    r.score = r.profit_factor * freq_boost * profitable * consistency

    return r


def _remove_outliers(trades, iqr_mult=2.0):
    if len(trades) < 10:
        return trades, 0
    pnls = np.array([t.pnl_pct for t in trades])
    q1, q3 = np.percentile(pnls, 25), np.percentile(pnls, 75)
    iqr = q3 - q1
    lo, hi = q1 - iqr_mult * iqr, q3 + iqr_mult * iqr
    clean = [t for t in trades if lo <= t.pnl_pct <= hi]
    return clean, len(trades) - len(clean)


def rank_strategies(results: List[StrategyResult], min_trades: int = 10) -> List[StrategyResult]:
    qualified = [r for r in results if r.total >= min_trades and r.total_pnl > 0]
    # Cap PF at 99 to prevent infinity skew
    for r in qualified:
        r.profit_factor = min(r.profit_factor, 99.0)
        # Recalculate score with capped PF
        if 0.5 <= r.freq_per_day <= 2.5:
            fb = 1.5
        elif 0.2 <= r.freq_per_day < 0.5 or 2.5 < r.freq_per_day <= 4.0:
            fb = 1.0
        else:
            fb = 0.5
        profitable = 1.0 if r.total_pnl > 0 else 0.05
        consistency = min(max(r.sharpe * 10, 0.5), 2.0)
        r.score = r.profit_factor * fb * profitable * consistency
    qualified.sort(key=lambda r: r.score, reverse=True)
    return qualified
