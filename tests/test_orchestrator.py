"""
tests/test_orchestrator.py

Unit tests for the Step 3 Session Orchestrator (speed_x_technology/orchestrator/app.py).

Uses FastAPI TestClient with subprocess.Popen mocked.  No real facefusion
process or camera is spawned.

Covers:
  1. Start session  -> 202, session_id returned
  2. Status running -> fps populated from sidecar file
  3. Status after consent rejection (exit 2)   -> failed_consent + distinct msg
  4. Status after missing identity  (exit 3)   -> failed_missing + distinct msg
  5. Status after startup failure   (exit 1)   -> failed_startup + distinct msg
  6. Status after SIGTERM kill      (rc < 0)   -> killed
  7. Clean stop                     (exit 0)   -> stopped
  8. Stop running session           -> 204; SIGTERM sent to process
  9. Stop non-existent session      -> 404
  10. Consent vs startup messages are different

Run from SPEED_X_TECHNOLOGY project root:
    python3 tests/test_orchestrator.py
"""
import json
import os
import signal as _signal
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi.testclient import TestClient

import speed_x_technology.orchestrator.app as app_mod
import speed_x_technology.orchestrator.session_store as store_mod
from speed_x_technology.orchestrator.exit_codes import (
    EXIT_CONSENT_REJECTION,
    EXIT_MISSING_IDENTITY,
    EXIT_OK,
    EXIT_STARTUP_FAILURE,
)
from speed_x_technology.orchestrator.session_store import SessionStatus

# ---------------------------------------------------------------------------
# Shared temp session dir
# ---------------------------------------------------------------------------
_SESSION_DIR = tempfile.mkdtemp(prefix="speed_x_technology_orch_test_")
app_mod._SESSION_DIR = _SESSION_DIR
app_mod._RUNNER_SCRIPT = "/dev/null"


def _write_sidecar(session_id: str, payload: dict) -> None:
    path = os.path.join(_SESSION_DIR, f"{session_id}.json")
    with open(path, "w") as f:
        json.dump(payload, f)


def _reset_state():
    """Clear in-memory stores between tests."""
    with store_mod._LOCK:
        store_mod._SESSIONS.clear()
    with app_mod._PROCS_LOCK:
        app_mod._PROCS.clear()


def _make_proc(returncode: int, delay: float = 0.0) -> MagicMock:
    """Mock Popen; .wait() sleeps `delay` then returns `returncode`."""
    proc = MagicMock()
    proc.pid = 42000
    def _wait():
        if delay:
            time.sleep(delay)
        return returncode
    proc.wait.side_effect = _wait
    proc.returncode = returncode
    proc.poll.return_value = None
    proc.send_signal = MagicMock()
    proc.stdin = MagicMock()
    proc.stderr = MagicMock()
    proc.stdout = MagicMock()
    return proc


def _poll_status(client, sid, terminal_statuses, timeout=4.0) -> dict:
    """Poll GET until status is in terminal_statuses or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/v1/sessions/{sid}")
        body = resp.json()
        if body["status"] in terminal_statuses:
            return body
        time.sleep(0.05)
    return body


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_start_returns_202():
    _reset_state()
    proc = _make_proc(EXIT_OK, delay=5.0)
    with patch.object(app_mod, "subprocess") as mock_sp:
        mock_sp.Popen.return_value = proc
        client = TestClient(app_mod.app)
        resp = client.post("/v1/sessions", json={"identity_id": str(uuid.uuid4())})
    assert resp.status_code == 202
    body = resp.json()
    assert "session_id" in body
    assert body["status"] == "starting"
    assert body["pid"] is not None
    print("PASS  test_start_returns_202")


def test_list_sessions():
    _reset_state()
    proc = _make_proc(EXIT_OK, delay=5.0)
    with patch.object(app_mod, "subprocess") as mock_sp:
        mock_sp.Popen.return_value = proc
        client = TestClient(app_mod.app)
        iid = str(uuid.uuid4())
        resp = client.post("/v1/sessions", json={"identity_id": iid})
        sid = resp.json()["session_id"]
        resp2 = client.get("/v1/sessions")
    assert resp2.status_code == 200
    ids = [s["session_id"] for s in resp2.json()]
    assert sid in ids
    print("PASS  test_list_sessions")


def test_unknown_session_404():
    _reset_state()
    proc = _make_proc(EXIT_OK, delay=5.0)
    with patch.object(app_mod, "subprocess") as mock_sp:
        mock_sp.Popen.return_value = proc
        client = TestClient(app_mod.app)
        resp = client.get(f"/v1/sessions/{uuid.uuid4()}")
    assert resp.status_code == 404
    print("PASS  test_unknown_session_404")


def test_status_running_from_sidecar():
    _reset_state()
    proc = _make_proc(EXIT_OK, delay=5.0)
    with patch.object(app_mod, "subprocess") as mock_sp:
        mock_sp.Popen.return_value = proc
        client = TestClient(app_mod.app)
        resp = client.post("/v1/sessions", json={"identity_id": str(uuid.uuid4())})
        sid = resp.json()["session_id"]
        _write_sidecar(sid, {"session_id": sid, "status": "running",
                              "fps": 24.5, "frames_total": 100,
                              "failure_detail": None})
        resp2 = client.get(f"/v1/sessions/{sid}")
    body = resp2.json()
    assert body["status"] == "running"
    assert body["fps"] == 24.5
    print("PASS  test_status_running_from_sidecar")


def _run_session_and_get_status(returncode: int,
                                sidecar_detail: str | None = None) -> dict:
    """
    Helper: start a session whose mock process exits immediately with
    `returncode`. Writes sidecar if sidecar_detail provided.
    Polls until a terminal status is reached and returns the status body.
    """
    _reset_state()
    proc = _make_proc(returncode, delay=0.02)
    with patch.object(app_mod, "subprocess") as mock_sp:
        mock_sp.Popen.return_value = proc
        client = TestClient(app_mod.app)
        iid = str(uuid.uuid4())
        resp = client.post("/v1/sessions", json={"identity_id": iid})
        assert resp.status_code == 202
        sid = resp.json()["session_id"]

        if sidecar_detail is not None:
            _write_sidecar(sid, {
                "session_id":     sid,
                "status":         "failed",
                "fps":            None,
                "frames_total":   0,
                "failure_detail": sidecar_detail,
            })

        terminal = {"stopped", "failed_consent", "failed_missing",
                    "failed_startup", "killed"}
        body = _poll_status(client, sid, terminal, timeout=5.0)
    return body


def test_consent_rejection_status():
    body = _run_session_and_get_status(EXIT_CONSENT_REJECTION)
    assert body["status"] == "failed_consent", \
        f"Expected failed_consent, got {body['status']!r}"
    assert body["failure_detail"] is not None
    msg = body["failure_detail"].lower()
    assert "approved" in msg or "identity" in msg, \
        f"Message doesn't mention identity/approval: {body['failure_detail']!r}"
    print("PASS  test_consent_rejection_status")


def test_missing_identity_status():
    body = _run_session_and_get_status(EXIT_MISSING_IDENTITY)
    assert body["status"] == "failed_missing", \
        f"Expected failed_missing, got {body['status']!r}"
    assert body["failure_detail"] is not None
    msg = body["failure_detail"].lower()
    assert "identity" in msg or "config" in msg, \
        f"Message doesn't mention identity/config: {body['failure_detail']!r}"
    print("PASS  test_missing_identity_status")


def test_startup_failure_status():
    camera_msg = "Camera at index 0 could not be opened. Check device is connected."
    body = _run_session_and_get_status(EXIT_STARTUP_FAILURE,
                                       sidecar_detail=camera_msg)
    assert body["status"] == "failed_startup", \
        f"Expected failed_startup, got {body['status']!r}"
    assert body["failure_detail"] is not None
    msg = body["failure_detail"].lower()
    assert "camera" in msg or "exit code" in msg, \
        f"Message doesn't mention camera: {body['failure_detail']!r}"
    print("PASS  test_startup_failure_status")


def test_killed_status():
    body = _run_session_and_get_status(returncode=-15)
    assert body["status"] == "killed", \
        f"Expected killed, got {body['status']!r}"
    print("PASS  test_killed_status")


def test_clean_stop_status():
    body = _run_session_and_get_status(EXIT_OK)
    assert body["status"] == "stopped", \
        f"Expected stopped, got {body['status']!r}"
    print("PASS  test_clean_stop_status")


def test_consent_and_startup_messages_are_different():
    consent_body  = _run_session_and_get_status(EXIT_CONSENT_REJECTION)
    startup_body  = _run_session_and_get_status(EXIT_STARTUP_FAILURE)
    assert consent_body["status"] != startup_body["status"], \
        "Consent rejection and startup failure must have different status values"
    assert consent_body["failure_detail"] != startup_body["failure_detail"], \
        "Consent rejection and startup failure must have different failure_detail messages"
    print("PASS  test_consent_and_startup_messages_are_different")


def test_stop_sends_sigterm():
    _reset_state()
    proc = _make_proc(EXIT_OK, delay=10.0)   # stays alive long enough
    with patch.object(app_mod, "subprocess") as mock_sp:
        mock_sp.Popen.return_value = proc
        client = TestClient(app_mod.app)
        resp = client.post("/v1/sessions", json={"identity_id": str(uuid.uuid4())})
        sid = resp.json()["session_id"]

        # Wait until _PROCS is populated
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            with app_mod._PROCS_LOCK:
                if sid in app_mod._PROCS:
                    break
            time.sleep(0.01)

        resp2 = client.delete(f"/v1/sessions/{sid}")
        assert resp2.status_code == 204
        proc.send_signal.assert_called_once_with(_signal.SIGTERM)
    print("PASS  test_stop_sends_sigterm")


def test_stop_nonexistent_404():
    _reset_state()
    proc = _make_proc(EXIT_OK, delay=5.0)
    with patch.object(app_mod, "subprocess") as mock_sp:
        mock_sp.Popen.return_value = proc
        client = TestClient(app_mod.app)
        resp = client.delete(f"/v1/sessions/{uuid.uuid4()}")
    assert resp.status_code == 404
    print("PASS  test_stop_nonexistent_404")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [
        test_start_returns_202,
        test_list_sessions,
        test_unknown_session_404,
        test_status_running_from_sidecar,
        test_consent_rejection_status,
        test_missing_identity_status,
        test_startup_failure_status,
        test_killed_status,
        test_clean_stop_status,
        test_consent_and_startup_messages_are_different,
        test_stop_sends_sigterm,
        test_stop_nonexistent_404,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            import traceback
            print(f"FAIL  {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    if failed:
        print(f"\n{failed}/{len(tests)} orchestrator tests FAILED")
        sys.exit(1)
    else:
        print(f"\n{len(tests)}/{len(tests)} orchestrator tests passed")
