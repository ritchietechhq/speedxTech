# Face quality profiles

The session runner and Colab webcam demo now share HyperSwap configuration.
Defaults are a live profile at 256 pixels, masked Lab skin correction at strength
0.8, target-derived semantic boundaries, occlusion masking, and alpha blending.
Source identities still come from the consent store.

Set these environment variables **before starting a session**:

| Variable | Values / default |
| --- | --- |
| `SPEED_X_QUALITY_PROFILE` | `live` (256) or `offline` (512) |
| `SPEED_X_SWAP_MODEL` | `hyperswap_1a_256` (default), `hyperswap_1b_256`, `hyperswap_1c_256` |
| `SPEED_X_SKIN_MATCH` | `masked_lab` (application default), `legacy`, `off` |
| `SPEED_X_SKIN_STRENGTH` | 0 to 1; default 0.8 |
| `SPEED_X_BLEND` | `alpha` (default); offline also permits `multiband`, `poisson` |
| `SPEED_X_QUALITY_METRICS` | Optional writable JSONL file for per-face stage timings |

Standalone FaceFusion retains legacy color matching by default. For CLI offline
exports, explicitly select the model, pixel boost, and mask options using the
FaceFusion CLI; application profile selection applies to the runner/demo only.
The offline application profile selects quality settings; it does not turn the
webcam runner into a video export command. Restoration remains disabled.

Skin correction estimates bounded Lab offsets from overlapping visible skin,
rejecting extreme luminance. It preserves shading by limiting luminance changes.
No valid skin means no correction. Temporal state is local to the stream and
resets on face loss, identity changes, long gaps, and large lighting changes.
Offline worker calls are stateless. Live capture keeps one pending latest frame;
frames may be dropped to avoid accumulating latency, and swaps stay sequential.
A camera backend can still have its own buffer.

## Verification

Run `python -m pytest -q tests/test_face_quality.py` in an environment containing
NumPy, OpenCV, and pytest. Tests need neither GPU nor model downloads.

For timing comparisons, set `SPEED_X_QUALITY_METRICS=/tmp/face-quality.jsonl`,
run an approved session, then use:

```sh
python scripts/report_face_quality.py /tmp/face-quality.jsonl --warmup 5
```

The report groups settings and shows p50/p95 stage times after warmup. Timings
cover a face swap, excluding detection, transport, capture, and encoding; they
must not be interpreted as end-to-end FPS. Record deployment hardware, runtime
versions, actual execution providers, and end-to-end frame age separately.
The runner logs available/configured and loaded execution providers.

For aligned saved crops, generate an annotated comparison grid and masked skin
color diagnostics:

```sh
python scripts/compare_face_quality.py --reference target.png --mask skin.png \
  --candidates baseline.png corrected.png --output comparison.png
```

This reports CIE76, not CIEDE2000. Masks must exclude features, occlusions, and
non-skin pixels. Lower color error alone does not establish identity retention,
texture quality, good boundaries, or temporal consistency.

## Remaining deployment evaluation

Use held-out approved footage across complexions, illumination changes, profiles,
glasses, facial hair, speech, and hand occlusions. Compare legacy and masked
matching first, then model variants, resolution, and compositing independently.
Review jaw/temple/forehead seams and frame sequences, not just still images.
Measure identity similarity, motion-compensated flicker, CIEDE2000, GPU memory,
and capture-to-output latency in the deployment environment before selecting a
winning configuration. Mask inference adds cost; live 25–30 FPS is a target,
not a measured result. Parser failures and extreme pose can still cause artifacts.
No real-footage quality improvement or GPU throughput is claimed by the unit tests.

## Source complexion and bounded output

Application profiles now default `SPEED_X_TONE_REFERENCE=source`; select `target`
to retain matching to webcam complexion. Source complexion uses cached visible
skin statistics and bounded Lab offsets, preserving local shading. This is an
approximation from the photograph, not recovery of lighting-independent skin
reflectance. It does not recolor the neck or body.

Native capture waits fail after five seconds without a frame. Encoder writes
have a five-second deadline, and encoder shutdown escalates to terminate/kill
with bounded waits. These controls do not yet isolate hung GPU calls or guarantee
that a camera backend's release call returns. Automatic reconnecting output,
worker-process restart, and Colab browser-side deadlines remain unimplemented.
