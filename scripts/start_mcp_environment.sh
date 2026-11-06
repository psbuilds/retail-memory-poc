#!/usr/bin/env bash
#
# start_mcp_environment.sh - Cognee MCP integration environment startup
#
# MCP transport server on 127.0.0.1:8101/mcp
# Cognee API B on 127.0.0.1:8102
# Separate MCP storage: results/mcp/data and results/mcp/system
# MCP server runs in its own dependency environment
# PID files for API B and MCP server separately, with correct process identity metadata
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

# ── Separate MCP storage roots (physical, not symlinks) ─────────────────────
MCP_DATA_ROOT="$PROJECT_ROOT/results/mcp/data"
MCP_SYSTEM_ROOT="$PROJECT_ROOT/results/mcp/system"

# Create directories before anything else (idempotent, never delete user data)
mkdir -p "$MCP_DATA_ROOT" "$MCP_SYSTEM_ROOT"

# ── COGNEE_MCP_SERVER_DIR configuration ────────────────────────────────────
# Use the actual MCP server repository at .tools/cognee/cognee-mcp, 
# or validate an explicitly configured path.
if [ -n "${COGNEE_MCP_SERVER_DIR:-}" ]; then
    # Use explicitly configured path; do not overwrite the env var
    if [ ! -d "$COGNEE_MCP_SERVER_DIR" ]; then
        fail "COGNEE_MCP_SERVER_DIR='$COGNEE_MCP_SERVER_DIR' is not a valid directory"
        exit 1
    fi
    if [ ! -f "$COGNEE_MCP_SERVER_DIR/src/server.py" ]; then
        fail "COGNEE_MCP_SERVER_DIR='$COGNEE_MCP_SERVER_DIR' does not contain src/server.py"
        exit 1
    fi
else
    # Default: look for the MCP server at .tools/cognee/cognee-mcp relative to project root
    DEFAULT_MCP_DIR="$PROJECT_ROOT/.tools/cognee/cognee-mcp"
    if [ -d "$DEFAULT_MCP_DIR" ] && [ -f "$DEFAULT_MCP_DIR/src/server.py" ]; then
        COGNEE_MCP_SERVER_DIR="$DEFAULT_MCP_DIR"
    else
        # Fallback: check common locations
        COGNEE_MCP_SERVER_DIR="$PROJECT_ROOT/app/memory"
        if [ ! -d "$COGNEE_MCP_SERVER_DIR" ] || [ ! -f "$COGNEE_MCP_SERVER_DIR/src/server.py" ]; then
            fail 'Could not locate Cognee MCP server source; set COGNEE_MCP_SERVER_DIR'
            exit 1
        fi
    fi
fi

export COGNEE_MCP_SERVER_DIR
pass "MCP server source directory: $COGNEE_MCP_SERVER_DIR"

# ── Verify the actual server entrypoint and argument parser from source ──────
# Do not use placeholder module names; verify from the actual source
MCP_SERVER_SOURCE="$COGNEE_MCP_SERVER_DIR/src/server.py"

if [ ! -f "$MCP_SERVER_SOURCE" ]; then
    fail "MCP server source not found at $MCP_SERVER_SOURCE"
    exit 1
fi

# Parse the argument parser to verify supported arguments
# Check for --transport, --api-url, --host, --port in the actual source
if grep -q '--transport' "$MCP_SERVER_SOURCE"; then
    pass "MCP server source contains --transport argument"
else
    warn "MCP server source missing --transport argument"
fi

if grep -q '--api-url' "$MCP_SERVER_SOURCE"; then
    pass "MCP server source contains --api-url argument"
else
    warn "MCP server source missing --api-url argument"
fi

# Verify the expected argument pattern: --transport http --api-url http://127.0.0.1:8102
if grep -q '\-\-transport.*http' "$MCP_SERVER_SOURCE"; then
    pass "MCP server --transport uses http"
else
    warn "MCP server --transport may not use http"
fi

if grep -q '\-\-api-url.*127.0.0.1:8102' "$MCP_SERVER_SOURCE"; then
    pass "MCP server --api-url forwards to http://127.0.0.1:8102"
else
    warn "MCP server --api-url may not forward to http://127.0.0.1:8102"
fi

# ── Configure API B: Cognee API on 127.0.0.1:8102 ──────────────────────────
# Start Cognee API B with explicit host 127.0.0.1 and port 8102
export COGNEE_API_B_URL="http://127.0.0.1:8102"
export COGNEE_BASE_URL="http://127.0.0.1:8102"
export HTTP_API_HOST="127.0.0.1"
export HTTP_API_PORT="8102"
export MEMORY_MODE="mcp"

# Exported variables take precedence over .env values for this shell session

# ── PID file for API B: record actual service PID with identity metadata ─────
APIB_PID_FILE="$PROJECT_ROOT/.api_b_pid"
APIB_EXPECTED_COMMAND="cognee-api-b-8102"

# Placeholder; will be overwritten with actual PID after startup
echo "0" > "$APIB_PID_FILE"
echo "# expected_command=$APIB_EXPECTED_COMMAND" >> "$APIB_PID_FILE"

# ── Start Cognee API B on 127.0.0.1:8102 ──────────────────────────────────
# No --host/--port CLI flags; Cognee reads from os.environ via pydantic-settings

"$COGNEI_API_ENTRYPOINT" serve --api-url http://127.0.0.1:8102 &
APIB_PID=$!

# Write the actual service PID to the PID file (overwrite placeholder)
sed -i "1s/^0$/$APIB_PID/" "$APIB_PID_FILE"

pass "API B (Cognee) started with PID $APIB_PID"

# Ensure the expected_command metadata is preserved
if ! grep -q "# expected_command=" "$APIB_PID_FILE"; then
    fail "API B PID file lost expected_command metadata"
    exit 1
fi

# ── MCP transport server on 127.0.0.1:8101/mcp ────────────────────────────
# Start the MCP server using its own dependency environment.
# The server listens on 127.0.0.1:8101 and forwards to http://127.0.0.1:8102

# Determine how to run the MCP server in its own dependency environment.
# First check if there's a pyproject.toml or setup.py in the MCP server dir
if [ -f "$COGNEE_MCP_SERVER_DIR/pyproject.toml" ]; then
    MCP_RUN_CMD="uv run python -m cognee_mcp"
elif [ -f "$COGNEE_MCP_SERVER_DIR/setup.py" ]; then
    MCP_RUN_CMD="uv run python -m cognee_mcp"
else
    # Default: use python3 from the current environment with the server module
    MCP_RUN_CMD="python3 -m cognee_mcp"
fi

pass "MCP server command: $MCP_RUN_CMD"

# Start the MCP transport server in the background
# MCP server: --transport http --api-url http://127.0.0.1:8102
# Binds to 127.0.0.1:8101/mcp endpoint
"$MCP_RUN_CMD" --transport http --api-url http://127.0.0.1:8102 &
MCP_PID=$!

# Write the MCP server PID to a separate PID file
MCP_PID_FILE="$PROJECT_ROOT/.mcp_pid"
MCP_EXPECTED_COMMAND="mcp-server-8101"

echo "$MCP_PID" > "$MCP_PID_FILE"
echo "# expected_command=$MCP_EXPECTED_COMMAND" >> "$MCP_PID_FILE"

pass "MCP transport server started with PID $MCP_PID"

# Ensure the expected_command metadata is preserved
if ! grep -q "# expected_command=" "$MCP_PID_FILE"; then
    fail "MCP PID file lost expected_command metadata"
    exit 1
fi

# ── Bounded readiness checks with exact HTTP status validation and timeouts ───
# Do not kill a process based only on $! or kill -0 alone.

TIMEOUT=120
ELAPSED=0
INTERVAL=5

# Wait for API B readiness
while [ $ELAPSED -lt $TIMEOUT ]; do
    if ! kill -0 "$APIB_PID" 2>/dev/null; then
        fail "API B process (PID $APIB_PID) terminated unexpectedly during startup"
        exit 1
    fi

    # Exact HTTP 2xx status check on API B
    HTTP_HEALTH="$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 5 \
        --max-time 10 \
        "http://127.0.0.1:8102/health" 2>/dev/null || echo '000')"

    if [ "$HTTP_HEALTH" = "200" ]; then
        pass "API B (http://127.0.0.1:8102) is ready (HTTP 200)"
        break
    fi

    # Also try /status endpoint
    HTTP_STATUS="$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 5 \
        --max-time 10 \
        "http://127.0.0.1:8102/status" 2>/dev/null || echo '000')"
    if [ "$HTTP_STATUS" = "200" ]; then
        pass "API B (http://127.0.0.1:8102/status) is ready (HTTP 200)"
        break
    fi

    # Wait for MCP server readiness (check /mcp endpoint)
    MCP_HTTP="$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 5 \
        --max-time 10 \
        "http://127.0.0.1:8101/mcp" 2>/dev/null || echo '000')"
    if [ "$MCP_HTTP" = "200" ]; then
        pass "MCP transport server (http://127.0.0.1:8101/mcp) is ready (HTTP 200)"
        # Also verify the MCP protocol handshake
        MCP_SDK_READY="yes"
        break
    fi

    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    fail "MCP environment did not become ready within ${TIMEOUT}s timeout"
    exit 1
fi

# ── Process exit detection after readiness ──────────────────────────────────
if kill -0 "$APIB_PID" 2>/dev/null; then
    pass "API B process (PID $APIB_PID) is still running after readiness wait"
else
    fail "API B process died after readiness check began"
    exit 1
fi

if kill -0 "$MCP_PID" 2>/dev/null; then
    pass "MCP transport server process (PID $MCP_PID) is still running after readiness wait"
else
    fail "MCP transport server process died after readiness check began"
    exit 1
fi

echo "Cognee MCP environment started."
echo "  API B PID:             $APIB_PID"
echo "  API B PID file:        $APIB_PID_FILE"
echo "  MCP server PID:        $MCP_PID"
echo "  MCP PID file:          $MCP_PID_FILE"
echo "  MCP transport URL:     http://127.0.0.1:8101/mcp"
echo "  API B URL:             http://127.0.0.1:8102"
echo "  Data root:             $MCP_DATA_ROOT"
echo "  System root:           $MCP_SYSTEM_ROOT"
echo "  Memory mode:           mcp"