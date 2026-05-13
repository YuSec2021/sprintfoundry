# Event Types

Use short, stable event names.

## Recommended events

- `orchestrator_started`
- `routing_decision`
- `planner_started`
- `planner_finished`
- `contract_requested`
- `contract_approved`
- `contract_rejected`
- `generator_started`
- `generator_committed`
- `generator_fix_committed`
- `evaluator_started`
- `evaluator_passed`
- `evaluator_failed`
- `retry_incremented`
- `run_paused`
- `run_completed`

## Example event stream

```json
{"ts":"2026-04-15T10:00:00+08:00","event":"orchestrator_started","mode":"checking","current_sprint":3}
{"ts":"2026-04-15T10:00:01+08:00","event":"routing_decision","rule":"eval-trigger-exists","action":"invoke_evaluator"}
{"ts":"2026-04-15T10:05:12+08:00","event":"evaluator_failed","current_sprint":3,"reason":"Functionality below threshold"}
{"ts":"2026-04-15T10:05:14+08:00","event":"retry_incremented","current_sprint":3,"retry_count":2}
{"ts":"2026-04-15T10:05:20+08:00","event":"run_paused","current_sprint":3,"needs_human":true,"reason":"retry limit exceeded"}
```

## Naming rules

- Use lowercase snake_case event names
- Keep names stable once adopted
- Prefer one event per state transition
- Put variable details in fields, not in the event name itself
