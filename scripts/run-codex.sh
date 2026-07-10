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
#
# Sandbox (env overrides):
#   SPRINTFOUNDRY_CODEX_SANDBOX   workspace-write (default) | danger
#                                 "danger" restores --dangerously-bypass-…
#                                 for projects that truly need full access.
#   SPRINTFOUNDRY_CODEX_NETWORK   1 (default) | 0 — network inside the
#                                 workspace-write sandbox (installs need it).
# ─────────────────────────────────────────────────────────────────────────────
set -u

PROMPT="${1:?usage: run-codex.sh <prompt_file> <log_file> [hard_timeout_s] [idle_timeout_s]}"
LOG="${2:?usage: run-codex.sh <prompt_file> <log_file> [hard_timeout_s] [idle_timeout_s]}"
HARD_TIMEOUT="${3:-3600}"
IDLE_TIMEOUT="${4:-300}"
PROMPT_SIZE_LIMIT_BYTES="${SPRINTFOUNDRY_PROMPT_LIMIT:-16384}"
SANDBOX_MODE="${SPRINTFOUNDRY_CODEX_SANDBOX:-workspace-write}"
NETWORK_ACCESS="${SPRINTFOUNDRY_CODEX_NETWORK:-1}"

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

# ── Package-manager caches inside the workspace ──────────────────────────────
# workspace-write blocks writes to ~/.npm, ~/.cache etc.; point every common
# cache at .sprintfoundry/cache (gitignored, persists across attempts) so
# installs neither fail nor re-download each retry.
CACHE_ROOT="$PWD/.sprintfoundry/cache"
mkdir -p "$CACHE_ROOT" 2>/dev/null || true
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$CACHE_ROOT/xdg}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$CACHE_ROOT/pip}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$CACHE_ROOT/uv}"
export npm_config_cache="${npm_config_cache:-$CACHE_ROOT/npm}"

# ── Sandboxed invocation ─────────────────────────────────────────────────────
# Default: --sandbox workspace-write — reads are unrestricted (prompt files,
# contracts, archived verdicts all readable), writes are confined to the
# project + /tmp, and .git/ stays read-only (Git metadata is Orchestrator-
# owned anyway). Approval policy "never": blocked commands fail fast instead
# of stalling an unattended run; the watchdog below still bounds total time.
if [[ "$SANDBOX_MODE" == "danger" ]]; then
    CODEX_ARGS=(--dangerously-bypass-approvals-and-sandbox)
else
    CODEX_ARGS=(--sandbox workspace-write --ask-for-approval never)
    if [[ "$NETWORK_ACCESS" != "0" ]]; then
        CODEX_ARGS+=(-c 'sandbox_workspace_write.network_access=true')
    fi
fi

# Job control on: the background job below gets its OWN process group, so the
# watchdog can kill Codex together with every child it spawned (builds, test
# runners, dev servers). Killing only $PID leaves orphans holding ports/locks
# that poison the next attempt.
set -m
codex exec "${CODEX_ARGS[@]}" \
  -c 'shell_environment_policy.inherit=all' \
  --skip-git-repo-check \
  "$WRAPPER_PROMPT" >>"$LOG" 2>&1 &
PID=$!
set +m
START=$(date +%s)

kill_codex() {
    # Whole process group first (portable macOS/Linux); fall back to the
    # single PID if the group is already gone.
    kill -TERM -- "-$PID" 2>/dev/null || kill -TERM "$PID" 2>/dev/null
    sleep 5
    kill -KILL -- "-$PID" 2>/dev/null || kill -KILL "$PID" 2>/dev/null
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
