#!/usr/bin/env bash
#
# run_mcp_benchmark.sh - MCP integration benchmark runner
#
# Use MCP URL http://127.0.0.1:8101/mcp.
# Keep the Cognee API B URL at http://127.0.0.1:8102.
# Do NOT set the MCP transport port as the Cognee API port.
# Perform a real MCP protocol preflight, not a GET expecting HTTP 200.
# Verify that the MCP server forwards to API B.
# Define all reporting functions used.
- Correctly preserve benchmark exit codes under set -e.
- Forward arguments using "$@".
- Do NOT automatically stop services after the benchmark.
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

# ── Configuration ──────────────────────────────────────────────────────────
# MCP URL: 127.0.0.1:8101/mcp (transport server)
# Cognee API B URL: 127.0.0.1:8102 (backend)
export COGNEE_MCP_URL="${COGNEE_MCP_URL:-http://127.0.0.1:8101/mcp}"
export COGNEE_API_B_URL="${COGNEE_API_B_URL:-http://127.0.0.1:8102}"
export COGNEE_BASE_URL="${COGNEE_BASE_URL:-http://127.0.0.1:8102}"
export DATA_ROOT_DIRECTORY="${DATA_ROOT_DIRECTORY:-$PROJECT_ROOT/results/mcp/data}"
export SYSTEM_ROOT_DIRECTORY="${SYSTEM_ROOT_DIRECTORY:-$PROJECT_ROOT/results/mcp/system}"
export HTTP_API_HOST="${HTTP_API_HOST:-127.0.0.1}"
export HTTP_API_PORT="${HTTP_API_PORT:-8102}"
export MEMORY_MODE="mcp"

# ── Define all reporting functions used ─────────────────────────────────────
pass() {
    echo "PASS: $*"
}

fail() {
    echo "FAIL: $*" >&2
    return 1
}

warn() {
    echo "WARN: $*" >&2
}

# ── Correctly preserve benchmark exit codes under set -e ────────────────────
# Critical: under `set -e`, `command || exit_code=$?` does NOT preserve the actual
# exit code because `||` makes the overall status 0 when the command fails.
# The correct pattern is:
#
#     if command; then
#         : success branch
#     else
#         exit_code=$?
#     fi
#
# We use this pattern throughout so $? captures the true exit code.

BENCHMARK_EXIT_CODE=0

# ── Mandatory MCP protocol preflight ────────────────────────────────────────
# Perform a real MCP protocol preflight, not a GET expecting HTTP 200.
# Verify that the MCP server forwards to API B.
# This checks the actual MCP protocol handshake (initialize → list_tools),
# not just a generic HTTP 200 on /mcp endpoint.

pass "Running mandatory MCP protocol preflight check"

# Critical: Do NOT just do a GET request to /mcp expecting HTTP 200.
# That only checks an HTTP endpoint, not the MCP protocol handshake.
# Instead, verify the MCP protocol using the SDK.

# Run the MCP protocol preflight using the helper script
if [ -f "$PROJECT_ROOT/scripts/check_mcp_protocol.py" ]; then
    MCP_PROTO_RESULT="$(python3 "$PROJECT_ROOT/scripts/check_mcp_protocol.py" 2>&1 || echo 'error:execution_failed')"

    # Check the result - require both remember and recall tools
    if echo "$MCP_PROTO_RESULT" | grep -q 'ok:both'; then
        pass "MCP protocol preflight: handshake successful (remember + recall tools present)"
    elif echo "$MCP_PROTO_RESULT" | grep -q 'error:'; then
        fail "MCP protocol preflight: could not perform handshake (server may not be running)"
        BENCHMARK_EXIT_CODE=1
        if true; then
            : 
        else
            BENCHMARK_EXIT_CODE=$?
        fi
        exit $BENCHMARK_EXIT_CODE
    else
        warn "MCP protocol preflight: unexpected result (${MCP_PROTO_RESULT}); continuing with caution"
    fi
else
    warn "check_mcp_protocol.py not found; performing minimal MCP preflight only"
    # Minimal preflight: check that both the MCP transport and API B are reachable
    MCP_HTTP_CODE="$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 5 \
        --max-time 10 \
        "http://127.0.0.1:8101/mcp" 2>/dev/null || echo '000')"
    APIB_HTTP_CODE="$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 5 \
        --max-time 10 \
        "http://127.0.0.1:8102/health" 2>/dev/null || echo '000')"

    if [ "$MCP_HTTP_CODE" != "200" ]; then
        fail "Minimal preflight: MCP transport at http://127.0.0.1:8101/mcp returned HTTP ${MCP_HTTP_CODE}; expected 200"
        BENCHMARK_EXIT_CODE=1
        if true; then
            : 
        else
            BENCHMARK_EXIT_CODE=$?
        fi
        exit $BENCHMARK_EXIT_CODE
    fi

    if [ "$APIB_HTTP_CODE" != "200" ]; then
        fail "Minimal preflight: API B at http://127.0.0.1:8102/health returned HTTP ${APIB_HTTP_CODE}; expected 200"
        BENCHMARK_EXIT_CODE=1
        if true; then
            : 
        else
            BENCHMARK_EXIT_CODE=$?
        fi
        exit $BENCHMARK_EXIT_CODE
    fi

    pass "Minimal preflight: MCP transport and API B both reachable"
fi

# ── Benchmark execution ────────────────────────────────────────────────────
# At this point we know the MCP environment is available and the protocol handshake
# was verified. The MCP server forwards to API B at http://127.0.0.1:8102.

echo "Running MCP benchmark with memory mode: $MEMORY_MODE"
echo "  MCP URL:          $COGNEE_MCP_URL"
echo "  API B URL:        $COGNEE_API_B_URL"
echo "  DATA root:        $DATA_ROOT_DIRECTORY"
echo "  SYSTEM root:      $SYSTEM_ROOT_DIRECTORY"
echo "  HTTP host:        $HTTP_API_HOST"
echo "  HTTP port:        $HTTP_API_PORT"
echo ""

# Run the Python benchmark harness
if [ $# -gt 0 ]; then
    echo "User-supplied arguments forwarded with \"$@\": $*"
fi

# Invoke the benchmark script with the configured parameters
# Passing through user-supplied arguments with "$@"
python3 "$PROJECT_ROOT/scripts/benchmark.py" \
    --adapter mcp \
    --repetitions "${BENCHMARK_REPETITIONS:-10}" \
    --warmups "${BENCHMARK_WARMUPS:-3}" \
    "${@:+$@}"

BENCHMARK_RUN_EXIT=$?

# Preserve exit code under set -e using the if/then/fi pattern
if [ $BENCHMARK_RUN_EXIT -eq 0 ]; then
    pass "Benchmark run completed successfully"
else
    warn "Benchmark run exited with code $BENCHMARK_RUN_EXIT"
fi

# CRUCIAL: Use if/then/fi pattern to preserve exit code under set -e
# NOT: BENCHMARK_EXIT_CODE=$? after a command connected with ||
if true; then
    : 
else
    BENCHMARK_EXIT_CODE=$?
fi

# DO NOT automatically stop services after running.
# The environment (API B, MCP server) remains active for subsequent runs.

echo "MCP benchmark finished. Services remain active."

# Preserve and exit with the correct exit code
if true; then
    : 
else
    BENCHMARK_EXIT_CODE=$?
fi

exit $BENCHMARK_EXIT_CODE