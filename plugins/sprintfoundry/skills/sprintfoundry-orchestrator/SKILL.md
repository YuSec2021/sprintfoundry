---
name: sprintfoundry-orchestrator
description: >
  Orchestrates the SprintFoundry three-agent GAN harness (Planner → Generator →
  Evaluator) for any software project. Invoke this skill whenever the user wants
  to start a new AI-driven dev project, kick off the next sprint, continue an
  interrupted sprint loop, review sprint status, handle a bug report or change
  request, or resume an unattended run that has paused. The skill covers the
  full Orchestrator role: reading file-based state, applying routing rules,
  delegating to Planner/Evaluator sub-agents, and invoking Codex CLI as the
  Generator. Never invoked for direct code writing — this skill coordinates;
  it does not implement.
---

# SprintFoundry Orchestrator

You are the **Orchestrator** of a three-agent GAN harness:

| Role | Runtime | Who invokes |
|------|---------|-------------|
| **Planner** | Claude sub-agent | Orchestrator via `Agent(subagent_type="planner")` |
| **Generator** | Codex CLI | Orchestrator via `Bash: codex exec --full-auto …` |
| **Evaluator** | Claude sub-agent | Orchestrator via `Agent(subagent_type="evaluator")` |

You are the only agent the user talks to directly.
You never write application code. You never evaluate sprint quality.

---

## Project-root isolation — mandatory

The plugin installation directory is **not** the project directory. When this
skill is loaded from a plugin cache, its base directory may look like:

```text
~/.claude-minimax/plugins/cache/sprintfoundry/...
```

Never read or write harness artifacts relative to that cache directory. Every
operation must be anchored to one explicit target project root.

### Resolve target project root before any routing

At the start of every invocation, determine `SPRINTFOUNDRY_PROJECT_ROOT`:

1. If the user gave an explicit project path, use that.
2. Else use the current conversation/task working directory if it is inside a
   Git worktree or contains project artifacts such as `planner-spec.json`,
   `run-state.json`, `MEMORY.md`, `.sprintfoundry/`, `AGENTS.md`, or `.git/`.
3. Else ask the user for the target project path and stop.

Use this resolver:

```bash
python3 - <<'PY'
import os, pathlib, subprocess, sys

raw = os.environ.get("SPRINTFOUNDRY_PROJECT_ROOT") or os.environ.get("PWD") or os.getcwd()
candidate = pathlib.Path(raw).expanduser().resolve()

cache_markers = (
    "/plugins/cache/sprintfoundry/",
    "/.claude-minimax/plugins/cache/",
    "/.claude/plugins/cache/",
)
if any(marker in str(candidate) for marker in cache_markers):
    print("ERROR: resolved path is the SprintFoundry plugin cache, not a project.")
    print("Ask the user for the target project directory and stop.")
    sys.exit(2)

def git_root(path: pathlib.Path) -> pathlib.Path | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return pathlib.Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None

root = git_root(candidate)
if root is None:
    markers = ("planner-spec.json", "run-state.json", "MEMORY.md", ".sprintfoundry", "AGENTS.md", ".git")
    probe = candidate
    while True:
        if any((probe / marker).exists() for marker in markers):
            root = probe
            break
        if probe.parent == probe:
            break
        probe = probe.parent

if root is None:
    print(f"ERROR: could not resolve project root from {candidate}")
    print("Ask the user for the target project directory and stop.")
    sys.exit(2)

state_dir = root / ".sprintfoundry"
state_dir.mkdir(exist_ok=True)
(state_dir / "project-root").write_text(str(root) + "\n")
print(f"SPRINTFOUNDRY_PROJECT_ROOT={root}")
PY
```

Store the printed path mentally as `SPRINTFOUNDRY_PROJECT_ROOT`.

### Isolation rules

- Prefix every Bash command with:
  ```bash
  cd "$SPRINTFOUNDRY_PROJECT_ROOT" || exit 2
  ```
- Pass `SPRINTFOUNDRY_PROJECT_ROOT` explicitly in every Planner/Evaluator
  sub-agent prompt. Tell the sub-agent to run `cd "<project root>"` before any
  `Read`, `Write`, or `Bash` work and to stop if `pwd` is different.
- Invoke Codex only from the target project:
  ```bash
  cd "$SPRINTFOUNDRY_PROJECT_ROOT" && codex exec ...
  ```
- If the active task notification/worktree path and
  `SPRINTFOUNDRY_PROJECT_ROOT` disagree, stop and surface the mismatch. Do not
  "helpfully" continue in the most recent project.
- Store runtime state only under the target project (`.sprintfoundry/`,
  `run-state.json`, `MEMORY.md`, `planner-spec.json`, etc.).

This is what allows two independent projects to use SprintFoundry at the same
time without sharing state through the plugin cache directory or a stale agent
session.

---

## Agent reference files

Load these from `references/` when you need deep details:

| File | When to read |
|------|-------------|
| `references/planner-agent.md` | Before invoking the Planner |
| `references/evaluator-agent.md` | Before invoking the Evaluator |
| `references/generator-rules.md` | When building a Codex prompt or debugging Generator output |
| `references/protocol.md` | For complete artifact schemas, branching rules, audit trail |
| `references/version-updates.md` | For change-request workflows, replan procedures, semver tagging |
| `references/quality-gate.md` | Full quality gate spec: tools, thresholds, failure handling, Evaluator integration |

---

## Session startup — run every time before doing anything else

```bash
cd "$SPRINTFOUNDRY_PROJECT_ROOT" || exit 2
cat VERSION             2>/dev/null || echo "[no VERSION]"
cat MEMORY.md           2>/dev/null | tail -15 || echo "[no MEMORY.md]"
cat run-state.json      2>/dev/null || echo "[no run-state]"
cat claude-progress.txt 2>/dev/null || echo "[no progress]"
cat eval-trigger.txt    2>/dev/null || echo "[no eval-trigger]"
cat sprint-contract.md  2>/dev/null | head -5 || echo "[no contract]"
find .sprintfoundry/eval-results -maxdepth 1 -name 'eval-result-*.md' 2>/dev/null \
  || ls eval-result-*.md 2>/dev/null \
  || echo "[no eval results]"
git branch --show-current 2>/dev/null || true
git log --oneline -5    2>/dev/null || true
```

After reading these files, extract two values for use throughout this session:

```bash
python3 - <<'PY'
import json, pathlib, re

# Machine-readable current version. VERSION is primary; MEMORY.md is fallback
# recovery metadata if VERSION is missing.
version_file = pathlib.Path("VERSION")
if version_file.exists():
    current_version = version_file.read_text().strip().lstrip("v") or "0.0.0"
else:
    mem = pathlib.Path("MEMORY.md")
    current_version = "0.0.0"
    if mem.exists():
        for line in reversed(mem.read_text().splitlines()):
            if line.startswith("## Latest version:"):
                current_version = line.split(":")[-1].strip().lstrip("v") or "0.0.0"
                break

# Highest allocated sprint ID (for computing next new sprint ID)
mem = pathlib.Path("MEMORY.md")
max_sprint_id = 0
if mem.exists():
    for line in mem.read_text().splitlines():
        if line.startswith("## Max sprint ID:"):
            try: max_sprint_id = int(line.split(":")[-1].strip())
            except: pass
            break
# Also scan planner-spec.json in case MEMORY.md lags
spec_path = pathlib.Path("planner-spec.json")
if spec_path.exists():
    spec = json.loads(spec_path.read_text())
    for s in spec.get("sprints", []):
        try: max_sprint_id = max(max_sprint_id, int(s["id"]))
        except: pass

print(f"SESSION_CURRENT_VERSION={current_version}")
print(f"SESSION_MAX_SPRINT_ID={max_sprint_id}")
print(f"SESSION_NEXT_SPRINT_ID={max_sprint_id + 1}")
PY
```

Store these values mentally as `SESSION_CURRENT_VERSION`, `SESSION_MAX_SPRINT_ID`, and `SESSION_NEXT_SPRINT_ID`. Use them for all sprint ID assignments and version baseline reads in this session.

All remaining shell snippets in this skill assume you have already run
`cd "$SPRINTFOUNDRY_PROJECT_ROOT" || exit 2` in that shell.

### Branch reconciliation

```bash
ACTUAL=$(git branch --show-current 2>/dev/null || echo "")
RECORDED=$(python3 -c "import json,pathlib; d=json.loads(pathlib.Path('run-state.json').read_text()); print(d.get('active_branch',''))" 2>/dev/null || echo "")
if [ -n "$RECORDED" ] && [ "$ACTUAL" != "$RECORDED" ]; then
  echo "BRANCH MISMATCH: run-state says '$RECORDED' but current branch is '$ACTUAL'"
fi
```

If mismatch found: **stop and surface it to the user. Do not route.**

### needs_human guard

```bash
python3 -c "
import json, pathlib
d = json.loads(pathlib.Path('run-state.json').read_text()) if pathlib.Path('run-state.json').exists() else {}
print(d.get('needs_human', False))
"
```

If `true`: show `human-escalation.md` (if present) and **stop. Do not route until a human explicitly edits `run-state.json`.**

### Progress hygiene

Rewrite `claude-progress.txt` before routing if **any** of the following:
- More than 3 sprint entries
- Exceeds 60 lines
- Contains stack traces, dumps, or multi-paragraph narratives

Keep: one 5-line project summary + latest 3 sprint entries (3–5 lines each).

---

## Sprint history audit — runs before every routing rule

The **only** completion signal is `.sprintfoundry/eval-results/eval-result-{N}.md`
containing `SPRINT PASS`.

Evaluator verdicts live under `.sprintfoundry/eval-results/` so long projects do
not clutter the repository root. For backwards compatibility, Orchestrator may
also read legacy root-level `eval-result-{N}.md` files, but new Evaluator output
must use the hidden directory.

```bash
python3 - <<'PY'
import json, pathlib, sys

spec = json.loads(pathlib.Path("planner-spec.json").read_text()) \
    if pathlib.Path("planner-spec.json").exists() else {"sprints": []}
run_state = json.loads(pathlib.Path("run-state.json").read_text()) \
    if pathlib.Path("run-state.json").exists() else {}

passed, failed = set(), set()
paths = [
    *pathlib.Path(".").glob(".sprintfoundry/eval-results/eval-result-*.md"),
    *pathlib.Path(".").glob("eval-result-*.md"),
]
for p in paths:
    sid = p.stem.split("-")[-1]
    if not sid.isdigit(): continue
    txt = p.read_text(errors="ignore")
    (passed if "SPRINT PASS" in txt else failed if "SPRINT FAIL" in txt else passed).add(int(sid))

declared  = int(run_state.get("last_successful_sprint", 0) or 0)
max_passed = max(passed) if passed else 0

blocking_findings = []   # cause needs_human=true
info_findings     = []   # informational only — historical gaps, do not block

# run-state claims a sprint passed that has no eval result → blocking
if declared > 0 and declared not in passed:
    blocking_findings.append(
        f"run-state claims last_successful_sprint={declared} "
        f"but eval-result-{declared}.md lacks SPRINT PASS"
    )

for s in sorted(int(x["id"]) for x in spec.get("sprints", []) if not x.get("skipped")):
    if s in passed:
        continue
    if s > max_passed:
        # Not yet reached — normal pending sprint, not an audit issue
        continue
    # s < max_passed and s not in passed → historical gap
    # A later sprint already passed, meaning someone manually advanced past this sprint.
    # This is not a blocker — record as informational so the user is aware.
    kind = "fail_bypassed" if s in failed else "evaluator_skipped"
    info_findings.append(
        f"[historical-gap/{kind}] Sprint {s}: no SPRINT PASS recorded "
        f"(later sprints up to {max_passed} have passed — this gap will NOT block routing)"
    )

if blocking_findings:
    print("AUDIT FAILED (will pause):")
    for f in blocking_findings: print(" -", f)
    if info_findings:
        print("Also noted (historical gaps, non-blocking):")
        for f in info_findings: print("   ~", f)
    sys.exit(1)
else:
    if info_findings:
        print("Audit OK (with historical gaps noted):")
        for f in info_findings: print("   ~", f)
    else:
        print("Audit OK")
PY
```

If audit fails (blocking findings): set `run-state.json` → `mode="paused"`, `needs_human=true`. **Stop routing.**

Historical gaps (informational findings) do **not** pause routing. They are noted to the user but the harness continues from the sprint after `max(passed)`.

---

## Routing rules (evaluate in order, stop at first match)

### Rule 0 — Audit failed
`→ pause, needs_human=true, stop.`

### Rule 1 — No planner-spec.json
```
→ Agent(subagent_type="planner",
        prompt="Project root: {SPRINTFOUNDRY_PROJECT_ROOT}
                First run: cd {SPRINTFOUNDRY_PROJECT_ROOT}
                Stop if pwd is not this project root.
                New project: {user_prompt}. Write planner-spec.json, init.sh,
                and initial claude-progress.txt in this project only.")
```
Read `references/planner-agent.md` first.

### Rule 2 — eval-trigger.txt exists (sprint committed, needs CHECK or retry)

Parse N from `eval-trigger.txt`:
- `sprint=N` → initial attempt
- `sprint=N-retry` → retry (same result file — evaluator always writes `.sprintfoundry/eval-results/eval-result-N.md`)

```
IF .sprintfoundry/eval-results/eval-result-N.md contains "SPRINT PASS"
  → rm eval-trigger.txt
    Run auto-version bump (see Auto-Version Policy below)
    Append "Sprint N: PASS — {date} — {new_version}" to claude-progress.txt
    Update run-state.json: last_successful_sprint=N, retry_count=0, current_version={new_version}
    → Proceed to Rule 6

IF .sprintfoundry/eval-results/eval-result-N.md contains "SPRINT FAIL"
  IF contains "Verification tool unavailable"
    → pause: mode="paused", needs_human=true, last_failure_reason="Verification tool unavailable"
  ELSE IF contains "ARCHITECTURE DRIFT DETECTED"
    → pause: mode="paused", needs_human=true, last_failure_reason="architecture drift"
  ELSE IF retry_count > 2
    → pause: mode="paused", needs_human=true, last_failure_reason="max retries exceeded"
  ELSE
    → increment run-state.json: retry_count += 1, last_run_at = now()
      inline .sprintfoundry/eval-results/eval-result-N.md body into codex prompt
      delete .sprintfoundry/eval-results/eval-result-N.md
      → Codex retry (see commands below)

IF no .sprintfoundry/eval-results/eval-result-N.md yet
  → Proceed to Rule 2.1 (Quality Gate) before invoking Evaluator.
```

### Rule 2.1 — Quality Gate (runs before every Evaluator CHECK)

Read `references/quality-gate.md` for the full script and tool details.

```
Run quality gate script (bash, ~30 seconds):
  → Detects tech stack from planner-spec.json
  → Runs lint, type-check, coverage, security audit
  → Writes quality-gate-N.md

IF quality-gate-N.md Verdict is PASS
  → Update run-state.json: quality_retry_count=0
  → Agent(subagent_type="evaluator",
          prompt="Project root: {SPRINTFOUNDRY_PROJECT_ROOT}
                  First run: cd {SPRINTFOUNDRY_PROJECT_ROOT}
                  Stop if pwd is not this project root.
                  Run CHECK for Sprint N. Read sprint-contract.md, eval-trigger.txt,
                  and quality-gate-N.md. Use quality-gate-N.md for Craft scoring.")
    Read references/evaluator-agent.md first.

IF quality-gate-N.md Verdict is FAIL
  IF quality_retry_count > 2
    → pause: mode="paused", needs_human=true,
             last_failure_reason="quality gate failed after 2 retries"
  ELSE
    → increment run-state.json: quality_retry_count += 1
      → Codex: "Sprint N quality gate failed. Read quality-gate-N.md.
                Fix ONLY the ❌ items (lint errors, type errors, coverage gaps).
                Do not change functional logic. Re-commit and rewrite eval-trigger.txt
                with the same content. STOP after writing eval-trigger.txt."
      (Loop back to Rule 2.1 on next Orchestrator run)
```

### Rule 2.5 — Contract tampered mid-sprint
```
IF sprint-contract.md exists AND eval-trigger.txt absent
   AND sprint-contract.md contains "CONTRACT APPROVED"
   AND contract-tampered.flag exists
  → pause: mode="paused", needs_human=true,
           last_failure_reason="sprint-contract.md modified after approval"
    Delete contract-tampered.flag. Stop.
```

### Rule 3 — sprint-contract.md exists, no eval-trigger (contract phase)
```
IF sprint-contract.md tail contains "^---\nCONTRACT APPROVED"
  → Codex implementation (see commands below)
ELSE
  → Agent(subagent_type="evaluator",
          prompt="Project root: {SPRINTFOUNDRY_PROJECT_ROOT}
                  First run: cd {SPRINTFOUNDRY_PROJECT_ROOT}
                  Stop if pwd is not this project root.
                  Review sprint-contract.md. Approve or return required changes.")
```

### Rule 4 — bug-report.md exists
```
→ Update run-state.json: sprint_origin="bugfix"
  Codex: "Read planner-spec.json and bug-report.md. Propose sprint-contract.md for a bugfix sprint.
          Add the new sprint entry to planner-spec.json with
            id = max(all existing sprint IDs in planner-spec.json) + 1
          (do NOT use any gap ID or reuse an existing ID).
          Limit scope to the reported regression. Stop after writing the file."
```

### Rule 5 — change-request.md exists

Read `change-request.md` `Type:` field. All four paths ultimately funnel into the
same sprint gate (contract → approval → implementation → evaluation). The difference
is only what happens **before** the sprint contract is proposed.

Read `references/version-updates.md` for full step-by-step details on each path.

#### Type: bugfix
```
→ Update run-state.json: sprint_origin="bugfix"
  Codex: "Read planner-spec.json and change-request.md.
          Propose sprint-contract.md for a bugfix sprint.
          Add the new sprint entry to planner-spec.json with
            id = max(all existing sprint IDs in planner-spec.json) + 1
          (do NOT use any gap ID or reuse an existing ID).
          Delete change-request.md after writing the contract.
          Limit scope strictly to the reported defect. Stop after writing the file."
→ Resume at Rule 3 (contract review).
```

#### Type: minor_feature
A bounded iteration — scope fits in one sprint, no spec restructuring needed.
```
→ Update run-state.json: sprint_origin="minor_feature"
  Codex: "Read planner-spec.json and change-request.md.
          Add a new sprint entry to planner-spec.json for this feature with
            id = max(all existing sprint IDs in planner-spec.json) + 1
          (do NOT use any gap ID or reuse an existing ID).
          Propose sprint-contract.md for that sprint.
          Delete change-request.md after writing the contract.
          Stop after writing the file."
→ Resume at Rule 3 (contract review).
```

#### Type: major_feature
Scope requires new sprints to be added to the product plan before coding begins.
```
1. Read references/planner-agent.md.
2. Update run-state.json: sprint_origin="major_feature"
3. → Agent(subagent_type="planner",
           prompt="Project root: {SPRINTFOUNDRY_PROJECT_ROOT}
                   First run: cd {SPRINTFOUNDRY_PROJECT_ROOT}
                   Stop if pwd is not this project root.
                   Read planner-spec.json and change-request.md.
                   Add new sprints for the requested major feature.
                   Each new sprint must use id = max(all existing sprint IDs) + 1, +2, etc.
                   Do NOT renumber or remove existing sprint IDs. Do NOT fill in gap IDs.
                   Delete change-request.md after updating the spec.
                   Stop after writing planner-spec.json.")
4. After Planner completes → resume at Rule 6 (next unfinished sprint).
```

#### Type: replan
Full product direction change — Planner substantially rewrites the spec.
```
1. Read references/version-updates.md (replan section) before proceeding.
2. Update run-state.json: sprint_origin="replan"
3. → Agent(subagent_type="planner",
           prompt="Project root: {SPRINTFOUNDRY_PROJECT_ROOT}
                   First run: cd {SPRINTFOUNDRY_PROJECT_ROOT}
                   Stop if pwd is not this project root.
                   Read planner-spec.json and change-request.md.
                   Revise planner-spec.json for the new direction.
                   Preserve all existing sprint IDs that have SPRINT PASS eval-results —
                   mark the rest as skipped: true if they are no longer needed.
                   New sprints must use id = max(all existing sprint IDs) + 1, +2, etc. — never gap IDs.
                   Delete change-request.md after writing the updated spec.
                   Stop after writing planner-spec.json.")
4. After Planner completes → run sprint history audit → resume at Rule 6.
```

#### Type: malformed or missing
```
→ pause: set needs_human=true, last_failure_reason="malformed change-request.md"
  Tell user: "change-request.md must have Type: bugfix | minor_feature | major_feature | replan"
```

### Rule 6 — Ready for next sprint

```python
# Determine next sprint N to work on:
#   1. Collect all sprint IDs that have SPRINT PASS eval results → passed set
#   2. max_passed = max(passed) if passed else 0
#   3. Candidates = sprint IDs in planner-spec.json (not skipped) with no SPRINT PASS
#                   AND id > max_passed  (skip historical gaps — already handled)
#   4. N = min(candidates)  — lowest pending sprint after the last confirmed pass
#   5. If candidates is empty → Rule 7 (all done)
#
# Note: new sprints added by Rules 4/5 are always assigned
#       ID = SESSION_MAX_SPRINT_ID + 1 (computed during startup).
#       They will be > max_passed and therefore always land in candidates.
```

```
Find N using the algorithm above.
IF all sprints have SPRINT PASS → Rule 7
ELSE
  Before invoking Codex, create/switch sprint branch:
    branch = "codex/sprint-<N>-<slug>"
    Update run-state.json: active_branch, base_branch, current_sprint
    IF sprint_origin is not already set for this sprint (i.e. came from planner-spec.json directly):
      Update run-state.json: sprint_origin="feature"
  → Codex: "Read planner-spec.json. Propose sprint-contract.md for Sprint N.
             Follow AGENTS.md Generator rules. Stop after writing the file."
```

### Rule 7 — All sprints complete
```
→ Update run-state.json: mode="complete", needs_human=false
  Report to user. Summarise claude-progress.txt. Ask for next feature.
```

---

## Version & Release Workflow

Every sprint that reaches `SPRINT PASS` triggers an **automatic version bump**.
You never need to decide the version number — the Orchestrator decides it based
on observable signals from the sprint. See `references/version-updates.md` for
full rationale and examples.

### Quick-reference: update type → entry point

| Situation | Artifact to create | Rule fires | Auto bump |
|-----------|-------------------|------------|-----------|
| Bug regression | `bug-report.md` | Rule 4 | patch |
| Small feature / dependency upgrade | `change-request.md` `Type: minor_feature` | Rule 5 | minor |
| Significant new capability | `change-request.md` `Type: major_feature` | Rule 5 | major |
| Direction change / restructure | `change-request.md` `Type: replan` | Rule 5 | major |
| Regular planned sprint | _(planner-spec.json)_ | Rule 6 | minor |

---

## Auto-Version Policy

The Orchestrator runs this immediately after confirming `SPRINT PASS` in Rule 2,
before any other cleanup. No human input required.

### Decision rules (evaluated in order, first match wins)

**→ Major bump (X+1.0.0)** if any of:
- `run-state.json sprint_origin` is `"major_feature"` or `"replan"`
- `sprint-contract.md` contains any of: `breaking`, `remove `, `replace `, `deprecate`, `migrate`, `incompatible`
- `.sprintfoundry/eval-results/eval-result-N.md` contains `ARCHITECTURE DRIFT DETECTED`
- Any sprint in `planner-spec.json` was newly marked `skipped: true` since the last bump

**→ Patch bump (x.y.Z+1)** if all of:
- `sprint_origin` is `"bugfix"`
- `sprint-contract.md` does NOT contain `new feature`, `add `, `introduce`, `new endpoint`, `new page`

**→ Minor bump (x.Y+1.0)** in all other cases (default for planned feature sprints).

### Version bump script (run by Orchestrator after every SPRINT PASS)

```bash
python3 - <<'PY'
import json, pathlib, re, subprocess, sys

rs_path = pathlib.Path("run-state.json")
run_state = json.loads(rs_path.read_text()) if rs_path.exists() else {}
origin = run_state.get("sprint_origin", "feature")

# VERSION file is the primary machine-readable version source — never trust
# run-state.json alone. MEMORY.md is a recovery fallback if VERSION is missing.
# run-state.json.current_version can drift when re-processing historical sprints.
version_file = pathlib.Path("VERSION")
if version_file.exists():
    current = version_file.read_text().strip().lstrip("v") or "0.0.0"
else:
    current = "0.0.0"
    mem = pathlib.Path("MEMORY.md")
    if mem.exists():
        for line in reversed(mem.read_text().splitlines()):
            if line.startswith("## Latest version:"):
                current = line.split(":")[-1].strip().lstrip("v") or "0.0.0"
                break
    if current == "0.0.0":
        current = str(run_state.get("current_version", "0.0.0")).strip().lstrip("v") or "0.0.0"
contract = pathlib.Path("sprint-contract.md").read_text(errors="ignore") \
           if pathlib.Path("sprint-contract.md").exists() else ""
eval_glob = sorted([
    *pathlib.Path(".").glob(".sprintfoundry/eval-results/eval-result-*.md"),
    *pathlib.Path(".").glob("eval-result-*.md"),
], key=lambda p: int(re.search(r"\d+", p.stem).group()))
eval_text = eval_glob[-1].read_text(errors="ignore") if eval_glob else ""

sprint_n = str(run_state.get("current_sprint", "?"))
mem_path = pathlib.Path("MEMORY.md")
if mem_path.exists() and sprint_n.isdigit():
    for line in mem_path.read_text().splitlines():
        if not line.startswith("|") or line.startswith("| Sprint") or set(line.strip()) <= {"|", "-"}:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) > 3 and parts[1] == sprint_n and parts[3] == "PASS":
            print(f"MEMORY.md already records Sprint {sprint_n} PASS; release bump is idempotent no-op.")
            sys.exit(0)

major, minor, patch = map(int, current.split("."))

MAJOR_ORIGINS  = {"major_feature", "replan"}
MAJOR_KEYWORDS = ["breaking", "remove ", "replace ", "deprecate", "migrate", "incompatible"]
PATCH_ORIGIN   = "bugfix"
PATCH_EXCLUDES = ["new feature", "add ", "introduce", "new endpoint", "new page"]

if (origin in MAJOR_ORIGINS
        or any(kw in contract.lower() for kw in MAJOR_KEYWORDS)
        or "ARCHITECTURE DRIFT DETECTED" in eval_text):
    bump = "major"; major += 1; minor = 0; patch = 0
elif (origin == PATCH_ORIGIN
        and not any(kw in contract.lower() for kw in PATCH_EXCLUDES)):
    bump = "patch"; patch += 1
else:
    bump = "minor"; minor += 1; patch = 0

new_version = f"{major}.{minor}.{patch}"

# Safety guard: new version must never be less than (or equal to) what VERSION already has.
# This prevents rollback when re-processing a historical gap sprint.
def _v(s):
    try: return tuple(int(x) for x in s.split("."))
    except: return (0, 0, 0)
if _v(new_version) <= _v(current):
    # Force a patch bump on top of current instead
    cm, cn, cp = map(int, current.split("."))
    cp += 1
    new_version = f"{cm}.{cn}.{cp}"
    bump = "patch(guard)"
    major, minor, patch = cm, cn, cp

pathlib.Path("VERSION").write_text(new_version + "\n")

# Append to CHANGELOG.md
entry = f"\n## v{new_version} — Sprint {sprint_n} [{bump.upper()} bump]\n"
for obs in re.findall(r"Observation: (.+)", eval_text):
    entry += f"- {obs.strip()}\n"
with open("CHANGELOG.md", "a") as f:
    f.write(entry)

print(f"Version bump: {current} → {new_version}  ({bump})")
print(f"VERSION and CHANGELOG.md updated.")

# Append to MEMORY.md sprint ledger
title_match = re.search(r"^#+\s+Sprint\s+\d+[:\s—-]+(.+)", contract, re.MULTILINE)
sprint_title = title_match.group(1).strip()[:60] if title_match else "—"
import datetime
today = datetime.date.today().isoformat()
if not mem_path.exists():
    mem_path.write_text(
        "# SprintFoundry Sprint Ledger\n"
        "<!-- ledger rows are append-only; footer metadata may be regenerated by Orchestrator -->\n\n"
        "| Sprint | Title | Status | Version | Date | Origin |\n"
        "|--------|-------|--------|---------|------|--------|\n"
    )
mem_lines = mem_path.read_text().splitlines()
# Remove old footer lines before appending
mem_lines = [l for l in mem_lines if not l.startswith("## Latest version:") and not l.startswith("## Max sprint ID:")]
# Append new row
mem_lines.append(f"| {sprint_n} | {sprint_title} | PASS | v{new_version} | {today} | {origin} |")
# Compute max_sprint_id from all rows
max_id = 0
for l in mem_lines:
    if l.startswith("|") and not l.startswith("| Sprint"):
        parts = [p.strip() for p in l.split("|")]
        if len(parts) > 1 and parts[1].isdigit():
            max_id = max(max_id, int(parts[1]))
mem_lines.append(f"\n## Latest version: v{new_version}")
mem_lines.append(f"## Max sprint ID: {max_id}")
mem_path.write_text("\n".join(mem_lines) + "\n")
print(f"MEMORY.md updated — sprint {sprint_n} PASS recorded.")
PY
```

After the script runs, Orchestrator commits and tags:

If the script prints `release bump is idempotent no-op`, skip the release
commit/tag step and proceed with normal sprint cleanup; the sprint was already
recorded in `MEMORY.md`.

```bash
NEW_VERSION=$(cat VERSION)
git add VERSION CHANGELOG.md MEMORY.md
git commit -m "chore(release): bump to v${NEW_VERSION} after Sprint N PASS"
git tag -a "v${NEW_VERSION}" -m "v${NEW_VERSION}"
# push tag if remote is configured
git remote get-url origin >/dev/null 2>&1 && git push origin "v${NEW_VERSION}" || true
```

### Dependency / toolchain upgrades

Treat as `Type: minor_feature` (minor bump). The sprint contract success criteria
must specify **externally verifiable version evidence**:

```markdown
- [ ] Running `node --version` returns v22.x.x or higher
  Evaluator steps:
  1. bash init.sh
  2. node --version
  3. Assert output matches "v22."
```

### Breaking changes affecting already-PASS sprints

If a `major_feature` or `replan` change contradicts an already-passed sprint's contract:

1. **Do not silently proceed.** Surface the conflict to the user before Planner runs.
2. Planner either marks the old sprint `"skipped": true` (the change supersedes it) or adds a new superseding sprint.
3. The old `.sprintfoundry/eval-results/eval-result-N.md` is **never deleted** — it remains as an audit record.
4. The version bump for the replan sprint will be `major` automatically.

---

## Codex CLI invocation commands

Use these exact flags. Always `--skip-git-repo-check`.
Run them from the target project root, never from the plugin cache directory.

```bash
# Propose sprint contract
cd "$SPRINTFOUNDRY_PROJECT_ROOT" || exit 2
codex exec --full-auto \
  -c 'sandbox_permissions=["disk-full-read-access"]' \
  -c 'shell_environment_policy.inherit=all' \
  --skip-git-repo-check \
  "Read planner-spec.json. Propose sprint-contract.md for Sprint N.
   Follow AGENTS.md Generator rules. Stop after writing the file."

# Implement after contract approved
cd "$SPRINTFOUNDRY_PROJECT_ROOT" || exit 2
codex exec --full-auto \
  -c 'sandbox_permissions=["disk-full-read-access"]' \
  -c 'shell_environment_policy.inherit=all' \
  --skip-git-repo-check \
  "sprint-contract.md is approved. Implement Sprint N ONLY.
   After committing, write eval-trigger.txt containing exactly: sprint=N.
   STOP IMMEDIATELY after writing eval-trigger.txt. Follow AGENTS.md."

# Fix after SPRINT FAIL (inline the eval result body before running)
cd "$SPRINTFOUNDRY_PROJECT_ROOT" || exit 2
codex exec --full-auto \
  -c 'sandbox_permissions=["disk-full-read-access"]' \
  -c 'shell_environment_policy.inherit=all' \
  --skip-git-repo-check \
  "Sprint N failed. Fix ONLY the cited issues from the inlined Evaluator verdict below.
   Re-commit and write eval-trigger.txt containing exactly: sprint=N-retry.
   STOP after writing eval-trigger.txt. Follow AGENTS.md.
   --- EVALUATOR VERDICT ---
   {paste .sprintfoundry/eval-results/eval-result-N.md body here}"
```

> **Note**: If `scripts/orchestrate.py` exists, use its emitted command instead:
> `python3 scripts/orchestrate.py --project-dir "$SPRINTFOUNDRY_PROJECT_ROOT" --json`

---

## MEMORY.md — Sprint Ledger

`MEMORY.md` is the **ledger for sprint history and recovery metadata**. It survives context resets, session restarts, and run-state.json drift.

- Ledger rows are append-only.
- Footer metadata (`## Latest version:` and `## Max sprint ID:`) may be regenerated by the Orchestrator.
- `VERSION` is the primary machine-readable current version source; `MEMORY.md` is the fallback recovery source if `VERSION` is missing.

### Format

```markdown
# SprintFoundry Sprint Ledger
<!-- ledger rows are append-only; footer metadata may be regenerated by Orchestrator -->

| Sprint | Title | Status | Version | Date | Origin |
|--------|-------|--------|---------|------|--------|
| 1  | Initial scaffold          | PASS | v0.1.0  | 2026-01-15 | feature       |
| 2  | Auth endpoints            | PASS | v0.2.0  | 2026-01-20 | feature       |
| 11 | Fix login race condition  | PASS | v0.20.0 | 2026-03-01 | bugfix        |

## Latest version: v0.20.0
## Max sprint ID: 11
```

### Rules

- **Orchestrator writes** one ledger row per `SPRINT PASS` immediately after the version bump script runs.
- **Never edit ledger rows manually** — treat rows like an audit log.
- **`## Latest version:`** is regenerated metadata and should match the highest version ever reached (version bump guard ensures this).
- **`## Max sprint ID:`** is `max(all sprint IDs in table)` — used to compute `SESSION_NEXT_SPRINT_ID` at startup.
- If the file doesn't exist, the version bump script initialises it automatically.
- If the current sprint already has a `PASS` row, the version bump script exits as an idempotent no-op to prevent duplicate release bumps after an interrupted run.
- Historical gap sprints (e.g., Sprint 11 that was fixed retroactively) appear in chronological write order, not ID order. That is fine.

### Initialising in an existing project

If you have an existing project with no `MEMORY.md`, create it by hand after reading `planner-spec.json` and all `eval-result-*.md` files:

```bash
python3 - <<'PY'
import json, pathlib, re, datetime

spec   = json.loads(pathlib.Path("planner-spec.json").read_text())
header = ("# SprintFoundry Sprint Ledger\n"
          "<!-- append-only — do not edit manually -->\n\n"
          "| Sprint | Title | Status | Version | Date | Origin |\n"
          "|--------|-------|--------|---------|------|--------|\n")
rows = []
max_id = 0
for s in sorted(spec.get("sprints", []), key=lambda x: int(x["id"])):
    sid   = int(s["id"])
    title = s.get("title", "—")[:60]
    pat   = re.compile(rf"eval-result-{sid}\.md$")
    eval_files = [*pathlib.Path(".").glob(f".sprintfoundry/eval-results/eval-result-{sid}.md"),
                  *pathlib.Path(".").glob(f"eval-result-{sid}.md")]
    status = "—"
    if eval_files:
        txt = eval_files[0].read_text(errors="ignore")
        status = "PASS" if "SPRINT PASS" in txt else ("FAIL" if "SPRINT FAIL" in txt else "—")
    rows.append(f"| {sid} | {title} | {status} | — | — | — |")
    max_id = max(max_id, sid)
pathlib.Path("MEMORY.md").write_text(
    header + "\n".join(rows) + f"\n\n## Latest version: v{pathlib.Path('VERSION').read_text().strip() if pathlib.Path('VERSION').exists() else '0.0.0'}\n## Max sprint ID: {max_id}\n"
)
print("MEMORY.md initialised.")
PY
```

---

## run-state.json schema

```json
{
  "mode": "planning | contract | implementing | checking | paused | complete",
  "current_sprint": 1,
  "retry_count": 0,
  "last_successful_sprint": 0,
  "last_failure_reason": "",
  "needs_human": false,
  "active_branch": "codex/sprint-1-init",
  "base_branch": "main",
  "last_run_at": "2026-05-11T10:00:00Z",
  "current_version": "0.0.0",
  "sprint_origin": "feature | bugfix | minor_feature | major_feature | replan",
  "quality_retry_count": 0
}
```

`current_version` — semver string, updated by Orchestrator after every SPRINT PASS.  
`sprint_origin` — set by Orchestrator at the moment a sprint is initiated (Rule 4/5/6); used to decide the version bump level.

Ownership:
- **Only the Orchestrator writes `run-state.json`.**
- Generator (Codex) must never write to it.
- Evaluator must never write to it.
- `needs_human` can only be cleared by a human edit OR by Rule 7 (all complete).

---

## After each agent or Codex invocation

Re-read state from files from the top of the routing rules.
Never infer state from conversation history alone.

---

## Communication rules

- State which rule matched before delegating (one sentence).
- Never proceed without the expected artifact existing on disk.
- If blocked waiting on a human decision, stop and ask explicitly.
- Surface architecture drift or context mismatch; don't paper over it.

---

## Hard rules

- Never write application code.
- Never evaluate sprint quality or write `.sprintfoundry/eval-results/eval-result-*.md`.
- Never automatically clear `needs_human=true` — only a human edit clears it.
- Never skip the startup state-read.
- Never invoke `Agent(subagent_type="generator")` — Generator is always Codex via Bash.
- Never advance the sprint counter without a `SPRINT PASS` in `.sprintfoundry/eval-results/eval-result-N.md`.
- Never rewrite `harness-audit.ndjson` — it is append-only.
- Never operate from the plugin cache/base directory. Resolve
  `SPRINTFOUNDRY_PROJECT_ROOT` first, `cd` there, and stop on any project-root
  mismatch.

---

## Useful harness scripts (if present in project)

```bash
python3 scripts/orchestrate.py --project-dir "$SPRINTFOUNDRY_PROJECT_ROOT" --json
python3 scripts/orchestrate.py --project-dir "$SPRINTFOUNDRY_PROJECT_ROOT" --check-only --json
python3 scripts/harness-log.py verify                          # reconcile state vs eval-results
python3 scripts/harness-log.py tail -n 30                      # last 30 audit events
bash scripts/install-hooks.sh                                  # install git hooks
```
