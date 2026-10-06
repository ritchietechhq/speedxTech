"""
speed_x_technology/orchestrator/session_store.py

In-memory session registry for Phase 1.

Each facefusion streaming session maps to one SessionRecord.  The record
is created when a session is started and updated as the subprocess
transitions through states.

Thread safety: all mutations go through _LOCK so the FastAPI background
thread (process monitor) and the request-handler threads never race on
the same record.

Phase 1 scope: single-node, in-process dict.  No persistence across
orchestrator restarts — sessions are ephemeral.  The schema is designed
to be easily migrated to Redis or Postgres in a future phase.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional


class SessionStatus(str, Enum):
    """
    Lifecycle states of a streaming session.

    starting      Process launched; consent check in progress.
    running       Process alive; frames are being produced.
    stopped       User-requested stop; process exited 0 cleanly.
    failed_consent  Process exited 2 — identity not approved or revoked.
    failed_missing  Process exited 3 — identity_id was never supplied
                    (orchestrator configuration error, not a user error).
    failed_startup  Process exited 1 or other positive non-zero code —
                    camera missing, model load error, CUDA OOM, etc.
    killed          Process was SIGTERM'd by the orchestrator (stop
                    request issued after process started) or by the OS.
    """
    starting         = "starting"
    running          = "running"
    stopped          = "stopped"
    failed_consent   = "failed_consent"
    failed_missing   = "failed_missing"
    failed_startup   = "failed_startup"
    killed           = "killed"


# Human-readable failure detail templates — used by the status endpoint.
_DETAIL: Dict[SessionStatus, str] = {
    SessionStatus.stopped:          "Session ended by user request.",
    SessionStatus.failed_consent:   (
        "Identity '{identity_id}' is not approved for use as a swap target. "
        "Check the consent record status."
    ),
    SessionStatus.failed_missing:   (
        "Session could not start: no identity_id was provided to the pipeline. "
        "This is an orchestrator configuration error — report to the operator."
    ),
    SessionStatus.failed_startup:   (
        "Facefusion process exited with an error before producing frames "
        "(exit code {exit_code}). Possible causes: camera not connected, "
        "model weights not downloaded, CUDA out-of-memory, or invalid "
        "argument. Check the session logs for detail."
    ),
    SessionStatus.killed:           "Session was terminated by the orchestrator.",
}


@dataclass
class SessionRecord:
    session_id:   str
    identity_id:  str
    stream_mode:  str
    pid:          Optional[int]       = None
    status:       SessionStatus       = SessionStatus.starting
    started_at:   datetime            = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    ended_at:     Optional[datetime]  = None
    exit_code:    Optional[int]       = None
    fps:          Optional[float]     = None
    frames_total: int                 = 0
    # Detailed message surfaced to the API caller.
    failure_detail: Optional[str]    = None

    def resolve_failure_detail(self) -> Optional[str]:
        """
        Return a caller-friendly failure message, substituting in any
        dynamic values.  Returns None for non-failure statuses.
        """
        template = _DETAIL.get(self.status)
        if template is None:
            return None
        return template.format(
            identity_id=self.identity_id,
            exit_code=self.exit_code,
        )

    def to_api_dict(self) -> dict:
        """Serialise to the shape returned by GET /v1/sessions/{id}/status."""
        detail = self.failure_detail or self.resolve_failure_detail()
        return {
            "session_id":     self.session_id,
            "status":         self.status.value,
            "identity_id":    self.identity_id,
            "stream_mode":    self.stream_mode,
            "pid":            self.pid,
            "started_at":     self.started_at.isoformat(),
            "ended_at":       self.ended_at.isoformat() if self.ended_at else None,
            "exit_code":      self.exit_code,
            "fps":            self.fps,
            "frames_total":   self.frames_total,
            "failure_detail": detail,
        }


# ── Global registry ───────────────────────────────────────────────────────────

_LOCK: threading.Lock = threading.Lock()
_SESSIONS: Dict[str, SessionRecord] = {}


def create(record: SessionRecord) -> None:
    with _LOCK:
        _SESSIONS[record.session_id] = record


def get(session_id: str) -> Optional[SessionRecord]:
    with _LOCK:
        return _SESSIONS.get(session_id)


def update(session_id: str, **kwargs) -> Optional[SessionRecord]:
    """Atomically update fields on an existing record. Returns the record."""
    with _LOCK:
        rec = _SESSIONS.get(session_id)
        if rec is None:
            return None
        for k, v in kwargs.items():
            setattr(rec, k, v)
        return rec


def list_all() -> list[SessionRecord]:
    with _LOCK:
        return list(_SESSIONS.values())
