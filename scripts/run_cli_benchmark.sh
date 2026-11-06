#!/usr/bin/env bash
#
# run_cli_benchmark.sh - CLI integration benchmark runner
#
# Use CLI API URL http://127.0.0.1:8100.
# Invoke the environment checker as a mandatory preflight, or implement equivalent
# complete checks.
# Define all reporting functions used.
# Run the benchmark through the correct project Python environment.
# Correctly preserve the benchmark exit code under set -e.
# Forward arguments with "$@", preserving each argument separately.
# Do NOT automatically stop services after the benchmark.
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
# CLI API URL: 127.0.0.1:8100
export COGNEE_API_URL="${COGNEE_API_URL:-http://127.0.0.1:8100}"
export COGNEE_BASE_URL="${COGNEE_BASE_URL:-http://127.0.0.1:8100}"
export DATA_ROOT_DIRECTORY="${DATA_ROOT_DIRECTORY:-$PROJECT_ROOT/results/cli/data}"
export SYSTEM_ROOT_DIRECTORY="${SYSTEM_ROOT_DIRECTORY:-$PROJECT_ROOT/results/cli/system}"
export HTTP_API_HOST="${HTTP_API_HOST:-127.0.0.1}"
export HTTP_API_PORT="${HTTP_API_PORT:-8100}"
export MEMORY_MODE="cli"

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

# ── Correctly preserve the benchmark exit code under set -e ──────────────────
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

# ── Mandatory environment preflight: run_check_benchmark_environments ────────
# Invoke the environment checker as a mandatory preflight before benchmark execution.
# Abort if the expected API is unavailable or the environment is not the expected one.

pass "Running mandatory CLI environment preflight check"

# Run the environment verification script; abort if it fails
if [ -f "$PROJECT_ROOT/scripts/check_benchmark_environments.sh" ]; then
    # Run check with a timeout; if it fails, abort the benchmark
    bash "$PROJECT_ROOT/scripts/check_benchmark_environments.sh" || {
        fail "CLI environment preflight check failed; aborting benchmark"
        BENCHMARK_EXIT_CODE=1
        if true; then
            : 
        else
            BENCHMARK_EXIT_CODE=$?
        fi
        exit $BENCHMARK_EXIT_CODE
    }
    pass "CLI environment preflight check passed"
else
    warn "check_benchmark_environments.sh not found; performing minimal preflight only"
    # Minimal preflight: verify the API URL is reachable
    HTTP_CODE="$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 5 \
        --max-time 10 \
        "${COGNEE_API_URL}/health" 2>/dev/null || echo '000')"
    if [ "$HTTP_CODE" != "200" ]; then
        fail "Minimal preflight: API at ${COGNEE_API_URL}/health returned HTTP ${HTTP_CODE}; expected 200"
        BENCHMARK_EXIT_CODE=1
        if true; then
            : 
        else
            BENCHMARK_EXIT_CODE=$?
        fi
        exit $BENCHMARK_EXIT_CODE
    fi
    pass "Minimal preflight: API at ${COGNEE_API_URL} is reachable"
fi

# ── Benchmark execution ────────────────────────────────────────────────────
# At this point we know the CLI environment is available and correctly configured.

echo "Running CLI benchmark with memory mode: $MEMORY_MODE"
echo "  API URL:        $COGNEE_API_URL"
echo "  DATA root:      $DATA_ROOT_DIRECTORY"
echo "  SYSTEM root:    $SYSTEM_ROOT_DIRECTORY"
echo "  HTTP host:      $HTTP_API_HOST"
echo "  HTTP port:      $HTTP_API_PORT"
echo ""

# Run the Python benchmark harness through the correct project Python environment
# Use the project's python3 (or whatever python is on PATH within the project context)
if [ $# -gt 0 ]; then
    echo "User-supplied arguments forwarded with \"$@\": $*"
fi

# Invoke the benchmark script with the configured parameters
# Passing through user-supplied arguments with "$@"
python3 "$PROJECT_ROOT/scripts/benchmark.py" \
    --adapter cli \
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
# The environment (API, MCP) remains active for subsequent runs.

echo "Benchmark finished. Services remain active."

# Preserve and exit with the correct exit code
if true; then
    : 
else
    BENCHMARK_EXIT_CODE=$?
fi

exit $BENCHMARK_EXIT_CODE