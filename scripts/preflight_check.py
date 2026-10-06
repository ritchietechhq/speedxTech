#!/usr/bin/env python3
"""
scripts/preflight_check.py

Run this on the cloud GPU BEFORE the hardware smoke test.
Each check prints PASS or FAIL with an actionable reason.
Exit code 0 = all clear.  Non-zero = fix what failed, then re-run.

Usage (from SPEED_X_TECHNOLOGY project root):
    python3 scripts/preflight_check.py \
        --ff-root  ./Modules/facefusion \
        --db-path  ./data/consent.db \
        --identity-id <uuid-of-approved-identity>
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
WARN = "\033[33mWARN\033[0m"

failures = 0


def ok(label: str, detail: str = "") -> None:
    msg = f"  {PASS}  {label}"
    if detail:
        msg += f"  ({detail})"
    print(msg)


def fail(label: str, reason: str, fix: str = "") -> None:
    global failures
    failures += 1
    print(f"  {FAIL}  {label}: {reason}")
    if fix:
        print(f"         fix: {fix}")


def warn(label: str, detail: str) -> None:
    print(f"  {WARN}  {label}: {detail}")


# ── Parse args ─────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument("--ff-root",      required=True)
parser.add_argument("--db-path",      required=True)
parser.add_argument("--identity-id",  required=True)
parser.add_argument("--model-dir",    default=None,
                    help="Override model directory (default: <ff-root>/.assets/models)")
args = parser.parse_args()

FF_ROOT   = os.path.abspath(args.ff_root)
DB_PATH   = os.path.abspath(args.db_path)
MODEL_DIR = args.model_dir or os.path.join(FF_ROOT, ".assets", "models")

print("\n-- SPEED_X_TECHNOLOGY Hardware Smoke-Test Pre-Flight ----------------------------------\n")

# ── 1. Python version ──────────────────────────────────────────────────────────
print("[ 1 ] Python")
v = sys.version_info
if v >= (3, 10):
    ok("Python version", f"{v.major}.{v.minor}.{v.micro}")
else:
    fail("Python version",
         f"{v.major}.{v.minor}.{v.micro} -- facefusion requires >=3.10",
         "Use pyenv or the correct conda env")

# ── 2. nvidia-smi ─────────────────────────────────────────────────────────────
print("\n[ 2 ] NVIDIA GPU")
if shutil.which("nvidia-smi"):
    r = subprocess.run(
        ["nvidia-smi",
         "--query-gpu=name,memory.total,driver_version,compute_cap",
         "--format=csv,noheader"],
        capture_output=True, text=True)
    if r.returncode == 0:
        for line in r.stdout.strip().splitlines():
            ok("nvidia-smi", line.strip())
    else:
        fail("nvidia-smi", "command returned non-zero", "Check GPU driver installation")
else:
    fail("nvidia-smi", "not found in PATH", "Install NVIDIA drivers and CUDA toolkit")

# ── 3. CUDA in onnxruntime ────────────────────────────────────────────────────
print("\n[ 3 ] onnxruntime-gpu / CUDAExecutionProvider")
try:
    import onnxruntime as ort
    available = ort.get_available_providers()
    if "CUDAExecutionProvider" in available:
        ok("onnxruntime CUDAExecutionProvider",
           f"ort {ort.__version__}, providers={available}")
    else:
        fail("onnxruntime CUDAExecutionProvider",
             f"NOT in available providers: {available}",
             "pip install onnxruntime-gpu  (not onnxruntime-cpu)")
except ImportError:
    fail("onnxruntime import", "not installed", "pip install onnxruntime-gpu")

# ── 4. Required model weights ─────────────────────────────────────────────────
print("\n[ 4 ] Model weights (face_swapper: hyperswap_1a_256)")
REQUIRED_MODELS = [
    "hyperswap_1a_256.onnx",  # face_swapper
    "yoloface_8n.onnx",       # face detector (yolo_face)
    "2dfan4.onnx",            # face landmarker
    "nsfw_1.onnx",            # content analyser
    "nsfw_2.onnx",
    "nsfw_3.onnx",
]
for model in REQUIRED_MODELS:
    path = os.path.join(MODEL_DIR, model)
    if os.path.isfile(path):
        size_mb = os.path.getsize(path) / (1024 * 1024)
        ok(model, f"{size_mb:.1f} MB")
    else:
        fail(model, f"not found at {path}",
             f"Run from {FF_ROOT}: python3 facefusion.py force-download")

# ── 5. facefusion imports ─────────────────────────────────────────────────────
print("\n[ 5 ] facefusion package import")
if FF_ROOT not in sys.path:
    sys.path.insert(0, FF_ROOT)
try:
    import facefusion.state_manager  # noqa: F401
    ok("facefusion.state_manager")
    from facefusion.execution import get_available_execution_providers
    eps = get_available_execution_providers()
    if "cuda" in eps:
        ok("facefusion detects CUDA EP", f"available={eps}")
    else:
        fail("facefusion CUDA EP detection",
             f"'cuda' not in facefusion available EPs: {eps}",
             "Check onnxruntime-gpu is installed in the ACTIVE Python env, not a venv")
except Exception as exc:
    fail("facefusion import", str(exc),
         f"Check {FF_ROOT} contains a valid facefusion checkout")

# ── 6. SPEED_X_TECHNOLOGY package imports ────────────────────────────────────────────────────
print("\n[ 6 ] speed_x_technology package")
SPEED_X_TECHNOLOGY_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SPEED_X_TECHNOLOGY_ROOT not in sys.path:
    sys.path.insert(0, SPEED_X_TECHNOLOGY_ROOT)
try:
    from speed_x_technology.consent_gate import require_approved_identity  # noqa: F401
    ok("speed_x_technology.consent_gate import")
except Exception as exc:
    fail("speed_x_technology.consent_gate import", str(exc))

try:
    from speed_x_technology.orchestrator.exit_codes import EXIT_CONSENT_REJECTION  # noqa: F401
    ok("speed_x_technology.orchestrator.exit_codes import")
except Exception as exc:
    fail("speed_x_technology.orchestrator.exit_codes import", str(exc))

# ── 7. Consent DB -- approved identity ────────────────────────────────────────
print("\n[ 7 ] Consent DB")
if not os.path.isfile(DB_PATH):
    fail("Consent DB file", f"not found at {DB_PATH}",
         "Run consent DB migrations or copy from dev machine")
else:
    ok("Consent DB file exists", DB_PATH)
    try:
        os.environ["SPEED_X_TECHNOLOGY_CONSENT_DB_PATH"] = DB_PATH
        from speed_x_technology.consent_gate import require_approved_identity
        approved = require_approved_identity(args.identity_id)
        ok("Identity approved",
           f"id={args.identity_id}, {len(approved.sample_paths)} sample path(s)")
        missing = [p for p in approved.sample_paths if not os.path.isfile(p)]
        if missing:
            fail("Sample files on disk", f"missing: {missing}",
                 "Copy verified sample images to the GPU machine")
        else:
            ok("Sample files present", f"{approved.sample_paths}")
    except Exception as exc:
        fail("Consent gate check", str(exc),
             f"Ensure identity {args.identity_id!r} has status='approved' in {DB_PATH}")

# ── 8. System dependencies ─────────────────────────────────────────────────────
print("\n[ 8 ] System dependencies (ffmpeg, ffprobe, curl)")
for dep in ["ffmpeg", "ffprobe", "curl"]:
    path = shutil.which(dep)
    if path:
        ok(dep, path)
    else:
        fail(dep, "not found in PATH", f"apt install {dep}")

# ── 9. Camera / video input ───────────────────────────────────────────────────
print("\n[ 9 ] Camera or video input (index 0)")
try:
    import cv2
    cap = cv2.VideoCapture(0)
    if cap.isOpened():
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        ok("Camera device 0", f"{w}x{h}")
        cap.release()
    else:
        warn("Camera device 0",
             "not opened -- if using a video file as target input, set --camera-index to "
             "the file path or use a virtual camera (v4l2loopback)")
except ImportError:
    fail("cv2 import", "opencv-python not installed", "pip install opencv-python-headless")

# ── Summary ────────────────────────────────────────────────────────────────────
print("\n-------------------------------------------------------------------------")
if failures == 0:
    print(f"\n  {PASS}  All pre-flight checks passed -- safe to run smoke test.\n")
    sys.exit(0)
else:
    print(f"\n  {FAIL}  {failures} check(s) failed -- fix the above before running smoke test.\n")
    sys.exit(failures)
