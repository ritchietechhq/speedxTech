"""
tests/test_consent_gate.py

Verifies that the consent gate correctly approves and rejects identities.

Run from the SPEED_X_TECHNOLOGY project root:
    SPEED_X_TECHNOLOGY_CONSENT_DB_PATH=/tmp/test_consent.db python3 -m pytest tests/test_consent_gate.py -v

Or without pytest:
    SPEED_X_TECHNOLOGY_CONSENT_DB_PATH=/tmp/test_consent.db python3 tests/test_consent_gate.py
"""
import os
import sys
import tempfile
import uuid

# ── Test DB isolation ─────────────────────────────────────────────────────────
# Use a fresh temp file so tests never touch the real consent.db
_TEST_DB = tempfile.mktemp(suffix=".db", prefix="speed_x_technology_test_")
os.environ["SPEED_X_TECHNOLOGY_CONSENT_DB_PATH"] = _TEST_DB

# Add project root to path so `speed_x_technology` package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from speed_x_technology.consent_gate import ConsentGateRejected, require_approved_identity, ApprovedIdentity
from speed_x_technology.consent_gate.db import (
    init_db,
    insert_identity,
    insert_sample_path,
    approve_identity,
    revoke_identity,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def fresh_id() -> str:
    return str(uuid.uuid4())


def _setup_identity(status: str, with_sample: bool = True) -> tuple[str, str]:
    """Insert an identity and optional sample; return (identity_id, tmp_path)."""
    init_db()
    iid = fresh_id()
    insert_identity(iid, "Test Person", "real-time video/voice transformation", status=status)
    tmp_path = ""
    if with_sample:
        # Create a real temp file so the on-disk check passes
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.close()
        tmp_path = tmp.name
        insert_sample_path(iid, tmp_path, "face")
    return iid, tmp_path


# ── Test cases ────────────────────────────────────────────────────────────────

def test_approved_identity_returns_approved_identity_object():
    """An identity in 'approved' status with a real sample path must succeed."""
    iid, tmp_path = _setup_identity("approved")
    result = require_approved_identity(iid)
    assert isinstance(result, ApprovedIdentity)
    assert result.identity_id == iid
    assert tmp_path in result.sample_paths
    os.unlink(tmp_path)
    print("PASS  test_approved_identity_returns_approved_identity_object")


def test_pending_identity_raises_consent_gate_rejected():
    """An identity in 'pending_verification' must be rejected with ConsentGateRejected."""
    iid, tmp_path = _setup_identity("pending_verification")
    try:
        require_approved_identity(iid)
        raise AssertionError("Expected ConsentGateRejected was NOT raised")
    except ConsentGateRejected as exc:
        assert exc.identity_id == iid
        assert "pending_verification" in exc.reason
    finally:
        if tmp_path:
            os.unlink(tmp_path)
    print("PASS  test_pending_identity_raises_consent_gate_rejected")


def test_revoked_identity_raises_consent_gate_rejected():
    """A revoked identity must be rejected even if it previously had 'approved' status."""
    iid, tmp_path = _setup_identity("approved")
    revoke_identity(iid)
    try:
        require_approved_identity(iid)
        raise AssertionError("Expected ConsentGateRejected was NOT raised for revoked identity")
    except ConsentGateRejected as exc:
        assert exc.identity_id == iid
        assert "revoked" in exc.reason
    finally:
        if tmp_path:
            os.unlink(tmp_path)
    print("PASS  test_revoked_identity_raises_consent_gate_rejected")


def test_nonexistent_identity_raises_consent_gate_rejected():
    """An identity_id that doesn't exist in the DB must be rejected."""
    iid = fresh_id()  # never inserted
    try:
        require_approved_identity(iid)
        raise AssertionError("Expected ConsentGateRejected was NOT raised for nonexistent identity")
    except ConsentGateRejected as exc:
        assert exc.identity_id == iid
        assert "no record" in exc.reason
    print("PASS  test_nonexistent_identity_raises_consent_gate_rejected")


def test_approved_identity_without_sample_raises():
    """An approved identity with no sample paths registered must be rejected."""
    iid, _ = _setup_identity("approved", with_sample=False)
    try:
        require_approved_identity(iid)
        raise AssertionError("Expected ConsentGateRejected was NOT raised for identity with no paths")
    except ConsentGateRejected as exc:
        assert "no sample paths" in exc.reason
    print("PASS  test_approved_identity_without_sample_raises")


def test_approved_identity_with_missing_file_raises():
    """An approved identity whose sample file has been deleted must be rejected."""
    iid, tmp_path = _setup_identity("approved")
    os.unlink(tmp_path)  # delete the file so on-disk check fails
    try:
        require_approved_identity(iid)
        raise AssertionError("Expected ConsentGateRejected for missing file was NOT raised")
    except ConsentGateRejected as exc:
        assert "missing from disk" in exc.reason
    print("PASS  test_approved_identity_with_missing_file_raises")


def test_approved_identity_sample_paths_are_gate_verified():
    """
    sample_paths on the returned ApprovedIdentity must exactly match the
    DB-registered paths — not the raw state_manager source_paths.
    This confirms the structural bypass prevention: you cannot inject an
    arbitrary path through the gate.
    """
    iid, tmp_path = _setup_identity("approved")
    result = require_approved_identity(iid)
    assert result.sample_paths == [tmp_path], (
        "sample_paths must come exclusively from the consent DB, "
        "not from any caller-supplied path"
    )
    os.unlink(tmp_path)
    print("PASS  test_approved_identity_sample_paths_are_gate_verified")


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    tests = [
        test_approved_identity_returns_approved_identity_object,
        test_pending_identity_raises_consent_gate_rejected,
        test_revoked_identity_raises_consent_gate_rejected,
        test_nonexistent_identity_raises_consent_gate_rejected,
        test_approved_identity_without_sample_raises,
        test_approved_identity_with_missing_file_raises,
        test_approved_identity_sample_paths_are_gate_verified,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"FAIL  {test.__name__}: {e}")
            failed += 1
    if os.path.exists(_TEST_DB):
        os.unlink(_TEST_DB)
    if failed:
        print(f"\n{failed}/{len(tests)} tests FAILED")
        sys.exit(1)
    else:
        print(f"\n{len(tests)}/{len(tests)} tests passed")
