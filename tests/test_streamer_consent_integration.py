"""
tests/test_streamer_consent_integration.py

Integration test: exercises multi_process_capture() in the actual
modified facefusion/streamer.py with mocked camera input and a real
consent DB.

Heavy native facefusion deps (cv2, onnxruntime, ...) are stubbed in
sys.modules so this test runs on the host without a GPU.  The consent
gate (speed_x_technology package) uses a real SQLite DB -- gate internals are NOT mocked.

Three scenarios:
  A. Approved identity  -> no SystemExit; read_static_images called with
     only the DB-verified path, never the attacker path from state_manager.
  B. Rejected identity  -> SystemExit(EXIT_CONSENT_REJECTION=2); pipeline blocked.
  C. Missing speed_x_technology_identity_id -> SystemExit(EXIT_MISSING_IDENTITY=3); no passthrough.

Run from SPEED_X_TECHNOLOGY project root:
    python3 tests/test_streamer_consent_integration.py
"""
import importlib.util
import os
import sys
import tempfile
import types
import uuid
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# 1. PATH SETUP  (must come before any speed_x_technology import)
# ---------------------------------------------------------------------------
_ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FF_ROOT = os.path.join(_ROOT, "Modules", "facefusion")
for p in [_ROOT, _FF_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)

from speed_x_technology.orchestrator.exit_codes import EXIT_CONSENT_REJECTION, EXIT_MISSING_IDENTITY  # noqa: E402

# ---------------------------------------------------------------------------
# 2. STUB EVERY FACEFUSION SYMBOL IMPORTED BY streamer.py
#    (exact list from `grep "^from facefusion" streamer.py`)
# ---------------------------------------------------------------------------

def _mod(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m

# cv2
_cv2 = _mod("cv2")
_cv2.VideoCapture = MagicMock

# tqdm
_tqdm_pkg = _mod("tqdm")
class _NoopTqdm:
    def __init__(self, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def update(self): pass
_tqdm_pkg.tqdm = _NoopTqdm

# facefusion top-level package
_ff = _mod("facefusion")
_ff.ffmpeg_builder = MagicMock()
_ff.logger         = MagicMock()
_ff.state_manager  = MagicMock()
_ff.translator     = MagicMock()

# facefusion.audio
_audio = _mod("facefusion.audio")
_audio.create_empty_audio_frame = MagicMock(return_value=object())

# facefusion.content_analyser
_ca = _mod("facefusion.content_analyser")
_ca.analyse_stream = MagicMock(return_value=False)

# facefusion.ffmpeg
_ffmpeg = _mod("facefusion.ffmpeg")
_ffmpeg.open_ffmpeg = MagicMock()

# facefusion.filesystem
_fs = _mod("facefusion.filesystem")
_fs.is_directory = MagicMock(return_value=False)

# facefusion.processors + facefusion.processors.core
_proc_pkg  = _mod("facefusion.processors")
_proc_core = _mod("facefusion.processors.core")
_proc_core.get_processors_modules = MagicMock(return_value=[])

# facefusion.types
_types_mod = _mod("facefusion.types")
_types_mod.Fps         = float
_types_mod.StreamMode  = str
_types_mod.VisionFrame = object

# facefusion.vision
_vision = _mod("facefusion.vision")
_vision.extract_vision_mask = MagicMock(return_value=None)
_vision.is_vision_frame     = MagicMock(return_value=False)
_vision.read_static_images  = MagicMock(return_value=[])

# ---------------------------------------------------------------------------
# 3. LOAD streamer.py DIRECTLY (bypasses facefusion package __init__)
# ---------------------------------------------------------------------------
_STREAMER_PATH = os.path.join(_FF_ROOT, "facefusion", "streamer.py")
_spec = importlib.util.spec_from_file_location("facefusion.streamer", _STREAMER_PATH)
_streamer_module = importlib.util.module_from_spec(_spec)
sys.modules["facefusion.streamer"] = _streamer_module
_spec.loader.exec_module(_streamer_module)

# ---------------------------------------------------------------------------
# 4. CONSENT DB (real SQLite, isolated per test run)
# ---------------------------------------------------------------------------
_TEST_DB = tempfile.mktemp(suffix=".db", prefix="speed_x_technology_streamer_inttest_")
os.environ["SPEED_X_TECHNOLOGY_CONSENT_DB_PATH"] = _TEST_DB

from speed_x_technology.consent_gate.db import init_db, insert_identity, insert_sample_path  # noqa
init_db()

# ---------------------------------------------------------------------------
# 5. HELPERS
# ---------------------------------------------------------------------------

def _reload_streamer():
    """Re-exec streamer so _CONSENT_GATE_ENABLED is re-evaluated fresh."""
    _spec.loader.exec_module(_streamer_module)
    return _streamer_module


def _make_approved_identity():
    iid = str(uuid.uuid4())
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    insert_identity(iid, "Approved User", "real-time video/voice", status="approved")
    insert_sample_path(iid, tmp.name, "face")
    return iid, tmp.name


def _make_pending_identity():
    iid = str(uuid.uuid4())
    insert_identity(iid, "Pending User", "real-time video/voice",
                    status="pending_verification")
    return iid


def _make_state(speed_x_technology_identity_id):
    return {
        "speed_x_technology_identity_id":        speed_x_technology_identity_id,
        "source_paths":            ["/arbitrary/attacker.jpg"],
        "log_level":               "error",
        "execution_thread_count":  1,
        "processors":              [],
    }


def _mock_camera(num_frames=2):
    import numpy as np
    frame = (np.zeros((480, 640, 3), dtype="uint8") + 128)
    cam = MagicMock()
    cam.isOpened.side_effect = [True] * num_frames + [False]
    cam.read.return_value = (True, frame)
    return cam


def _drive(gen, max_frames=4):
    """Advance generator; return (frames, exit_code_or_None)."""
    frames = []
    for _ in range(max_frames):
        try:
            frames.append(next(gen))
        except StopIteration:
            break
        except SystemExit as e:
            return frames, e.code
    return frames, None


def _wire_streamer(streamer, state, mock_rsi=None):
    """Assign per-test mocks directly onto the loaded module object."""
    mock_sm = MagicMock()
    mock_sm.get_item.side_effect = lambda k: state.get(k)
    streamer.state_manager           = mock_sm
    streamer.read_static_images      = mock_rsi or MagicMock(return_value=[])
    streamer.analyse_stream          = lambda *a: False
    streamer.is_vision_frame         = lambda *a: False
    streamer.extract_vision_mask     = lambda *a: None
    streamer.get_processors_modules  = lambda *a: []
    streamer.create_empty_audio_frame = lambda: object()
    streamer.translator              = MagicMock()
    streamer.translator.get.return_value = "stream"
    streamer.logger                  = MagicMock()
    return mock_sm


# ---------------------------------------------------------------------------
# 6. TESTS
# ---------------------------------------------------------------------------

def test_approved_identity_routes_db_paths_to_pipeline():
    """
    Approved identity:
    - No SystemExit raised.
    - read_static_images receives the DB-verified sample path.
    - Attacker path from state_manager never reaches read_static_images.
    """
    iid, sample_path = _make_approved_identity()
    state = _make_state(iid)
    streamer = _reload_streamer()
    mock_rsi = MagicMock(return_value=[])
    _wire_streamer(streamer, state, mock_rsi)

    gen = streamer.multi_process_capture(_mock_camera(1), 30.0)
    _, exit_code = _drive(gen)

    assert exit_code is None, \
        f"Unexpected SystemExit({exit_code}) for approved identity"

    mock_rsi.assert_called_once()
    actual_paths = mock_rsi.call_args[0][0]
    assert sample_path in actual_paths, \
        f"DB path {sample_path!r} not in read_static_images call; got {actual_paths!r}"
    assert "/arbitrary/attacker.jpg" not in actual_paths, \
        "Attacker path from state_manager reached read_static_images — bypass!"

    os.unlink(sample_path)
    print("PASS  test_approved_identity_routes_db_paths_to_pipeline")


def test_rejected_identity_exits_before_first_frame():
    """
    Pending identity:
    - SystemExit(1) before any frame is yielded.
    - read_static_images never called.
    """
    iid = _make_pending_identity()
    state = _make_state(iid)
    streamer = _reload_streamer()
    mock_rsi = MagicMock(return_value=[])
    _wire_streamer(streamer, state, mock_rsi)

    gen = streamer.multi_process_capture(_mock_camera(2), 30.0)
    frames, exit_code = _drive(gen)

    assert exit_code == EXIT_CONSENT_REJECTION, \
        f"Expected SystemExit({EXIT_CONSENT_REJECTION}), got {exit_code!r}; frames yielded={len(frames)}"
    assert frames == [], \
        f"Generator yielded {len(frames)} frame(s) — passthrough leak!"
    mock_rsi.assert_not_called()

    print("PASS  test_rejected_identity_exits_before_first_frame")


def test_missing_identity_id_exits_before_first_frame():
    """
    Missing speed_x_technology_identity_id in state:
    - SystemExit(1) fired immediately.
    - read_static_images never called.
    """
    state = {
        "speed_x_technology_identity_id":       None,
        "source_paths":           ["/some/source.jpg"],
        "log_level":              "error",
        "execution_thread_count": 1,
        "processors":             [],
    }
    streamer = _reload_streamer()
    mock_rsi = MagicMock(return_value=[])
    _wire_streamer(streamer, state, mock_rsi)

    gen = streamer.multi_process_capture(_mock_camera(1), 30.0)
    frames, exit_code = _drive(gen)

    assert exit_code == EXIT_MISSING_IDENTITY, \
        f"Expected SystemExit({EXIT_MISSING_IDENTITY}) when identity_id is None; got {exit_code!r}"
    assert frames == []
    mock_rsi.assert_not_called()

    print("PASS  test_missing_identity_id_exits_before_first_frame")


# ---------------------------------------------------------------------------
# 7. RUNNER
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [
        test_approved_identity_routes_db_paths_to_pipeline,
        test_rejected_identity_exits_before_first_frame,
        test_missing_identity_id_exits_before_first_frame,
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

    if os.path.exists(_TEST_DB):
        os.unlink(_TEST_DB)

    if failed:
        print(f"\n{failed}/{len(tests)} integration tests FAILED")
        sys.exit(1)
    else:
        print(f"\n{len(tests)}/{len(tests)} integration tests passed")
