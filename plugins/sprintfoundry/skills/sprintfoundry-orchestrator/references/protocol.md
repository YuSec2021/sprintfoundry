# SprintFoundry Protocol Reference

Full artifact schemas, branching rules, unattended mode contract, and audit trail.

---

## Persistent Artifacts

State lives in files, never in conversation memory.

| File | Owner | Purpose |
|------|-------|---------|
| `.sprintfoundry/state/scope-classification.json` | Planner | Scale decision: `standard` or `large_system`, with evidence and epic outline |
| `planner-spec.json` | Planner | Source of truth — product spec and sprint list |
| `sprint-contract.md` | Generator + Evaluator | Current sprint definition of done — archived to `.sprintfoundry/archive/sprint-{N}/` and removed by Orchestrator after SPRINT PASS |
| `.sprintfoundry/results/eval/eval-result-{N}.md` | Evaluator | Per-sprint scores and critique; kept out of the project root. A PASS only counts when Orchestrator-attested (`--attest-eval N`) |
| `.sprintfoundry/signals/commit-requests/sprint-{N}.json` | Generator | Request for Orchestrator-owned commit and trigger creation |
| `.sprintfoundry/signals/eval-trigger.txt` | Orchestrator | Signal file: `sprint=N` or `sprint=N-retry` written after Orchestrator commit |
| `.sprintfoundry/results/quality/quality-gate-{N}.md` | Orchestrator | Static quality gate result before Evaluator CHECK. Only counts when Orchestrator-attested (`--attest-quality N`); an unattested report is archived and the gate re-runs |
| `.sprintfoundry/state/sprint-fence.json` | Orchestrator | Records expected sprint + base git commit + approved-contract sha before Codex starts. Mirrored into the external attestation store when written — a deleted/rewritten fence rejects the commit and pauses (fail-closed) |
| `~/.sprintfoundry/attest/<project-hash>.json` | Orchestrator | **External attestation store (outside the project root)** — HMAC records for eval verdicts, the contract approval, quality-gate reports, and the sprint fence. Unwritable from inside the default Codex workspace-write sandbox, so the Generator cannot self-certify any trust point |
| `.sprintfoundry/state/run-state.json` | Orchestrator | Unattended mode state, retry counters, pause/escalation flags |
| `.sprintfoundry/claude-progress.txt` | Generator | Cross-session handoff log (compact rolling summary) |
| `.sprintfoundry/logs/harness-audit.ndjson` | Orchestrator + hooks | **Append-only forensic timeline** — never rewritten |
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
               Orchestrator attests the approval (--attest-contract), then
               writes .sprintfoundry/state/sprint-fence.json (fence recorded
               in the external attestation store)
3. IMPLEMENT   Codex implements Sprint N ONLY → writes commit request → STOPS
               Orchestrator validates (fence integrity, fence sha, branch,
               protected paths), commits, writes .sprintfoundry/signals/eval-trigger.txt
               Quality gate runs → Orchestrator attests the report (--attest-quality N)
4. EVALUATE    Evaluator runs black-box CHECK → writes .sprintfoundry/results/eval/eval-result-N.md
               Orchestrator attests the verdict (--attest-eval N)

SPRINT PASS?
  Yes → Orchestrator archives sprint-contract.md to .sprintfoundry/archive/sprint-{N}/, then removes it plus .sprintfoundry/state/sprint-fence.json and .sprintfoundry/signals/eval-trigger.txt (and clears their attestation entries)
        → Sprint N+1 gate starts
  No  → Retry (max 2) or pause
```

An approval marker without attestation routes back to Evaluator review
(`contract_approval_unattested`) — a Generator embedding "CONTRACT APPROVED"
in its own proposal never reaches implementation. A contract modified after
its approval was attested pauses (`contract_attestation_tampered`).

**The invariant**: `sprint-contract.md` absent = previous sprint complete, next not yet contracted.
Its presence always means "sprint in progress."

---

## Completion Signal (set-based progress)

The **only** completion signal is `.sprintfoundry/results/eval/eval-result-{N}.md`
containing `SPRINT PASS` as a dedicated, line-anchored verdict line **and**
carrying a valid Orchestrator attestation (`--attest-eval N`, stored outside
the project). Quoted tokens, the unfilled `SPRINT PASS / SPRINT FAIL`
template, and unattested or subsequently modified PASS files never count —
they pause the harness as suspected Generator self-certification.

Everything else is derived state (.sprintfoundry/state/run-state.json, .sprintfoundry/claude-progress.txt, branch names).

The Orchestrator re-derives the *set* of passed sprints from eval-result files on
every invocation. It reads the hidden directory first and may read legacy
root-level `eval-result-{N}.md` files during migration. It never trusts
`.sprintfoundry/state/run-state.json` for advancement decisions.

Sprint IDs are stable identities, independent of execution order. The default
next sprint is the lowest-ID non-skipped sprint without a `SPRINT PASS`, so a
lower-ID sprint left unpassed after a higher-ID one passed stays *pending* and
is resumed later — it is never buried or renumbered. Out-of-order execution is
supported via `target_sprint` (run-state.json) or
`.sprintfoundry/signals/target-sprint.txt`. The one integrity rule that still
pauses the harness: run-state must not claim a `last_successful_sprint` that no
eval-result supports.

---

## Unattended Mode

`.sprintfoundry/state/run-state.json` is the authoritative loop state.

Pause conditions (mandatory — don't retry past these):
- Same sprint fails more than 2 times
- `init.sh` cannot restore a runnable environment
- Evaluator indicates architecture drift or contract mismatch
- Required external dependencies unavailable
- Verification tool unavailable (environment failure — do NOT increment retry_count)

When pausing:
- Set `mode="paused"`, `needs_human=true`
- Write reason to `.sprintfoundry/state/run-state.json.last_failure_reason`
- Append short summary to `.sprintfoundry/claude-progress.txt`
- Stop routing

`needs_human` lifecycle:

| Condition | Who sets it | Value |
|-----------|-------------|-------|
| Any pause condition met | Orchestrator | `true` |
| Human reviewed and chose action | Human (edits .sprintfoundry/state/run-state.json) | `false` |
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

`.sprintfoundry/state/run-state.json` tracks: `active_branch`, `base_branch`

---

## Append-Only Audit Trail (`.sprintfoundry/logs/harness-audit.ndjson`)

Never rewritten. Records:

- `orchestrator_run` — every invocation: `{rule, action, mode, needs_human, rationale}`
- `audit_finding` — every sprint history audit violation
- `state_transition` — every `.sprintfoundry/state/run-state.json` change with `{key: [old, new]}` diffs
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

## Sprint History Audit — Failure Modes Guarded Against

| Failure mode | What could happen | How the harness handles it |
|--------------|-------------------|-----------------------------|
| Manual FAIL/complete override | `chore: sprint N complete` commit rewrites `.sprintfoundry/state/run-state.json` to claim a `last_successful_sprint` no eval-result supports | Pre-commit hook rejects the advance-chore; orchestrator pauses (`run_state_unsupported`) on next routing call |
| Lower sprint left unpassed | Sprint K passes while Sprint M < K has no eval-result | Not a violation — Sprint M is pending; routing resumes at it (lowest-first), keeping its ID. Nothing is buried or renumbered |
| Silent manual override | Human edits .sprintfoundry/state/run-state.json with no audit trail | Post-commit hook records `commit_recorded` flagging .sprintfoundry/state/run-state.json as sensitive |

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
