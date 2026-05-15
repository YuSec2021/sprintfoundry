# CLAUDE.md

Compact Claude Code guide for SprintFoundry. Keep this file small; detailed
protocol notes live in `docs/protocol.md`, and role-specific prompts live in
`plugin/agents/` (canonical) or `.claude/agents/` (local dev fallback).

## Repository Layout

```
autonomous-sprint-harness/
├── plugin/                        # Distributable plugin source (source of truth)
│   ├── .claude-plugin/
│   │   └── plugin.json           # Plugin manifest
│   ├── skills/
│   │   ├── sprintfoundry-orchestrator/  # Entry-point skill (routes, coordinates)
│   │   │   ├── SKILL.md
│   │   │   └── references/       # 6 reference docs loaded on demand
│   │   ├── harness-branching/    # Git branch lifecycle skill
│   │   └── harness-observability/ # Audit log skill
│   └── agents/                   # Sub-agents called by the orchestrator skill
│       ├── planner.md
│       ├── evaluator.md
│       └── generator.md          # Reference only — Generator is Codex CLI
├── .claude/
│   ├── agents/                   # Local dev fallback agents
│   │   ├── orchestrator.md       # DEPRECATED — superseded by plugin skill
│   │   ├── planner.md
│   │   ├── evaluator.md
│   │   └── generator.md
│   └── skills/                   # Local dev skills (harness-branching, observability)
├── examples/                      # Example / template files for new projects
│   ├── run-state.json
│   ├── planner-spec.json
│   ├── sprint-contract.md
│   ├── .sprintfoundry/eval-results/eval-result-1.md
│   ├── bug-report.md
│   ├── change-request.md
│   └── human-escalation.md
├── scripts/
│   ├── orchestrate.py            # Orchestrator helper / state inspector
│   ├── harness-log.py            # Audit log writer
│   ├── install-hooks.sh          # Git hook installer
│   └── package_plugin.sh         # Build sprintfoundry.plugin from plugin/
├── docs/protocol.md              # Full protocol reference
├── AGENTS.md                     # Codex (Generator) contract
└── sprintfoundry.plugin          # Built artifact — DO NOT EDIT DIRECTLY
```

## Runtime Roles

| Role | Runtime | Invocation |
| --- | --- | --- |
| Orchestrator | **Plugin skill** `sprintfoundry-orchestrator` | Triggered by user |
| Planner | Claude sub-agent | `Agent(subagent_type="planner", ...)` |
| Generator | Codex CLI | `codex exec --full-auto ...` |
| Evaluator | Claude sub-agent | `Agent(subagent_type="evaluator", ...)` |

Generator is always Codex CLI. Never invoke a Claude `generator` sub-agent.

The Orchestrator is the **plugin skill** — not an agent. Users trigger it via the
skill interface. It then delegates to planner/evaluator agents and Codex.

## Plugin Development Workflow

Edit source in `plugin/`, then rebuild:

```bash
bash scripts/package_plugin.sh              # builds sprintfoundry.plugin
bash scripts/package_plugin.sh --bump minor # bumps minor version then builds
```

Keep `plugin/` and `.claude/agents/` in sync for planner/evaluator/generator.
The orchestrator logic lives **only** in `plugin/skills/sprintfoundry-orchestrator/SKILL.md`.

## Startup Snapshot

At the start of a harness session, read current files instead of relying on
memory:

```bash
cat run-state.json 2>/dev/null || echo "[no run-state]"
cat claude-progress.txt 2>/dev/null || echo "[no progress]"
cat eval-trigger.txt 2>/dev/null || echo "[no eval-trigger]"
cat sprint-contract.md 2>/dev/null | head -40 || echo "[no contract]"
find .sprintfoundry/eval-results -maxdepth 1 -name 'eval-result-*.md' 2>/dev/null \
  || ls eval-result-*.md 2>/dev/null \
  || echo "[no eval results]"
git branch --show-current 2>/dev/null || true
git log --oneline -5 2>/dev/null || true
```

If `run-state.json.needs_human` is true, stop and surface the pause reason.
Do not route any agent until a human explicitly clears it.

If `run-state.json.active_branch` is set and differs from the current Git
branch, stop and resolve the branch mismatch before routing.

## Routing Order

Apply this order:

1. `needs_human=true` → pause.
2. Missing `planner-spec.json` → Planner creates spec, `init.sh`, progress log.
3. Sprint-history audit inconsistent → pause.
4. `eval-trigger.txt` exists → Quality Gate → Evaluator CHECK or targeted Codex retry.
5. `sprint-contract.md` exists but unapproved → Evaluator contract review.
6. Approved `sprint-contract.md` → prepare branch/fence, invoke Codex implementation.
7. `bug-report.md` → Codex proposes bugfix contract.
8. `change-request.md` → route by `Type`.
9. All planned sprints PASS → complete.
10. Otherwise → Codex proposes the next sprint contract.

## Verification Modes

Planner should include:

```json
{
  "verification": {
    "mode": "browser | api | cli | job | library",
    "base_url": "http://localhost:3000",
    "command": "pytest -q"
  }
}
```

## Codex Commands

```bash
codex exec --full-auto \
  -c 'sandbox_permissions=["disk-full-read-access"]' \
  -c 'shell_environment_policy.inherit=all' \
  --skip-git-repo-check \
  "<prompt>"
```

## Hard Rules

- Claude Planner/Evaluator/Orchestrator never write application code.
- Codex Generator never evaluates itself.
- No code before `CONTRACT APPROVED`.
- No sprint advancement without `SPRINT PASS`.
- Do not clear `needs_human=true` automatically.
- Do not rewrite `harness-audit.ndjson`.
- Prefer pausing with a clear reason over silent autonomous drift.
