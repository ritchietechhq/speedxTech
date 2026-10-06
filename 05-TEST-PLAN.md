# Test Plan & Acceptance Criteria
## Real-Time AI Face, Look & Voice Transformation Platform

**Version:** 0.1 (Draft)

---

## 1. Testing Philosophy

Given the realistic limits established in the PRD (no hardware/model eliminates occlusion glitches entirely), this test plan defines **measurable, honest** pass/fail criteria rather than vague goals like "works perfectly." Every criterion here should be checkable with a stopwatch, a frame counter, or a clear yes/no observation.

---

## 2. Test Environment Matrix

| Environment | Purpose |
|---|---|
| Rented cloud GPU (RunPod/Vast.ai, RTX 3090/4090) | Primary development testing — fast iteration |
| Target client-tier GPU (RTX 4070/5060/5070, 8GB) | Acceptance testing — must match what you'll actually ship on |
| Local i3 dev laptop | Negative test only — confirm the app correctly detects "no usable GPU" and fails gracefully, rather than hanging |

---

## 3. Functional Test Cases

### 3.1 Face Swap Core

| ID | Case | Expected Result |
|---|---|---|
| FS-01 | Single face, frontal, good lighting | Stable swap, ≥24fps on 8GB GPU |
| FS-02 | Head turn ±45° | Swap tracks without losing lock |
| FS-03 | Partial occlusion (hand covers half of face) | Degraded but recoverable — no permanent lock loss, glitch duration bounded (define max acceptable glitch duration, e.g., <500ms) |
| FS-04 | Full occlusion (hand fully covers face, 3 fingers scenario) | System falls back to last-good-frame or raw passthrough — does **not** crash or render a corrupted frame |
| FS-05 | No face in frame | System shows raw passthrough, no error state |
| FS-06 | Two faces in frame (v1: single-face mode) | System swaps only the primary/tracked face, does not glitch on the second face |

### 3.2 Look/Style Layer

| ID | Case | Expected Result |
|---|---|---|
| LS-01 | LUT applied at Basic tier | No measurable FPS drop vs. swap-only |
| LS-02 | GAN style applied at Pro tier | FPS stays ≥20fps on target 8GB GPU |
| LS-03 | Tier downgrade mid-session (VRAM pressure) | Automatic fallback to LUT-only, logged, client notified via WebSocket message |

### 3.3 Voice Conversion

| ID | Case | Expected Result |
|---|---|---|
| VC-01 | Voice conversion alone | Round-trip latency < 300ms |
| VC-02 | Voice + face swap concurrent, single face | Combined VRAM stays within budget (see Architecture doc §4), both run at target quality |
| VC-03 | Audio/video sync | Lip movement and converted audio stay within acceptable drift (define threshold, e.g., <100ms) over a 10-minute session |

### 3.4 Consent Gating

| ID | Case | Expected Result |
|---|---|---|
| CG-01 | Attempt to start session with unapproved identity | API returns 403, session does not start |
| CG-02 | Attempt to submit identity without live sample | Automated rejection, never reaches admin review queue |
| CG-03 | Revoke identity mid-session | Active session using it is terminated immediately, client notified |

### 3.5 Billing & Metering

| ID | Case | Expected Result |
|---|---|---|
| BM-01 | 30-minute session | Billed minutes within ±5 seconds of actual duration |
| BM-02 | Client with 0 minutes remaining | Session start blocked (409), clear error returned |
| BM-03 | Session crash mid-stream | Partial minutes still billed accurately up to crash point, not over-billed |

### 3.6 Sustained Load / Thermal

| ID | Case | Expected Result |
|---|---|---|
| TL-01 | 30-minute continuous session on target laptop GPU | No thermal-throttling-induced FPS drop below acceptable threshold |
| TL-02 | 2-hour continuous session (stress test beyond expected use) | Document actual degradation point — informs client-facing session-length guidance |

---

## 4. Performance Benchmarks to Record (Not Just Pass/Fail)

For each GPU tier you plan to support, record:
- FPS at each quality tier
- VRAM usage at each quality tier
- Voice conversion latency
- Time-to-first-frame (session startup latency)
- Glitch frequency under FS-03/FS-04 (count glitches per minute, not just "it glitched")

This data becomes your **actual marketing-safe claims** — e.g., "24fps+ on RTX 4070, 8GB" is defensible; "runs perfectly" is not.

---

## 5. Acceptance Criteria for v1 Release

v1 ships when:
- [ ] All FS-01, FS-02, FS-05 pass on target hardware (RTX 4070/5060/5070, 8GB)
- [ ] FS-03/FS-04 degrade gracefully (no crash, bounded glitch duration) — not glitch-free, graceful
- [ ] LS-01 passes with no FPS regression
- [ ] VC-01, VC-02 pass within latency/VRAM budget
- [ ] CG-01, CG-02, CG-03 all pass — consent gating has zero bypass paths found in testing
- [ ] BM-01, BM-02, BM-03 pass — billing is accurate and fails safely
- [ ] TL-01 passes on the actual laptop model you intend to deploy on first

---

## 6. Regression Testing Note

Any change to the swap/restoration/voice models (version upgrade, quality tier addition) should re-run at minimum FS-01 through FS-04 and VC-01/VC-02 before shipping — these are the cases most likely to silently regress with a model update.
