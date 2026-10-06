"""
speed_x_technology/consent_gate/db.py

SQLite consent record store — Phase 1 (local, single-node).

Schema is intentionally minimal for this phase:
  - identities table maps identity_id → status + metadata
  - sample_paths table maps identity_id → one or more file paths

This schema is designed to be forward-compatible with the full
PostgreSQL schema specified in 04-API-CONTRACT.md §4.  Column names
match the API contract so a migration script can map them 1:1.
Status values mirror the state machine in 03-CONSENT-DATA-POLICY.md §3.

NO data is ever written by the consent gate itself — only read.
Records are inserted by a separate admin/onboarding tool (not built yet).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Generator, List, Optional

# Default DB path — override via SPEED_X_TECHNOLOGY_CONSENT_DB_PATH env var.
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "consent.db"
)
_DB_PATH: str = os.environ.get("SPEED_X_TECHNOLOGY_CONSENT_DB_PATH", _DEFAULT_DB_PATH)

# ── Schema DDL ────────────────────────────────────────────────────────────────

_DDL = """
-- Identity consent records.
-- Status values per 03-CONSENT-DATA-POLICY.md §3 state machine:
--   pending_verification | pending_admin_review | approved | rejected | revoked
CREATE TABLE IF NOT EXISTS identities (
    identity_id       TEXT PRIMARY KEY,
    owner_legal_name  TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending_verification',
    authorized_use    TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One-to-many: each identity may have multiple sample image/audio paths.
-- Only paths for identities in 'approved' status should ever be used.
CREATE TABLE IF NOT EXISTS sample_paths (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id  TEXT NOT NULL REFERENCES identities(identity_id) ON DELETE CASCADE,
    file_path    TEXT NOT NULL,
    sample_type  TEXT NOT NULL DEFAULT 'face'  -- 'face' | 'voice' | 'both'
);

CREATE INDEX IF NOT EXISTS idx_identities_status ON identities(status);
CREATE INDEX IF NOT EXISTS idx_sample_paths_identity ON sample_paths(identity_id);
"""


# ── Connection management ─────────────────────────────────────────────────────

def _db_path() -> str:
    """Return DB path, allowing runtime override via env var."""
    return os.environ.get("SPEED_X_TECHNOLOGY_CONSENT_DB_PATH", _DEFAULT_DB_PATH)


@contextmanager
def _get_conn() -> Generator[sqlite3.Connection, None, None]:
    path = _db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist.  Safe to call multiple times."""
    with _get_conn() as conn:
        conn.executescript(_DDL)
        conn.commit()


# ── Read-only query functions ─────────────────────────────────────────────────

def get_identity_status(identity_id: str) -> Optional[str]:
    """
    Return the status string for the given identity_id, or None if
    the record does not exist.
    """
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM identities WHERE identity_id = ?",
            (identity_id,),
        ).fetchone()
    return row["status"] if row else None


def get_identity_record(identity_id: str) -> Optional[sqlite3.Row]:
    """
    Return the full identity row, or None if not found.
    Columns: identity_id, owner_legal_name, status, authorized_use, ...
    """
    with _get_conn() as conn:
        return conn.execute(
            "SELECT * FROM identities WHERE identity_id = ?",
            (identity_id,),
        ).fetchone()


def get_approved_sample_paths(identity_id: str) -> List[str]:
    """
    Return the list of sample file paths for an approved identity.
    Returns an empty list if the identity is not in 'approved' status
    or has no sample paths registered.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT sp.file_path
            FROM sample_paths sp
            JOIN identities i ON i.identity_id = sp.identity_id
            WHERE sp.identity_id = ?
              AND i.status = 'approved'
            ORDER BY sp.id
            """,
            (identity_id,),
        ).fetchall()
    return [row["file_path"] for row in rows]


# ── Admin-only write functions (used by onboarding tool, not by gate) ─────────

def insert_identity(
    identity_id: str,
    owner_legal_name: str,
    authorized_use: str,
    status: str = "pending_verification",
) -> None:
    """Insert a new identity record (admin/onboarding use only)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO identities
              (identity_id, owner_legal_name, status, authorized_use)
            VALUES (?, ?, ?, ?)
            """,
            (identity_id, owner_legal_name, status, authorized_use),
        )
        conn.commit()


def insert_sample_path(
    identity_id: str,
    file_path: str,
    sample_type: str = "face",
) -> None:
    """Register a sample file path for an identity (admin use only)."""
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO sample_paths (identity_id, file_path, sample_type) VALUES (?, ?, ?)",
            (identity_id, file_path, sample_type),
        )
        conn.commit()


def approve_identity(identity_id: str) -> None:
    """Set an identity to 'approved' status (admin use only)."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE identities SET status='approved', updated_at=datetime('now') WHERE identity_id=?",
            (identity_id,),
        )
        conn.commit()


def revoke_identity(identity_id: str) -> None:
    """Revoke an identity immediately (admin use only)."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE identities SET status='revoked', updated_at=datetime('now') WHERE identity_id=?",
            (identity_id,),
        )
        conn.commit()
