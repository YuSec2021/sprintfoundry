---
name: harness-observability
description: Maintain observability, unattended loop state, escalation summaries, and context-compaction artifacts for the Claude + Codex sprint harness. Use when the harness needs structured run-state updates, append-only event logs, routing audit logs, human handoff summaries, or pause/retry decisions for unattended execution.
---

# Harness Observability

Use this skill when working on the Claude + Codex sprint harness and you need to:

- create or update `run-state.json`
- append `run-events.ndjson`
- append `orchestrator-log.ndjson`
- write or refresh `human-escalation.md`
- compact `claude-progress.txt`
- decide whether unattended execution should continue or pause

This skill is for harness operations, not product code.

## Inputs to read first

Read only the minimum needed:

- `planner-spec.json`
- `run-state.json` if present
- `claude-progress.txt` if present
- `sprint-contract.md` if present
- latest `eval-result-{N}.md` relevant to the current sprint

If you need field details or event names, read:

- [references/file-schemas.md](./references/file-schemas.md)
- [references/event-types.md](./references/event-types.md)

If you need starter templates, use the repository examples when available:

- `run-state.example.json`
- `run-events.example.ndjson`
- `orchestrator-log.example.ndjson`
- `human-escalation.example.md`

## Core workflow

### 1. Reconstruct current state from artifacts

Determine:

- current mode
- current sprint
- latest known pass/fail outcome
- retry count
- whether a pause condition is already met

Prefer file artifacts over chat history.

### 2. Update `run-state.json`

Keep `run-state.json` minimal and machine-readable.

Always set or refresh:

- `mode`
- `current_sprint`
- `retry_count`
- `last_successful_sprint`
- `last_failure_reason`
- `needs_human`
- `last_run_at`

If the system is blocked, set:

- `mode` to `paused`
- `needs_human` to `true`

### 3. Append structured events

Append one line per event to `run-events.ndjson`.

Use one event per meaningful transition:

- orchestration start
- routing decision
- planner start/finish
- contract requested/approved/rejected
- generator start/commit/fix
- evaluator pass/fail
- retry increment
- pause
- completion

Do not rewrite history in `run-events.ndjson`; append only.

### 4. Append routing audit entries

Append one line per orchestrator decision to `orchestrator-log.ndjson`.

Include:

- timestamp
- observed artifact state
- matched rule
- chosen action
- rationale
- result if known

### 5. Write human escalation summary when needed

When unattended mode pauses or needs human review, write `human-escalation.md`.

Keep it short:

- current sprint
- what happened
- why the system paused
- exact artifact files to inspect next
- recommended human action

### 6. Compact `claude-progress.txt`

Keep:

- one short project summary
- latest 3 sprint entries

Remove verbose history, repeated logs, and duplicated evaluator details.

## Pause rules

Pause unattended execution instead of retrying forever when:

- the same sprint has failed more than 2 times
- `init.sh` cannot restore a runnable environment
- evaluator identifies architecture drift instead of a local defect
- the sprint contract must materially change mid-sprint
- required services, secrets, or dependencies are unavailable

When pausing:

- update `run-state.json`
- append pause events
- write `human-escalation.md`
- add a brief pause note to `claude-progress.txt`

## Output quality rules

- Keep state files factual and compact
- Separate machine-readable state from human-readable summaries
- Never use chat history as the source of truth when artifact files disagree
- Do not turn `claude-progress.txt` into an audit log
- Do not silently continue after a pause condition is met

## File roles

- `run-state.json`: current machine-readable state
- `run-events.ndjson`: append-only system event stream
- `orchestrator-log.ndjson`: append-only routing audit trail
- `human-escalation.md`: current human takeover summary
- `claude-progress.txt`: compact rolling handoff only
