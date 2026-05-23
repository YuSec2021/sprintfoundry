# AGENTS.md

SprintFoundry compact agent contract. Codex reads this file directly; keep it
short and operational. Full background lives in `docs/protocol.md`.

## Roles

| Agent | Runtime | Responsibility |
| --- | --- | --- |
| Orchestrator | **Plugin skill** `sprintfoundry-orchestrator` | Routes by file state; entry point for all user requests. |
| Planner | Claude sub-agent | Writes `planner-spec.json`, `init.sh`, and initial `claude-progress.txt`. |
| Generator | Codex CLI | Implements exactly one approved sprint and writes a commit request. |
| Evaluator | Claude sub-agent | Reviews contracts and runs independent black-box CHECK. |

The Orchestrator is now a **skill** (not an agent) — the entry point that users trigger.
Planner and Evaluator are sub-agents called by the Orchestrator skill via `Agent(subagent_type=...)`.
Generator is always Codex CLI via Bash — never a Claude sub-agent.

The gate rule: Generator never writes `SPRINT PASS` or `SPRINT FAIL`. Only the
Evaluator writes `.sprintfoundry/eval-results/eval-result-{N}.md`.
Git rule: Generator never writes Git metadata, commits, or `eval-trigger.txt`.
The Orchestrator owns `git add`, `git commit`, and trigger creation after it
validates the Generator's commit request.

> **Plugin source**: `plugin/` directory. Build: `bash scripts/package_plugin.sh`
> **Example files**: `examples/` directory (run-state, planner-spec, sprint-contract, etc.)

## State Files

State lives on disk, not in chat memory.

| File | Owner | Meaning |
| --- | --- | --- |
| `scope-classification.json` | Planner | Planning scale: `standard` or `large_system`, with evidence and epic outline. |
| `planner-spec.json` | Planner | Product spec, sprint list, tech stack, verification mode. |
| `sprint-contract.md` | Generator + Evaluator | Current sprint definition of done. Must be approved before code. |
| `sprint-fence.json` | Orchestrator | Authorized sprint number and base commit. |
| `.sprintfoundry/commit-requests/sprint-{N}.json` | Generator | Request for Orchestrator-owned commit and trigger creation. |
| `eval-trigger.txt` | Orchestrator | Signal after Orchestrator commit. Must contain exactly `sprint=N` or `sprint=N-retry`. |
| `.sprintfoundry/eval-results/eval-result-{N}.md` | Evaluator | Authoritative sprint verdict kept out of the project root. |
| `run-state.json` | Orchestrator | Cache: mode, retry count, pause state, branch state. |
| `claude-progress.txt` | Generator | Compact handoff, not a transcript. |
| `change-request.md` | User + Orchestrator | Classified product iteration. |
| `bug-report.md` | User + Orchestrator | Dedicated defect intake. |
| `harness-audit.ndjson` | Orchestrator + hooks | Append-only forensic log. |
| `init.sh` | Planner | Idempotent startup for the project under test. |

Authoritative completion signal:
`.sprintfoundry/eval-results/eval-result-{N}.md` exists and contains the literal
string `SPRINT PASS`. Everything else is derived state. Legacy root-level
`eval-result-{N}.md` files may be read during migration, but new files belong
in `.sprintfoundry/eval-results/`.

## Verification Modes

Planner must include:

```json
{
  "verification": {
    "mode": "browser | api | cli | job | library",
    "base_url": "http://localhost:3000",
    "command": "pytest -q"
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

- `run-state.json.needs_human=true` -> pause immediately.
- No `planner-spec.json` -> Planner.
- `bug-report.md` -> Codex proposes a bugfix sprint contract.
- `change-request.md` -> route by `Type: bugfix | minor_feature | major_feature | replan`.
- Unapproved `sprint-contract.md` -> Evaluator contract review.
- Approved `sprint-contract.md` with no trigger -> prepare sprint branch, write fence, invoke Codex.
- `.sprintfoundry/commit-requests/sprint-{N}.json` -> Orchestrator validates, commits, writes `eval-trigger.txt`.
- `eval-trigger.txt` -> Evaluator CHECK unless a stale FAIL requires retry routing.
- SPRINT PASS -> cleanup trigger, contract, and fence before the next sprint.

Never start Sprint N if any prior planned sprint lacks `SPRINT PASS`.

## Generator Startup Ritual

Every Codex session starts with:

```bash
cat claude-progress.txt 2>/dev/null || echo "[no progress]"
git log --oneline -10
bash init.sh
```

After `init.sh`, run one smoke test before editing code. If startup or smoke
fails, diagnose and fix that first.

Before writing code, reread only:

- `planner-spec.json`
- `sprint-contract.md`
- the inlined Evaluator failure details when retrying

Do not treat old chat context as truth.

## Branch Rules

- Orchestrator commits implementation changes on the current sprint branch, not `main`.
- Preferred branch: `codex/sprint-<N>-<short-slug>`.
- Retries stay on the same sprint branch.
- A new sprint gets a new branch.
- Verify `git branch --show-current` matches `run-state.json.active_branch`
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

Before editing code:

```bash
sha256sum sprint-contract.md > sprint-contract.md.sha256
```

If the contract changes after this point, stop and surface it. Do not request a
commit against a modified contract.

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
pytest -q
git diff --stat
```

Also remove debug output, dead code, temporary files, and duplicated logic.

Prepare a commit request. Do not run `git add`, `git commit`, or write
`eval-trigger.txt` from Codex:

```json
{
  "sprint": N,
  "attempt": "initial",
  "contract_sha256": "<sha256 from sprint-contract.md.sha256>",
  "commit_message": "feat(sprint-<N>): <imperative description>",
  "changed_files": ["<relative paths>"],
  "tests": [{"command": "pytest -q", "status": "passed"}]
}
```

Write it to `.sprintfoundry/commit-requests/sprint-<N>.json`, update
`claude-progress.txt` compactly, then stop. The Orchestrator will commit and
write `eval-trigger.txt` after validation.

## Retry Phase

When invoked after SPRINT FAIL:

- Fix only the cited Evaluator issues.
- Do not depend on `.sprintfoundry/eval-results/eval-result-{N}.md` being present; Orchestrator may have
  inlined it into the prompt and deleted the file.
- Keep the retry on the same sprint branch.
- Write a retry commit request with:

```json
{
  "sprint": N,
  "attempt": "retry",
  "commit_message": "fix(sprint-<N>): address evaluator failure"
}
```

Then update `claude-progress.txt` compactly and stop. The Orchestrator commits
and writes `eval-trigger.txt` with `sprint=N-retry`.

## Progress Log Policy

`claude-progress.txt` must stay small:

- latest project summary
- latest three sprint entries only
- each sprint entry 3 to 5 lines

Compress immediately if it exceeds 60 lines, contains entries for more than
three sprints, or includes stack traces/test dumps/multi-paragraph narratives.

## Hard Stops

Stop and surface to Orchestrator/human when:

- `run-state.json.needs_human=true`
- retry limit is exceeded
- `init.sh` repeatedly fails
- required secrets/services/tools are unavailable
- the contract changed after implementation started
- the Evaluator reports architecture drift
- the requested fix would require broad unrelated cleanup

## Never

- Never code before `CONTRACT APPROVED`.
- Never self-evaluate or write `.sprintfoundry/eval-results/eval-result-{N}.md`.
- Never write `SPRINT PASS` or `SPRINT FAIL`.
- Never run `git add`, `git commit`, or write `eval-trigger.txt`.
- Never write to `run-state.json`.
- Never implement multiple sprints in one Codex session.
- Never start a new sprint on the previous sprint branch.
- Never merge an unapproved sprint branch into `main`.
- Never rewrite `harness-audit.ndjson`; append only.
- Never use destructive git commands unless explicitly requested by the user.
