"""Generate DH|S2 replay HTML from .scid data — runs on server start."""

import json
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = str(Path(__file__).parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.data.scid_parser import read_scid, aggregate_to_bars, load_6e_combined
from src.mega.engine import precompute, execute_exit
from src.mega.pip_hunt import pips, PipTrade

logger = logging.getLogger("trading-console.replay")
IST = timedelta(hours=5, minutes=30)
PIP = 0.0001

# Best exit config from pip_hunt results (tr_05 = trail 0.5x ATR, 1.0x SL)
BEST_EXIT = {"mode": "trail", "sl_mult": 1.0, "trail_mult": 0.5, "max_bars": 30, "time_cutoff_ist": 12}


def get_replay_signals(df5, ind, window_start=8, window_end=12):
    """DH signals for replay — more permissive (1+ level, includes session VPOCs)."""
    signals = []
    for i in range(30, len(df5)):
        ist_dt = df5.index[i] + IST
        ist_h = ist_dt.hour + ist_dt.minute / 60
        if not (window_start <= ist_h < window_end):
            continue

        atr = ind["atr"].iloc[i]
        if np.isnan(atr) or atr < 0.00020:  # 2-pip min on 5-min bars (not daily)
            continue

        price = df5["close"].iloc[i]
        vwap = ind["vwap"].iloc[i]
        if np.isnan(vwap):
            continue

        delta = ind["delta"].iloc[i] if not np.isnan(ind["delta"].iloc[i]) else 0

        # Cum delta momentum
        cd_now = sum(ind["delta"].iloc[max(0, i - 5 + j)]
                     for j in range(6)
                     if not np.isnan(ind["delta"].iloc[max(0, i - 5 + j)]))
        cd_prev = sum(ind["delta"].iloc[max(0, i - 10 + j)]
                      for j in range(5)
                      if not np.isnan(ind["delta"].iloc[max(0, i - 10 + j)]))

        delta_long = delta > 0 and cd_now > cd_prev
        delta_short = delta < 0 and cd_now < cd_prev
        if not (delta_long or delta_short):
            continue

        direction = "long" if delta_long else "short"
        vwap_ok = (direction == "long" and price < vwap) or (direction == "short" and price > vwap)
        if not vwap_ok:
            continue

        # Level check — expanded level types, 1+ level minimum
        date_key = ist_dt.strftime("%Y-%m-%d")
        threshold = atr * 0.5
        levels_near = []

        if date_key in ind.get("std_pivots", {}):
            for name, lvl in ind["std_pivots"][date_key].items():
                if abs(price - lvl) <= threshold:
                    levels_near.append(name)
        if date_key in ind.get("fib_pivots", {}):
            for name, lvl in ind["fib_pivots"][date_key].items():
                if abs(price - lvl) <= threshold:
                    levels_near.append(name)
        if date_key in ind.get("daily_levels", {}):
            dl = ind["daily_levels"][date_key]
            for name in ["pd_high", "pd_low", "pd_close", "pd_open",
                         "wk_high", "wk_low", "mo_high", "mo_low"]:
                if name in dl and abs(price - dl[name]) <= threshold:
                    levels_near.append(name)
        if date_key in ind.get("vpoc_tpoc", {}):
            vt = ind["vpoc_tpoc"][date_key]
            for name in ["pd_vpoc", "pd_tpoc", "wk_vpoc", "wk_tpoc",
                         "mo_vpoc", "mo_tpoc",
                         "ny_vpoc", "ny_tpoc", "ldn_vpoc", "ldn_tpoc",
                         "asia_vpoc", "asia_tpoc"]:
                if name in vt and abs(price - vt[name]) <= threshold:
                    levels_near.append(name)
        # VWAP counts as a level
        if abs(price - vwap) <= threshold:
            levels_near.append("vwap")

        # Allow entries even without nearby levels (delta + VWAP is sufficient)
        # Original replay had entries at 0 levels

        signals.append({
            "idx": i, "direction": direction, "atr": atr,
            "price": price, "delta": delta, "levels": levels_near,
            "time_ist": ist_dt.strftime("%Y-%m-%d %H:%M"),
        })

    return signals


def generate_replay(scid_path: str) -> list[dict]:
    """Generate bar-by-bar replay data from all .scid files in data dir."""
    data_dir = os.path.join(PROJECT_ROOT, "data")

    # Load all 6E .scid files + live.scid for full history
    try:
        ticks = load_6e_combined(data_dir)
        # Also append live.scid if it exists (has latest data)
        live_path = os.path.join(data_dir, "live.scid")
        if Path(live_path).exists():
            live_ticks = read_scid(live_path)
            ticks = pd.concat([ticks, live_ticks]).sort_index()
            ticks = ticks[~ticks.index.duplicated(keep="last")]
        logger.info(f"Loaded combined data: {len(ticks):,} ticks")
    except Exception:
        if not Path(scid_path).exists():
            logger.warning(f"SCID file not found: {scid_path}")
            return []
        logger.info(f"Falling back to single file: {scid_path}")
        ticks = read_scid(scid_path)

    df5 = aggregate_to_bars(ticks, "5min")
    df_daily = aggregate_to_bars(ticks, "1D")

    if len(df5) < 50 or len(df_daily) < 2:
        logger.warning("Not enough data for replay")
        return []

    ind = precompute(df5, daily_df=df_daily)

    # Get DH signals (8-12 IST) — permissive for replay (1+ level)
    signals = get_replay_signals(df5, ind, window_start=8, window_end=12)
    logger.info(f"Found {len(signals)} DH signals")

    # Run backtest with best exit config
    trades = []
    last_exit_idx = -1
    trade_num = 0
    active_trade = None

    for sig in signals:
        i = sig["idx"]
        if i <= last_exit_idx:
            continue

        ec = dict(BEST_EXIT)
        xp, xi, pnl_pct, mfe, mae, bars, reason, partial = execute_exit(
            df5, i, sig["direction"], sig["atr"], ec, ind["vwap"]
        )
        last_exit_idx = xi
        trade_num += 1
        pip_pnl = pips(sig["price"], xp, sig["direction"])

        trades.append({
            "entry_idx": i,
            "exit_idx": xi,
            "direction": sig["direction"],
            "entry_price": sig["price"],
            "exit_price": xp,
            "pnl_pips": round(pip_pnl, 1),
            "reason": reason,
            "levels": sig["levels"],
            "num": trade_num,
            "sl": round(sig["price"] - sig["atr"] * BEST_EXIT["sl_mult"], 5) if sig["direction"] == "long"
                  else round(sig["price"] + sig["atr"] * BEST_EXIT["sl_mult"], 5),
        })

    # Build bar-by-bar replay (only bars in 8-12 IST window near levels or in trades)
    trade_map = {}  # idx -> trade info
    for t in trades:
        for idx in range(t["entry_idx"], t["exit_idx"] + 1):
            trade_map[idx] = t

    replay_rows = []
    cum_pnl = 0

    for i in range(30, len(df5)):
        ist_dt = df5.index[i] + IST
        ist_h = ist_dt.hour + ist_dt.minute / 60
        if not (8 <= ist_h < 12):
            continue

        row = df5.iloc[i]
        price = float(row["close"])
        date_str = ist_dt.strftime("%Y-%m-%d")

        # Level check
        atr = float(ind["atr"].iloc[i]) if not np.isnan(ind["atr"].iloc[i]) else 0.0005
        threshold = atr * 0.5
        levels_near = []

        if date_str in ind.get("std_pivots", {}):
            for name, lvl in ind["std_pivots"][date_str].items():
                if abs(price - lvl) <= threshold:
                    levels_near.append(name)
        if date_str in ind.get("fib_pivots", {}):
            for name, lvl in ind["fib_pivots"][date_str].items():
                if abs(price - lvl) <= threshold:
                    levels_near.append(name)
        if date_str in ind.get("daily_levels", {}):
            dl = ind["daily_levels"][date_str]
            for name in ["pd_high", "pd_low", "pd_close", "pd_open", "wk_high", "wk_low", "mo_high", "mo_low"]:
                if name in dl and abs(price - dl[name]) <= threshold:
                    levels_near.append(name)
        if date_str in ind.get("vpoc_tpoc", {}):
            vt = ind["vpoc_tpoc"][date_str]
            for name in ["pd_vpoc", "pd_tpoc", "wk_vpoc", "wk_tpoc",
                         "mo_vpoc", "mo_tpoc",
                         "ny_vpoc", "ny_tpoc", "ldn_vpoc", "ldn_tpoc",
                         "asia_vpoc", "asia_tpoc"]:
                if name in vt and abs(price - vt[name]) <= threshold:
                    levels_near.append(name)
        # VWAP
        vwap_val = float(ind["vwap"].iloc[i]) if not np.isnan(ind["vwap"].iloc[i]) else 0
        if vwap_val and abs(price - vwap_val) <= threshold:
            levels_near.append("vwap")

        # Trade status
        in_trade = i in trade_map
        trade = trade_map.get(i)
        status = ""
        is_entry = False
        is_exit = False
        is_hold = False
        row_class = ""

        if trade:
            if i == trade["entry_idx"]:
                is_entry = True
                status = f"ENTER {trade['direction'].upper()} #{trade['num']} | SL:{trade['sl']:.5f}"
                row_class = "entry"
            elif i == trade["exit_idx"]:
                is_exit = True
                cum_pnl += trade["pnl_pips"] * 12.50  # $12.50/pip (1 lot 6E)
                win = trade["pnl_pips"] > 0
                status = f"EXIT {trade['reason']} | {'+' if win else ''}{trade['pnl_pips']}p (${trade['pnl_pips'] * 12.50:+.1f}) | Cum:${cum_pnl:.0f}"
                row_class = "exit-win" if win else "exit-loss"
            else:
                is_hold = True
                hold_pnl = pips(trade["entry_price"], price, trade["direction"])
                status = f"HOLD {trade['direction'].upper()} | P&L:{hold_pnl:+.1f}p"
                row_class = "hold"

        # Skip bars with no levels and no trade (unless they're near a trade)
        if not levels_near and not in_trade:
            continue

        delta_val = float(ind["delta"].iloc[i]) if not np.isnan(ind["delta"].iloc[i]) else 0
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        color = "GRN" if c > o else "RED" if c < o else "DOJ"
        vwap_side = "above" if price > vwap_val else "below" if price < vwap_val else "at"

        replay_rows.append({
            "date": date_str,
            "time": ist_dt.strftime("%H:%M"),
            "o": f"{o:.5f}", "h": f"{h:.5f}", "l": f"{l:.5f}", "c": f"{c:.5f}",
            "vol": int(row["volume"]),
            "delta": int(delta_val),
            "color": color[0],
            "vwap": f"{vwap_val:.5f}",
            "vwap_side": vwap_side,
            "levels": ",".join(levels_near) if levels_near else "",
            "n_levels": len(levels_near),
            "status": status,
            "is_entry": is_entry,
            "is_exit": is_exit,
            "is_hold": is_hold,
            "row_class": row_class,
        })

    logger.info(f"Replay: {len(replay_rows)} bars, {len(trades)} trades")
    return replay_rows, trades


def build_html(replay_rows: list[dict], trades: list[dict]) -> str:
    """Build the replay HTML from data."""
    # Stats
    wins = [t for t in trades if t["pnl_pips"] > 0]
    losses = [t for t in trades if t["pnl_pips"] <= 0]
    total_pnl = sum(t["pnl_pips"] * 12.50 for t in trades)
    wr = len(wins) / len(trades) * 100 if trades else 0
    avg_win = np.mean([t["pnl_pips"] * 12.50 for t in wins]) if wins else 0
    avg_loss = np.mean([t["pnl_pips"] * 12.50 for t in losses]) if losses else 0
    gross_win = sum(t["pnl_pips"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl_pips"] for t in losses)) if losses else 0.001
    pf = gross_win / gross_loss

    # Group by date for day headers
    html_rows = []
    current_date = None
    for r in replay_rows:
        if r["date"] != current_date:
            current_date = r["date"]
            html_rows.append(f'<tr class="day-header" data-d="{r["date"]}"><td colspan="14">{r["date"]}</td></tr>')

        cls = f' class="{r["row_class"]}"' if r["row_class"] else ""
        d_span = f'<span class="g">+{r["delta"]}</span>' if r["delta"] > 0 else f'<span class="r">{r["delta"]}</span>' if r["delta"] < 0 else f'<span class="d">+0</span>'
        c_span = f'<span class="g">GRN</span>' if r["color"] == "G" else f'<span class="r">RED</span>' if r["color"] == "R" else f'<span class="d">DOJ</span>'
        v_span = f'<span class="g">blw</span>' if r["vwap_side"] == "below" else f'<span class="r">abv</span>'
        lvl_span = f'<span class="lvl">{r["levels"]}</span>' if r["levels"] else ""
        n_span = f'<span class="g">{r["n_levels"]}</span>' if r["n_levels"] >= 2 else f'<span class="d">{r["n_levels"]}</span>' if r["n_levels"] > 0 else ""

        status_html = ""
        if r["is_entry"]:
            status_html = f'<span class="status-entry">{r["status"]}</span>'
        elif r["is_exit"]:
            gcls = "g" if "+" in r["status"].split("|")[1] else "r"
            status_html = f'<span class="status-exit {gcls}">{r["status"]}</span>'
        elif r["is_hold"]:
            status_html = f'<span class="status-hold">{r["status"]}</span>'

        dl = f'{r["date"]}'
        data_attrs = f'data-d="{r["date"]}" data-l="{r["n_levels"]}" data-t="{1 if r["row_class"] else 0}" data-e="{1 if r["is_entry"] else 0}"'
        html_rows.append(
            f'<tr{cls} {data_attrs}>'
            f'<td>{dl}</td><td>{r["time"]}</td>'
            f'<td>{r["o"]}</td><td>{r["h"]}</td><td>{r["l"]}</td><td>{r["c"]}</td>'
            f'<td>{r["vol"]}</td><td>{d_span}</td><td>{c_span}</td>'
            f'<td>{r["vwap"]}</td><td>{v_span}</td>'
            f'<td>{lvl_span}</td><td>{n_span}</td><td>{status_html}</td></tr>'
        )

    tbody = "\n".join(html_rows)

    return f'''<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DH|S2 Full Replay</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',sans-serif;background:#0a0a1a;color:#e8eaf6;padding:20px;height:100vh;overflow:hidden;display:flex;flex-direction:column}}
h1{{font-size:1.5rem;margin-bottom:8px;background:linear-gradient(135deg,#00e676,#448aff);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.stats{{display:flex;gap:12px;margin:12px 0 20px;font-family:'JetBrains Mono',monospace;font-size:0.75rem;color:#9fa8da;flex-wrap:wrap}}
.stats span{{background:#141428;padding:4px 10px;border-radius:6px;border:1px solid #2a2a4a}}
.filters{{margin:12px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.filters button{{padding:6px 14px;border:1px solid #2a2a4a;background:#141428;color:#9fa8da;border-radius:6px;cursor:pointer;font-size:0.72rem}}
.filters button.active{{border-color:#00e676;color:#00e676;background:rgba(0,230,118,0.1)}}
.filters button:hover{{border-color:#448aff}}
input[type=text]{{padding:6px 12px;border:1px solid #2a2a4a;background:#141428;color:#e8eaf6;border-radius:6px;font-size:0.8rem;width:120px}}
table{{width:100%;border-collapse:collapse;font-family:'JetBrains Mono',monospace;font-size:0.68rem}}
th{{text-align:left;padding:6px 6px;background:#141428;color:#5c6bc0;border-bottom:2px solid #2a2a4a;position:sticky;top:0;z-index:2;font-size:0.62rem;text-transform:uppercase;letter-spacing:0.05em}}
td{{padding:3px 6px;border-bottom:1px solid #1a1a3a;white-space:nowrap}}
tr:hover{{background:rgba(255,255,255,0.03)}}
tr.entry{{background:rgba(0,230,118,0.1) !important}}
tr.exit-win{{background:rgba(0,230,118,0.06) !important}}
tr.exit-loss{{background:rgba(255,82,82,0.06) !important}}
tr.hold{{background:rgba(68,138,255,0.04) !important}}
.g{{color:#00e676}}.r{{color:#ff5252}}.d{{color:#5c6bc0}}
.status-entry{{color:#00e676;font-weight:600}}
.status-exit{{font-weight:600}}
.status-hold{{color:#448aff}}
.lvl{{color:#b388ff;font-size:0.62rem}}
.day-header{{background:#1c1c3a !important}}
.day-header td{{font-weight:700;color:#e8eaf6;padding:8px 6px;font-size:0.78rem;font-family:'Inter',sans-serif}}
</style>
</head><body>
<h1>DH|S2 Full Replay — Morning (8-12 IST)</h1>
<p style="color:#9fa8da;font-size:0.82rem">Every 5-min candle with levels, delta, VWAP, and trade status. Most recent first.</p>
<div class="stats"><span>{len(replay_rows):,} bars</span><span>{len(trades)} trades</span><span>WR: {wr:.0f}%</span><span>PF: {pf:.2f}</span><span>Avg Win: ${avg_win:+.0f}</span><span>Avg Loss: ${avg_loss:+.0f}</span><span>Total: ${total_pnl:+,.0f}</span></div>
<div class="filters">
<span style="color:#5c6bc0;font-size:0.72rem;padding:6px 0">Show:</span>
<button class="active" onclick="f('all')">All Bars</button>
<button onclick="f('trades')">Trades Only</button>
<button onclick="f('entries')">Entries Only</button>
<button onclick="f('levels')">At 2+ Levels</button>
<span style="color:#5c6bc0;font-size:0.72rem;padding:6px 0;margin-left:12px">Jump:</span>
<input type="text" id="dj" placeholder="YYYY-MM-DD" onchange="j(this.value)">
</div>
<div style="flex:1;overflow-y:auto;border:1px solid #2a2a4a;border-radius:8px;min-height:0">
<table id="t"><thead><tr><th>Date</th><th>Time</th><th>O</th><th>H</th><th>L</th><th>C</th><th>Vol</th><th>Delta</th><th>Clr</th><th>VWAP</th><th>Side</th><th>Levels Near</th><th>#</th><th>Trade Status</th></tr></thead><tbody>
{tbody}
</tbody></table></div>
<script>
// Reverse day groups — most recent first
(function(){{
  const tbody = document.querySelector('#t tbody');
  const rows = Array.from(tbody.children);
  const groups = [];
  let cur = [];
  for (const r of rows) {{
    if (r.classList.contains('day-header')) {{
      if (cur.length) groups.push(cur);
      cur = [r];
    }} else {{
      cur.push(r);
    }}
  }}
  if (cur.length) groups.push(cur);
  groups.reverse();
  tbody.innerHTML = '';
  for (const g of groups) for (const r of g) tbody.appendChild(r);
}})();

function f(m){{document.querySelectorAll('.filters button').forEach(b=>b.classList.remove('active'));event.target.classList.add('active');document.querySelectorAll('#t tbody tr').forEach(tr=>{{if(tr.classList.contains('day-header')){{tr.style.display='';return}}if(m==='all')tr.style.display='';else if(m==='trades')tr.style.display=tr.dataset.t==='1'?'':'none';else if(m==='entries')tr.style.display=tr.dataset.e==='1'?'':'none';else if(m==='levels')tr.style.display=parseInt(tr.dataset.l||'0')>=2?'':'none'}})}}
function j(d){{const r=document.querySelector('tr[data-d="'+d+'"]');if(r)r.scrollIntoView({{behavior:'smooth',block:'start'}})}}
</script></body></html>'''


def regenerate(scid_path: str = None):
    """Full regenerate replay HTML from .scid data using build_html."""
    if not scid_path:
        data_dir = os.path.join(PROJECT_ROOT, "data")
        candidates = sorted(Path(data_dir).glob("*.scid"), key=lambda p: p.stat().st_size, reverse=True)
        if not candidates:
            logger.warning("No .scid files found")
            return False
        scid_path = str(candidates[0])

    try:
        result = generate_replay(scid_path)
        if not result:
            return False
        replay_rows, trades = result
        if not replay_rows:
            return False

        html = build_html(replay_rows, trades)
        out_path = os.path.join(PROJECT_ROOT, "web", "static", "replay.html")
        with open(out_path, "w") as f:
            f.write(html)
        logger.info(f"Replay HTML written: {out_path} ({len(replay_rows)} bars, {len(trades)} trades)")
        return True
    except Exception as e:
        logger.error(f"Replay generation failed: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    regenerate()
