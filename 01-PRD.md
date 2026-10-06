# Product Requirements Document (PRD)
## Real-Time AI Face, Look & Voice Transformation Platform

**Version:** 0.1 (Draft)
**Status:** Pre-development
**Owner:** Ritchie / RitchieTech Solutions

---

## 1. Vision

A locally-deployable, agency-controllable real-time video/audio transformation engine that lets a user:
- Swap their face in real time during a video stream or call, to a **consented, verified** target face/avatar
- Apply a "look" layer (color grade, style, lighting) in real time
- Convert their voice in real time to a **consented, verified** target voice
- Have the system remain stable and glitch-resistant under common conditions (hand movement, partial occlusion, head turns), with graceful (not perfect) degradation under heavy occlusion
- Be sold as a usage-metered service (minutes + quality tier) to agency clients, controlled from a central admin panel

This is **not** a biometric spoofing tool. Identity/voice verification bypass is explicitly out of scope and must be structurally prevented (see §7, Non-Goals, and the Consent & Data Policy document).

---

## 2. Problem Statement

Existing tools (Higgsfield, Reface, DeepFaceLive, roop) are either:
- Cloud-only with high per-minute cost and latency, or
- Local-only with no multi-tenant billing/admin layer, or
- Lacking a built-in consent-verification gate suitable for reselling to agency clients

There is no local-first, consent-gated, billing-aware package combining face swap + style transformation + voice conversion with an admin control plane for resale.

---

## 3. Users & Personas

| Persona | Need |
|---|---|
| **Ritchie (Owner/Admin)** | Deploys the system, manages client accounts, sets quality tiers, monitors usage/billing, approves consent uploads |
| **Agency Client (End User)** | Streams/records with their own face/voice swapped to a verified alternate identity or avatar, pays per minute/quality tier |
| **Verified Target Identity Owner** | The person whose face/voice is being used as a swap target — must have given explicit, auditable consent |

---

## 4. Core Features (v1 Scope)

### 4.1 Real-time single-face swap
- Live webcam input → detect → align → swap → restore → output
- Target real-time frame rate: **≥ 24fps on an 8GB VRAM GPU** (RTX 4070/5060/5070 class)

### 4.2 Look/style layer
- LUT-based color grading (v1) — cheap, real-time, no extra VRAM pressure
- GAN-based style net (v1.1, optional/premium tier)

### 4.3 Voice conversion
- Real-time pitch/timbre conversion to a verified target voice
- Must run concurrently with video pipeline on the same 8GB VRAM budget — allocate ≤2–3GB to voice model

### 4.4 Consent-gated identity system
- No arbitrary uploaded photo/voice may be used as a swap target
- Only identities that have passed the verification + consent workflow are selectable (see Consent & Data Policy doc)

### 4.5 Usage metering & billing
- Per-minute tracking of active inference sessions
- Quality tiers (e.g., Basic / Pro / Studio) gated by subscription or minute-pack purchase

### 4.6 Admin control plane
- Client account management
- Usage/billing dashboard
- Consent record approval queue
- Quality-tier assignment per client
- System health (GPU load, session count, error rates)

---

## 5. Explicit Non-Goals (v1)

- ❌ Multi-face (2+) simultaneous swap — **v2 scope**, deferred until single-face+voice is stable
- ❌ Diffusion-based (StreamDiffusion/LCM) looks — too VRAM/latency-heavy for 8GB cards; revisit for Studio tier on 16GB+ GPUs only
- ❌ "Zero glitch under heavy occlusion" — not achievable by any known system; v1 target is *reduced* glitch frequency via temporal smoothing, not elimination
- ❌ Any feature whose primary function is to defeat biometric liveness/identity verification systems. This is a hard architectural exclusion, not a policy note.
- ❌ Public, unauthenticated self-serve signup in v1 — agency-mediated onboarding only, so every account is traceable to a known client relationship

---

## 6. Success Criteria (v1)

| Metric | Target |
|---|---|
| Frame rate, single face swap + look layer | ≥ 24fps on RTX 4070/5060/5070 (8GB) |
| Added latency, voice conversion | < 300ms round-trip |
| Glitch rate under moderate occlusion (hand partially over face) | Noticeably reduced vs. no smoothing — qualitative pass/fail per Test Plan |
| Consent verification | 100% of swap targets have an auditable consent record before being selectable |
| Billing accuracy | Usage-minutes tracked within ±5 seconds of actual session time |

---

## 7. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Misuse for impersonation/fraud | Hard consent gate (§4.4), no arbitrary target upload, audit log of every swap session + target identity used |
| VRAM exhaustion crashing sessions | Hard per-session resource budget + automatic quality-tier downgrade before OOM |
| GPU cost exceeding per-minute price | Metering must be live-tested against actual cloud GPU $/hour before pricing is finalized |
| Occlusion glitches damaging client trust | Set realistic expectations in client-facing docs; never market as "glitch-free" |

---

## 8. Milestones (3-month build, solo + Claude Code)

| Month | Deliverable |
|---|---|
| 1 | Single-face swap + look layer pipeline, stable on rented cloud GPU |
| 2 | Voice conversion integrated + consent-gating system + basic billing/metering |
| 3 | Admin dashboard, packaging/deployment, client onboarding flow, stress testing |

---

## 9. Open Questions

- Final pricing per minute, per tier (depends on validated production GPU $/hour — see Deployment Runbook)
- Hosting model for v1: client-side local deployment vs. your own hosted multi-tenant service vs. hybrid
- Payment processor choice (must tolerate the identity-transformation category — verify ToS before integrating)
