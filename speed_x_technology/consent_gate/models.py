"""
speed_x_technology/consent_gate/models.py

Typed data models for the consent gate.

ApprovedIdentity is the *only* object that may be passed as a source
identity to the face-swap pipeline.  It can only be obtained by calling
require_approved_identity(), which validates against the SQLite consent
store.  There is no public constructor — use the factory in gate.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class ApprovedIdentity:
    """
    Immutable token representing a single verified, approved identity.

    Fields
    ------
    identity_id : str
        UUID that matches the `identity_id` column in the consent DB.
        Corresponds to the `identity_id` in 04-API-CONTRACT.md §4.
    owner_legal_name : str
        Full legal name from the consent record (not exposed to the
        inference pipeline — present for audit logging only).
    sample_paths : List[str]
        Absolute paths to the pre-verified image/video sample(s) that
        have been cleared for use.  These are what get passed to
        read_static_images().
    authorized_use : str
        The use-case string from the consent document.  Stored but not
        currently machine-validated; present for auditor inspection.

    Construction
    ------------
    
    Do NOT instantiate directly.  Use:
        from speed_x_technology.consent_gate.gate import require_approved_identity
        identity = require_approved_identity(identity_id)
    """

    identity_id: str
    owner_legal_name: str
    sample_paths: List[str]
    authorized_use: str

    # Frozen dataclass: __hash__ and __eq__ are auto-generated.
    # This means an ApprovedIdentity can safely be used as a dict key
    # or in a set if needed by the session orchestrator.

    def __str__(self) -> str:
        return (
            f"ApprovedIdentity("
            f"id={self.identity_id!r}, "
            f"owner={self.owner_legal_name!r})"
        )
