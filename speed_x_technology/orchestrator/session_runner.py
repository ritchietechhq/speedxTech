"""
speed_x_technology/orchestrator/session_runner.py

Subprocess entry point that runs a single facefusion streaming session.

This script is launched by the orchestrator (app.py) as an isolated
subprocess per session.  It:

  1. Sets SPEED_X_TECHNOLOGY_IDENTITY_ID in env (already done by the orchestrator
     before Popen, but re-asserted here for clarity).
  2. Configures facefusion state_manager items required for the stream.
  3. Opens the camera and the ffmpeg UDP output pipe.
  4. Drives multi_process_capture() in a generator loop, counting frames
     and writing live FPS + status to a JSON sidecar file every second.
  5. Exits with a named exit code (see exit_codes.py) so the orchestrator
     can classify the failure without parsing stderr.

Exit codes:
  0   Clean stop (camera exhausted or SIGTERM caught after first frame).
  1   Startup failure (camera not found, model load, CUDA error, etc.).
  2   Consent gate rejected identity (EXIT_CONSENT_REJECTION).
  3   Missing identity_id (EXIT_MISSING_IDENTITY).

The status sidecar file at STATUS_FILE_PATH is read by the orchestrator
when the GET /status endpoint is called.  It survives the subprocess exit
so failure_detail is available after the process terminates.

Usage (from orchestrator):
    python3 session_runner.py \\
        --session-id  <uuid> \\
        --identity-id <uuid> \\
        --stream-mode udp \\
        --stream-resolution 1280x720 \\
        --stream-fps 30 \\
        --status-file /tmp/speed_x_technology_sessions/<session_id>.json \\
        --ff-root /app/facefusion \\
        --db-path /app/data/consent.db
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional

# ── Exit codes (must be importable before facefusion path is set) ─────────────
# Inline constants here so this script is self-contained and importable
# even if speed_x_technology isn't on sys.path when a user inspects it directly.
EXIT_OK                = 0
EXIT_STARTUP_FAILURE   = 1
EXIT_CONSENT_REJECTION = 2
EXIT_MISSING_IDENTITY  = 3


# ── Status sidecar helpers ────────────────────────────────────────────────────

def _write_status(path: str, payload: dict) -> None:
    """Write status JSON atomically (write to .tmp then rename)."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except OSError:
        pass  # best-effort; orchestrator tolerates missing sidecar


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ── Signal handling ───────────────────────────────────────────────────────────

_stop_requested = False


def _handle_sigterm(signum, frame):
    global _stop_requested
    _stop_requested = True


signal.signal(signal.SIGTERM, _handle_sigterm)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="SPEED_X_TECHNOLOGY facefusion session runner")
    parser.add_argument("--session-id",        required=True)
    parser.add_argument("--identity-id",       required=True)
    parser.add_argument("--stream-mode",       default="udp",
                        choices=["udp", "v4l2"])
    parser.add_argument("--stream-resolution", default="1280x720")
    parser.add_argument("--stream-fps",        type=float, default=30.0)
    parser.add_argument("--status-file",       required=True,
                        help="Path to the JSON sidecar file for live status.")
    parser.add_argument("--ff-root",           required=True,
                        help="Absolute path to the facefusion/ repo root.")
    parser.add_argument("--db-path",           default="",
                        help="Override SPEED_X_TECHNOLOGY_CONSENT_DB_PATH.")
    parser.add_argument("--camera-index",      default=0,
                        help="cv2.VideoCapture device index or video file path.")
    args = parser.parse_args()

    # ── Environment assertions ────────────────────────────────────────────────
    os.environ["SPEED_X_TECHNOLOGY_IDENTITY_ID"] = args.identity_id
    if args.db_path:
        os.environ["SPEED_X_TECHNOLOGY_CONSENT_DB_PATH"] = args.db_path

    # Initial sidecar — lets the orchestrator know we're alive
    _write_status(args.status_file, {
        "session_id":   args.session_id,
        "status":       "starting",
        "fps":          None,
        "frames_total": 0,
        "failure_detail": None,
        "updated_at":   _now_iso(),
    })

    # ── Facefusion path setup ─────────────────────────────────────────────────
    ff_root = os.path.abspath(args.ff_root)
    speed_x_technology_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    for p in [ff_root, speed_x_technology_root]:
        if p not in sys.path:
            sys.path.insert(0, p)

    # ── Import facefusion internals ───────────────────────────────────────────
    try:
        import cv2
        from facefusion import state_manager
        from facefusion.streamer import multi_process_capture, open_stream
    except Exception as exc:
        detail = f"Facefusion import failed: {exc}"
        _write_status(args.status_file, {
            "session_id":     args.session_id,
            "status":         "failed_startup",
            "fps":            None,
            "frames_total":   0,
            "failure_detail": detail,
            "updated_at":     _now_iso(),
        })
        print(f"[Runner] {detail}", file=sys.stderr)
        return EXIT_STARTUP_FAILURE

    # ── Complete state_manager setup ───────────────────────────────────────────
    # Sets ALL keys that the stream path reads (32 total).
    # Values mirror facefusion's program.py hardcoded defaults exactly —
    # same values the CLI uses when facefusion.ini entries are blank.
    # Audit source: state_key_audit.md in the brain artifacts directory.

    # SPEED_X_TECHNOLOGY identity (orchestrator-specific)
    state_manager.init_item("speed_x_technology_identity_id",         args.identity_id)
    state_manager.init_item("source_paths",             [])   # gate replaces this

    # Group 1 — Core / orchestration
    state_manager.init_item("processors",               ["face_swapper"])
    state_manager.init_item("log_level",                "info")
    state_manager.init_item("execution_thread_count",   2)    # intentionally lower than default 8

    # Group 2 — Execution / GPU
    # execution_device_ids: configurable via SPEED_X_TECHNOLOGY_GPU_DEVICE_ID (set in env);
    # fall back to [0] for single-GPU target hardware.
    gpu_device_id = int(os.environ.get("SPEED_X_TECHNOLOGY_GPU_DEVICE_ID", "0"))
    state_manager.init_item("execution_device_ids",     [gpu_device_id])
    # execution_providers: prefer CUDA; fall back to CPU if CUDA unavailable.
    # The real default is get_first(get_available_execution_providers()) but
    # that requires native deps.  We set ["cuda", "cpu"] and let onnxruntime
    # select the first available one at session-creation time.
    state_manager.init_item("execution_providers",      ["cuda", "cpu"])
    state_manager.init_item("video_memory_strategy",    "strict")

    # Group 2b — Model download
    # Read by facefusion.download.resolve_download_url() when a model is first
    # needed.  Mirrors facefusion.choices.download_providers (program.py default).
    state_manager.init_item("download_providers",       ["github", "huggingface"])

    # Group 3 — Face detector
    state_manager.init_item("face_detector_model",      "yolo_face")
    state_manager.init_item("face_detector_size",       "640x640")
    state_manager.init_item("face_detector_score",      0.5)
    state_manager.init_item("face_detector_margin",     [0, 0, 0, 0])
    state_manager.init_item("face_detector_angles",     [0])

    # Group 4 — Face landmarker
    state_manager.init_item("face_landmarker_model",    "2dfan4")
    state_manager.init_item("face_landmarker_score",    0.5)

    # Group 5 — Face selector
    state_manager.init_item("face_selector_mode",       "reference")
    state_manager.init_item("face_selector_order",      "large-small")
    state_manager.init_item("face_selector_age_start",  None)   # no age filter
    state_manager.init_item("face_selector_age_end",    None)   # no age filter
    state_manager.init_item("face_selector_gender",     None)   # no gender filter
    state_manager.init_item("face_selector_race",       None)   # no race filter
    state_manager.init_item("reference_face_position",  0)
    state_manager.init_item("reference_face_distance",  0.3)

    # Group 6 — Face masker
    state_manager.init_item("face_mask_types",          ["box"])
    state_manager.init_item("face_mask_blur",           0.3)
    state_manager.init_item("face_mask_padding",        [0, 0, 0, 0])
    # face_mask_areas / face_mask_regions: pass all available so nothing is
    # silently dropped.  The actual list of valid values lives in
    # facefusion.choices — hardcode the most conservative full set here.
    state_manager.init_item("face_mask_areas",          [
        "forehead", "left-eyebrow", "right-eyebrow", "left-eye", "right-eye",
        "glasses", "nose", "mouth", "upper-lip", "lower-lip", "chin",
        "left-cheek", "right-cheek", "left-ear", "right-ear",
    ])
    state_manager.init_item("face_mask_regions",        [
        "skin", "left-eyebrow", "right-eyebrow", "left-eye", "right-eye",
        "glasses", "nose", "mouth", "upper-lip", "lower-lip",
        "left-ear", "right-ear", "face-hair",
    ])
    state_manager.init_item("face_occluder_model",      "xseg_1")
    state_manager.init_item("face_parser_model",        "bisenet_resnet_34")

    # Group 7 — Face swapper processor
    state_manager.init_item("face_swapper_model",       "hyperswap_1a_256")
    state_manager.init_item("face_swapper_pixel_boost", "128x128")
    state_manager.init_item("face_swapper_weight",      0.5)

    # Group 8 — Face tracker
    state_manager.init_item("face_tracker_score",       0.0)

    # Group 9 — Style / Restoration processors (pre-wired ahead of activation)
    # Keys are only read when the corresponding processor is in the 'processors'
    # list; pre-wiring them now means adding face_enhancer, frame_colorizer, or
    # frame_enhancer to 'processors' will never crash on a missing state key.
    # All defaults mirror facefusion's program.py hardcoded values.
    # Audit source: style_rest_audit.md in the brain artifacts directory.

    # face_enhancer (REST stage: GFPGAN / CodeFormer face restoration)
    state_manager.init_item("face_enhancer_model",      "gfpgan_1.4")
    state_manager.init_item("face_enhancer_blend",      80)    # int 0-100
    state_manager.init_item("face_enhancer_weight",     0.5)

    # frame_colorizer (STYLE stage: neural colourisation)
    state_manager.init_item("frame_colorizer_model",    "ddcolor")
    state_manager.init_item("frame_colorizer_size",     "256x256")
    state_manager.init_item("frame_colorizer_blend",    100)   # int 0-100

    # frame_enhancer (STYLE/REST: super-resolution upscaling)
    state_manager.init_item("frame_enhancer_model",     "span_kendata_x4")
    state_manager.init_item("frame_enhancer_blend",     80)    # int 0-100

    # ── CUDA EP pre-check: log before any model load ──────────────────────────
    # This runs before the camera opens. Prints to stdout so the orchestrator's
    # Popen stderr/stdout capture and the smoke-test script can grep for it.
    try:
        from facefusion.execution import get_available_execution_providers
        _available_eps = get_available_execution_providers()
        _configured_eps = state_manager.get_item("execution_providers")
        _device_ids     = state_manager.get_item("execution_device_ids")
        print(
            f"[SPEED_X_TECHNOLOGY-EP] facefusion available EPs : {_available_eps}",
            flush=True,
        )
        print(
            f"[SPEED_X_TECHNOLOGY-EP] configured execution_providers : {_configured_eps}",
            flush=True,
        )
        print(
            f"[SPEED_X_TECHNOLOGY-EP] configured execution_device_ids: {_device_ids}",
            flush=True,
        )
        if "cuda" not in _available_eps:
            detail = (
                "CUDA ExecutionProvider not available in this onnxruntime build. "
                f"Available EPs: {_available_eps}. "
                "Install onnxruntime-gpu and ensure CUDA drivers are present."
            )
            _write_status(args.status_file, {
                "session_id":     args.session_id,
                "status":         "failed_startup",
                "fps":            None,
                "frames_total":   0,
                "failure_detail": detail,
                "updated_at":     _now_iso(),
            })
            print(f"[Runner] {detail}", file=sys.stderr)
            return EXIT_STARTUP_FAILURE
    except Exception as _ep_exc:
        # Non-fatal: log but continue — the real failure will surface at model load.
        print(f"[SPEED_X_TECHNOLOGY-EP] EP pre-check warning: {_ep_exc}", flush=True)

    # ── Open camera ───────────────────────────────────────────────────────────
    cam_index = int(args.camera_index) if str(args.camera_index).lstrip("-").isdigit() else args.camera_index
    camera = cv2.VideoCapture(cam_index)
    if not camera.isOpened():
        detail = (
            f"Camera at index {args.camera_index} could not be opened. "
            "Check that the capture device is connected and not in use by another process."
        )
        _write_status(args.status_file, {
            "session_id":     args.session_id,
            "status":         "failed_startup",
            "fps":            None,
            "frames_total":   0,
            "failure_detail": detail,
            "updated_at":     _now_iso(),
        })
        print(f"[Runner] {detail}", file=sys.stderr)
        return EXIT_STARTUP_FAILURE

    camera_fps: float = camera.get(cv2.CAP_PROP_FPS) or args.stream_fps

    # ── Open ffmpeg output stream ─────────────────────────────────────────────
    try:
        stream_pipe = open_stream(args.stream_mode, args.stream_resolution, camera_fps)
    except Exception as exc:
        detail = f"Failed to open output stream ({args.stream_mode}): {exc}"
        _write_status(args.status_file, {
            "session_id":     args.session_id,
            "status":         "failed_startup",
            "fps":            None,
            "frames_total":   0,
            "failure_detail": detail,
            "updated_at":     _now_iso(),
        })
        print(f"[Runner] {detail}", file=sys.stderr)
        return EXIT_STARTUP_FAILURE

    # ── Frame loop ────────────────────────────────────────────────────────────
    # The consent gate inside multi_process_capture() will call sys.exit()
    # with EXIT_CONSENT_REJECTION (2) or EXIT_MISSING_IDENTITY (3) if the
    # identity check fails — those exits propagate through the generator's
    # first next() call and terminate this process.
    frame_count  = 0
    fps_window   = 30          # rolling window for FPS calculation
    window_start = time.monotonic()
    last_sidecar = time.monotonic()

    try:
        gen = multi_process_capture(camera, camera_fps)

        for frame in gen:
            if _stop_requested:
                break

            # Write frame to ffmpeg pipe
            if stream_pipe.stdin and stream_pipe.poll() is None:
                import cv2 as _cv2
                _, encoded = _cv2.imencode(".jpg", frame)
                stream_pipe.stdin.write(encoded.tobytes())

            frame_count += 1

            # ── First-frame CUDA confirmation (runs exactly once) ────────────
            # After the first frame is processed, the ONNX InferenceSession for
            # face_swapper has been created and loaded.  Query its actual
            # providers to confirm CUDA was used — not just configured.
            # A silent onnxruntime CUDA→CPU fallback appears here as
            # ['CPUExecutionProvider'] even though 'cuda' was in our list.
            if frame_count == 1:
                try:
                    from facefusion.inference_manager import INFERENCE_POOL_SET
                    from facefusion.app_context import detect_app_context
                    _ctx = detect_app_context()
                    _pool_set = INFERENCE_POOL_SET.get(_ctx, {})
                    _confirmed_cuda = False
                    for _inf_ctx, _pool in _pool_set.items():
                        for _model_name, _session in _pool.items():
                            _live_providers = _session.get_providers()
                            print(
                                f"[SPEED_X_TECHNOLOGY-EP-LIVE] model={_model_name!r} "
                                f"ctx={_inf_ctx!r} "
                                f"providers={_live_providers}",
                                flush=True,
                            )
                            if any("CUDA" in p for p in _live_providers):
                                _confirmed_cuda = True
                    if _confirmed_cuda:
                        print(
                            "[SPEED_X_TECHNOLOGY-EP-LIVE] CONFIRMED: CUDAExecutionProvider "
                            "is active for at least one model.",
                            flush=True,
                        )
                    else:
                        print(
                            "[SPEED_X_TECHNOLOGY-EP-LIVE] WARNING: No model is using CUDA — "
                            "all sessions fell back to CPU. Check onnxruntime-gpu "
                            "version and CUDA driver compatibility.",
                            flush=True,
                        )
                except Exception as _live_exc:
                    print(f"[SPEED_X_TECHNOLOGY-EP-LIVE] provider query failed: {_live_exc}", flush=True)
            # ────────────────────────────────────────────────────────────────

            # Update FPS estimate and write sidecar ~ once per second
            now = time.monotonic()
            if now - last_sidecar >= 1.0:
                elapsed = now - window_start
                fps = frame_count / elapsed if elapsed > 0 else 0.0
                _write_status(args.status_file, {
                    "session_id":   args.session_id,
                    "status":       "running",
                    "fps":          round(fps, 1),
                    "frames_total": frame_count,
                    "failure_detail": None,
                    "updated_at":   _now_iso(),
                })
                last_sidecar = now

    except SystemExit:
        # Consent gate or facefusion itself called sys.exit() —
        # let the exit code propagate naturally.
        raise
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        import traceback
        detail = f"Runtime error in streaming loop: {exc}\n{traceback.format_exc()}"
        _write_status(args.status_file, {
            "session_id":     args.session_id,
            "status":         "failed_startup",
            "fps":            None,
            "frames_total":   frame_count,
            "failure_detail": detail,
            "updated_at":     _now_iso(),
        })
        print(f"[Runner] {detail}", file=sys.stderr)
        return EXIT_STARTUP_FAILURE
    finally:
        camera.release()
        if stream_pipe.stdin:
            stream_pipe.stdin.close()
        stream_pipe.wait()

    # Clean stop
    elapsed = time.monotonic() - window_start
    fps = frame_count / elapsed if elapsed > 0 else 0.0
    _write_status(args.status_file, {
        "session_id":   args.session_id,
        "status":       "stopped",
        "fps":          round(fps, 1),
        "frames_total": frame_count,
        "failure_detail": None,
        "updated_at":   _now_iso(),
    })
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
