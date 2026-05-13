# File Schemas

## `run-state.json`

Recommended shape:

```json
{
  "mode": "planning",
  "current_sprint": 0,
  "retry_count": 0,
  "last_successful_sprint": 0,
  "last_failure_reason": "",
  "needs_human": false,
  "last_run_at": "2026-04-15T00:00:00+08:00"
}
```

Field guidance:

- `mode`: one of `planning`, `contract`, `implementing`, `checking`, `paused`, `complete`
- `current_sprint`: active sprint number, `0` before sprint work begins
- `retry_count`: retries for the current sprint only
- `last_successful_sprint`: most recent sprint with `SPRINT PASS`
- `last_failure_reason`: short plain-text reason for the latest blocking failure
- `needs_human`: `true` when autonomous execution should stop
- `last_run_at`: ISO 8601 timestamp with timezone

## `run-events.ndjson`

One JSON object per line.

Minimum recommended fields:

```json
{"ts":"2026-04-15T10:00:00+08:00","event":"orchestrator_started","mode":"contract","current_sprint":2}
```

Useful optional fields:

- `rule`
- `action`
- `result`
- `reason`
- `retry_count`
- `commit`
- `needs_human`

## `orchestrator-log.ndjson`

One JSON object per line describing a routing decision.

Recommended shape:

```json
{"ts":"2026-04-15T10:00:04+08:00","observed":{"has_spec":true,"has_contract":true,"has_eval_trigger":false},"rule":"contract_phase","action":"invoke_evaluator","rationale":"contract exists but not approved"}
```

Use this file for routing audit, not for every internal substep.

## `human-escalation.md`

Recommended sections:

```markdown
# Human Escalation

## Current State
- Sprint: 3
- Mode: paused

## Why It Paused
- Retry limit exceeded after repeated evaluator failures

## Files To Inspect
- `run-state.json`
- `eval-result-3.md`
- `sprint-contract.md`
- `claude-progress.txt`

## Recommended Action
- Decide whether to revise the contract or re-plan the sprint
```

Keep it short and current. Replace stale content instead of appending forever.
