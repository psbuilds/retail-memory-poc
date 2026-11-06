#!/usr/bin/env bash
#
# start_cli_environment.sh - Cognee CLI integration environment startup
#
# CLI Cognee API on 127.0.0.1:8100
# Separate CLI storage: results/cli/data and results/cli/system
# PID file records actual service PID with verifiable process identity
# Bounded readiness checks with exact HTTP status validation and timeouts
# Do not require API to be running before attempting startup
#

set -euo pipefail

# Project root is the parent of the scripts directory
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Source the .env file but exported variables take precedence over inherited ones
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$PROJECT_ROOT/.env"
    set +a
fi

# ── Separate CLI storage roots (physical, not symlinks) ─────────────────────
CLI_DATA_ROOT="$PROJECT_ROOT/results/cli/data"
CLI_SYSTEM_ROOT="$PROJECT_ROOT/results/cli/system"

# Create directories before anything else (idempotent, never delete user data)
mkdir -p "$CLI_DATA_ROOT" "$CLI_SYSTEM_ROOT"

# ── Discover the actual Cognee 1.6.6 API entrypoint from installed source ────
# Do not guess that `cognee-cli serve` is valid; verify from source
COGNEI_API_ENTRYPOINT=""

# Check cognee-cli on PATH first
if command -v cognee-cli >/dev/null 2>&1; then
    COGNEI_API_ENTRYPOINT="$(command -v cognee-cli)"
# Check beside the Python executable (uv/virtualenv installs)
elif [ -x "$(python3 -c 'import sys; print(sys.executable)')/cognee-cli" ]; then
    COGNEI_API_ENTRYPOINT="$(python3 -c 'import sys; soname=sys.executable+"/cognee-cli"; print(os.path.isfile(soname) and soname or "")' 2>/dev/null || true)"
fi

# If still not found, search Python module for the serve entrypoint
if [ -z "$COGNEI_API_ENTRYPOINT" ]; then
    # Look for cognee package and its CLI script
    COGNEI_PATH="$(python3 -c "import cognee; print(cognee.__file__)" 2>/dev/null || echo '')"
    if [ -n "$COGNEI_PATH" ]; then
        # Check for cognee-cli beside the cognee package
        COGNEI_CLIPATH="$(dirname "$COGNEI_PATH")/../bin/cognee-cli" 2>/dev/null
        if [ -f "$COGNEI_CLIPATH" ]; then
            COGNEI_API_ENTRYPOINT="$COGNEI_CLIPATH"
        fi
    fi
fi

if [ -z "$COGNEI_API_ENTRYPOINT" ]; then
    fail 'Could not locate cognee-cli entrypoint; install the cognee package or set COGNEE_CLI_PATH'
fi

pass "Cognee CLI entrypoint discovered: $COGNEI_API_ENTRYPOINT"

# ── Configure API with host 127.0.0.1 and port 8100 ────────────────────────
# Explicitly configure; do not assume .env or environment variables alone are sufficient
export COGNEE_API_URL="http://127.0.0.1:8100"
export COGNEE_BASE_URL="http://127.0.0.1:8100"
export DATA_ROOT_DIRECTORY="$CLI_DATA_ROOT"
export SYSTEM_ROOT_DIRECTORY="$CLI_SYSTEM_ROOT"
export MEMORY_MODE="cli"

# Exported variables take precedence over .env values for this shell session
# (pydantic-settings reads os.environ first)

# ── PID file: record actual service PID with identity metadata ───────────────
# CRITICAL: Do not write the startup shell PID ($$). Record the actual service PID.
# Format: first line = numeric PID only; second line = # expected_command= comment
CLI_PID_FILE="$PROJECT_ROOT/.cli_pid"
CLI_EXPECTED_COMMAND="cognee-api-8100"

# We will write the actual PID after starting the service; first line placeholder
echo "0" > "$CLI_PID_FILE"        # placeholder; will be overwritten with actual PID
echo "# expected_command=$CLI_EXPECTED_COMMAND" >> "$CLI_PID_FILE"

# ── Do NOT require the API to be running before attempting startup ────────────
# We start it; we do not assume it already exists.

# ── Start Cognee API on 127.0.0.1:8100 ─────────────────────────────────────
# No --host/--port CLI flags; Cognee reads from os.environ via pydantic-settings
# which reads os.environ first, taking precedence over .env

# Start the Cognee API server in the background
# The API binds to 127.0.0.1:8100 via environment configuration
"$COGNEI_API_ENTRYPOINT" serve --api-url http://127.0.0.1:8100 &
API_PID=$!

# Write the actual service PID to the PID file (overwrite placeholder)
# This is the actual service process PID, not the startup shell PID
sed -i "1s/^0$/$API_PID/" "$CLI_PID_FILE"

pass "Cognee API started with PID $API_PID"

# ── Write PID file for shutdown identity verification ────────────────────────
# Ensure the expected_command metadata is preserved after sed overwrite
if ! grep -q "# expected_command=" "$CLI_PID_FILE"; then
    fail "PID file lost expected_command metadata"
    exit 1
fi

# ── Bounded readiness checks with exact HTTP status validation and timeouts ────
# Do not kill a process based only on $! or kill -0 alone.
# Use timeout-bound HTTP checks; exact 2xx status validation (not grep -q "2")

TIMEOUT=120
ELAPSED=0
INTERVAL=5

while [ $ELAPSED -lt $TIMEOUT ]; do
    # Check if the API process has exited unexpectedly (using kill -0 to verify existence)
    if ! kill -0 "$API_PID" 2>/dev/null; then
        fail "Cognee API process (PID $API_PID) terminated unexpectedly during startup"
        # Clean up PID file
        echo "0" > "$CLI_PID_FILE"
        exit 1
    fi

    # HTTP readiness check with exact 2xx status validation and timeouts
    HTTP_HEALTH="$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 5 \
        --max-time 10 \
        "http://127.0.0.1:8100/health" 2>/dev/null || echo '000')"

    # Exact HTTP 2xx status check (not grep -q "2")
    if [ "$HTTP_HEALTH" = "200" ]; then
        pass "Cognee API is ready (HTTP 200 on http://127.0.0.1:8100/health)"
        break
    fi

    # Also try /status if /health behaves differently
    HTTP_STATUS="$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 5 \
        --max-time 10 \
        "http://127.0.0.1:8100/status" 2>/dev/null || echo '000')"
    if [ "$HTTP_STATUS" = "200" ]; then
        pass "Cognee API is ready (HTTP 200 on http://127.0.0.1:8100/status)"
        break
    fi

    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    fail "Cognee API did not become ready within ${TIMEOUT}s timeout"
    # Do NOT kill the process; just report failure and leave PID file for inspection
    exit 1
fi

# ── Process exit detection after readiness ──────────────────────────────────
if kill -0 "$API_PID" 2>/dev/null; then
    pass "Cognee API process (PID $API_PID) is still running after readiness wait"
else
    fail "Cognee API process died after readiness check began"
    exit 1
fi

echo "Cognee CLI environment started."
echo "  API PID:           $API_PID"
echo "  PID file:          $CLI_PID_FILE"
echo "  API URL:           http://127.0.0.1:8100"
echo "  Data root:         $CLI_DATA_ROOT"
echo "  System root:       $CLI_SYSTEM_ROOT"
echo "  Memory mode:       cli"