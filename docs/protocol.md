# SprintFoundry Full Protocol Reference

This file is a cold-storage reference for the longer protocol text that used
to live in `AGENTS.md`. It is intentionally not the hot-path agent prompt.

Use:

- `AGENTS.md` for Codex's compact operating contract.
- `CLAUDE.md` for Claude Code's compact routing guide.
- `.claude/agents/*.md` for role-specific instructions.

---

# Historical Full AGENTS.md

> GAN-inspired three-agent harness. **Code implementation is delegated to Codex CLI.**
> Planner and Evaluator run in Claude Code. Generator runs in Codex.
> This file is read natively by Codex — the Generator section is Codex's instruction set.

---

## Agent Responsibilities

| Agent | Tool | Role |
|-------|------|------|
| Planner | Claude Code | Turns user prompt into `planner-spec.json`. Runs once per project. |
| Generator | **Codex CLI** | Reads spec + approved contract, implements one sprint, commits. |
| Evaluator | Claude Code | Contract review + independent black-box CHECK via browser, API, CLI, job, or library verification. |
| Orchestrator | Claude Code | Routes between agents. Never writes code or evaluates. |

---

## Architecture

```
User prompt (1–4 sentences)
    │
    ▼
┌─────────┐   planner-spec.json    ┌─────────────────────────────────────┐
│ Planner │ ──────────────────────▶│          Sprint Loop (N times)      │
│ (Claude)│                        │                                     │
└─────────┘                        │   sprint-contract.md negotiation    │
                                   │         ┌───────────┐               │
                                   │         │ Generator │               │
                                   │         │  (Codex)  │               │
                                   │         └─────┬─────┘               │
                                   │               │ code + commit        │
                                   │         ┌─────▼──────┐              │
                                   │         │  Evaluator │ ◀── verify   │
                                   │         │  (Claude)  │               │
                                   │         └─────┬──────┘              │
                                   │               │ PASS / FAIL+critique │
                                   │         ◀─────┘                     │
                                   └─────────────────────────────────────┘
```

**The gate rule**: Generator never marks a sprint complete. Only Evaluator writes SPRINT PASS.

---

## Persistent Artifacts

State lives in files, never in conversation memory.

| File | Owner | Purpose |
|------|-------|---------|
| `planner-spec.json` | Planner | Source of truth — product spec and sprint list |
| `change-request.md` | User + Orchestrator | Classifies post-launch work as `bugfix`, `minor_feature`, `major_feature`, or `replan` |
| `bug-report.md` | User + Orchestrator | Dedicated regression/defect intake used to create tightly scoped bugfix sprints |
| `claude-progress.txt` | Generator | Cross-session handoff log |
| `sprint-contract.md` | Generator + Evaluator | Current sprint definition of done — **deleted by Orchestrator after SPRINT PASS** |
| `.sprintfoundry/eval-results/eval-result-{N}.md` | Evaluator | Per-sprint scores and critique |
| `eval-trigger.txt` | Generator | Signal file: `sprint=N` written after commit — **must match the fenced sprint** |
| `sprint-fence.json` | Orchestrator | Written before Codex starts implementing; records expected sprint + base git commit. Any eval-trigger.txt that names a different sprint triggers an immediate boundary-violation pause. |
| `run-state.json` | Orchestrator | Unattended mode state, retry counters, pause/escalation flags — **cache, not truth** |
| `harness-audit.ndjson` | Orchestrator + git hooks + humans | **Append-only forensic timeline**: every orchestrator run, audit finding, state transition, commit, hook block/bypass, and human note. Never rewritten. See "Append-only audit trail" below. |
| `init.sh` | Planner | Reproducible dev server startup |
| `git history` | Generator | State recovery and audit trail |

After the initial plan exists, all new work must be classified before Generator sees it:

- use `bug-report.md` for a defect or regression
- use `change-request.md` for a product iteration request
- classify `change-request.md` as one of:
  - `bugfix`
  - `minor_feature`
  - `major_feature`
  - `replan`
- never send a bugfix or iteration request straight to Generator without first creating one of these artifacts

---

## Unattended Mode

This harness may run in an unattended loop, but only as a bounded, pauseable system.
The goal is hands-off progress with explicit stop conditions, not infinite autonomous iteration.

### Principles

- Unattended mode must always be resumable from files alone.
- Unattended mode must have explicit retry limits.
- Unattended mode must pause on repeated failure, architecture drift, or environment instability.
- Unattended mode must leave a clear machine-readable state for the next run.

### Ownership of run-state.json

`run-state.json` is owned exclusively by the Orchestrator.

- The Orchestrator increments `retry_count` **before** invoking Codex for a retry.
  If Codex then fails to commit, the count may be one ahead — this is intentional and
  conservative (better to pause one iteration early than to loop forever).
- `retry_count` is **preserved**, not reset, when the Orchestrator routes to the
  Evaluator mid-cycle (i.e. right after a Codex retry has re-committed). Only
  genuine forward progress — SPRINT PASS, contract/planner phases, or starting
  the next sprint — zeroes the counter. Otherwise the retry budget for a stubborn
  sprint would be silently unbounded.
- When the Orchestrator routes to `invoke_codex_for_retry` it **inlines** the
  body of `.sprintfoundry/eval-results/eval-result-{N}.md` into the Codex prompt and **deletes** the file
  before Codex runs. This forces the next round to re-invoke the Evaluator on
  the retry commit instead of looping on a stale FAIL verdict. Codex must never
  depend on the file still being on disk during the retry — the verdict lives
  in the prompt.
- The Orchestrator updates `last_run_at` on every routing decision.
- The Orchestrator sets `mode`, `needs_human`, `active_branch`, and `last_failure_reason`.
- Generator (Codex) must never write to `run-state.json`.
- Evaluator must never write to `run-state.json`.

### Required unattended artifacts

When unattended mode is enabled, maintain `run-state.json` with at least:

- current mode: `planning`, `contract`, `implementing`, `checking`, `paused`, `complete`
- current sprint number
- retry count for the current sprint
- last successful sprint
- last failure reason
- whether human escalation is required
- timestamp of last orchestration run
- active sprint branch name

### Required stop conditions

Unattended mode must pause instead of looping forever when any of these occurs:

- the same sprint fails more than 2 times
- `init.sh` fails repeatedly
- the sprint contract must change materially after implementation has started
- the evaluator identifies broad architecture drift instead of a local defect
- required secrets, environment variables, or services are unavailable

When pausing, write the reason into `run-state.json` and a short human-readable summary into `claude-progress.txt`.

### Required completion condition

Unattended mode stops cleanly when every sprint in `planner-spec.json` has a corresponding `SPRINT PASS`.

---

## Context Hygiene Rules

Long-running projects must resist context bloat and patch-on-patch AI code drift.

### Shared rules

- Always prefer current file state over remembered conversation state.
- Re-read the minimum required artifacts at the start of each phase instead of relying on prior chat context.
- Keep `claude-progress.txt` as a compact handoff log, not a narrative transcript.
- Do not append long retrospectives, design essays, or duplicate test output to `claude-progress.txt`.
- If a file artifact and the conversation disagree, trust the file artifact and resolve the discrepancy explicitly.

### `claude-progress.txt` policy

Treat `claude-progress.txt` as a rolling summary with a hard cap:

- Keep only the latest project summary plus the latest 3 sprint entries.
- Each sprint entry should be 3 to 5 lines maximum.
- Include only:
  - sprint number and timestamp
  - status
  - key files or behavior changed
  - blockers or evaluator-required follow-up
- Delete or compress older entries instead of appending forever.

**Compression trigger — mandatory, not optional:**

Compression must happen whenever any of the following is true:

- The file contains entries for > 3 sprints.
- The file exceeds 60 lines total.
- The file contains stack traces, test output dumps, or multi-paragraph narratives.

Any agent that appends to `claude-progress.txt` must check these conditions
**after** appending and compress the file immediately if any threshold is exceeded.
The Orchestrator also checks at session start and compresses before routing.

### Anti-slop rules

- Never preserve a bad abstraction just because it already exists in model context.
- On each sprint, prefer small coherent changes over opportunistic extra refactors.
- If a failed sprint requires broad unrelated cleanup, stop and surface that as a planning problem instead of smuggling it into the retry.
- Do not create placeholder architecture, fake extensibility, or generic helper layers unless the current sprint truly needs them.
- In unattended mode, prefer pausing with escalation over silently compounding low-quality code.

---

## Git Branching Rules

This harness uses one Git branch per sprint.

### Why

- isolate each sprint's implementation and retry history
- make evaluator failures easier to inspect and revert
- keep `main` or trunk clean until a sprint is accepted
- make unattended recovery safer because the active branch is explicit

### Branch policy

- Create a fresh branch before implementation begins for each sprint.
- Branch naming should be stable and machine-friendly:
  - preferred: `codex/sprint-<N>-<short-slug>`
  - acceptable fallback: `codex/sprint-<N>`
- Contract drafting may happen on the sprint branch or on the current working branch, but implementation commits must happen on the sprint branch.
- Retries for a failed sprint stay on the same sprint branch.
- A new sprint always gets a new branch; never reuse the previous sprint branch.

### Branch state tracking

When branch-per-sprint mode is used, `run-state.json` should also track:

- `active_branch`
- `base_branch`

### Merge expectation

- `main` should represent accepted progress only.
- Merge or fast-forward a sprint branch only after its evaluator result is `SPRINT PASS`.
- If a sprint is abandoned or re-planned, keep the branch for audit or close it explicitly; do not silently reuse it for a different sprint.

---

## Agent 1 — Planner (Claude Code)

**Runs**: once per project, triggered by a new user prompt.

**Output**: `planner-spec.json` + `init.sh` + initial entry in `claude-progress.txt`.

### Responsibilities

1. Read any existing context (`claude-progress.txt`, `git log`) before starting.
2. Turn the user prompt into a complete, ambitious product spec.
3. Stay high-level — define *what* and *why*, never implementation details.
4. Expand scope: target 12–20 features across 8–12 sprints.
5. Embed a **Visual Design Language** section in the spec:
   - Color palette (3–5 tokens with hex values)
   - Typography: display font, body font, mono font
   - Spacing unit, border radius, mood adjective
6. Choose a `verification.mode` for the project:
   - `browser` for UI/web flows, verified with Playwright MCP
   - `api` for HTTP services, verified with real requests and response assertions
   - `cli` for command-line tools, verified with commands, exit codes, and output
   - `job` for queue/worker systems, verified by enqueueing work and checking side effects
   - `library` for packages, verified from an external consumer harness
7. Identify opportunities for AI-native features.
8. Write `init.sh` — starts the full dev stack (frontend + backend).
   `init.sh` must satisfy the following contract:
   - **Idempotent**: safe to run twice in a row without side effects (kill existing
     processes before starting, skip already-installed dependencies, etc.).
   - **Fail-fast**: each major step (install, migrate, build, serve) must check its
     exit code and abort with a non-zero exit if it fails.
   - **Timeout-wrapped** for any step that could hang:
     `timeout 60 <command> || { echo "step timed out"; exit 1; }`
   - **No silent swallowing**: do not use `|| true` unless the failure is provably
     non-blocking.
9. Write `planner-spec.json`:

```json
{
  "product": "string",
  "design_language": "full VDL description",
  "tech_stack": { "frontend": "...", "backend": "...", "db": "..." },
  "verification": {
    "mode": "browser | api | cli | job | library",
    "base_url": "http://localhost:3000",
    "command": "pytest -q"
  },
  "features": ["..."],
  "sprints": [
    { "id": 1, "title": "string", "features": ["..."] }
  ]
}
```

### Hard rules

- Never write application code.
- Stop after `planner-spec.json` is written. Report to user before handoff.

---

## Agent 2 — Generator (Codex CLI)

> Codex reads this file directly. The instructions below are Codex's operating rules.

**Invoked by**: Orchestrator via `codex exec --full-auto --skip-git-repo-check "..."`

**Output**: committed code + updated `claude-progress.txt` + `eval-trigger.txt`.

### Session startup ritual (mandatory, no exceptions)

```bash
cat claude-progress.txt        # read last handoff
git log --oneline -10          # orient in history
bash init.sh                   # start dev server
```

After `init.sh`, run one smoke test before touching any code. If it fails, diagnose and fix first.

Before writing any code, re-read only the artifacts needed for the current sprint:

- `planner-spec.json`
- `sprint-contract.md`
- latest relevant `.sprintfoundry/eval-results/eval-result-{N}.md` when retrying

Do not treat old chat context as authoritative.

Before implementation starts, ensure you are on the correct sprint branch:

- if the sprint branch does not exist, create it from the base branch
- if it exists, switch to it
- verify `git branch --show-current` matches the sprint branch recorded in `run-state.json` when unattended mode is active

### Sprint workflow

**Step 1 — Identify current sprint**

Read `planner-spec.json`. Find the lowest-numbered sprint with no `.sprintfoundry/eval-results/eval-result-{N}.md`
containing "SPRINT PASS". That is the current sprint.

**Step 2 — Propose sprint contract** (if `sprint-contract.md` absent)

Write `sprint-contract.md` following the schema below. The Evaluator enforces
these constraints during contract review and will reject a contract that violates them.

```markdown
## Sprint <N>: <title from planner-spec.json>

### Features
- <feature from spec>

### Success criteria (black-box-verifiable)
- [ ] <observable user-facing behavior — must be testable without reading source code>
  Evaluator steps:
  1. Start the system, e.g. `bash init.sh`
  2. Exercise the external surface for the configured verification mode
  3. Assert the exact expected observable result
```

**Schema constraints (Evaluator will reject on violation):**

- Every success criterion must be written as an observable client/user action or
  externally visible state, not an implementation detail (e.g. "POST /users returns
  201 with an id" ✓ — "UserService.create inserts a row" ✗).
- Every success criterion must include its own `Evaluator steps:` block directly beneath it.
- Every success criterion must have **at least 2 Evaluator test steps** in that block.
- Every test step that requires navigation or an HTTP request must include a full
  URL path (e.g. `http://localhost:3000/settings` or `http://localhost:8000/users`).
- A test step must be executable without reading source code or inspecting internals.
- The contract must have **at least 1** success criterion.
- Total test steps across all criteria must be **≥ 3**.

Then stop. The Orchestrator routes this to Evaluator for contract review.

**Step 3 — Implement** (only after `sprint-contract.md` contains "CONTRACT APPROVED")

Before writing any code, record a contract checksum:

```bash
sha256sum sprint-contract.md > sprint-contract.md.sha256
```

If `sprint-contract.md` is modified after this point (checksum mismatch), stop
immediately and surface the change to the Orchestrator — do not commit code
against a modified contract.

- Read `planner-spec.json` for VDL and architecture constraints before writing code.
- Follow the Visual Design Language for all UI work.
- Write tests alongside implementation — never after.
- Never use inline styles in React/frontend components.
- Do not carry forward abstractions, helpers, or TODO scaffolding unless they are required by the current sprint.
- Prefer editing or deleting weak code over wrapping it in another layer.
- Do not implement a sprint on `main` when branch-per-sprint mode is enabled.

**Step 4 — Self-check**

For each success criterion in `sprint-contract.md`, verify it manually.
Fix any failures before committing.

```bash
pytest -q           # unit tests must pass
git diff --stat     # review scope of changes
```

Also do one context hygiene pass before commit:

- remove dead code introduced during the sprint
- remove temporary debug output
- collapse duplicated logic created during iteration
- check that file names, components, and helpers still match the current architecture
- ensure the change set is still about the approved sprint, not opportunistic extras

**Step 5 — Commit**

```bash
git add -A
git commit -m "feat(sprint-<N>): <imperative description, 72 chars max>"
```

Confirm the commit is on the active sprint branch before signaling the evaluator.

**Step 6 — Signal Evaluator**

Write `eval-trigger.txt` **before** updating `claude-progress.txt`. This ordering
ensures the Orchestrator can always discover a committed sprint even if the
progress-log write is interrupted.

```bash
# 1. Write the trigger first — this is the authoritative signal to Orchestrator.
echo "sprint=<N>" > eval-trigger.txt

# 2. Update the progress log after the trigger is safely on disk.
echo "## Sprint <N> — $(date '+%Y-%m-%d %H:%M')" >> claude-progress.txt
echo "Status: committed, pending Evaluator CHECK" >> claude-progress.txt
```

When updating `claude-progress.txt`, keep the file compact per the policy above.
If necessary, rewrite older entries into a short summary before appending the new one.

### Handling SPRINT FAIL

When invoked after a SPRINT FAIL:

1. Read `.sprintfoundry/eval-results/eval-result-{N}.md` fully.
2. Fix only what the Evaluator cited.
3. `git commit -m "fix(sprint-<N>): address evaluator failure"`
4. Write `eval-trigger.txt` **before** updating `claude-progress.txt`:
   ```bash
   echo "sprint=<N>-retry" > eval-trigger.txt
   echo "## Sprint <N> retry — $(date '+%Y-%m-%d %H:%M')" >> claude-progress.txt
   echo "Status: fix committed, pending re-CHECK" >> claude-progress.txt
   ```
5. `retry_count` is owned by the Orchestrator. Generator must not modify `run-state.json`.
   The Orchestrator increments `retry_count` before invoking this Codex session.

### Hard rules

- Never evaluate your own output.
- Never write "SPRINT PASS" or "SPRINT FAIL".
- Never begin coding before "CONTRACT APPROVED" is in `sprint-contract.md`.
- Never remove or modify existing tests.
- Never commit with failing tests.
- Use `git revert` (not patches) to recover from broken state.
- Never let `claude-progress.txt` grow into a full transcript.
- Never justify keeping low-quality code by citing earlier conversation context.
- Never keep retrying indefinitely in unattended mode once pause conditions are met.
- Never start a new sprint on the previous sprint's branch.
- Never merge an unapproved sprint branch into `main`.
- Never write to `run-state.json` — that file is owned by the Orchestrator.
- **Stop immediately after writing `eval-trigger.txt`.** Do not read `planner-spec.json` to find the next sprint. Do not create a new branch. Do not implement any subsequent sprint. The Orchestrator is the only entity permitted to advance the sprint counter.
- **Write `eval-trigger.txt` with the exact content `sprint=N`** where N is the sprint you just implemented. Never write a different sprint number.
- **Respect `sprint-fence.json`.** If this file exists, its `sprint` field is the only sprint you are authorised to implement in this session. Stop without writing code if you are being asked to implement a different sprint.

---

## Agent 3 — Evaluator (Claude Code)

**Runs**: twice per sprint — contract review before coding, black-box CHECK after commit.

**Output**: "CONTRACT APPROVED" in `sprint-contract.md` (Mode 1), or `.sprintfoundry/eval-results/eval-result-{N}.md` (Mode 2).

### Mode 1 — Contract Review

Read `planner-spec.json` and its `verification.mode`. Check each success criterion: is it externally observable through the configured mode? Specific enough to test? Mapped to concrete test steps?

**If approved**, append to `sprint-contract.md`:
```
CONTRACT APPROVED
Sprint: <N>
Approved criteria: <count>
```

**If changes needed**, return required changes and do not proceed to Mode 2.

### Mode 2 — CHECK

```bash
cat sprint-contract.md
cat eval-trigger.txt
bash init.sh
```

If `init.sh` fails → write SPRINT FAIL: "Dev environment failed to start". Do not evaluate.

**Scope verification** (before functional evaluation):

```bash
git diff "$(git merge-base HEAD main)"..HEAD --stat
```

Compare the full sprint branch diff against the sprint contract. Flag any files
or behaviour outside the contracted scope as a Craft defect in
`.sprintfoundry/eval-results/eval-result-{N}.md`. Scope violations do not auto-fail a sprint but reduce the
Craft score.

Execute each test step through the configured verification surface:

- `browser`: use Playwright MCP and capture screenshot/visible-state evidence.
- `api`: send real HTTP requests with `curl`, `httpx`, or an equivalent client; capture status codes, response bodies, and externally visible state.
- `cli`: run the real commands; capture exit codes, stdout/stderr, and generated files.
- `job`: enqueue or trigger work; poll status and verify side effects.
- `library`: create or use an external consumer harness; install/import the package and verify public API output.

**Scoring rubric**:

| Dimension | Weight | Threshold |
|-----------|--------|-----------|
| Design quality | 30% | ≥ 7/10 |
| Originality | 30% | ≥ 6/10 |
| Craft | 20% | ≥ 7/10 |
| Functionality | 20% | ≥ 8/10 — hard gate |

Functionality < 8 always fails the sprint.
Be harder on Originality than feels comfortable — the model defaults to safe.

**Write `.sprintfoundry/eval-results/eval-result-{N}.md`**:

```markdown
# Eval Result — Sprint <N>
Date: <ISO timestamp>

## Scores
| Dimension      | Score | Threshold | Result    |
|----------------|-------|-----------|-----------|
| Design quality | X/10  | ≥ 7       | PASS/FAIL |
| Originality    | X/10  | ≥ 6       | PASS/FAIL |
| Craft          | X/10  | ≥ 7       | PASS/FAIL |
| Functionality  | X/10  | ≥ 8       | PASS/FAIL |

## Verdict: SPRINT PASS / SPRINT FAIL

## Evidence
### Criterion: <text>
Result: PASS/FAIL
Observation: <what you observed through the configured verification surface>

## Required fixes (if SPRINT FAIL)
1. <concrete fix>
```

### Architecture drift — definition and pause signal

Architecture drift is a failure condition that **cannot be resolved by fixing
the implementation alone**. Objective criteria for classification:

| Condition | Classification |
|-----------|---------------|
| Fix requires changing `sprint-contract.md` or `planner-spec.json` | Architecture drift |
| Fix would require rewriting > 50 % of the committed code | Architecture drift |
| Tech stack or dependencies are insufficient for the criterion | Architecture drift |
| VDL in `planner-spec.json` conflicts with what the criterion requires | Architecture drift |
| Same root cause has failed across 2+ retries without improvement | Architecture drift |
| Fix can be made in < 30 lines touching < 3 files | Local defect — **not** drift |

When drift is detected, write in `.sprintfoundry/eval-results/eval-result-{N}.md`:

```
ARCHITECTURE DRIFT DETECTED
Reason: <one sentence stating which condition above was met>
Recommended action: <re-plan sprint / revise contract / escalate to human>
```

### Hard rules

- Never write application code.
- Never approve without running the configured black-box verification steps.
- Never approve where any Functionality criterion failed.
- When failing a sprint, cite generic scaffolding, duplicate logic, fake interactivity, or patch-on-patch code smell if they materially hurt craft or functionality.
- In unattended mode, prefer a clear `SPRINT FAIL` plus escalation signal over vague partial approval.

---

## Sprint Gate Architecture

Every sprint must pass through all four phases in order.  No phase may be skipped.

```
┌─────────────────────────────────────────────────────────┐
│  Sprint N Gate                                          │
│                                                         │
│  1. CONTRACT    Generator proposes sprint-contract.md   │
│       │         Orchestrator routes to Evaluator        │
│       ▼                                                 │
│  2. APPROVAL    Evaluator writes CONTRACT APPROVED      │
│       │         Orchestrator writes sprint-fence.json   │
│       ▼                                                 │
│  3. IMPLEMENT   Codex implements Sprint N ONLY          │
│       │         Writes eval-trigger.txt  → STOPS        │
│       ▼                                                 │
│  4. EVALUATE    Evaluator runs black-box CHECK          │
│       │         Writes .sprintfoundry/eval-results/eval-result-N.md                 │
│       ▼                                                 │
│  SPRINT PASS?  ──Yes──▶  Orchestrator deletes           │
│                          sprint-contract.md             │
│                          sprint-fence.json              │
│                          eval-trigger.txt               │
│                          ──▶ Sprint N+1 Gate starts     │
│               ──No───▶  Retry (max 2) or pause         │
└─────────────────────────────────────────────────────────┘
```

**The invariant**: `sprint-contract.md` is absent at the start of every sprint.
Its presence always means "this sprint is in progress."  Its absence means
"the previous sprint is complete and the next sprint has not yet been contracted."

This prevents the most common form of AI drift — implementing multiple sprints
in a single Codex session — by making it mechanically impossible to start
coding without a freshly approved contract.

---

## Monotonic-PASS Invariant (authoritative completion signal)

The **only** signal that Sprint N is complete is:

> `.sprintfoundry/eval-results/eval-result-{N}.md` exists AND contains the literal string `SPRINT PASS`.

Everything else is derived state:

- `run-state.json.last_successful_sprint` — cache, not truth.
- `claude-progress.txt` — human-readable handoff, not truth.
- branch name, commit log, `sprint-contract.md` deletion — all derived.

### Consequences

1. The Orchestrator re-derives "which sprints have passed" from
   `.sprintfoundry/eval-results/eval-result-{N}.md` files on every invocation; it never trusts
   `run-state.json` for advancement decisions.
2. The Orchestrator runs an audit (`audit_sprint_history` in
   `scripts/orchestrate.py`) **before every routing rule**. If declared state
   disagrees with the eval-result files — e.g. Sprint N marked advanced while
   `.sprintfoundry/eval-results/eval-result-{N}.md` is missing or contains `SPRINT FAIL` — the
   Orchestrator pauses with `needs_human=true` before any other rule can fire.
3. The Orchestrator refuses to start Sprint N while any prior Sprint 1..N-1
   lacks a `SPRINT PASS` eval-result, even if a human tries to edit
   `run-state.json` past the gap.
4. A Git pre-commit hook (`.githooks/pre-commit`, installed by
   `scripts/install-hooks.sh`) refuses commits that advance the sprint
   counter while any earlier sprint lacks `SPRINT PASS`. The hook can only
   be bypassed with `HARNESS_BYPASS=1 git commit ...` — intended for
   explicit, human-reviewed rescue commits only.

### Append-only audit trail (`harness-audit.ndjson`)

All enforcement above is *detective* — it pauses or blocks when things go
wrong. The **audit log** is the *forensic* companion: a single append-only
NDJSON file (`harness-audit.ndjson`) that records every harness operation so
humans can reconstruct what happened without rerunning the orchestrator.

Events written to it:

- `orchestrator_run` — every invocation: `{rule, action, mode, needs_human, rationale}`.
- `audit_finding` — every `audit_sprint_history` violation, one line per finding.
- `state_transition` — every change to `run-state.json` with `{key: [old, new]}` diffs.
- `eval_result_observed` — snapshot of every `.sprintfoundry/eval-results/eval-result-{N}.md` verdict on
  each orchestrator run, so offline auditors can reconstruct the verdict
  timeline from the log alone.
- `commit_recorded` — written by `.githooks/post-commit` for every commit
  (sha, author, subject, files, and which "sensitive" paths — run-state.json,
  eval-result-\*.md, sprint-contract.md, sprint-fence.json — were touched).
- `commit_blocked` — pre-commit rejection (rule + subject + context).
- `commit_bypassed` — every use of `HARNESS_BYPASS=1` is recorded so no
  emergency override is ever invisible.
- `note` — human free-form annotation via `scripts/harness-log.py note`.

**Never rewrite this file.** To rotate, copy it aside and let a new one be
created on the next append. Treat it like a write-ahead log.

Useful commands:

```bash
python3 scripts/harness-log.py tail -n 30
python3 scripts/harness-log.py filter --event audit_finding
python3 scripts/harness-log.py filter --sprint 3 --json
python3 scripts/harness-log.py verify               # reconcile state vs eval-results
python3 scripts/harness-log.py note --text "reason" # annotate a manual action
```

### Historical failure modes this invariant prevents

| Failure mode | What used to happen | How the invariant blocks it |
|--------------|---------------------|-----------------------------|
| **Bootstrap bypass** | Codex writes Sprint 1 code + `planner-spec.json` in one commit, skipping contract/eval-trigger; later sprints proceed. | Audit fires on next orchestrator run: ".sprintfoundry/eval-results/eval-result-1.md is missing but Sprint ≥ 2 is already in progress". |
| **Manual FAIL override** | `chore: sprint N complete, advance to N+1` commit rewrites `run-state.json` while `.sprintfoundry/eval-results/eval-result-N.md` still says SPRINT FAIL. | (a) pre-commit hook rejects the commit subject pattern when audit fails; (b) if bypassed, the orchestrator pauses on the very next routing call. |
| **Non-contiguous PASS** | Sprint K marked PASS while some Sprint M \< K has no eval-result. | Audit flags `evaluator_skipped` / `fail_bypassed` for every gap. |
| **Silent manual override** | Human edits `run-state.json` directly, no audit trail, root-cause takes hours to find. | `post-commit` hook writes a `commit_recorded` entry flagging `run-state.json` as sensitive; `orchestrator_run` writes `state_transition` diffs on every invocation. |

---

## Sprint Loop

```
planner-spec.json ready
    │
    ▼
[SPRINT N]
    ├─ Codex proposes sprint-contract.md
    ├─ Claude Evaluator: CONTRACT APPROVED  (no code yet)
    ├─ Codex implements + commits + writes eval-trigger.txt
    ├─ Claude Evaluator: eval-result-{N}.md
    │       SPRINT PASS → Orchestrator cleans up, next sprint
    │       SPRINT FAIL → Codex revises → re-CHECK
    └─▶ Sprint N+1
```

---

## Codex CLI Invocation

Orchestrator calls Codex via Bash. Standard invocation patterns:

```bash
# Propose sprint contract
codex exec --full-auto \
  -c 'sandbox_permissions=["disk-full-read-access"]' \
  -c 'shell_environment_policy.inherit=all' \
  --skip-git-repo-check \
  "Read planner-spec.json. Propose sprint-contract.md for Sprint N. Follow AGENTS.md Generator rules."

# Implement after contract approved
codex exec --full-auto \
  -c 'sandbox_permissions=["disk-full-read-access"]' \
  -c 'shell_environment_policy.inherit=all' \
  --skip-git-repo-check \
  "sprint-contract.md is approved. Implement Sprint N. Commit and write eval-trigger.txt. Follow AGENTS.md."

# Fix after SPRINT FAIL
codex exec --full-auto \
  -c 'sandbox_permissions=["disk-full-read-access"]' \
  -c 'shell_environment_policy.inherit=all' \
  --skip-git-repo-check \
  "Sprint N failed. Read .sprintfoundry/eval-results/eval-result-N.md. Fix only the cited issues. Re-commit and update eval-trigger.txt."
```

---

## Hard Rules (all agents)

- Never skip contract negotiation — code does not begin before CONTRACT APPROVED.
- Never self-evaluate — Codex never writes eval-result. Evaluator never writes code.
- Never mark a sprint complete without independent black-box verification.
- Never remove or modify existing tests.
- State lives in files — read artifacts at session start, not conversation history.

---

## Hard Environment Requirements

The harness will not function correctly if any of the following are missing.
`init.sh` should validate these at startup and exit non-zero if a requirement
is not met.

### Runtime requirements for `init.sh`

These are the requirements `init.sh` may enforce because they are needed to
start or verify the application stack itself:

| Requirement | Minimum version | Purpose |
|-------------|----------------|---------|
| Node.js | 18 LTS | frontend/backend runtime and builds |
| npm | 9 | package management |
| Python | 3.9 | unit testing with pytest |
| pytest | 7 | unit test runner |
| Git | 2.30 | version control, sprint branches |
| Bash | 4 | `init.sh`, hooks |

Recommended validation snippet for `init.sh`:

```bash
for cmd in node npm python3 pytest git bash; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Missing required tool: $cmd"; exit 1; }
done
```

### Agent-specific requirements

These may be required by Generator or Evaluator, but must not be enforced by
`init.sh` because not every harness phase needs all of them:

| Requirement | Minimum version | Purpose |
|-------------|----------------|---------|
| Codex CLI (`@openai/codex`) | latest stable | Generator runtime |
| Codex authenticated session or OpenAI API key | — | Generator authentication; in desktop or already-authenticated Codex environments, `OPENAI_API_KEY` is not required |
| Playwright MCP (`@playwright/mcp`) | pinned (see CLAUDE.md) | Evaluator browser CHECK only |

## Tech Stack

```
Testing   : verification.mode-specific black-box checks, pytest (unit)
VCS       : Git — one clean commit per sprint
```

---

## Test Strategy Alignment

Two independent test layers run in this harness. Understanding their relationship
prevents misattributing failures.

| Layer | Owner | Runner | Scope | Characteristics |
|-------|-------|--------|-------|----------------|
| Unit tests | Generator | `pytest -q` | Functions, components, logic | Fast, deterministic, no browser |
| Black-box checks | Evaluator | verification.mode-specific tools | Full external behavior through browser/API/CLI/job/library surface | Slower, may be flaky on env issues |

**Failure attribution rules:**

- Generator's unit tests pass **and** Evaluator's E2E tests fail
  → Likely an environment/integration issue (missing env var, wrong port, DB not seeded).
  Diagnose `init.sh` and integration layer before blaming the code.
- Generator's unit tests fail → Do not signal Evaluator. Fix before committing.
- Evaluator's E2E tests fail repeatedly on the same criterion after code fixes
  → Treat as architecture drift candidate; check the criteria above.

Generator must never skip unit tests to pass Evaluator faster. Evaluator must
never accept passing unit tests as a substitute for independent black-box verification.

## Build & Test Commands

```bash
bash init.sh                               # start full dev stack
pytest -q                                  # unit tests
npx playwright test                        # E2E tests
cat claude-progress.txt && git log --oneline -10   # session orientation
```
