"""
speed_x_technology/orchestrator/app.py

FastAPI Session Orchestrator — Phase 1 scope.

Endpoints (all under /v1/sessions, matching 04-API-CONTRACT.md §3):
  POST   /v1/sessions                  Start a new streaming session
  GET    /v1/sessions/{session_id}     Get session status
  DELETE /v1/sessions/{session_id}     Stop a running session
  GET    /v1/sessions                  List all sessions (diagnostic)

Deliberately OUT OF SCOPE for Phase 1 (per user instruction):
  - Billing / usage metering
  - Multi-tenant admin endpoints
  - WebSocket media channel
  - quality_tier / vram_allocated_gb negotiation

Failure classification (per Step 3 requirements):
  The status endpoint distinguishes between:
    failed_consent   exit 2 — identity not approved / revoked
    failed_missing   exit 3 — orchestrator forgot to set identity_id
    failed_startup   exit 1 or other positive — camera/model/CUDA error
    killed           negative returncode — SIGTERM from orchestrator

Run with:
    uvicorn speed_x_technology.orchestrator.app:app --host 0.0.0.0 --port 8080

Environment variables:
  SPEED_X_TECHNOLOGY_FF_ROOT          Path to facefusion repo root (default: ./Modules/facefusion)
  SPEED_X_TECHNOLOGY_CONSENT_DB_PATH  Path to consent SQLite DB
  SPEED_X_TECHNOLOGY_SESSION_DIR      Directory for session sidecar JSON files (default: /tmp/speed_x_technology_sessions)
  SPEED_X_TECHNOLOGY_CAMERA_INDEX     Default camera device index (default: 0)
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from speed_x_technology.orchestrator.exit_codes import (
    EXIT_CONSENT_REJECTION,
    EXIT_MISSING_IDENTITY,
    EXIT_OK,
    EXIT_STARTUP_FAILURE,
)
from speed_x_technology.orchestrator.session_store import (
    SessionRecord,
    SessionStatus,
    create as store_create,
    get as store_get,
    list_all as store_list,
    update as store_update,
)

# ── Configuration ─────────────────────────────────────────────────────────────

_ROOT_DIR      = Path(__file__).resolve().parent.parent.parent
_FF_ROOT       = os.environ.get("SPEED_X_TECHNOLOGY_FF_ROOT",
                    str(_ROOT_DIR / "Modules" / "facefusion"))
_SESSION_DIR   = os.environ.get("SPEED_X_TECHNOLOGY_SESSION_DIR", "/tmp/speed_x_technology_sessions")
_DB_PATH       = os.environ.get("SPEED_X_TECHNOLOGY_CONSENT_DB_PATH", "")
_CAMERA_INDEX_RAW = os.environ.get("SPEED_X_TECHNOLOGY_CAMERA_INDEX", "0")
# Accept either an integer device index ("0") or a file path ("/path/to/video.mp4")
_CAMERA_INDEX: Union[int, str] = (
    int(_CAMERA_INDEX_RAW) if _CAMERA_INDEX_RAW.lstrip("-").isdigit() else _CAMERA_INDEX_RAW
)
_RUNNER_SCRIPT = str(Path(__file__).resolve().parent / "session_runner.py")

os.makedirs(_SESSION_DIR, exist_ok=True)

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="SPEED_X_TECHNOLOGY Session Orchestrator",
    description=(
        "Starts, stops, and reports on facefusion streaming sessions. "
        "Phase 1: single-node, no billing, no multi-tenancy."
    ),
    version="0.1.0",
)


# ── Request / Response models ─────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    identity_id: str = Field(
        description="UUID of the identity to use as swap target. "
                    "Must be in 'approved' status in the consent store."
    )
    stream_mode: str = Field(
        default="udp",
        description="Output stream mode: 'udp' (default) or 'v4l2'."
    )
    stream_resolution: str = Field(
        default="1280x720",
        description="Output resolution, e.g. '1280x720' or '1920x1080'."
    )
    stream_fps: float = Field(
        default=30.0,
        description="Target output frame rate."
    )
    camera_index: Optional[int] = Field(
        default=None,
        description="Camera device index. Defaults to SPEED_X_TECHNOLOGY_CAMERA_INDEX env var (0)."
    )


class StartSessionResponse(BaseModel):
    session_id: str
    status:     str
    pid:        Optional[int]


class SessionStatusResponse(BaseModel):
    session_id:     str
    status:         str
    identity_id:    str
    stream_mode:    str
    pid:            Optional[int]
    started_at:     str
    ended_at:       Optional[str]
    exit_code:      Optional[int]
    fps:            Optional[float]
    frames_total:   int
    failure_detail: Optional[str]


# ── Active process registry ───────────────────────────────────────────────────
# Maps session_id → subprocess.Popen object.  The store has the SessionRecord;
# this dict has the live handle for sending signals.

_PROCS: Dict[str, subprocess.Popen] = {}
_PROCS_LOCK = threading.Lock()


# ── Subprocess monitor ────────────────────────────────────────────────────────

def _status_file(session_id: str) -> str:
    return os.path.join(_SESSION_DIR, f"{session_id}.json")


def _read_sidecar(session_id: str) -> Optional[dict]:
    path = _status_file(session_id)
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _exit_code_to_status(returncode: int) -> SessionStatus:
    """Map a subprocess return code to a SessionStatus."""
    if returncode == EXIT_OK:
        return SessionStatus.stopped
    if returncode == EXIT_CONSENT_REJECTION:
        return SessionStatus.failed_consent
    if returncode == EXIT_MISSING_IDENTITY:
        return SessionStatus.failed_missing
    if returncode > 0:
        return SessionStatus.failed_startup
    # returncode < 0 → killed by signal
    return SessionStatus.killed


def _monitor_process(session_id: str, proc: subprocess.Popen) -> None:
    """
    Background thread: waits for the subprocess to exit, then updates the
    session record with the final status.  Reads the sidecar file for any
    failure_detail written by the runner before it exited.
    """
    returncode = proc.wait()
    now = datetime.now(tz=timezone.utc)

    final_status = _exit_code_to_status(returncode)

    # Try to read failure_detail from sidecar
    sidecar = _read_sidecar(session_id)
    sidecar_detail = sidecar.get("failure_detail") if sidecar else None

    # Build the record to update
    rec = store_get(session_id)
    fps = (sidecar.get("fps") if sidecar else None)
    frames = (sidecar.get("frames_total", 0) if sidecar else 0)

    store_update(
        session_id,
        status=final_status,
        exit_code=returncode,
        ended_at=now,
        fps=fps,
        frames_total=frames,
        # Prefer the runner's detailed message; fall back to template.
        failure_detail=sidecar_detail,
    )

    with _PROCS_LOCK:
        _PROCS.pop(session_id, None)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post(
    "/v1/sessions",
    response_model=StartSessionResponse,
    status_code=202,
    summary="Start a new streaming session",
    description=(
        "Launches a facefusion subprocess for the specified identity. "
        "Returns 202 Accepted immediately; poll GET /v1/sessions/{session_id} "
        "to check when status transitions from 'starting' to 'running' or a "
        "failure state."
    ),
)
async def start_session(body: StartSessionRequest) -> StartSessionResponse:
    session_id   = str(uuid.uuid4())
    camera_index = body.camera_index if body.camera_index is not None else _CAMERA_INDEX

    # Create record immediately so GET works even before process starts
    record = SessionRecord(
        session_id=session_id,
        identity_id=body.identity_id,
        stream_mode=body.stream_mode,
    )
    store_create(record)

    # Build subprocess command
    cmd = [
        sys.executable,
        _RUNNER_SCRIPT,
        "--session-id",        session_id,
        "--identity-id",       body.identity_id,
        "--stream-mode",       body.stream_mode,
        "--stream-resolution", body.stream_resolution,
        "--stream-fps",        str(body.stream_fps),
        "--status-file",       _status_file(session_id),
        "--ff-root",           _FF_ROOT,
        "--camera-index",      str(camera_index),
    ]
    if _DB_PATH:
        cmd += ["--db-path", _DB_PATH]

    env = os.environ.copy()
    env["SPEED_X_TECHNOLOGY_IDENTITY_ID"] = body.identity_id
    if _DB_PATH:
        env["SPEED_X_TECHNOLOGY_CONSENT_DB_PATH"] = _DB_PATH

    try:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception as exc:
        store_update(
            session_id,
            status=SessionStatus.failed_startup,
            ended_at=datetime.now(tz=timezone.utc),
            failure_detail=f"Failed to launch session subprocess: {exc}",
        )
        raise HTTPException(status_code=500, detail=str(exc))

    store_update(session_id, pid=proc.pid, status=SessionStatus.starting)

    with _PROCS_LOCK:
        _PROCS[session_id] = proc

    # Start background monitor thread
    monitor = threading.Thread(
        target=_monitor_process,
        args=(session_id, proc),
        daemon=True,
        name=f"monitor-{session_id[:8]}",
    )
    monitor.start()

    return StartSessionResponse(
        session_id=session_id,
        status=SessionStatus.starting.value,
        pid=proc.pid,
    )


@app.get(
    "/v1/sessions/{session_id}",
    response_model=SessionStatusResponse,
    summary="Get session status",
    description=(
        "Returns the current status of a session. "
        "The 'failure_detail' field distinguishes:\n"
        "  - failed_consent: identity not approved or revoked\n"
        "  - failed_missing: identity_id was not set (config error)\n"
        "  - failed_startup: camera/model/CUDA error\n"
        "  - killed: stopped by orchestrator\n"
        "  - stopped: clean user-requested stop"
    ),
)
async def get_session(session_id: str) -> SessionStatusResponse:
    rec = store_get(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    # If still running, pull live FPS from sidecar
    if rec.status in (SessionStatus.starting, SessionStatus.running):
        sidecar = _read_sidecar(session_id)
        if sidecar:
            store_update(
                session_id,
                fps=sidecar.get("fps"),
                frames_total=sidecar.get("frames_total", 0),
                # Promote starting→running once sidecar shows "running"
                status=(
                    SessionStatus.running
                    if sidecar.get("status") == "running"
                    else rec.status
                ),
            )
        rec = store_get(session_id)

    return SessionStatusResponse(**rec.to_api_dict())


@app.delete(
    "/v1/sessions/{session_id}",
    summary="Stop a running session",
    description=(
        "Sends SIGTERM to the session subprocess. "
        "The session status transitions to 'killed'. "
        "Returns 204 No Content on success."
    ),
    status_code=204,
)
async def stop_session(session_id: str) -> None:
    rec = store_get(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    with _PROCS_LOCK:
        proc = _PROCS.get(session_id)

    if proc is None:
        # Already finished
        if rec.status in (
            SessionStatus.stopped,
            SessionStatus.failed_consent,
            SessionStatus.failed_missing,
            SessionStatus.failed_startup,
            SessionStatus.killed,
        ):
            return  # idempotent — already done
        raise HTTPException(
            status_code=409,
            detail=f"Session '{session_id}' has no live process to stop.",
        )

    try:
        proc.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        pass  # already exited between the lock release and send_signal


@app.get(
    "/v1/sessions",
    summary="List all sessions",
    description="Returns all sessions known to this orchestrator instance.",
)
async def list_sessions() -> list[dict]:
    return [rec.to_api_dict() for rec in store_list()]


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"status": "ok", "active_sessions": len(_PROCS)}
