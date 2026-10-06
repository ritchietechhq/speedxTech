#!/usr/bin/env bash
# scripts/smoke_test_hardware.sh
#
# Hardware smoke test for SPEED_X_TECHNOLOGY face_swapper — cloud GPU validation.
# Runs the full stack against a real camera/video and confirms:
#   1. Session reaches status=running (not just "didn't crash")
#   2. FPS > 0 (frames are actually being processed)
#   3. CUDA was selected, not a silent CPU fallback
#
# Prerequisites: run preflight_check.py first and get all PASS.
#
# Usage:
#   chmod +x scripts/smoke_test_hardware.sh
#   ./scripts/smoke_test_hardware.sh \
#       --identity-id <approved-uuid> \
#       --ff-root     ./Modules/facefusion \
#       --db-path     ./data/consent.db \
#       [--camera-index 0] \
#       [--duration 30] \
#       [--orch-port 8080]
#
# Exit code 0 = all checks passed.  Non-zero = see FAILED lines.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
IDENTITY_ID=""
FF_ROOT="./Modules/facefusion"
DB_PATH="./data/consent.db"
CAMERA_INDEX=0
DURATION=30           # seconds to let the session run before checking
ORCH_PORT=8080
ORCH_HOST="127.0.0.1"
STREAM_MODE="udp"
LOG_DIR="/tmp/speed_x_technology_smoke_$(date +%Y%m%d_%H%M%S)"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'
pass() { echo -e "  ${GREEN}PASS${NC}  $*"; }
fail() { echo -e "  ${RED}FAIL${NC}  $*"; FAILURES=$((FAILURES+1)); }
info() { echo -e "  ${YELLOW}INFO${NC}  $*"; }
FAILURES=0

# ── Parse args ─────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --identity-id)  IDENTITY_ID="$2";  shift 2 ;;
        --ff-root)      FF_ROOT="$2";      shift 2 ;;
        --db-path)      DB_PATH="$2";      shift 2 ;;
        --camera-index) CAMERA_INDEX="$2"; shift 2 ;;
        --duration)     DURATION="$2";     shift 2 ;;
        --orch-port)    ORCH_PORT="$2";    shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [[ -z "$IDENTITY_ID" ]]; then
    echo "Usage: $0 --identity-id <uuid> [options]"
    exit 1
fi

ORCH_BASE="http://${ORCH_HOST}:${ORCH_PORT}"
mkdir -p "$LOG_DIR"
ORCH_LOG="${LOG_DIR}/orchestrator.log"
RUNNER_LOG="${LOG_DIR}/runner_combined.log"

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "  SPEED_X_TECHNOLOGY Hardware Smoke Test — face_swapper only"
echo "  GPU:     $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'unknown')"
echo "  Identity: ${IDENTITY_ID}"
echo "  Logs:    ${LOG_DIR}/"
echo "════════════════════════════════════════════════════════════════════"
echo ""

# ── Step 1: Start orchestrator ────────────────────────────────────────────────
echo "[ 1 ] Starting orchestrator (port ${ORCH_PORT})"

SPEED_X_TECHNOLOGY_FF_ROOT="$(realpath "$FF_ROOT")" \
SPEED_X_TECHNOLOGY_CONSENT_DB_PATH="$(realpath "$DB_PATH")" \
SPEED_X_TECHNOLOGY_SESSION_DIR="${LOG_DIR}/sessions" \
SPEED_X_TECHNOLOGY_CAMERA_INDEX="${CAMERA_INDEX}" \
    uvicorn speed_x_technology.orchestrator.app:app \
        --host "$ORCH_HOST" \
        --port "$ORCH_PORT" \
        --log-level info \
        > "$ORCH_LOG" 2>&1 &
ORCH_PID=$!
info "Orchestrator PID=${ORCH_PID}, log=${ORCH_LOG}"

# Wait for it to be ready
for i in $(seq 1 15); do
    sleep 1
    if curl -sf "${ORCH_BASE}/v1/sessions" > /dev/null 2>&1; then
        pass "Orchestrator up and accepting requests (${i}s)"
        break
    fi
    if [[ $i -eq 15 ]]; then
        fail "Orchestrator did not start within 15 seconds"
        echo "  Last orchestrator log:"
        tail -20 "$ORCH_LOG" | sed 's/^/    /'
        kill "$ORCH_PID" 2>/dev/null || true
        exit 1
    fi
done

# ── Step 2: Start session ─────────────────────────────────────────────────────
echo ""
echo "[ 2 ] Starting streaming session"

START_RESP=$(curl -sf -X POST "${ORCH_BASE}/v1/sessions" \
    -H "Content-Type: application/json" \
    -d "{\"identity_id\": \"${IDENTITY_ID}\", \"stream_mode\": \"${STREAM_MODE}\"}")

echo "  Response: ${START_RESP}"
SESSION_ID=$(echo "$START_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])" 2>/dev/null || true)

if [[ -z "$SESSION_ID" ]]; then
    fail "Could not extract session_id from start response"
    kill "$ORCH_PID" 2>/dev/null || true
    exit 1
fi
pass "Session started: session_id=${SESSION_ID}"

# ── Step 3: Wait for running state ────────────────────────────────────────────
echo ""
echo "[ 3 ] Polling for status=running (up to 60s for model load)"

STATUS="starting"
for i in $(seq 1 60); do
    sleep 1
    STATUS_RESP=$(curl -sf "${ORCH_BASE}/v1/sessions/${SESSION_ID}" 2>/dev/null || echo '{}')
    STATUS=$(echo "$STATUS_RESP" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('status','unknown'))" 2>/dev/null || echo "unknown")
    FPS=$(echo "$STATUS_RESP" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('fps') or 0)" 2>/dev/null || echo "0")

    echo "  [${i}s] status=${STATUS}  fps=${FPS}"

    if [[ "$STATUS" == "running" ]]; then
        break
    fi
    if [[ "$STATUS" == failed_* ]] || [[ "$STATUS" == "killed" ]]; then
        DETAIL=$(echo "$STATUS_RESP" | python3 -c \
            "import sys,json; d=json.load(sys.stdin); print(d.get('failure_detail',''))" 2>/dev/null || echo "")
        fail "Session entered failure state: status=${STATUS}, detail=${DETAIL}"
        break
    fi
done

if [[ "$STATUS" == "running" ]]; then
    pass "Session is running (reached status=running within ${i}s)"
else
    fail "Session never reached running state (final status=${STATUS})"
fi

# ── Step 4: Confirm FPS > 0 ───────────────────────────────────────────────────
echo ""
echo "[ 4 ] Confirming FPS > 0 (actual frames processed)"

# Let it run for DURATION seconds to accumulate a stable FPS reading
info "Letting session run for ${DURATION}s..."
sleep "$DURATION"

STATUS_RESP=$(curl -sf "${ORCH_BASE}/v1/sessions/${SESSION_ID}" 2>/dev/null || echo '{}')
STATUS=$(echo "$STATUS_RESP" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('status','unknown'))" 2>/dev/null)
FPS=$(echo "$STATUS_RESP" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); v=d.get('fps'); print(v if v is not None else 0)" 2>/dev/null)
FRAMES=$(echo "$STATUS_RESP" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('frames_total',0))" 2>/dev/null)

echo "  Final status=${STATUS}  fps=${FPS}  frames_total=${FRAMES}"

if python3 -c "import sys; sys.exit(0 if float('${FPS}') > 0 else 1)" 2>/dev/null; then
    pass "FPS=${FPS} (frames_total=${FRAMES}) — inference loop is running"
else
    fail "FPS=${FPS} — no frames processed. Check camera input and model load."
fi

# ── Step 5: Grep runner logs for CUDA confirmation ────────────────────────────
echo ""
echo "[ 5 ] Checking CUDA EP confirmation in runner output"

# The runner logs to the orchestrator's combined log (captured via Popen)
# Also check the session sidecar directory for any stderr captured there
RUNNER_LOG_COMBINED="${LOG_DIR}/sessions/${SESSION_ID}.log"

# Give the orchestrator log a moment to flush
sleep 1

# Check orchestrator log (runner stdout is captured there)
if grep -q "SPEED_X_TECHNOLOGY-EP" "$ORCH_LOG" 2>/dev/null; then
    info "Found [SPEED_X_TECHNOLOGY-EP] lines in orchestrator log:"
    grep "SPEED_X_TECHNOLOGY-EP" "$ORCH_LOG" | sed 's/^/    /'
else
    # Runner stdout goes to Popen's pipe — check if it was redirected to sidecar dir
    info "No [SPEED_X_TECHNOLOGY-EP] in orchestrator log — checking session dir"
    find "${LOG_DIR}/sessions/" -name "*.log" -exec grep -l "SPEED_X_TECHNOLOGY-EP" {} \; 2>/dev/null | \
        xargs grep "SPEED_X_TECHNOLOGY-EP" 2>/dev/null | sed 's/^/    /' || true
fi

# The definitive check: SPEED_X_TECHNOLOGY-EP-LIVE CONFIRMED line
CUDA_CONFIRMED=false
if grep -rq "SPEED_X_TECHNOLOGY-EP-LIVE.*CONFIRMED" "${LOG_DIR}/" 2>/dev/null; then
    CUDA_CONFIRMED=true
fi

# Also check orchestrator stdout which captures runner subprocess output
if grep -q "SPEED_X_TECHNOLOGY-EP-LIVE.*CONFIRMED" "$ORCH_LOG" 2>/dev/null; then
    CUDA_CONFIRMED=true
fi

if $CUDA_CONFIRMED; then
    CUDA_LINE=$(grep -r "SPEED_X_TECHNOLOGY-EP-LIVE.*CONFIRMED" "${LOG_DIR}/" "$ORCH_LOG" 2>/dev/null | head -1)
    pass "CUDA confirmed active:  ${CUDA_LINE}"
else
    # Check if we got a WARNING instead
    if grep -rq "SPEED_X_TECHNOLOGY-EP-LIVE.*WARNING" "${LOG_DIR}/" "$ORCH_LOG" 2>/dev/null; then
        fail "CUDA WARNING found — sessions fell back to CPU. See logs:"
        grep -r "SPEED_X_TECHNOLOGY-EP-LIVE" "${LOG_DIR}/" "$ORCH_LOG" 2>/dev/null | sed 's/^/    /'
    else
        fail "No [SPEED_X_TECHNOLOGY-EP-LIVE] lines found — runner may not have reached first-frame. Check:"
        echo "    Orchestrator log: ${ORCH_LOG}"
        echo "    Session dir:      ${LOG_DIR}/sessions/"
        echo ""
        echo "  Last 30 lines of orchestrator log:"
        tail -30 "$ORCH_LOG" | sed 's/^/    /'
    fi
fi

# ── Step 6: Stop session cleanly ──────────────────────────────────────────────
echo ""
echo "[ 6 ] Stopping session cleanly"

STOP_RESP=$(curl -sf -X DELETE "${ORCH_BASE}/v1/sessions/${SESSION_ID}" -o /dev/null -w "%{http_code}")
if [[ "$STOP_RESP" == "204" ]]; then
    pass "DELETE returned 204 — SIGTERM sent"
else
    fail "DELETE returned ${STOP_RESP} (expected 204)"
fi

# Confirm status transitions to stopped/killed
sleep 3
FINAL_STATUS=$(curl -sf "${ORCH_BASE}/v1/sessions/${SESSION_ID}" 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','unknown'))" 2>/dev/null)
if [[ "$FINAL_STATUS" == "stopped" ]] || [[ "$FINAL_STATUS" == "killed" ]]; then
    pass "Session final status=${FINAL_STATUS} (clean teardown)"
else
    fail "Session final status=${FINAL_STATUS} (expected stopped or killed)"
fi

# ── Cleanup ────────────────────────────────────────────────────────────────────
echo ""
echo "[ 7 ] Stopping orchestrator"
kill "$ORCH_PID" 2>/dev/null || true
wait "$ORCH_PID" 2>/dev/null || true
pass "Orchestrator stopped"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════════"
if [[ $FAILURES -eq 0 ]]; then
    echo -e "  ${GREEN}ALL CHECKS PASSED${NC} — face_swapper on CUDA confirmed."
    echo "  Logs preserved at: ${LOG_DIR}/"
    echo "════════════════════════════════════════════════════════════════════"
    echo ""
    exit 0
else
    echo -e "  ${RED}${FAILURES} CHECK(S) FAILED${NC} — review above and check logs:"
    echo "  ${LOG_DIR}/"
    echo "════════════════════════════════════════════════════════════════════"
    echo ""
    exit $FAILURES
fi
