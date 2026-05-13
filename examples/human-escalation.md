# Human Escalation

## Current State

- Mode: paused
- Sprint: 3
- Retry count: 3
- Last successful sprint: 2

## Why It Paused

- Sprint 3 exceeded the unattended retry limit.
- The latest evaluator result indicates architecture drift rather than a local defect.

## Files To Inspect

- `run-state.json`
- `eval-result-3.md`
- `sprint-contract.md`
- `claude-progress.txt`
- `orchestrator-log.ndjson`

## Required Human Decision

**Choose exactly one action before resuming. The system will not proceed until
`run-state.json` reflects a deliberate choice.**

| Option | Action required |
|--------|----------------|
| **RETRY** | Reset `retry_count` to 0 in `run-state.json`, set `mode` to `"checking"`. Resume unattended loop. Use only if you believe the last fix attempt addressed the root cause. |
| **REPLAN** | Revise `sprint-contract.md` or `planner-spec.json` to reflect new scope. Delete `eval-trigger.txt`. Set `mode` to `"contract"` and `retry_count` to 0. |
| **SKIP** | Mark sprint as skipped: add `"skipped": true` to the sprint entry in `planner-spec.json`. Set `mode` to `"planning"` and `retry_count` to 0. |
| **ABANDON** | Halt the project. Set `mode` to `"paused"`, `needs_human` to `true`, add a note explaining why. |

After choosing, set `needs_human` to `false` in `run-state.json` to allow the
next unattended run to proceed.
