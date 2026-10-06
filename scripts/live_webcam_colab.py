#!/usr/bin/env python3
"""
scripts/live_webcam_colab.py

Live "mirror" demo for Google Colab: browser webcam -> A100 face_swapper ->
side-by-side view (original | swapped) rendered back in the notebook cell.

Why this exists
---------------
A Colab VM has no camera.  Your *browser* does.  This script moves frames
browser -> kernel -> GPU -> browser one at a time (getUserMedia + eval_js),
so you can judge swap quality on your own face in near-real-time.

It is a quality/precision viewer, NOT the production streaming path:
  * Throughput is limited by the notebook round-trip (expect ~5-12 FPS,
    well below the 28 FPS the orchestrator reached on a local video file).
  * The same safety layers as production stay ON:
      - Consent gate  (require_approved_identity; sample paths come ONLY
        from the consent store)
      - NSFW content analyser (analyse_stream)

Usage (in a Colab cell, after the repo is at /content/SPEED_X_TECHNOLOGY):

    import sys; sys.path.insert(0, "/content/SPEED_X_TECHNOLOGY/scripts")
    import live_webcam_colab as live
    live.run("<identity_id>", seconds=120)
"""
from __future__ import annotations

import base64
import os
import sys
import time

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_FF_ROOT = os.path.join(_ROOT, "Modules", "facefusion")
_DEFAULT_DB = os.path.join(_ROOT, "data", "consent.db")


# ── Browser side (getUserMedia) ───────────────────────────────────────────────
_JS = """
var _cam = {video: null, canvas: null, out: null, stopped: false, stream: null, div: null};

async function startCam(w, h) {
  _cam.stopped = false;
  _cam.div = document.createElement('div');
  _cam.div.style.maxWidth = '100%';

  var btn = document.createElement('button');
  btn.textContent = 'Stop camera';
  btn.style.cssText = 'margin:6px 0;padding:8px 18px;font-size:14px;cursor:pointer;';
  btn.onclick = function() { _cam.stopped = true; };
  _cam.div.appendChild(btn);

  _cam.out = document.createElement('img');
  _cam.out.style.cssText = 'display:block;width:100%;max-width:1280px;border-radius:8px;';
  _cam.div.appendChild(_cam.out);

  _cam.video = document.createElement('video');
  _cam.video.style.display = 'none';
  _cam.video.muted = true;
  _cam.div.appendChild(_cam.video);
  document.body.appendChild(_cam.div);

  _cam.stream = await navigator.mediaDevices.getUserMedia({video: {width: w, height: h}});
  _cam.video.srcObject = _cam.stream;
  await _cam.video.play();

  _cam.canvas = document.createElement('canvas');
  _cam.canvas.width = w;
  _cam.canvas.height = h;
  return true;
}

// Show the previous result (if any) and return the next camera frame as base64 JPEG.
async function grab(resultB64, quality) {
  if (resultB64 && resultB64.length > 0) {
    _cam.out.src = 'data:image/jpeg;base64,' + resultB64;
  }
  if (_cam.stopped) { return 'STOP'; }
  var ctx = _cam.canvas.getContext('2d');
  ctx.drawImage(_cam.video, 0, 0, _cam.canvas.width, _cam.canvas.height);
  return _cam.canvas.toDataURL('image/jpeg', quality).split(',')[1];
}

function stopCam() {
  _cam.stopped = true;
  if (_cam.stream) { _cam.stream.getTracks().forEach(function(t) { t.stop(); }); }
  if (_cam.video) { _cam.video.remove(); }
  return true;
}
"""


# ── facefusion state (mirrors session_runner.py) ──────────────────────────────
def _init_state(state_manager, identity_id: str) -> None:
    """Same state keys / values the orchestrator's session_runner sets."""
    items = {
        "speed_x_technology_identity_id": identity_id,
        "source_paths": [],  # the consent gate replaces this
        "processors": ["face_swapper"],
        "log_level": "warn",
        "execution_thread_count": 2,
        "execution_device_ids": [int(os.environ.get("SPEED_X_TECHNOLOGY_GPU_DEVICE_ID", "0"))],
        "execution_providers": ["cuda", "cpu"],
        "video_memory_strategy": "strict",
        "download_providers": ["github", "huggingface"],
        "face_detector_model": "yolo_face",
        "face_detector_size": "640x640",
        "face_detector_score": 0.5,
        "face_detector_margin": [0, 0, 0, 0],
        "face_detector_angles": [0],
        "face_landmarker_model": "2dfan4",
        "face_landmarker_score": 0.5,
        "face_selector_mode": "one",
        "face_selector_order": "large-small",
        "face_selector_age_start": None,
        "face_selector_age_end": None,
        "face_selector_gender": None,
        "face_selector_race": None,
        "reference_face_position": 0,
        "reference_face_distance": 0.3,
        "face_mask_types": ["box"],
        "face_mask_blur": 0.3,
        "face_mask_padding": [0, 0, 0, 0],
        "face_mask_areas": [
            "forehead", "left-eyebrow", "right-eyebrow", "left-eye", "right-eye",
            "glasses", "nose", "mouth", "upper-lip", "lower-lip", "chin",
            "left-cheek", "right-cheek", "left-ear", "right-ear",
        ],
        "face_mask_regions": [
            "skin", "left-eyebrow", "right-eyebrow", "left-eye", "right-eye",
            "glasses", "nose", "mouth", "upper-lip", "lower-lip",
            "left-ear", "right-ear", "face-hair",
        ],
        "face_occluder_model": "xseg_1",
        "face_parser_model": "bisenet_resnet_34",
        "face_swapper_model": "hyperswap_1a_256",
        "face_swapper_pixel_boost": "128x128",
        "face_swapper_weight": 0.5,
        "face_tracker_score": 0.0,
    }
    for key, value in items.items():
        state_manager.init_item(key, value)


def _prepare(identity_id: str, db_path: str):
    """Set up paths, state, consent gate, models.  Returns (source_frames, funcs)."""
    os.environ["SPEED_X_TECHNOLOGY_IDENTITY_ID"] = identity_id
    os.environ["SPEED_X_TECHNOLOGY_CONSENT_DB_PATH"] = os.path.abspath(db_path)
    for p in (_FF_ROOT, _ROOT):
        if p not in sys.path:
            sys.path.insert(0, p)

    from facefusion import state_manager
    from facefusion.content_analyser import analyse_stream
    from facefusion.processors.core import get_processors_modules
    from facefusion.streamer import process_stream_frame
    from facefusion.vision import read_static_images
    from speed_x_technology.consent_gate import ConsentGateRejected, require_approved_identity

    _init_state(state_manager, identity_id)

    # Consent gate: sample paths come ONLY from the approved consent record.
    try:
        approved = require_approved_identity(identity_id)
    except ConsentGateRejected as exc:
        raise SystemExit(f"[ConsentGate] REFUSED: {exc}")
    state_manager.set_item("source_paths", approved.sample_paths)

    # Download / verify models (no-op if already present).
    for module in get_processors_modules(state_manager.get_item("processors")):
        if not module.pre_check():
            raise SystemExit(f"Model pre_check failed for {module.__name__}")

    source_frames = read_static_images(approved.sample_paths)
    if not source_frames:
        raise SystemExit(f"Could not read identity image(s): {approved.sample_paths}")
    return source_frames, analyse_stream, process_stream_frame


# ── Main entry ────────────────────────────────────────────────────────────────
def run(
    identity_id: str,
    seconds: int = 120,
    db_path: str = _DEFAULT_DB,
    width: int = 640,
    height: int = 480,
    jpeg_quality: float = 0.7,
    mirror: bool = True,
) -> None:
    """Stream the browser webcam through the face swapper for `seconds`."""
    import cv2
    import numpy
    from google.colab.output import eval_js
    from IPython.display import Javascript, display

    print("Preparing (consent gate, state, models)...")
    source_frames, analyse_stream, process_stream_frame = _prepare(identity_id, db_path)

    display(Javascript(_JS))
    time.sleep(0.5)
    print("Allow camera access in your browser...")
    eval_js(f"startCam({width}, {height})")

    result_b64 = ""
    frames = 0
    started = time.time()
    window_start = started
    window_frames = 0
    live_fps = 0.0
    blocked = False

    try:
        while time.time() - started < seconds:
            raw = eval_js(f'grab("{result_b64}", {jpeg_quality})')
            if not raw or raw == "STOP":
                break

            frame = cv2.imdecode(
                numpy.frombuffer(base64.b64decode(raw), numpy.uint8), cv2.IMREAD_COLOR
            )
            if frame is None:
                continue
            if mirror:
                frame = cv2.flip(frame, 1)

            # Same NSFW safety check as the production streamer (every ~10th frame).
            if analyse_stream(frame, 10):
                blocked = True
                print("Content analyser flagged the stream - stopping.")
                break

            swapped = process_stream_frame(source_frames, frame)

            frames += 1
            window_frames += 1
            now = time.time()
            if now - window_start >= 1.0:
                live_fps = window_frames / (now - window_start)
                window_start, window_frames = now, 0

            left, right = frame.copy(), swapped.copy()
            cv2.putText(left, "ORIGINAL", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(right, f"SWAPPED  {live_fps:.1f} fps", (12, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            ok, buf = cv2.imencode(".jpg", numpy.hstack([left, right]), [cv2.IMWRITE_JPEG_QUALITY, 80])
            result_b64 = base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""
    finally:
        try:
            eval_js("stopCam()")
        except Exception:
            pass

    elapsed = max(time.time() - started, 1e-6)
    print(
        f"Done: {frames} frames in {elapsed:.1f}s "
        f"({frames / elapsed:.1f} fps average){' [blocked by content analyser]' if blocked else ''}"
    )
