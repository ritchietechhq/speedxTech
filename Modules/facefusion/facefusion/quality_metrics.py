"""Opt-in per-face timings. No imagery or identity embeddings are recorded."""
import json
import os


def record_timings(stages, settings):
    destination = os.environ.get('SPEED_X_QUALITY_METRICS')
    if not destination:
        return
    payload = json.dumps({'stages_ms': stages, 'settings': settings}) + '\n'
    # A single append write keeps records together across offline workers.
    descriptor = os.open(destination, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, payload.encode())
    finally:
        os.close(descriptor)
