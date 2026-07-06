# AGENTS.md

SprintFoundry compact agent contract. Codex reads this file directly; keep it
short and operational. Full background lives in `docs/protocol.md`.

## Roles

| Agent | Runtime | Responsibility |
| --- | --- | --- |
| Orchestrator | **Plugin skill** `sprintfoundry-orchestrator` | Routes by file state; entry point for all user requests. |
| Planner | Claude sub-agent | Writes `planner-spec.json`, `init.sh`, and initial `.sprintfoundry/claude-progress.txt`. |
| Generator | Codex CLI | Implements exactly one approved sprint and writes a commit request. |
| Evaluator | Claude sub-agent | Reviews contracts and runs independent black-box CHECK. |

The Orchestrator is now a **skill** (not an agent) — the entry point that users trigger.
Planner and Evaluator are sub-agents called by the Orchestrator skill via `Agent(subagent_type=...)`.
Generator is always Codex CLI via Bash — never a Claude sub-agent.

The gate rule: Generator never writes `SPRINT PASS` or `SPRINT FAIL`. Only the
Evaluator writes `.sprintfoundry/results/eval/eval-result-{N}.md`.
Git rule: Generator never writes Git metadata, commits, or `.sprintfoundry/signals/eval-trigger.txt`.
The Orchestrator owns `git add`, `git commit`, and trigger creation after it
validates the Generator's commit request.

> **Plugin source**: `plugin/` directory. Build: `bash scripts/package_plugin.sh`
> **Example files**: `examples/` directory (run-state, planner-spec, sprint-contract, etc.)

## State Files

State lives on disk, not in chat memory.

| File | Owner | Meaning |
| --- | --- | --- |
| `.sprintfoundry/state/scope-classification.json` | Planner | Planning scale: `standard` or `large_system`, with evidence and epic outline. |
| `planner-spec.json` | Planner | Product spec, sprint list, tech stack, verification mode. |
| `sprint-contract.md` | Generator + Evaluator | Current sprint definition of done. Must be approved before code. |
| `.sprintfoundry/state/sprint-fence.json` | Orchestrator | Authorized sprint number and base commit. |
| `.sprintfoundry/signals/commit-requests/sprint-{N}.json` | Generator | Request for Orchestrator-owned commit and trigger creation. |
| `.sprintfoundry/signals/eval-trigger.txt` | Orchestrator | Signal after Orchestrator commit. Must contain exactly `sprint=N` or `sprint=N-retry`. |
| `.sprintfoundry/results/quality/quality-gate-{N}.md` | Orchestrator | Static quality gate result before Evaluator CHECK. |
| `.sprintfoundry/results/eval/eval-result-{N}.md` | Evaluator | Authoritative sprint verdict kept out of the project root. |
| `.sprintfoundry/state/run-state.json` | Orchestrator | Cache: mode, retry count, pause state, branch state. |
| `.sprintfoundry/claude-progress.txt` | Generator | Compact handoff, not a transcript. |
| `change-request.md` | User + Orchestrator | Classified product iteration. |
| `bug-report.md` | User + Orchestrator | Dedicated defect intake. |
| `.sprintfoundry/logs/harness-audit.ndjson` | Orchestrator + hooks | Append-only forensic log. |
| `init.sh` | Planner | Idempotent startup for the project under test. |

Authoritative completion signal:
`.sprintfoundry/results/eval/eval-result-{N}.md` exists and contains the literal
string `SPRINT PASS`. Everything else is derived state. Legacy root-level
`eval-result-{N}.md` files may be read during migration, but new files belong
in `.sprintfoundry/results/eval/`.

## Verification Modes

Planner must include:

```json
{
  "verification": {
    "mode": "browser | api | cli | job | library",
    "base_url": "http://localhost:3000",
    "command": "uv run --python <project-python-version> --with pytest pytest -q"
  }
}
```

Evaluator uses the configured mode:

- `browser`: Playwright MCP.
- `api`: real HTTP requests and response assertions.
- `cli`: real commands, exit codes, stdout/stderr, generated files.
- `job`: enqueue/trigger work, poll status, verify side effects.
- `library`: external consumer harness imports/installs the package.

Success criteria must be black-box-verifiable through that surface.

## Orchestrator Rules

Route strictly by current files:

- `.sprintfoundry/state/run-state.json.needs_human=true` -> pause immediately.
- No `planner-spec.json` -> Planner.
- `bug-report.md` -> Codex proposes a bugfix sprint contract.
- `change-request.md` -> route by `Type: bugfix | minor_feature | major_feature | replan`.
- Unapproved `sprint-contract.md` -> Evaluator contract review.
- Approved `sprint-contract.md` with no trigger -> prepare sprint branch, write fence, invoke Codex.
- `.sprintfoundry/signals/commit-requests/sprint-{N}.json` -> Orchestrator validates, commits, writes `.sprintfoundry/signals/eval-trigger.txt`.
- `.sprintfoundry/signals/eval-trigger.txt` -> Evaluator CHECK unless a stale FAIL requires retry routing.
- SPRINT PASS -> cleanup trigger, contract, and fence before the next sprint.

Never start Sprint N if any prior planned sprint lacks `SPRINT PASS`.

## Generator Startup Ritual

Every Codex session starts with:

```bash
cat .sprintfoundry/claude-progress.txt 2>/dev/null || echo "[no progress]"
git log --oneline -10
bash init.sh
```

After `init.sh`, run one smoke test before editing code. If startup or smoke
fails, diagnose and fix that first.

Before writing code, reread only:

- `planner-spec.json`
- `sprint-contract.md`
- `.sprintfoundry/prompts/sprint-*/attempt-*-invoke-codex-for-retry.md` when retrying

Do not treat old chat context as truth.

## Branch Rules

- Orchestrator commits implementation changes on the current sprint branch, not `main`.
- Preferred branch: `codex/sprint-<N>-<short-slug>`.
- Retries stay on the same sprint branch.
- A new sprint gets a new branch.
- Verify `git branch --show-current` matches `.sprintfoundry/state/run-state.json.active_branch`
  when unattended mode is active.

## Contract Phase

If `sprint-contract.md` is absent, propose it and stop. Do not code.

Contract schema:

```markdown
## Sprint <N>: <title from planner-spec.json>

### Features
- <feature from spec>

### Success criteria (black-box-verifiable)
- [ ] <observable client/user behavior>
  Evaluator steps:
  1. Start the system, e.g. `bash init.sh`
  2. Exercise the external surface for `planner-spec.json` verification.mode
  3. Assert the exact externally visible result
```

Constraints:

- At least one success criterion.
- Every criterion has its own `Evaluator steps:` block.
- Every criterion has at least two concrete test steps.
- Total test steps across the contract is at least three.
- URL/request steps must include full URLs.
- Steps must be executable without source-code or internal inspection.

After writing `sprint-contract.md`, stop. Evaluator approval is required.

## Implementation Phase

Only implement after `sprint-contract.md` contains `CONTRACT APPROVED`.

Contract-tamper enforcement is owned by the Orchestrator: it records the
approved contract's sha256 in `.sprintfoundry/state/sprint-fence.json` before
you start, and re-verifies it at commit time. You may keep a local
`sha256sum sprint-contract.md` as a courtesy self-check; if you detect a
mid-session contract change, stop and surface it instead of requesting a
commit.

Implementation rules:

- Implement only Sprint N.
- Follow the planner's tech stack and verification mode.
- Write focused tests alongside implementation.
- Never remove or weaken existing tests.
- Never use inline styles in frontend components.
- Prefer deleting weak code over wrapping it in new layers.
- Avoid placeholder architecture, fake extensibility, and opportunistic refactors.

Self-check before requesting a commit:

```bash
uv run --python <project-python-version> --with pytest pytest -q
git diff --stat
```

Resolve `<project-python-version>` from `SPRINTFOUNDRY_PYTHON_VERSION`,
`.python-version`, `runtime.txt`, or `pyproject.toml requires-python`; record
the concrete version in commit requests, not the placeholder.

Also remove debug output, dead code, temporary files, and duplicated logic.

Prepare a commit request. Do not run `git add`, `git commit`, or write
`.sprintfoundry/signals/eval-trigger.txt` from Codex:

```json
{
  "sprint": N,
  "attempt": "initial",
  "commit_message": "feat(sprint-<N>): <imperative description>",
  "changed_files": ["<relative paths>"],
  "tests": [{"command": "uv run --python <project-python-version> --with pytest pytest -q", "status": "passed"}]
}
```

Write it to `.sprintfoundry/signals/commit-requests/sprint-<N>.json`, update
`.sprintfoundry/claude-progress.txt` compactly, then stop. The Orchestrator
will commit and write `.sprintfoundry/signals/eval-trigger.txt` after validation.

## Retry Phase

When invoked after SPRINT FAIL:

- Fix only the cited Evaluator issues.
- Do not depend on `.sprintfoundry/results/eval/eval-result-{N}.md` being present; the
  Orchestrator archives the consumed verdict to
  `.sprintfoundry/archive/sprint-{N}/eval-result-attempt-{K}.md` and inlines a
  digest into the retry prompt. Read the archived file (path is in the prompt)
  if you need full evidence.
- Keep the retry on the same sprint branch.
- Write a retry commit request with:

```json
{
  "sprint": N,
  "attempt": "retry",
  "commit_message": "fix(sprint-<N>): address evaluator failure"
}
```

Then update `.sprintfoundry/claude-progress.txt` compactly and stop. The
Orchestrator commits and writes `.sprintfoundry/signals/eval-trigger.txt` with
`sprint=N-retry`.

## Progress Log Policy

`.sprintfoundry/claude-progress.txt` must stay small:

- latest project summary
- latest three sprint entries only
- each sprint entry 3 to 5 lines

Compress immediately if it exceeds 60 lines, contains entries for more than
three sprints, or includes stack traces/test dumps/multi-paragraph narratives.

## Hard Stops

Stop and surface to Orchestrator/human when:

- `.sprintfoundry/state/run-state.json.needs_human=true`
- retry limit is exceeded
- `init.sh` repeatedly fails
- required secrets/services/tools are unavailable
- the contract changed after implementation started
- the Evaluator reports architecture drift
- the requested fix would require broad unrelated cleanup

## Never

- Never code before `CONTRACT APPROVED`.
- Never self-evaluate or write `.sprintfoundry/results/eval/eval-result-{N}.md`.
- Never write `SPRINT PASS` or `SPRINT FAIL`.
- Never run `git add`, `git commit`, or write `.sprintfoundry/signals/eval-trigger.txt`.
- Never write to `.sprintfoundry/state/run-state.json`.
- Never implement multiple sprints in one Codex session.
- Never start a new sprint on the previous sprint branch.
- Never merge an unapproved sprint branch into `main`.
- Never rewrite `.sprintfoundry/logs/harness-audit.ndjson`; append only.
- Never use destructive git commands unless explicitly requested by the user.
