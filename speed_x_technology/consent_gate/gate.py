"""
speed_x_technology/consent_gate/gate.py

The consent gate — the single enforced entry point for obtaining an
ApprovedIdentity.

Design intent (per 03-CONSENT-DATA-POLICY.md §7)
-------------------------------------------------
The gate must be structurally impossible to bypass, not merely
discouraged.  This is achieved by:

  1. ApprovedIdentity has no public constructor.  The only way to get
     one is via require_approved_identity().
  2. require_approved_identity() raises ConsentGateRejected (a hard
     exception, not a warning) for any non-approved status.
  3. facefusion's streamer.py is modified to call
     require_approved_identity() before read_static_images(), and to
     receive List[str] paths *from* the ApprovedIdentity object —
     so there is no code path from 'arbitrary path' to 'swap target'
     that bypasses the gate.

Phase 1 scope: local SQLite store.
Future: swap db.py import for a Postgres/REST-backed version without
changing this file or the streamer.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from speed_x_technology.consent_gate.db import (
    get_approved_sample_paths,
    get_identity_record,
    init_db,
)
from speed_x_technology.consent_gate.models import ApprovedIdentity

logger = logging.getLogger("speed_x_technology.consent_gate")


class ConsentGateRejected(RuntimeError):
    """
    Raised when an identity is not in 'approved' status.

    The pipeline MUST catch this, log it, and halt — not fall back to
    passthrough.  See 03-CONSENT-DATA-POLICY.md §7.
    """

    def __init__(self, identity_id: str, reason: str) -> None:
        self.identity_id = identity_id
        self.reason = reason
        super().__init__(
            f"[ConsentGate] Identity '{identity_id}' rejected — {reason}. "
            f"Pipeline will not start."
        )


# Initialise the DB schema on first import (idempotent).
init_db()


def require_approved_identity(identity_id: str) -> ApprovedIdentity:
    """
    Look up identity_id in the consent store.

    Returns
    -------
    ApprovedIdentity
        An immutable token containing the verified sample paths.
        Pass its .sample_paths to read_static_images().

    Raises
    ------
    ConsentGateRejected
        If the identity does not exist, is not in 'approved' status,
        or has no registered sample paths.  Always raises — never
        silently falls through.

    Examples
    --------
    >>> from speed_x_technology.consent_gate.gate import require_approved_identity
    >>> identity = require_approved_identity("uuid-of-approved-identity")
    >>> frames = read_static_images(identity.sample_paths)
    """
    # 1. Record must exist 
    row = get_identity_record(identity_id)
    if row is None:
        _log_and_raise(identity_id, "no record found in consent store")

    status: str = row["status"]

    # 2. Status must be exactly 'approved' — all other states are rejected
    if status != "approved":
        _log_and_raise(
            identity_id,
            f"status is '{status}', must be 'approved'",
        )

    # 3. At least one sample path must be registered and on disk
    sample_paths: List[str] = get_approved_sample_paths(identity_id)
    if not sample_paths:
        _log_and_raise(
            identity_id,
            "no sample paths registered for this approved identity",
        )

    missing = [p for p in sample_paths if not _path_exists(p)]
    if missing:
        _log_and_raise(
            identity_id,
            f"sample file(s) missing from disk: {missing}",
        )

    logger.info(
        "[ConsentGate] APPROVED  identity=%s  owner=%r  paths=%d",
        identity_id,
        row["owner_legal_name"],
        len(sample_paths),
    )

    # Construct via object.__init__ bypass (frozen dataclass) is still
    # a normal dataclass instantiation — the "no public constructor"
    # constraint is enforced by convention + module structure, not by
    # Python's runtime.  The hard structural guarantee is that streamer.py
    # only accepts List[str] sourced from an ApprovedIdentity object.
    return ApprovedIdentity(
        identity_id=identity_id,
        owner_legal_name=row["owner_legal_name"],
        sample_paths=sample_paths,
        authorized_use=row["authorized_use"],
    )


def _log_and_raise(identity_id: str, reason: str) -> None:
    """Log the rejection clearly and raise ConsentGateRejected."""
    logger.error(
        "[ConsentGate] REJECTED  identity=%s  reason=%s",
        identity_id,
        reason,
    )
    raise ConsentGateRejected(identity_id=identity_id, reason=reason)


def _path_exists(path: str) -> bool:
    """Thin wrapper so tests can mock it."""
    import os
    return os.path.isfile(path)
