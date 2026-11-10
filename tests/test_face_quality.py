"""Model-free quality regression tests; require OpenCV and NumPy."""
import sys
from pathlib import Path
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Modules/facefusion'))
from facefusion.skin_match import match_skin, TemporalColor, stream_color_state
from facefusion.quality_blend import blend_face
from facefusion.quality_profile import apply_quality_profile
from facefusion.live_capture import latest_frames


def test_skin_moves_toward_target_without_changing_background():
    reference = np.full((64, 64, 3), [100, 130, 170], np.uint8)
    generated = np.full_like(reference, [90, 115, 155])
    mask = np.zeros((64, 64), np.float32)
    mask[8:56, 8:56] = 1
    corrected = match_skin(reference, generated, mask)
    assert np.linalg.norm(corrected[32, 32].astype(float) - reference[32, 32]) < np.linalg.norm(generated[32, 32].astype(float) - reference[32, 32])
    np.testing.assert_array_equal(corrected[mask == 0], generated[mask == 0])
    assert corrected.dtype == np.uint8


def test_empty_mask_and_zero_strength_are_neutral():
    image = np.full((32, 32, 3), 120, np.uint8)
    np.testing.assert_array_equal(match_skin(image, image, np.zeros((32, 32))), image)
    result = match_skin(image + 10, image, np.ones((32, 32)), strength=0)
    assert np.abs(result.astype(float) - image).max() <= 1


def test_shape_validation():
    with pytest.raises(ValueError):
        match_skin(np.zeros((10, 10, 3)), np.zeros((10, 10, 3)), np.zeros((9, 9)))


def test_temporal_smoothing_resets_and_returns_copies():
    state = TemporalColor()
    embedding, signature = np.array([1., 0.]), np.zeros(3)
    state.update(np.zeros(3), embedding, signature, now=0)
    result = state.update(np.ones(3) * 10, embedding, signature, now=0.03)
    assert np.all((result > 0) & (result < 10))
    result[:] = 99
    assert np.all(state.delta < 10)
    np.testing.assert_array_equal(state.update(np.ones(3), -embedding, signature, now=0.06), np.ones(3))
    np.testing.assert_array_equal(state.update(np.ones(3) * 5, embedding, signature, now=2), np.ones(3) * 5)
    np.testing.assert_array_equal(state.update(np.ones(3) * 8, embedding, signature + 50, now=2.03), np.ones(3) * 8)


def test_low_skin_resets_history():
    state = TemporalColor()
    state.update(np.zeros(3), [1., 0.], np.zeros(3), now=0)
    token = stream_color_state.set(state)
    try:
        image = np.full((32, 32, 3), 120, np.uint8)
        match_skin(image, image, np.zeros((32, 32)))
        assert state.timestamp is None
    finally:
        stream_color_state.reset(token)


@pytest.mark.parametrize('mode', ['alpha', 'multiband', 'poisson'])
def test_blending_preserves_outside_and_handles_odd_sizes(mode):
    target = np.full((65, 63, 3), 100, np.uint8)
    source = np.full_like(target, 150)
    mask = np.zeros((65, 63), np.float32)
    mask[10:50, 10:50] = 1
    result = blend_face(target, source, mask, mode)
    assert result.shape == target.shape
    np.testing.assert_array_equal(result[mask == 0], target[mask == 0])


def test_profiles_and_validation(monkeypatch):
    class State:
        values = {}
        def init_item(self, key, value):
            self.values[key] = value
    state = State()
    for key in ['SPEED_X_QUALITY_PROFILE', 'SPEED_X_SKIN_MATCH', 'SPEED_X_BLEND', 'SPEED_X_SKIN_STRENGTH', 'SPEED_X_SWAP_MODEL']:
        monkeypatch.delenv(key, raising=False)
    apply_quality_profile(state)
    assert state.values['face_swapper_pixel_boost'] == '256x256'
    monkeypatch.setenv('SPEED_X_QUALITY_PROFILE', 'offline')
    apply_quality_profile(state)
    assert state.values['face_swapper_pixel_boost'] == '512x512'
    monkeypatch.setenv('SPEED_X_SKIN_STRENGTH', 'nan')
    with pytest.raises(ValueError):
        apply_quality_profile(state)


def test_capture_bounds_backlog_and_releases_camera():
    class Camera:
        index = 0
        released = False
        def isOpened(self):
            return not self.released
        def read(self):
            self.index += 1
            return self.index <= 100, self.index
        def release(self):
            self.released = True
    camera = Camera()
    values = list(latest_frames(camera))
    assert values == sorted(set(values))
    assert values[-1] == 100
    assert camera.released


def test_capture_propagates_failure_and_releases():
    class Camera:
        released = False
        def isOpened(self):
            return True
        def read(self):
            raise RuntimeError('capture failed')
        def release(self):
            self.released = True
    camera = Camera()
    with pytest.raises(RuntimeError, match='capture failed'):
        list(latest_frames(camera))
    assert camera.released


def test_brightness_limits_and_strength_validation():
    for value in [0, 255]:
        image = np.full((32, 32, 3), value, np.uint8)
        np.testing.assert_array_equal(match_skin(image, image, np.ones((32, 32))), image)
    with pytest.raises(ValueError):
        match_skin(image, image, np.ones((32, 32)), strength=float('nan'))


def test_timing_report_groups_profiles():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from report_face_quality import summarize
    result = summarize([{'settings': {'profile': 'live'}, 'stages_ms': {'total': float(i)}} for i in range(10)], warmup=5)
    assert result[0]['measured_faces'] == 5
    assert result[0]['stages']['total']['p50_ms'] == 7


def test_source_complexion_and_invalid_reference():
    from facefusion.skin_match import skin_statistics
    source = np.full((64, 64, 3), [100, 130, 170], np.uint8)
    generated = np.full_like(source, [70, 90, 120])
    mask = np.ones((64, 64), np.float32)
    stats = skin_statistics(source, mask)
    corrected = match_skin(generated, generated, mask, source_stats=stats)
    assert np.linalg.norm(corrected[32, 32].astype(float) - source[32, 32]) < np.linalg.norm(generated[32, 32].astype(float) - source[32, 32])
    with pytest.raises(ValueError, match='insufficient'):
        skin_statistics(source, np.zeros_like(mask))


def test_encoder_write_deadline():
    import subprocess
    from facefusion.stream_io import write_frame, close_encoder
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], stdin=subprocess.PIPE)
    try:
        with pytest.raises(TimeoutError):
            write_frame(process, b'x' * 1000000, timeout=0.05)
    finally:
        close_encoder(process)
    assert process.poll() is not None
