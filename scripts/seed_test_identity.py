#!/usr/bin/env python3
"""
scripts/seed_test_identity.py

One-shot script: insert a single approved identity into the consent DB
so the hardware smoke test has a valid identity to pass to the gate.

This is NOT for production use.  In production, identities are inserted
and approved through the admin workflow (03-CONSENT-DATA-POLICY.md).

Usage:
    python3 scripts/seed_test_identity.py \
        --db-path    ./data/consent.db \
        --image-path /path/to/real_face_photo.jpg \
        [--identity-id <uuid>]     # generated if omitted
        [--name "Test Person"]

The script prints the identity_id that was created so you can pass it
directly to preflight_check.py and smoke_test_hardware.sh.

Exit code 0 = success.  Non-zero = error with reason.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid

# ── Resolve SPEED_X_TECHNOLOGY project root ──────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEED_X_TECHNOLOGY_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _SPEED_X_TECHNOLOGY_ROOT not in sys.path:
    sys.path.insert(0, _SPEED_X_TECHNOLOGY_ROOT)

from speed_x_technology.consent_gate.db import (
    approve_identity,
    init_db,
    insert_identity,
    insert_sample_path,
    get_identity_record,
    get_approved_sample_paths,
)

# ── Parse args ──────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(
    description="Seed one approved test identity into the SPEED_X_TECHNOLOGY consent DB"
)
parser.add_argument(
    "--db-path",
    required=True,
    help="Path to the SQLite consent DB (will be created if it does not exist)",
)
parser.add_argument(
    "--image-path",
    required=True,
    help=(
        "Absolute path to a face image on THIS machine that the consent gate "
        "will verify exists and is a valid image.  Must be a real .jpg/.png."
    ),
)
parser.add_argument(
    "--identity-id",
    default=None,
    help="UUID for the identity (auto-generated if omitted)",
)
parser.add_argument(
    "--name",
    default="Smoke Test Identity",
    help="Legal name stored in the consent record (default: 'Smoke Test Identity')",
)
args = parser.parse_args()

# ── Validate inputs ────────────────────────────────────────────────────────────
image_path = os.path.abspath(args.image_path)
if not os.path.isfile(image_path):
    print(f"ERROR: image file not found: {image_path}", file=sys.stderr)
    print("       Provide a real face image that exists on this machine.", file=sys.stderr)
    sys.exit(1)

# Quick sanity check: is it an image file?
ext = os.path.splitext(image_path)[1].lower()
if ext not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
    print(f"ERROR: {image_path!r} does not look like an image file (got {ext!r})", file=sys.stderr)
    print("       Supported: .jpg .jpeg .png .bmp .webp", file=sys.stderr)
    sys.exit(1)

identity_id = args.identity_id or str(uuid.uuid4())
db_path = os.path.abspath(args.db_path)

# ── Set env so db.py reads the right file ─────────────────────────────────────
os.environ["SPEED_X_TECHNOLOGY_CONSENT_DB_PATH"] = db_path

# ── Initialise schema ─────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(db_path), exist_ok=True)
init_db()

# ── Check for duplicate ───────────────────────────────────────────────────────
existing = get_identity_record(identity_id)
if existing:
    print(f"Identity {identity_id!r} already exists in DB (status={existing['status']!r}).")
    existing_paths = get_approved_sample_paths(identity_id)
    print(f"Approved sample paths: {existing_paths}")
    print(f"\nidentity_id: {identity_id}")
    sys.exit(0)

# ── Insert and approve ────────────────────────────────────────────────────────
insert_identity(
    identity_id=identity_id,
    owner_legal_name=args.name,
    authorized_use="smoke_test — hardware validation only",
    status="pending_verification",
)
insert_sample_path(
    identity_id=identity_id,
    file_path=image_path,
    sample_type="face",
)
approve_identity(identity_id)

# ── Verify ────────────────────────────────────────────────────────────────────
record = get_identity_record(identity_id)
paths  = get_approved_sample_paths(identity_id)

if record["status"] != "approved" or not paths:
    print("ERROR: insert succeeded but verify read-back failed", file=sys.stderr)
    sys.exit(1)

print("✓ Approved identity seeded successfully")
print(f"  DB:           {db_path}")
print(f"  identity_id:  {identity_id}")
print(f"  name:         {args.name}")
print(f"  status:       {record['status']}")
print(f"  sample_paths: {paths}")
print()
print("Pass this identity_id to the smoke test:")
print(f"  --identity-id {identity_id}")
