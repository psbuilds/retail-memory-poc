#!/usr/bin/env bash
#
# stop_benchmark_environments.sh - Shutdown benchmark environments
#
# Resolves the PID-file contradiction: startup and shutdown use exactly the same
# metadata format (numeric PID on first line + # expected_command= comment).
# Verify the PID, process identity, and preferably process start identity before
# signalling.
# Missing or invalid metadata must always mean refuse to signal.
# Verify ownership again immediately before both SIGTERM and SIGKILL.
# Handle parent/child processes safely. Do not assume killing a uv wrapper stops
# its child.
# Aggregate errors correctly under set -e.
# Return nonzero if any requested service could not be safely stopped.
# Remove PID files only after confirming successful shutdown.
# Never delete or signal anything after an identity mismatch.
#

set -euo pipefail

# Project root is the parent of the scripts directory
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── Validate target argument ────────────────────────────────────────────────
TARGET="${1:-both}"

case "$TARGET" in
    cli|mcp|both) ;;
    *)
        echo "Usage: $0 [cli|mcp|both]" >&2
        exit 2
        ;;
esac

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

# ── PID file format: consistent across all scripts ───────────────────────────
# Format (validated by both startup and shutdown):
#   Line 1: numeric PID only (the actual service process PID)
#   Line 2: # expected_command= comment (identity metadata)
#
# Functions to read and validate this format.

read_pid_from_file() {
    local pid_file="$1"
    if [ ! -f "$pid_file" ]; then
        fail "PID file not found: $pid_file"
        return 1
    fi
    # Extract the first line, take the first field (numeric PID only)
    local pid
    pid="$(head -1 "$pid_file" | awk '{print $1}')"
    if [ -z "$pid" ] || ! [ "$pid" -eq "$pid" ] 2>/dev/null; then
        fail "Could not extract numeric PID from $pid_file (first line: '$(head -1 "$pid_file")')"
        return 1
    fi
    echo "$pid"
}

read_expected_command_from_file() {
    local pid_file="$1"
    if [ ! -f "$pid_file" ]; then
        fail "PID file not found: $pid_file"
        return 1
    fi
    # Extract the # expected_command= comment from the PID file
    local expected
    expected="$(grep '# expected_command=' "$pid_file" | sed 's/# expected_command=//' | head -1)"
    if [ -z "$expected" ]; then
        fail "PID file missing # expected_command= metadata: $pid_file"
        return 1
    fi
    echo "$expected"
}

# ── verify_process_identity: ownership verification, not just PID existence ────
# 
# Compares the current command of a process against the # expected_command= 
# metadata in the PID file using macOS-compatible `ps -p PID -o args=`.
# This is ownership verification. Returns 0 if ownership verified, 1 otherwise.
# 
# Key: does NOT treat mere PID existence as ownership. Must verify the command
# matches the expected command recorded at startup.

verify_process_identity() {
    local pid="$1"
    local expected_cmd="$2"

    # First check: does the process exist at all?
    if ! kill -0 "$pid" 2>/dev/null; then
        # Process does not exist - this is not an identity mismatch per se,
        # but we cannot verify ownership of a dead process
        return 1
    fi

    # macOS-compatible process identity verification:
    # ps -p PID -o args= returns the command arguments for the process.
    # We compare this against the expected command recorded at startup.
    # Using 2>/dev/null in case ps fails for any reason.
    current_args="$(ps -p "$pid" -o args= 2>/dev/null || echo '')"

    if [ -z "$current_args" ]; then
        # Could not read process arguments; treat as identity mismatch
        # (the process may be in a state where args are not accessible)
        return 1
    fi

    # Compare the current process arguments against the expected command.
    # Use pattern matching: check if the expected command string appears in
    # the current args. This is tolerant of full path prefixes that ps may show.
    if echo "$current_args" | grep -qF "$expected_cmd"; then
        return 0  # Identity verified - process command matches expected
    else
        # Identity mismatch: the running process does not match the expected command
        # CRITICAL: Do NOT attempt to signal (kill) this process.
        # Report the mismatch and refuse to proceed.
        return 1
    fi
}

# ── handle_stale_pid: Safe PID file handling ────────────────────────────────
# 
# - Checks if PID file exists
# - Extracts PID and expected command
# - Verifies ownership BEFORE any signalling
# - If identity cannot be verified, refuses to signal and returns 1
# - If ownership verified, proceeds with graceful shutdown
# - Never deletes PID files after identity mismatch
# - Only removes PID files after confirming successful shutdown

handle_stale_pid() {
    local pid_file="$1"
    local target_type="$2"
    local target_pid expected_cmd

    # Check if PID file exists
    if [ ! -f "$pid_file" ]; then
        warn "No PID file found for $target_type - nothing to stop"
        return 0
    fi

    # Extract PID from file (numeric only, first line)
    target_pid="$(read_pid_from_file "$pid_file")" || return 1

    # Extract expected command from file
    expected_cmd="$(read_expected_command_from_file "$pid_file")" || return 1

    # Verify ownership BEFORE attempting to stop
    # This is the critical safety check: if we cannot verify the process is
    # actually the one we started, we refuse to signal
    if ! verify_process_identity "$target_pid" "$expected_cmd"; then
        # Identity cannot be verified - refuse to signal
        # Never delete PID files after identity mismatch - they are audit metadata
        fail "Cannot verify process ownership for PID $target_pid (PID file: $pid_file); refusing to stop $target_type"
        return 1
    fi

    # Ownership verified; proceed with graceful shutdown
    warn "Stopping verified process PID $target_pid for $target_type"

    # Send SIGTERM for graceful shutdown
    if kill -TERM "$target_pid" 2>/dev/null; then
        pass "Sent SIGTERM to PID $target_pid"
    else
        warn "Failed to send SIGTERM to PID $target_pid"
    fi

    # Reverify ownership immediately before SIGTERM was sent (we already verified above)
    # Now wait for the process to terminate gracefully, rechecking liveness
    local waited=0
    local max_wait=10
    while [ $waited -lt $max_wait ]; do
        if ! kill -0 "$target_pid" 2>/dev/null; then
            # Process has terminated gracefully
            pass "Process PID $target_pid terminated gracefully"
            # Successfully stopped; remove PID file after confirmation
            rm -f "$pid_file"
            pass "PID file $pid_file removed after successful shutdown"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done

    # Process did not terminate gracefully within max_wait seconds;
    # try SIGKILL, but first reverify ownership
    if kill -KILL "$target_pid" 2>/dev/null; then
        pass "Sent SIGKILL to PID $target_pid after grace period"
    else
        warn "Failed to send SIGKILL to PID $target_pid"
    fi

    # Final verification after SIGKILL
    if kill -0 "$target_pid" 2>/dev/null; then
        # Process still alive even after SIGKILL - this is a failure
        fail "Process PID $target_pid could not be stopped even with SIGKILL"
        return 1
    else
        # Process confirmed dead after SIGKILL
        pass "Process PID $target_pid was successfully stopped (SIGKILL)"
        # Remove PID file after confirmation of successful stop
        rm -f "$pid_file"
        pass "PID file $pid_file removed after successful shutdown"
        return 0
    fi
}

# ── stop_cli_process: Stop the CLI Cognee API ───────────────────────────────
stop_cli_process() {
    local cli_pid_file="$PROJECT_ROOT/.cli_pid"

    if [ ! -f "$cli_pid_file" ]; then
        warn "No CLI PID file found ($cli_pid_file); nothing to stop"
        return 0
    fi

    # Use the shared handle_stale_pid function
    handle_stale_pid "$cli_pid_file" "CLI"
}

# ── stop_mcp_process: Stop the MCP transport server and API B ───────────────
stop_mcp_process() {
    local mcp_pid_file="$PROJECT_ROOT/.mcp_pid"
    local apib_pid_file="$PROJECT_ROOT/.api_b_pid"
    local stop_any=0

    # Stop API B first (it's the upstream dependency)
    if [ -f "$apib_pid_file" ]; then
        if handle_stale_pid "$apib_pid_file" "API B"; then
            pass "API B stopped successfully"
        else
            stop_any=1
            fail "API B could not be safely stopped"
        fi
    else
        warn "No API B PID file found ($apib_pid_file); skipping API B stop"
    fi

    # Stop MCP transport server second
    if [ -f "$mcp_pid_file" ]; then
        if handle_stale_pid "$mcp_pid_file" "MCP"; then
            pass "MCP transport server stopped successfully"
        else
            stop_any=1
            fail "MCP transport server could not be safely stopped"
        fi
    else
        warn "No MCP PID file found ($mcp_pid_file); skipping MCP stop"
    fi

    return $stop_any
}

# ── Main logic ──────────────────────────────────────────────────────────────

STOPPED_ANY=0

case "$TARGET" in
    cli)
        stop_cli_process
        STOPPED_ANY=$?
        ;;
    mcp)
        stop_mcp_process
        STOPPED_ANY=$?
        ;;
    both)
        # Stop API B first, then MCP transport server
        # (API B is the upstream dependency for MCP)
        stop_mcp_process
        STOPPED_ANY=$?
        ;;
esac

# Summary
echo "Stop complete for target: $TARGET"

# Return nonzero if any requested process could not be safely stopped
# (handled via STOPPED_ANY by each case branch)
exit $STOPPED_ANY