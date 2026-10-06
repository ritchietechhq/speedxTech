# IPC Design — Container A ↔ Container B

## Problem

facefusion (Container A) produces processed video frames at the stream
rate (target ≥ 24 fps).  voice-changer (Container B) produces converted
audio chunks with an inherent latency (~50–200 ms per chunk depending on
the RVC model and chunk size).

These two streams must be muxed into a single output with < 100 ms drift
(per test case VC-03).  Because the two containers have irreconcilable
Python environments (different CUDA versions, different onnxruntime versions),
they **cannot share in-process queues** — they must use IPC.

## Chosen Mechanism: Unix Domain Socket (SOCK_DGRAM)

A **Unix domain socket** (AF_UNIX / SOCK_DGRAM) mounted at
`/ipc/speed_x_technology_sync.sock` via the shared `speed_x_technology_ipc` Docker volume.

### Why Unix domain socket instead of TCP, shared memory, or Redis

| Option | Latency | Setup complexity | Cross-container | Verdict |
|---|---|---|---|---|
| TCP loopback | ~0.1–1 ms | Low | Yes | Works but adds TCP stack |
| **Unix domain socket** | **< 0.05 ms** | **Low** | **Yes (shared volume)** | **Chosen** |
| Shared memory (mmap) | < 0.01 ms | High (alignment, locks) | Harder in Docker | Overkill for audio chunks |
| Redis queue | ~1–5 ms | Medium (extra container) | Yes | Too many moving parts |
| Named pipe (FIFO) | < 0.1 ms | Low | Yes (shared volume) | Less structured than socket |

Unix domain sockets over a shared volume work across Docker containers
because the socket file is a filesystem object — both containers see the
same inode when they mount the same volume at the same path.

### Message protocol

Messages are small JSON objects sent as individual datagrams.  For this
latency class (audio/video sync), JSON overhead is negligible.

**Producer message (facefusion → voice-changer):**
```json
{
  "type": "frame_tick",
  "frame_index": 1042,
  "pts_ms": 43416,
  "session_id": "abc123"
}
```

**Consumer message (voice-changer → facefusion):**
```json
{
  "type": "audio_chunk_ready",
  "frame_index": 1042,
  "pts_ms": 43416,
  "chunk_bytes_b64": "<base64-encoded PCM chunk>",
  "session_id": "abc123"
}
```

The `pts_ms` (presentation timestamp in milliseconds) is the sync anchor.
The compositor in the orchestrator (Step 3) matches frame_index on both
sides and tolerates up to 100 ms drift before flagging VC-03 as at risk.

### Socket paths in both containers

Both containers receive the socket path via the `SPEED_X_TECHNOLOGY_IPC_SOCKET_PATH`
environment variable (default: `/ipc/speed_x_technology_sync.sock`).  The `speed_x_technology_ipc`
named volume is mounted at `/ipc` in both containers.

## VRAM Budget Summary

| Container | Framework | VRAM Allocation | GPU |
|---|---|---|---|
| A (facefusion) | ONNX Runtime | ~5 GB (soft limit) | GPU 0 |
| B (voice-changer) | PyTorch | ≤ 2.5 GB (hard cap via `set_per_process_memory_fraction(0.31)`) | GPU 0 |
| OS + driver overhead | — | ~0.5 GB | GPU 0 |
| **Total** | | **≤ 8 GB** | |

The PyTorch cap in Container B is enforced inside the process before any
CUDA context is created (see `docker/voice-changer/Dockerfile` VRAM cap
shim).  ONNX Runtime does not have an equivalent process-level cap, so
the facefusion budget is controlled by which models/tiers are loaded by
the session orchestrator (Step 3).
