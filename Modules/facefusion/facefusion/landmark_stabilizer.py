"""Temporal One-Euro filter for affine transformation matrix and landmark stabilization."""
import math
import time
from contextvars import ContextVar
import numpy as np

# State exists only inside an active stream context; offline workers stay stateless.
stream_matrix_state = ContextVar('stream_matrix_state', default=None)


class LowPassFilter:
    def __init__(self, alpha=0.5):
        self.alpha = alpha
        self.prev = None

    def reset(self):
        self.prev = None

    def filter(self, value):
        if self.prev is None:
            self.prev = np.asarray(value, dtype=np.float64).copy()
            return self.prev.copy()
        filtered = self.alpha * np.asarray(value, dtype=np.float64) + (1.0 - self.alpha) * self.prev
        self.prev = filtered
        return filtered.copy()


class OneEuroFilter:
    """1€ Filter for multi-dimensional signals (e.g. 2x3 affine matrix or landmarks).

    References: Casiez et al., CHI 2012.
    """
    def __init__(self, min_cutoff=1.0, beta=0.007, d_cutoff=1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.x_filter = LowPassFilter()
        self.dx_filter = LowPassFilter()
        self.last_time = None
        self.last_raw = None

    def reset(self):
        self.x_filter.reset()
        self.dx_filter.reset()
        self.last_time = None
        self.last_raw = None

    @staticmethod
    def _compute_alpha(cutoff, dt):
        tau = 1.0 / (2.0 * math.pi * max(cutoff, 1e-5))
        return 1.0 / (1.0 + tau / max(dt, 1e-5))

    def filter(self, x, timestamp=None):
        x = np.asarray(x, dtype=np.float64)
        now = time.monotonic() if timestamp is None else float(timestamp)

        if self.last_time is None:
            self.last_time = now
            self.last_raw = x.copy()
            return self.x_filter.filter(x)

        dt = now - self.last_time
        # Reset filter on long time gaps (> 0.5s) or dramatic sudden displacement
        if dt > 0.5 or dt <= 0:
            self.reset()
            self.last_time = now
            self.last_raw = x.copy()
            return self.x_filter.filter(x)

        # Large shift check (e.g. shot change or face swap to different location)
        dx_dist = float(np.linalg.norm(x - self.last_raw))
        if dx_dist > 50.0:
            self.reset()
            self.last_time = now
            self.last_raw = x.copy()
            return self.x_filter.filter(x)

        # Estimate derivative
        dx = (x - self.last_raw) / max(dt, 1e-4)
        self.last_time = now
        self.last_raw = x.copy()

        d_alpha = self._compute_alpha(self.d_cutoff, dt)
        self.dx_filter.alpha = d_alpha
        dx_smoothed = self.dx_filter.filter(dx)

        speed = float(np.linalg.norm(dx_smoothed))
        cutoff = self.min_cutoff + self.beta * speed

        alpha = self._compute_alpha(cutoff, dt)
        self.x_filter.alpha = alpha
        return self.x_filter.filter(x)


class TemporalMatrixStabilizer:
    def __init__(self, min_cutoff=1.2, beta=0.008):
        self.filter = OneEuroFilter(min_cutoff=min_cutoff, beta=beta)

    def reset(self):
        self.filter.reset()

    def update(self, affine_matrix, timestamp=None):
        smoothed = self.filter.filter(affine_matrix, timestamp=timestamp)
        return smoothed.astype(np.float64)


def stabilize_affine_matrix(affine_matrix, timestamp=None):
    """Filter affine matrix in a live stream context; no-op when stream state is inactive."""
    state = stream_matrix_state.get()
    if state is None:
        return affine_matrix
    return state.update(affine_matrix, timestamp=timestamp)
