"""Trading Console — FastAPI server with WebSocket + background scanner."""

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Add project root
PROJECT_ROOT = str(Path(__file__).parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from . import config
from . import fetcher
from . import analysis
from . import sentiment
from .lessons import reset_session
from . import replay_gen
from .journal_models import get_db as journal_db, init_db as journal_init_db, dict_from_row, dicts_from_rows
from .journal_analysis import (
    get_overview, get_correlations, get_insights,
    compute_session_health_summary, compute_readiness,
)
from .journal_health_import import import_health_export

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("trading-console")

app = FastAPI(title="6E Trading Console")

# State
state = {
    "running": False,
    "auto_refresh": True,
    "contract": None,
    "last_scan": None,
    "last_result": None,
    "last_offset": 0,
    "scid_path": "",
    "connected": False,
    "scan_count": 0,
    "sentiment": None,
    "sentiment_fetching": False,
    "replay_generated": False,
}

scheduler = AsyncIOScheduler()
ws_clients: list[WebSocket] = []


# --- WebSocket ---

@app.websocket("/ws/scan")
async def ws_scan(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    logger.info(f"WebSocket client connected ({len(ws_clients)} total)")
    try:
        # Send current state immediately
        if state["last_result"]:
            await ws.send_json(state["last_result"])
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        ws_clients.remove(ws)
        logger.info(f"WebSocket client disconnected ({len(ws_clients)} total)")


async def broadcast(data: dict):
    dead = []
    for ws in ws_clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.remove(ws)


# --- Scanner ---

async def run_scan():
    """Pull data + run analysis + broadcast results."""
    logger.info("Running scan...")

    # Set up scid path
    if not state["scid_path"]:
        state["scid_path"] = os.path.join(PROJECT_ROOT, config.DATA_DIR, "live.scid")

    # Check connection to Windows PC
    state["connected"] = await fetcher.check_connection()

    if state["connected"]:
        state["last_error"] = None
        # Auto-detect contract on first run
        if not state["contract"]:
            state["contract"] = await fetcher.detect_contract()
            if not state["contract"]:
                state["contract"] = "6EM6.CME.scid"
                logger.warning(f"Using fallback contract: {state['contract']}")

        scid_path = state["scid_path"]

        # Fetch data
        if state["last_offset"] == 0 or not Path(scid_path).exists():
            file_size, ok = await fetcher.fetch_full(state["contract"], scid_path)
            if ok:
                state["last_offset"] = file_size
        else:
            n_records, new_offset, ok = await fetcher.fetch_incremental(
                state["contract"], scid_path, state["last_offset"]
            )
            if ok:
                state["last_offset"] = new_offset
    else:
        state["last_error"] = f"Windows PC not reachable at {config.WINDOWS_IP}:{config.WINDOWS_PORT}. Check: (1) Sierra Start running? (2) IP changed? Run ipconfig on Windows."
        logger.warning("Windows PC not reachable — trying cached data")

    # Find best available .scid file
    scid_path = state["scid_path"]
    if not Path(scid_path).exists():
        # Fallback: look for any existing .scid file in data/
        data_dir = os.path.join(PROJECT_ROOT, config.DATA_DIR)
        candidates = sorted(Path(data_dir).glob("6E*_live.scid"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            candidates = sorted(Path(data_dir).glob("6E*.scid"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            scid_path = str(candidates[0])
            logger.info(f"Using cached file: {scid_path}")
        else:
            result = {
                "error": "No SCID data available. Run Sierra Start on Windows and click Scan Now.",
                "connected": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            state["last_result"] = result
            await broadcast(result)
            return

    # Run analysis
    result = analysis.run_full_analysis(scid_path)
    if result:
        result["connected"] = state["connected"]
        result["contract"] = state["contract"]
        result["scan_count"] = state["scan_count"]
        result["auto_refresh"] = state["auto_refresh"]
        result["last_error"] = state.get("last_error")
        # Attach sentiment if available
        if state["sentiment"]:
            result["sentiment"] = state["sentiment"]
        state["last_result"] = result
        state["last_scan"] = datetime.now(timezone.utc).isoformat()
        state["scan_count"] += 1
        logger.info(f"Scan #{state['scan_count']}: {result['current_price']} | "
                     f"Setup: {'YES' if result['setup_triggered'] else 'NO'}")
    else:
        result = {
            "error": "Analysis failed. Check SCID file.",
            "connected": state["connected"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        state["last_result"] = result

    await broadcast(state["last_result"])

    # Fetch sentiment in background (every scan, cached internally)
    if not state["sentiment_fetching"]:
        asyncio.create_task(_fetch_sentiment())

    # Regenerate replay once per session (after first successful data pull)
    if not state["replay_generated"] and Path(scid_path).exists():
        asyncio.create_task(_regen_replay(scid_path))


async def _fetch_sentiment():
    """Fetch sentiment in background, update state."""
    # Only refetch every 15 min
    if state["sentiment"] and state["sentiment"].get("timestamp"):
        from datetime import datetime as dt
        try:
            last = dt.fromisoformat(state["sentiment"]["timestamp"])
            if (datetime.now(timezone.utc) - last).total_seconds() < config.SENTIMENT_INTERVAL:
                return
        except Exception:
            pass
    state["sentiment_fetching"] = True
    try:
        logger.info("Fetching market intel...")
        result = await sentiment.fetch_all_sentiment()
        state["sentiment"] = result
        logger.info(f"Market intel: {result.get('label', '?')} ({result.get('composite', 0):+.2f})")
        # Broadcast updated result with sentiment
        if state["last_result"] and "error" not in state["last_result"]:
            state["last_result"]["sentiment"] = result
            await broadcast(state["last_result"])
    except Exception as e:
        logger.error(f"Sentiment fetch failed: {e}")
    finally:
        state["sentiment_fetching"] = False


async def _regen_replay(scid_path: str):
    """Regenerate replay HTML in background."""
    try:
        logger.info("Regenerating replay HTML...")
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, replay_gen.regenerate, scid_path)
        if ok:
            state["replay_generated"] = True
            logger.info("Replay HTML regenerated successfully")
        else:
            logger.warning("Replay generation returned no data")
    except Exception as e:
        logger.error(f"Replay generation failed: {e}")


# --- API Routes ---

@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.post("/api/toggle")
async def toggle():
    state["running"] = not state["running"]
    if state["running"]:
        reset_session()
        state["scan_count"] = 0
        state["last_offset"] = 0  # Force full download on toggle on
        # Run first scan immediately
        asyncio.create_task(run_scan())
        # Start scheduler aligned to clock :00, :05, :10, :15...
        if not scheduler.running:
            scheduler.add_job(run_scan, "cron", minute="*/5", id="scanner", replace_existing=True)
            scheduler.start()
        else:
            try:
                scheduler.resume_job("scanner")
            except Exception:
                scheduler.add_job(run_scan, "cron", minute="*/5", id="scanner", replace_existing=True)
        logger.info("Scanner ON")
    else:
        # Pause scheduler
        if scheduler.running:
            try:
                scheduler.pause_job("scanner")
            except Exception:
                pass
        logger.info("Scanner OFF")

    return {"running": state["running"]}


@app.post("/api/scan-now")
async def scan_now():
    if not state["running"]:
        return JSONResponse({"error": "Scanner is off. Toggle ON first."}, status_code=400)
    asyncio.create_task(run_scan())
    return {"status": "scan_triggered"}


@app.post("/api/auto-refresh")
async def toggle_auto_refresh():
    state["auto_refresh"] = not state["auto_refresh"]
    if state["auto_refresh"]:
        try:
            scheduler.resume_job("scanner")
        except Exception:
            pass
        logger.info("Auto-refresh ON")
    else:
        try:
            scheduler.pause_job("scanner")
        except Exception:
            pass
        logger.info("Auto-refresh OFF")
    return {"auto_refresh": state["auto_refresh"]}


@app.get("/api/status")
async def status():
    return {
        "running": state["running"],
        "connected": state["connected"],
        "contract": state["contract"],
        "last_scan": state["last_scan"],
        "scan_count": state["scan_count"],
        "scan_interval": config.SCAN_INTERVAL,
        "windows_ip": config.WINDOWS_IP,
        "last_error": state.get("last_error"),
    }


@app.post("/api/set-ip/{ip}")
async def set_ip(ip: str):
    """Update Windows IP without restarting."""
    config.WINDOWS_IP = ip
    state["contract"] = None  # Force re-detect
    state["last_offset"] = 0  # Force full download
    state["last_error"] = None
    logger.info(f"Windows IP updated to {ip}")
    return {"windows_ip": ip, "status": "updated"}


@app.get("/api/scan")
async def get_scan():
    if state["last_result"]:
        return state["last_result"]
    return {"error": "No scan data yet. Toggle ON to start scanning."}


# ===== JOURNAL ROUTES =====

@contextmanager
def jdb():
    conn = journal_db()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

class JSessionStart(BaseModel):
    mood_before: Optional[str] = None
    sleep_hours: Optional[float] = None
    caffeine_cups: Optional[int] = 0
    exercise_today: Optional[int] = 0

class JSessionComplete(BaseModel):
    session_rating: Optional[int] = None
    lesson: Optional[str] = None
    notes: Optional[str] = None

class JTradeEntry(BaseModel):
    entry_time: Optional[str] = None
    exit_time: Optional[str] = None
    direction: Optional[str] = None
    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    pnl_pips: Optional[float] = None
    outcome: Optional[str] = None
    per_plan: Optional[int] = 1
    rules_broken: Optional[str] = None
    confidence_before: Optional[int] = None
    emotion_before: Optional[str] = None
    emotion_during: Optional[str] = None
    emotion_after: Optional[str] = None
    notes: Optional[str] = None

@app.post("/api/journal/session/start")
def j_start_session(data: JSessionStart):
    with jdb() as conn:
        active = conn.execute("SELECT id FROM sessions WHERE status='active'").fetchone()
        if active:
            raise HTTPException(400, "Session already active")
        now = datetime.now().isoformat()
        readiness = compute_readiness(data.sleep_hours, data.mood_before, data.caffeine_cups)
        cursor = conn.execute(
            "INSERT INTO sessions (start_time, mood_before, sleep_hours, caffeine_cups, exercise_today, readiness_score, status) VALUES (?, ?, ?, ?, ?, ?, 'active')",
            (now, data.mood_before, data.sleep_hours, data.caffeine_cups, data.exercise_today, readiness))
        return {"id": cursor.lastrowid, "start_time": now, "readiness_score": readiness, "status": "active"}

@app.post("/api/journal/session/stop")
def j_stop_session():
    with jdb() as conn:
        active = conn.execute("SELECT * FROM sessions WHERE status='active'").fetchone()
        if not active:
            raise HTTPException(400, "No active session")
        now = datetime.now().isoformat()
        conn.execute("UPDATE sessions SET end_time=?, status='stopped' WHERE id=?", (now, active["id"]))
        return {"id": active["id"], "end_time": now, "status": "stopped"}

@app.get("/api/journal/session/active")
def j_get_active():
    with jdb() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE status IN ('active','stopped') ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return {"active": False}
        session = dict_from_row(row)
        session["active"] = True
        session["health"] = compute_session_health_summary(conn, session["id"])
        session["trades"] = dicts_from_rows(conn.execute("SELECT * FROM trades WHERE session_id=?", (session["id"],)).fetchall())
        return session

@app.post("/api/journal/session/{session_id}/complete")
def j_complete_session(session_id: int, data: JSessionComplete):
    with jdb() as conn:
        conn.execute("UPDATE sessions SET status='completed', session_rating=?, lesson=?, notes=? WHERE id=?",
                     (data.session_rating, data.lesson, data.notes, session_id))
        return {"id": session_id, "status": "completed"}

@app.post("/api/journal/session/{session_id}/trade")
def j_add_trade(session_id: int, trade: JTradeEntry):
    with jdb() as conn:
        cursor = conn.execute(
            "INSERT INTO trades (session_id, entry_time, exit_time, direction, entry_price, exit_price, pnl_pips, outcome, per_plan, rules_broken, confidence_before, emotion_before, emotion_during, emotion_after, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (session_id, trade.entry_time, trade.exit_time, trade.direction, trade.entry_price, trade.exit_price, trade.pnl_pips, trade.outcome, trade.per_plan, trade.rules_broken, trade.confidence_before, trade.emotion_before, trade.emotion_during, trade.emotion_after, trade.notes))
        return {"id": cursor.lastrowid, "session_id": session_id}

@app.delete("/api/journal/trade/{trade_id}")
def j_delete_trade(trade_id: int):
    with jdb() as conn:
        conn.execute("DELETE FROM trades WHERE id=?", (trade_id,))
        return {"deleted": trade_id}

@app.get("/api/journal/sessions")
def j_list_sessions(limit: int = 50):
    with jdb() as conn:
        sessions = dicts_from_rows(conn.execute("SELECT * FROM sessions WHERE status='completed' ORDER BY start_time DESC LIMIT ?", (limit,)).fetchall())
        for s in sessions:
            s["trades"] = dicts_from_rows(conn.execute("SELECT * FROM trades WHERE session_id=?", (s["id"],)).fetchall())
            s["trade_count"] = len(s["trades"])
            s["health"] = compute_session_health_summary(conn, s["id"])
        return sessions

@app.get("/api/journal/analysis/overview")
def j_overview():
    with jdb() as conn:
        return get_overview(conn)

@app.get("/api/journal/analysis/correlations")
def j_correlations():
    with jdb() as conn:
        return get_correlations(conn)

@app.get("/api/journal/analysis/insights")
def j_insights():
    with jdb() as conn:
        return get_insights(conn)

@app.post("/api/journal/health-import")
async def j_health_import(file: UploadFile = File(...), days_back: int = 30):
    tmp_dir = tempfile.mkdtemp()
    try:
        tmp_file = os.path.join(tmp_dir, file.filename or "export")
        with open(tmp_file, "wb") as f:
            content = await file.read()
            f.write(content)
        if file.filename and file.filename.endswith(".zip"):
            with zipfile.ZipFile(tmp_file, "r") as z:
                z.extractall(tmp_dir)
            os.remove(tmp_file)
            xml_path = tmp_dir
        else:
            xml_path = tmp_file
        result = import_health_export(xml_path, days_back)
        return {"status": "ok", **result}
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Import failed: {str(e)}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

# Init journal DB on startup
journal_init_db()

# --- Static files ---
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


# --- Entry point ---

def main():
    import uvicorn
    logger.info(f"Starting Trading Console on http://localhost:{config.WEB_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=config.WEB_PORT, log_level="info")


if __name__ == "__main__":
    main()
