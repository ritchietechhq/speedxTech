"""
speed_x_technology.consent_gate

Public API for the consent gate.

Usage
-----
from speed_x_technology.consent_gate import require_approved_identity, ConsentGateRejected

identity = require_approved_identity(identity_id)  # raises if not approved
frames   = read_static_images(identity.sample_paths)
"""
from speed_x_technology.consent_gate.gate import ConsentGateRejected, require_approved_identity
from speed_x_technology.consent_gate.models import ApprovedIdentity

__all__ = [
    "require_approved_identity",
    "ConsentGateRejected",
    "ApprovedIdentity",
]
