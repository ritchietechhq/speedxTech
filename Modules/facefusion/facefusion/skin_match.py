"""Bounded skin-only color transfer; BGR uint8 input/output."""
import time
from contextvars import ContextVar
import cv2
import numpy as np


# State exists only inside a stream context; offline worker threads stay stateless.
stream_color_state = ContextVar('stream_color_state', default=None)


class TemporalColor:
    def __init__(self):
        self.reset()

    def reset(self):
        self.delta = self.embedding = self.signature = self.timestamp = None

    def update(self, delta, embedding, signature, now=None):
        now = time.monotonic() if now is None else now
        embedding = np.asarray(embedding, dtype=np.float32)
        embedding = embedding / max(float(np.linalg.norm(embedding)), 1e-8)
        signature = np.asarray(signature)
        reset = (self.timestamp is None or now - self.timestamp > 0.75 or
                 self.embedding.shape != embedding.shape or
                 float(np.dot(self.embedding, embedding)) < 0.65 or
                 np.linalg.norm(signature - self.signature) > 25)
        if reset:
            self.delta = np.asarray(delta).copy()
        else:
            alpha = 1 - np.exp(-max(0, now - self.timestamp) / 0.12)
            self.delta += alpha * (delta - self.delta)
        self.embedding, self.signature, self.timestamp = embedding, signature.copy(), now
        return self.delta.copy()


def match_skin(reference, generated, mask, strength=0.8, embedding=None, source_stats=None):
    if reference.shape != generated.shape or mask.shape != generated.shape[:2]:
        raise ValueError("Skin matching requires aligned images and mask")
    if not np.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError("Skin strength must be between zero and one")
    if strength == 0:
        return generated.copy()
    ref = cv2.cvtColor(reference.astype(np.float32) / 255, cv2.COLOR_BGR2LAB)
    out = cv2.cvtColor(generated.astype(np.float32) / 255, cv2.COLOR_BGR2LAB)
    valid = (mask > 0.9) & (ref[..., 0] > 5) & (ref[..., 0] < 95) & (out[..., 0] > 5) & (out[..., 0] < 95)
    if np.count_nonzero(valid) < max(32, mask.size * 0.01):
        state = stream_color_state.get()
        if state is not None:
            state.reset()
        return generated.copy()
    delta = np.zeros(3, np.float32)
    for channel in range(3):
        values = ref[..., channel][valid] - out[..., channel][valid]
        low, high = np.percentile(values, [10, 90])
        delta[channel] = np.mean(np.clip(values, low, high))
    if source_stats is not None:
        # Estimate complexion separately from local lighting; retain spatial detail.
        delta = np.asarray(source_stats, dtype=np.float32) - np.median(out[valid], axis=0)
        delta = np.clip(delta, [-30, -25, -25], [30, 25, 25])
    else:
        delta = np.clip(delta, [-10, -15, -15], [10, 15, 15])
        delta[0] *= 0.35
    state = stream_color_state.get()
    if state is not None and embedding is not None:
        delta = state.update(delta, embedding, np.median(ref[valid], axis=0))
    alpha = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 2).clip(0, 1) * (mask > 0)
    corrected = out + delta * np.clip(strength, 0, 1)
    bgr = cv2.cvtColor(corrected.astype(np.float32), cv2.COLOR_LAB2BGR).clip(0, 1) * 255
    return np.clip(generated * (1 - alpha[..., None]) + bgr * alpha[..., None], 0, 255).astype(np.uint8)


def skin_statistics(image, mask):
    lab = cv2.cvtColor(image.astype(np.float32) / 255, cv2.COLOR_BGR2LAB)
    valid = (mask > 0.9) & (lab[..., 0] > 5) & (lab[..., 0] < 95)
    if np.count_nonzero(valid) < max(32, mask.size * 0.01):
        raise ValueError('Source image has insufficient visible skin for complexion matching')
    return np.median(lab[valid], axis=0).astype(np.float32)
