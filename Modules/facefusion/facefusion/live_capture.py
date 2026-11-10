"""Single capture producer with a one-frame latest-image buffer."""
import time
from queue import Queue, Empty, Full
from threading import Event, Thread


def latest_frames(camera, timeout=5.0):
    frames = Queue(maxsize=1)
    stopped = Event()
    errors = []

    def capture():
        try:
            while not stopped.is_set() and camera.isOpened():
                ok, frame = camera.read()
                if not ok:
                    break
                try:
                    frames.put_nowait(frame)
                except Full:
                    try:
                        frames.get_nowait()
                    except Empty:
                        pass
                    try:
                        frames.put_nowait(frame)
                    except Full:
                        pass
        except Exception as error:
            errors.append(error)
        finally:
            stopped.set()

    worker = Thread(target=capture, name='speedx-capture', daemon=True)
    worker.start()
    last_frame = time.monotonic()
    try:
        while not stopped.is_set() or not frames.empty():
            try:
                frame = frames.get(timeout=0.1)
                last_frame = time.monotonic()
                yield frame
            except Empty:
                if time.monotonic() - last_frame > timeout:
                    raise TimeoutError("Camera capture stopped responding")
                continue
        if errors:
            raise errors[0]
    finally:
        stopped.set()
        camera.release()
        worker.join(timeout=1)
