"""Bounded writes and shutdown for encoder subprocesses (POSIX)."""
import os
import selectors
import subprocess
import time


def write_frame(process, payload, timeout=5.0):
    if process.stdin is None or process.poll() is not None:
        raise BrokenPipeError('Encoder is not running')
    descriptor = process.stdin.fileno()
    os.set_blocking(descriptor, False)
    deadline = time.monotonic() + timeout
    view = memoryview(payload)
    with selectors.DefaultSelector() as selector:
        selector.register(descriptor, selectors.EVENT_WRITE)
        while view:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError('Encoder made no completed-frame progress within deadline')
            try:
                count = os.write(descriptor, view)
            except BlockingIOError:
                continue
            if count == 0:
                raise BrokenPipeError('Encoder pipe closed')
            view = view[count:]


def close_encoder(process):
    if process.stdin:
        process.stdin.close()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
