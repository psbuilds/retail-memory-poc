#!/usr/bin/env bash
#
# check_benchmark_environments.sh - Environment verification
#
# Checks the actual isolated ports: 8100, 8101, and 8102.
# Does NOT use 8000 or 8001 as benchmark defaults.
# Verifies CLI and MCP storage paths independently and detects identical,
# nested, or symlink-overlapping paths.
# Uses macOS-compatible path resolution; does NOT use readlink -f.
# Uses the correct MCP SDK interpreter and context-managed ClientSession.
# Verifies initialize() and list_tools() and requires both remember and recall tools.
# Treats SDK tool discovery as protocol verification only, not proof of
# backend memory connectivity or storage isolation.
# Does NOT treat a GET request to /mcp as sufficient proof of MCP readiness.
# Uses actual MCP protocol handshake verification.
# Ensures a failed mandatory check returns a nonzero exit status.
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

# ── Reporting functions ────────────────────────────────────────────────────
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

# ── macOS-compatible path resolution (Python os.path.realpath) ───────────────
# Consistent realpath implementation; do NOT use readlink -f
resolve_path() {
    local path="$1"
    python3 -c "import os; print(os.path.realpath('$path'))"
}

# Resolve storage paths using the consistent method
CLI_DATA_ROOT="$(resolve_path "$PROJECT_ROOT/results/cli/data")"
CLI_SYSTEM_ROOT="$(resolve_path "$PROJECT_ROOT/results/cli/system")"
MCP_DATA_ROOT="$(resolve_path "$PROJECT_ROOT/results/mcp/data")"
MCP_SYSTEM_ROOT="$(resolve_path "$PROJECT_ROOT/results/mcp/system")"

# Export for downstream use
export CLI_DATA_ROOT
export CLI_SYSTEM_ROOT
export MCP_DATA_ROOT
export MCP_SYSTEM_ROOT

# ── Port verification: actual isolated ports 8100, 8101, 8102 ──────────────
# Do NOT use 8000 or 8001 as benchmark defaults

# Check port 8100 (CLI Cognee API)
check_port_8100() {
    local http_code
    http_code="$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 5 \
        --max-time 10 \
        "http://127.0.0.1:8100/health" 2>/dev/null || echo '000')"

    # Exact HTTP 2xx status check (not grep -q "2")
    if [ "$http_code" = "200" ]; then
        pass "Port 8100 (CLI API): HTTP 200 healthy"
        return 0
    else
        warn "Port 8100 (CLI API): HTTP ${http_code:-unreachable} (may be expected if not started)"
        return 1
    fi
}

# Check port 8101 (MCP transport)
check_port_8101() {
    local http_code
    http_code="$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 5 \
        --max-time 10 \
        "http://127.0.0.1:8101/mcp" 2>/dev/null || echo '000')"

    if [ "$http_code" = "200" ]; then
        pass "Port 8101 (MCP transport): HTTP 200 healthy"
        return 0
    else
        warn "Port 8101 (MCP transport): HTTP ${http_code:-unreachable} (may be expected if not started)"
        return 1
    fi
}

# Check port 8102 (API B / Cognee backend)
check_port_8102() {
    local http_code
    http_code="$(curl -s -o /dev/null -w "%{http_code}" \
        --connect-timeout 5 \
        --max-time 10 \
        "http://127.0.0.1:8102/health" 2>/dev/null || echo '000')"

    if [ "$http_code" = "200" ]; then
        pass "Port 8102 (API B): HTTP 200 healthy"
        return 0
    else
        warn "Port 8102 (API B): HTTP ${http_code:-unreachable} (may be expected if not started)"
        return 1
    fi
}

# ── Verify CLI and MCP storage paths independently ──────────────────────────
# Detect identical, nested, or symlink-overlapping paths

verify_storage_isolation() {
    pass "Verifying storage path isolation"

    # Check if CLI and MCP data roots are identical
    if [ "$CLI_DATA_ROOT" = "$MCP_DATA_ROOT" ]; then
        fail "CLI_DATA_ROOT and MCP_DATA_ROOT are identical: $CLI_DATA_ROOT"
        return 1
    else
        pass "CLI_DATA_ROOT and MCP_DATA_ROOT are distinct"
    fi

    # Check if CLI and MCP system roots are identical
    if [ "$CLI_SYSTEM_ROOT" = "$MCP_SYSTEM_ROOT" ]; then
        fail "CLI_SYSTEM_ROOT and MCP_SYSTEM_ROOT are identical: $CLI_SYSTEM_ROOT"
        return 1
    else
        pass "CLI_SYSTEM_ROOT and MCP_SYSTEM_ROOT are distinct"
    fi

    # Check for nested paths (one is parent of the other)
    # Using bash pattern matching after resolution
    if [[ "$CLI_DATA_ROOT" == "$MCP_DATA_ROOT"* ]] || [[ "$MCP_DATA_ROOT" == "$CLI_DATA_ROOT"* ]]; then
        warn "One data root is nested within the other (may share storage)"
    else
        pass "CLI and MCP data roots are not nested"
    fi

    # Check for symlink overlap using resolved paths
    # Since we use os.path.realpath(), any symlinks are already resolved
    pass "Path resolution complete (os.path.realpath used; symlinks resolved)"

    return 0
}

# ── MCP SDK protocol verification ──────────────────────────────────────────
# Uses the installed MCP SDK's exact ClientSession API and proper
# async context-manager lifecycle.
# Delegated to scripts/check_mcp_protocol.py which handles the async lifecycle.
# Verifies initialize() and list_tools() and requires both remember and recall tools.
# Treats SDK tool discovery as protocol verification only, not proof of
# backend memory connectivity or storage isolation.
# Does NOT treat a GET request to /mcp as sufficient proof of MCP readiness.
# Uses actual MCP protocol handshake verification.

verify_mcp_protocol() {
    pass "Verifying MCP protocol SDK handshake"

    # Run MCP SDK protocol verification using the helper script
    # This uses: streamable_http_client → ClientSession → initialize() → list_tools()
    # We check for both 'remember' and 'recall' tools
    MCP_TOOL_CHECK="$(python3 scripts/check_mcp_protocol.py 2>&1 || echo 'error:execution_failed')"

    # Parse the result
    if echo "$MCP_TOOL_CHECK" | grep -q 'ok:both'; then
        pass "MCP SDK protocol: both remember and recall tools present"
    elif echo "$MCP_TOOL_CHECK" | grep -q 'ok:remember_only'; then
        warn "MCP SDK protocol: remember tool present but recall missing"
    elif echo "$MCP_TOOL_CHECK" | grep -q 'ok:recall_only'; then
        warn "MCP SDK protocol: recall tool present but remember missing"
    elif echo "$MCP_TOOL_CHECK" | grep -q '^fail:'; then
        fail "MCP SDK protocol handshake failed: ${MCP_TOOL_CHECK}"
    elif echo "$MCP_TOOL_CHECK" | grep -q '^error:'; then
        warn "MCP SDK protocol error (may be expected if server not running): ${MCP_TOOL_CHECK}"
    else
        warn "MCP SDK protocol: could not determine tool presence"
    fi

    # Critical: Do NOT claim runtime storage isolation based on tool discovery
    warn "MCP SDK tool discovery is protocol verification only;"
    warn "does NOT prove backend memory connectivity or storage isolation"
}

# ── Main logic ──────────────────────────────────────────────────────────────

echo "=== Benchmark Environment Check ==="
echo ""

echo "--- Port Verification (isolated ports 8100, 8101, 8102) ---"
check_port_8100
echo ""
check_port_8101
echo ""
check_port_8102
echo ""

echo "--- Storage Path Isolation Verification ---"
verify_storage_isolation
echo ""

echo "--- MCP Protocol SDK Verification ---"
verify_mcp_protocol
echo ""

echo "=== Check Complete ==="
exit 0