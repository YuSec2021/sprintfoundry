#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/run-codex.sh — watchdog wrapper for every Codex Generator invocation.
#
# Solves the "Codex hangs forever" failure class:
#   1. Prompt-size fuse   : refuses prompt files above a hard byte limit
#                           (oversized prompts are the main hang trigger).
#   2. Hard timeout       : kills Codex after HARD_TIMEOUT seconds.
#   3. Idle heartbeat     : kills Codex when its log has been silent for
#                           IDLE_TIMEOUT seconds (stall ≠ long task).
#   4. Log capture        : full stdout/stderr saved for post-mortem.
#
# Usage:
#   bash scripts/run-codex.sh <prompt_file> <log_file> [hard_timeout_s] [idle_timeout_s]
#
# Exit codes:
#   0    Codex completed normally
#   90   prompt file missing
#   91   prompt file exceeds size fuse
#   124  hard timeout hit (killed)
#   125  idle/stall timeout hit (killed)
#   *    Codex's own exit code otherwise
#
# Orchestrator policy on 124/125: retry the SAME prompt once (most stalls are
# transient network/service issues), then pause with needs_human=true and
# attach the log tail to last_failure_reason.
# ─────────────────────────────────────────────────────────────────────────────
set -u

PROMPT="${1:?usage: run-codex.sh <prompt_file> <log_file> [hard_timeout_s] [idle_timeout_s]}"
LOG="${2:?usage: run-codex.sh <prompt_file> <log_file> [hard_timeout_s] [idle_timeout_s]}"
HARD_TIMEOUT="${3:-3600}"
IDLE_TIMEOUT="${4:-300}"
PROMPT_SIZE_LIMIT_BYTES="${SPRINTFOUNDRY_PROMPT_LIMIT:-16384}"

# ── Prompt-size fuse ─────────────────────────────────────────────────────────
if [[ ! -f "$PROMPT" ]]; then
    echo "run-codex: prompt file not found: $PROMPT" >&2
    exit 90
fi
PROMPT_BYTES=$(wc -c < "$PROMPT" | tr -d ' ')
if (( PROMPT_BYTES > PROMPT_SIZE_LIMIT_BYTES )); then
    echo "run-codex: prompt file is ${PROMPT_BYTES}B > fuse ${PROMPT_SIZE_LIMIT_BYTES}B." >&2
    echo "           Digest the content and reference artifact files by path instead." >&2
    exit 91
fi

mkdir -p "$(dirname "$LOG")"
: > "$LOG"

# Portable file-mtime (GNU stat vs BSD/macOS stat).
mtime_of() {
    stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || date +%s
}

WRAPPER_PROMPT="Read the local SprintFoundry prompt file at ${PROMPT} and follow it exactly. The file content is the authoritative prompt for this Codex run."

codex exec --sandbox workspace-write \
  -c 'sandbox_permissions=["disk-full-read-access"]' \
  -c 'shell_environment_policy.inherit=all' \
  --skip-git-repo-check \
  "$WRAPPER_PROMPT" >>"$LOG" 2>&1 &
PID=$!
START=$(date +%s)

kill_codex() {
    kill -TERM "$PID" 2>/dev/null
    sleep 5
    kill -KILL "$PID" 2>/dev/null
}

while kill -0 "$PID" 2>/dev/null; do
    sleep 15
    NOW=$(date +%s)
    if (( NOW - START > HARD_TIMEOUT )); then
        kill_codex
        echo "CODEX_TIMEOUT hard=${HARD_TIMEOUT}s prompt=${PROMPT}" >>"$LOG"
        echo "run-codex: hard timeout after ${HARD_TIMEOUT}s — killed. See $LOG" >&2
        exit 124
    fi
    MTIME=$(mtime_of "$LOG")
    if (( NOW - MTIME > IDLE_TIMEOUT )); then
        kill_codex
        echo "CODEX_STALLED idle=${IDLE_TIMEOUT}s prompt=${PROMPT}" >>"$LOG"
        echo "run-codex: no output for ${IDLE_TIMEOUT}s — killed as stalled. See $LOG" >&2
        exit 125
    fi
done

wait "$PID"
exit $?
