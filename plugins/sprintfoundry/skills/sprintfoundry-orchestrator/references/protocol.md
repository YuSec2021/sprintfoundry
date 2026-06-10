# SprintFoundry Protocol Reference

Full artifact schemas, branching rules, unattended mode contract, and audit trail.

---

## Persistent Artifacts

State lives in files, never in conversation memory.

| File | Owner | Purpose |
|------|-------|---------|
| `.sprintfoundry/scope-classification.json` | Planner | Scale decision: `standard` or `large_system`, with evidence and epic outline |
| `planner-spec.json` | Planner | Source of truth — product spec and sprint list |
| `sprint-contract.md` | Generator + Evaluator | Current sprint definition of done — deleted by Orchestrator after SPRINT PASS |
| `.sprintfoundry/eval-results/eval-result-{N}.md` | Evaluator | Per-sprint scores and critique; kept out of the project root |
| `.sprintfoundry/commit-requests/sprint-{N}.json` | Generator | Request for Orchestrator-owned commit and trigger creation |
| `.sprintfoundry/eval-trigger.txt` | Orchestrator | Signal file: `sprint=N` or `sprint=N-retry` written after Orchestrator commit |
| `.sprintfoundry/quality-gates/quality-gate-{N}.md` | Orchestrator | Static quality gate result before Evaluator CHECK |
| `.sprintfoundry/sprint-fence.json` | Orchestrator | Records expected sprint + base git commit before Codex starts |
| `.sprintfoundry/run-state.json` | Orchestrator | Unattended mode state, retry counters, pause/escalation flags |
| `.sprintfoundry/claude-progress.txt` | Generator | Cross-session handoff log (compact rolling summary) |
| `.sprintfoundry/harness-audit.ndjson` | Orchestrator + hooks | **Append-only forensic timeline** — never rewritten |
| `init.sh` | Planner | Reproducible dev server startup |
| `bug-report.md` | User | Regression/defect intake — creates tightly scoped bugfix sprints |
| `change-request.md` | User | Post-launch iteration: bugfix / minor_feature / major_feature / replan |
| `human-escalation.md` | Orchestrator | Human-readable pause summary when needs_human=true |

After initial planning, all new work is classified before Generator sees it:
- `bug-report.md` for defects/regressions
- `change-request.md` for product iterations (with `Type:` field)
- Never send work straight to Generator without one of these artifacts

---

## Sprint Gate (four phases, no skipping)

```
1. CONTRACT    Generator proposes sprint-contract.md
2. APPROVAL    Evaluator writes "CONTRACT APPROVED"
               Orchestrator writes .sprintfoundry/sprint-fence.json
3. IMPLEMENT   Codex implements Sprint N ONLY → writes commit request → STOPS
               Orchestrator commits and writes .sprintfoundry/eval-trigger.txt
4. EVALUATE    Evaluator runs black-box CHECK → writes .sprintfoundry/eval-results/eval-result-N.md

SPRINT PASS?
  Yes → Orchestrator deletes sprint-contract.md, .sprintfoundry/sprint-fence.json, .sprintfoundry/eval-trigger.txt
        → Sprint N+1 gate starts
  No  → Retry (max 2) or pause
```

**The invariant**: `sprint-contract.md` absent = previous sprint complete, next not yet contracted.
Its presence always means "sprint in progress."

---

## Monotonic-PASS Invariant

The **only** completion signal is `.sprintfoundry/eval-results/eval-result-{N}.md`
containing the literal string `SPRINT PASS`.

Everything else is derived state (.sprintfoundry/run-state.json, .sprintfoundry/claude-progress.txt, branch names).

The Orchestrator re-derives which sprints passed from eval-result files on every
invocation. It reads the hidden directory first and may read legacy root-level
`eval-result-{N}.md` files during migration. It never trusts `.sprintfoundry/run-state.json`
for advancement decisions.

---

## Unattended Mode

`.sprintfoundry/run-state.json` is the authoritative loop state.

Pause conditions (mandatory — don't retry past these):
- Same sprint fails more than 2 times
- `init.sh` cannot restore a runnable environment
- Evaluator indicates architecture drift or contract mismatch
- Required external dependencies unavailable
- Verification tool unavailable (environment failure — do NOT increment retry_count)

When pausing:
- Set `mode="paused"`, `needs_human=true`
- Write reason to `.sprintfoundry/run-state.json.last_failure_reason`
- Append short summary to `.sprintfoundry/claude-progress.txt`
- Stop routing

`needs_human` lifecycle:

| Condition | Who sets it | Value |
|-----------|-------------|-------|
| Any pause condition met | Orchestrator | `true` |
| Human reviewed and chose action | Human (edits .sprintfoundry/run-state.json) | `false` |
| All sprints complete (Rule 7) | Orchestrator | `false` |
| Sprint PASS, next sprint | Orchestrator | remains `false` |

**Orchestrator must never automatically reset `needs_human` from `true` to `false`.**

---

## Git Branching Rules

One branch per sprint. Naming: `codex/sprint-<N>-<short-slug>` (fallback: `codex/sprint-<N>`).

- Create fresh branch before implementation begins each sprint
- Contract drafting may happen on any branch; Orchestrator implementation commits must be on the sprint branch
- Retries for a failed sprint stay on the same sprint branch
- New sprint always gets a new branch — never reuse the previous sprint branch
- Merge into `main` only after `SPRINT PASS`
- If sprint abandoned: keep branch for audit, do not reuse for a different sprint

`.sprintfoundry/run-state.json` tracks: `active_branch`, `base_branch`

---

## Append-Only Audit Trail (`.sprintfoundry/harness-audit.ndjson`)

Never rewritten. Records:

- `orchestrator_run` — every invocation: `{rule, action, mode, needs_human, rationale}`
- `audit_finding` — every sprint history audit violation
- `state_transition` — every `.sprintfoundry/run-state.json` change with `{key: [old, new]}` diffs
- `eval_result_observed` — snapshot of every eval verdict seen on each orchestrator run
- `commit_recorded` — from `.githooks/post-commit` (sha, author, subject, files, sensitive paths)
- `commit_blocked` — pre-commit rejection
- `commit_bypassed` — any use of `HARNESS_BYPASS=1 git commit`
- `note` — human annotation via `scripts/harness-log.py note --text "..."`

Useful commands:
```bash
python3 scripts/harness-log.py tail -n 30
python3 scripts/harness-log.py filter --event audit_finding
python3 scripts/harness-log.py filter --sprint 3 --json
python3 scripts/harness-log.py verify
python3 scripts/harness-log.py note --text "reason for manual action"
```

---

## Sprint History Audit — Historical Failure Modes Prevented

| Failure mode | What used to happen | How the invariant blocks it |
|--------------|---------------------|-----------------------------|
| Bootstrap bypass | Codex writes Sprint 1 code + spec in one commit, skipping contract/eval-trigger | Audit fires: ".sprintfoundry/eval-results/eval-result-1.md is missing but Sprint ≥ 2 in progress" |
| Manual FAIL override | `chore: sprint N complete` commit rewrites `.sprintfoundry/run-state.json` while eval-result still says SPRINT FAIL | Pre-commit hook rejects; orchestrator pauses on next routing call |
| Non-contiguous PASS | Sprint K marked PASS while Sprint M < K has no eval-result | Audit flags `evaluator_skipped`/`fail_bypassed` for every gap |
| Silent manual override | Human edits .sprintfoundry/run-state.json with no audit trail | Post-commit hook records `commit_recorded` flagging .sprintfoundry/run-state.json as sensitive |

---

## Test Layer Separation

| Layer | Owner | Runner | Scope |
|-------|-------|--------|-------|
| Unit tests | Generator | `uv run --python <project-python-version> --with pytest pytest -q` | Functions, components, logic |
| Black-box checks | Evaluator | verification.mode-specific | Full external behaviour |

Failure attribution:
- Unit pass + E2E fail → environment/integration issue (diagnose `init.sh` first)
- Unit fail → Generator fixes before requesting commit (never signal Evaluator)
- E2E fails repeatedly after code fixes → architecture drift candidate

---

## change-request.md Format

```markdown
# Change Request

Type: bugfix | minor_feature | major_feature | replan

## Description
...

## Motivation
...
```

## bug-report.md Format

```markdown
# Bug Report

## Summary
...

## Steps to Reproduce
...

## Expected vs Actual
...
```
