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
| **Generator** | Codex CLI | Orchestrator via `Bash: codex exec --sandbox workspace-write …` |
| **Evaluator** | Claude sub-agent | Orchestrator via `Agent(subagent_type="evaluator")` |

You are the only agent the user talks to directly.
You never write application code. You never evaluate sprint quality.
You do own Git metadata operations: Codex may be sandboxed away from `.git`, so
the Orchestrator validates commit requests, performs `git add`/`git commit`,
and writes `.sprintfoundry/eval-trigger.txt`.

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
   `.sprintfoundry/run-state.json`, `MEMORY.md`, `.sprintfoundry/`, `AGENTS.md`, or `.git/`.
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
    markers = ("planner-spec.json", ".sprintfoundry/run-state.json", "MEMORY.md", ".sprintfoundry", "AGENTS.md", ".git")
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
  `.sprintfoundry/run-state.json`, `MEMORY.md`, `planner-spec.json`, etc.).

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
cat .sprintfoundry/run-state.json      2>/dev/null || cat run-state.json 2>/dev/null || echo "[no run-state]"
cat .sprintfoundry/claude-progress.txt 2>/dev/null || cat claude-progress.txt 2>/dev/null || echo "[no progress]"
cat .sprintfoundry/eval-trigger.txt    2>/dev/null || cat eval-trigger.txt 2>/dev/null || echo "[no eval-trigger]"
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
RECORDED=$(python3 -c "import json,pathlib; d=json.loads(pathlib.Path('.sprintfoundry/run-state.json').read_text()); print(d.get('active_branch',''))" 2>/dev/null || echo "")
if [ -n "$RECORDED" ] && [ "$ACTUAL" != "$RECORDED" ]; then
  echo "BRANCH MISMATCH: run-state says '$RECORDED' but current branch is '$ACTUAL'"
fi
```

If mismatch found: **stop and surface it to the user. Do not route.**

### needs_human guard

```bash
python3 -c "
import json, pathlib
d = json.loads(pathlib.Path('.sprintfoundry/run-state.json').read_text()) if pathlib.Path('.sprintfoundry/run-state.json').exists() else {}
print(d.get('needs_human', False))
"
```

If `true`: show `human-escalation.md` (if present) and **stop. Do not route until a human explicitly edits `.sprintfoundry/run-state.json`.**

### Pending-merge recovery

After reading state, check whether a successful sprint is sitting unmerged:

```bash
python3 - <<'PY'
import json, pathlib, re

rs = json.loads(pathlib.Path(".sprintfoundry/run-state.json").read_text()) if pathlib.Path(".sprintfoundry/run-state.json").exists() else {}
active  = rs.get("active_branch", "")
base    = rs.get("base_branch", "main")
passed_n = int(rs.get("last_successful_sprint", 0) or 0)
needs_h  = rs.get("needs_human", False)

if needs_h or not active or active == base or passed_n == 0:
    print("No pending-merge recovery needed.")
else:
    # Check if the passed sprint's eval-result actually exists
    patterns = [
        f".sprintfoundry/eval-results/eval-result-{passed_n}.md",
        f"eval-result-{passed_n}.md",
    ]
    found_pass = any(
        "SPRINT PASS" in pathlib.Path(p).read_text(errors="ignore")
        for p in patterns if pathlib.Path(p).exists()
    )
    if found_pass:
        print(f"WARNING: Sprint {passed_n} PASSED but active_branch='{active}' != base='{base}'")
        print(f"  The sprint branch was never merged. Re-running sprint branch merge now.")
PY
```

If the warning fires, **run the Sprint Branch Merge script immediately** before any other routing. Do not proceed until the merge succeeds or `needs_human=true` is set.

### Progress hygiene

Rewrite `.sprintfoundry/claude-progress.txt` before routing if **any** of the following:
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
run_state = json.loads(pathlib.Path(".sprintfoundry/run-state.json").read_text()) \
    if pathlib.Path(".sprintfoundry/run-state.json").exists() else {}

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

If audit fails (blocking findings): set `.sprintfoundry/run-state.json` → `mode="paused"`, `needs_human=true`. **Stop routing.**

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
                New project: {user_prompt}. First write .sprintfoundry/scope-classification.json
                with planning_mode=standard or large_system. Then write
                planner-spec.json, init.sh, and initial .sprintfoundry/claude-progress.txt in
                this project only.")
```
Read `references/planner-agent.md` first.

### Rule 1.5 — Commit request exists (Generator finished, Orchestrator commits)

Commit requests live at `.sprintfoundry/commit-requests/sprint-N.json`. They
mean Codex finished implementation or a retry but did not touch `.git` or
`.sprintfoundry/eval-trigger.txt`.

Run this before Rule 2 so retries can be committed even while an old
`.sprintfoundry/eval-trigger.txt` is still present.

```bash
python3 - <<'PY'
import json, pathlib, subprocess, sys

root = pathlib.Path.cwd()
req_dir = root / ".sprintfoundry" / "commit-requests"
requests = sorted(req_dir.glob("sprint-*.json")) if req_dir.exists() else []
if not requests:
    print("[commit-request] none")
    sys.exit(0)
if len(requests) > 1:
    print(f"[commit-request] ERROR: multiple requests: {[p.name for p in requests]}")
    sys.exit(2)

req_path = requests[0]
req = json.loads(req_path.read_text())
sprint = int(req["sprint"])
attempt = req.get("attempt", "initial")
msg = req.get("commit_message") or f"feat(sprint-{sprint}): implement sprint"

rs_path = root / ".sprintfoundry" / "run-state.json"
if not rs_path.exists():
    rs_path = root / "run-state.json"  # legacy migration compatibility
rs = json.loads(rs_path.read_text()) if rs_path.exists() else {}
expected = int(rs.get("current_sprint", sprint))
if sprint != expected:
    print(f"[commit-request] ERROR: request sprint {sprint} != current_sprint {expected}")
    sys.exit(2)

cur = subprocess.check_output(["git", "branch", "--show-current"], text=True).strip()
active = rs.get("active_branch")
base = rs.get("base_branch", "main")
if active and cur != active:
    print(f"[commit-request] ERROR: current branch {cur!r} != active_branch {active!r}")
    sys.exit(2)
if cur == base:
    print(f"[commit-request] ERROR: refusing implementation commit on base branch {base!r}")
    sys.exit(2)

sha_file = root / "sprint-contract.md.sha256"
expected_sha = req.get("contract_sha256")
if expected_sha and sha_file.exists():
    actual = sha_file.read_text().split()[0]
    if actual != expected_sha:
        print("[commit-request] ERROR: contract sha mismatch")
        sys.exit(2)

changed = req.get("changed_files") or []
for path in changed:
    p = pathlib.Path(path)
    if p.is_absolute() or ".." in p.parts:
        print(f"[commit-request] ERROR: unsafe changed_files path: {path}")
        sys.exit(2)

if changed:
    subprocess.check_call(["git", "add", "--", *changed])
else:
    subprocess.check_call(["git", "add", "-A"])

# Never commit runtime handoff artifacts.
subprocess.run(["git", "reset", "-q", "--", ".sprintfoundry/eval-trigger.txt", "eval-trigger.txt", "sprint-contract.md.sha256", ".sprintfoundry"], check=False)

staged = subprocess.run(["git", "diff", "--cached", "--quiet"])
if staged.returncode == 0:
    print("[commit-request] ERROR: no staged changes to commit")
    sys.exit(2)

subprocess.check_call(["git", "commit", "-m", msg])

trigger = root / ".sprintfoundry" / "eval-trigger.txt"
trigger.parent.mkdir(parents=True, exist_ok=True)
if attempt == "initial":
    trigger.write_text(f"sprint={sprint}\n")
else:
    trigger.write_text(f"sprint={sprint}-retry\n")
req_path.unlink()
if sha_file.exists():
    sha_file.unlink()
print(f"[commit-request] committed sprint {sprint}; wrote {trigger.name}")
PY
```

If the script exits with code 2, pause with `needs_human=true` and surface the
printed error. If it succeeds, continue routing from Rule 2.

### Rule 2 — .sprintfoundry/eval-trigger.txt exists (sprint committed, needs CHECK or retry)

Parse N from `.sprintfoundry/eval-trigger.txt`:
- `sprint=N` → initial attempt
- `sprint=N-retry` → retry (same result file — evaluator always writes `.sprintfoundry/eval-results/eval-result-N.md`)

```
IF .sprintfoundry/eval-results/eval-result-N.md contains "SPRINT PASS"
  → rm .sprintfoundry/eval-trigger.txt
    Run auto-version bump (see Auto-Version Policy below)
    Append "Sprint N: PASS — {date} — {new_version}" to .sprintfoundry/claude-progress.txt
    Update .sprintfoundry/run-state.json: last_successful_sprint=N, retry_count=0, current_version={new_version}
    → Run Sprint Branch Merge (see below)
    → If merge succeeded: Update .sprintfoundry/run-state.json: active_branch={base_branch}
    → If merge failed: set needs_human=true, stop — do NOT proceed to Rule 6
    → Proceed to Rule 6

IF .sprintfoundry/eval-results/eval-result-N.md contains "SPRINT FAIL"
  IF contains "Verification tool unavailable"
    → pause: mode="paused", needs_human=true, last_failure_reason="Verification tool unavailable"
  ELSE IF contains "ARCHITECTURE DRIFT DETECTED"
    → pause: mode="paused", needs_human=true, last_failure_reason="architecture drift"
  ELSE IF retry_count > 2
    → pause: mode="paused", needs_human=true, last_failure_reason="max retries exceeded"
  ELSE
    → increment .sprintfoundry/run-state.json: retry_count += 1, last_run_at = now()
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
  → Writes .sprintfoundry/quality-gates/quality-gate-N.md

IF .sprintfoundry/quality-gates/quality-gate-N.md Verdict is PASS
  → Update .sprintfoundry/run-state.json: quality_retry_count=0
  → Agent(subagent_type="evaluator",
          prompt="Project root: {SPRINTFOUNDRY_PROJECT_ROOT}
                  First run: cd {SPRINTFOUNDRY_PROJECT_ROOT}
                  Stop if pwd is not this project root.
                  Run CHECK for Sprint N. Read sprint-contract.md, .sprintfoundry/eval-trigger.txt,
                  and .sprintfoundry/quality-gates/quality-gate-N.md.
                  Use the quality gate file for Craft scoring.")
    Read references/evaluator-agent.md first.

IF .sprintfoundry/quality-gates/quality-gate-N.md Verdict is FAIL
  IF quality_retry_count > 2
    → pause: mode="paused", needs_human=true,
             last_failure_reason="quality gate failed after 2 retries"
  ELSE
    → increment .sprintfoundry/run-state.json: quality_retry_count += 1
      → Codex: "Sprint N quality gate failed. Read .sprintfoundry/quality-gates/quality-gate-N.md.
                Fix ONLY the ❌ items (lint errors, type errors, coverage gaps).
                Do not change functional logic. Write
                .sprintfoundry/commit-requests/sprint-N.json with
                attempt='quality_retry'. Do not run git commit or edit
                .sprintfoundry/eval-trigger.txt. STOP after updating
                .sprintfoundry/claude-progress.txt."
      (Loop back to Rule 2.1 on next Orchestrator run)
```

### Rule 2.5 — Contract tampered mid-sprint
```
IF sprint-contract.md exists AND .sprintfoundry/eval-trigger.txt absent
   AND sprint-contract.md contains "CONTRACT APPROVED"
   AND .sprintfoundry/contract-tampered.flag exists
  → pause: mode="paused", needs_human=true,
           last_failure_reason="sprint-contract.md modified after approval"
    Delete .sprintfoundry/contract-tampered.flag. Stop.
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
→ Update .sprintfoundry/run-state.json: sprint_origin="bugfix"
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
→ Update .sprintfoundry/run-state.json: sprint_origin="bugfix"
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
→ Update .sprintfoundry/run-state.json: sprint_origin="minor_feature"
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
2. Update .sprintfoundry/run-state.json: sprint_origin="major_feature"
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
2. Update .sprintfoundry/run-state.json: sprint_origin="replan"
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
    Update .sprintfoundry/run-state.json: active_branch, base_branch, current_sprint
    IF sprint_origin is not already set for this sprint (i.e. came from planner-spec.json directly):
      Update .sprintfoundry/run-state.json: sprint_origin="feature"
  → Codex: "Read planner-spec.json. Propose sprint-contract.md for Sprint N.
             Follow AGENTS.md Generator rules. Stop after writing the file."
```

### Rule 7 — All sprints complete
```
→ Update .sprintfoundry/run-state.json: mode="complete", needs_human=false
  Report to user. Summarise .sprintfoundry/claude-progress.txt. Ask for next feature.
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
- `.sprintfoundry/run-state.json sprint_origin` is `"major_feature"` or `"replan"`
- `sprint-contract.md` contains an explicit positive compatibility/release declaration such as `Semver: major`, `Breaking changes: yes`, `Migration required: yes`, or `Public API: incompatible`
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

rs_path = pathlib.Path(".sprintfoundry/run-state.json")
run_state = json.loads(rs_path.read_text()) if rs_path.exists() else {}
origin = run_state.get("sprint_origin", "feature")

# VERSION file is the primary machine-readable version source — never trust
# .sprintfoundry/run-state.json alone. MEMORY.md is a recovery fallback if VERSION is missing.
# .sprintfoundry/run-state.json.current_version can drift when re-processing historical sprints.
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
PATCH_ORIGIN   = "bugfix"
PATCH_EXCLUDES = ["new feature", "add ", "introduce", "new endpoint", "new page"]

def _field_value(line, labels):
    stripped = re.sub(r"^\s*(?:[-*]|\d+\.)\s*", "", line).strip()
    if ":" not in stripped:
        return None
    label, value = stripped.split(":", 1)
    label = label.strip().lower()
    if label in labels:
        return value.strip().lower()
    return None

def _is_negative(value: str) -> bool:
    return bool(re.match(r"^(no|none|false|n/a|not required|without)\b", value))

def has_explicit_major_signal(text: str) -> bool:
    """Only explicit release/compatibility declarations can force a major bump."""
    for line in text.splitlines():
        value = _field_value(line, ("semver", "version bump"))
        if value and re.match(r"^major\b", value):
            return True

        value = _field_value(line, ("breaking change", "breaking changes"))
        if value and not _is_negative(value):
            return True

        value = _field_value(line, ("migration", "migration required"))
        if value and not _is_negative(value) and re.search(r"\b(yes|true|required)\b", value):
            return True

        value = _field_value(line, ("compatibility", "backward compatibility", "backwards compatibility"))
        if value and not _is_negative(value) and re.search(r"\b(breaking|broken|incompatible|not compatible)\b", value):
            return True

        value = _field_value(line, ("public api", "api compatibility"))
        if value and not _is_negative(value) and re.search(r"\b(remove|removed|replace|replaced|deprecate|deprecated|incompatible)\b", value):
            return True

    return False

if (origin in MAJOR_ORIGINS
        or has_explicit_major_signal(contract)
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

### Sprint branch merge (runs after every SPRINT PASS + version-bump commit)

Merge the sprint branch into `base_branch` (usually `main`) with full retry + git-lock recovery.

```bash
python3 - <<'PY'
import json, pathlib, subprocess, sys, time

rs_path = pathlib.Path(".sprintfoundry/run-state.json")
rs = json.loads(rs_path.read_text()) if rs_path.exists() else {}
sprint_branch = rs.get("active_branch", "")
base_branch   = rs.get("base_branch", "main")
sprint_n      = rs.get("current_sprint", "?")

# Nothing to merge if already on base or no branch recorded
if not sprint_branch or sprint_branch == base_branch:
    print(f"[merge] No sprint branch to merge — sprint_branch={sprint_branch!r}, base={base_branch!r}")
    sys.exit(0)

def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)

def clear_stale_locks():
    """Remove git lock files left by crashed processes."""
    for lock in [".git/index.lock", ".git/MERGE_HEAD", ".git/CHERRY_PICK_HEAD"]:
        p = pathlib.Path(lock)
        if p.exists():
            try:
                p.unlink()
                print(f"[merge] Removed stale lock: {lock}")
            except Exception as e:
                print(f"[merge] WARNING: could not remove {lock}: {e}")

def update_run_state(updates: dict):
    data = json.loads(rs_path.read_text()) if rs_path.exists() else {}
    data.update(updates)
    rs_path.write_text(json.dumps(data, indent=2))

MAX_RETRIES = 3
last_error  = ""

for attempt in range(1, MAX_RETRIES + 1):
    clear_stale_locks()

    # Make sure we are on the sprint branch before merging into base
    cur = run("git branch --show-current").stdout.strip()
    if cur != sprint_branch:
        r = run(f"git checkout {sprint_branch}")
        if r.returncode != 0:
            last_error = f"cannot checkout sprint branch: {r.stderr.strip()}"
            print(f"[merge] attempt {attempt}: {last_error}")
            time.sleep(5 * attempt)
            continue

    # Switch to base branch
    r = run(f"git checkout {base_branch}")
    if r.returncode != 0:
        last_error = f"cannot checkout {base_branch}: {r.stderr.strip()}"
        print(f"[merge] attempt {attempt}: {last_error}")
        run(f"git checkout {sprint_branch}")
        time.sleep(5 * attempt)
        continue

    # Attempt the merge
    msg = f"merge: sprint-{sprint_n} ({sprint_branch}) → {base_branch} after SPRINT PASS"
    r = run(f'git merge --no-ff {sprint_branch} -m "{msg}"')
    if r.returncode == 0:
        print(f"[merge] SUCCESS: {sprint_branch} → {base_branch}")
        update_run_state({"active_branch": base_branch, "merge_retry_count": 0})
        sys.exit(0)

    # Merge failed — abort cleanly and maybe retry
    last_error = r.stderr.strip() or r.stdout.strip()
    print(f"[merge] attempt {attempt} FAILED: {last_error}")
    run("git merge --abort 2>/dev/null || true")
    clear_stale_locks()
    run(f"git checkout {sprint_branch}")

    if attempt < MAX_RETRIES:
        wait = 5 * attempt
        print(f"[merge] retrying in {wait}s …")
        time.sleep(wait)

# All attempts exhausted
print(f"[merge] FAILED after {MAX_RETRIES} attempts. Last error: {last_error}")
update_run_state({
    "needs_human": True,
    "last_failure_reason": (
        f"Sprint {sprint_n} PASSED but branch merge failed after {MAX_RETRIES} attempts. "
        f"Last error: {last_error}. "
        f"To recover: git checkout {base_branch} && git merge --no-ff {sprint_branch} && "
        f"python3 -c \"import json,pathlib; d=json.loads(pathlib.Path('.sprintfoundry/run-state.json').read_text()); "
        f"d.update({{'needs_human':False,'active_branch':'{base_branch}','merge_retry_count':0}}); "
        f"pathlib.Path('.sprintfoundry/run-state.json').write_text(json.dumps(d,indent=2))\""
    )
})
sys.exit(2)
PY
```

If the script exits with code 2:
- `needs_human=true` has already been written to `.sprintfoundry/run-state.json`
- **Stop. Do NOT proceed to Rule 6.**
- Tell the user the exact recovery command printed above.

**Recovery after a stale merge failure** — if `needs_human=true` and `last_failure_reason` mentions "branch merge failed":
1. User runs the recovery commands printed in `last_failure_reason`.
2. User manually sets `needs_human=false` in `.sprintfoundry/run-state.json`.
3. Orchestrator resumes from Rule 6 on the next run.

---

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
codex exec --sandbox workspace-write \
  -c 'sandbox_permissions=["disk-full-read-access"]' \
  -c 'shell_environment_policy.inherit=all' \
  --skip-git-repo-check \
  "Read planner-spec.json. Propose sprint-contract.md for Sprint N.
   Follow AGENTS.md Generator rules. Stop after writing the file."

# Implement after contract approved
cd "$SPRINTFOUNDRY_PROJECT_ROOT" || exit 2
codex exec --sandbox workspace-write \
  -c 'sandbox_permissions=["disk-full-read-access"]' \
  -c 'shell_environment_policy.inherit=all' \
  --skip-git-repo-check \
  "sprint-contract.md is approved. Implement Sprint N ONLY.
   Do not run git add, git commit, or write .sprintfoundry/eval-trigger.txt.
   Write .sprintfoundry/commit-requests/sprint-N.json for Orchestrator commit.
   STOP IMMEDIATELY after updating .sprintfoundry/claude-progress.txt. Follow AGENTS.md."

# Fix after SPRINT FAIL (inline the eval result body before running)
cd "$SPRINTFOUNDRY_PROJECT_ROOT" || exit 2
codex exec --sandbox workspace-write \
  -c 'sandbox_permissions=["disk-full-read-access"]' \
  -c 'shell_environment_policy.inherit=all' \
  --skip-git-repo-check \
  "Sprint N failed. Fix ONLY the cited issues from the inlined Evaluator verdict below.
   Do not run git add, git commit, or write .sprintfoundry/eval-trigger.txt.
   Write .sprintfoundry/commit-requests/sprint-N.json with attempt='retry'.
   STOP after updating .sprintfoundry/claude-progress.txt. Follow AGENTS.md.
   --- EVALUATOR VERDICT ---
   {paste .sprintfoundry/eval-results/eval-result-N.md body here}"
```

> **Note**: If `scripts/orchestrate.py` exists, use its emitted command instead:
> `python3 scripts/orchestrate.py --project-dir "$SPRINTFOUNDRY_PROJECT_ROOT" --json`

---

## MEMORY.md — Sprint Ledger

`MEMORY.md` is the **ledger for sprint history and recovery metadata**. It survives context resets, session restarts, and .sprintfoundry/run-state.json drift.

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

## .sprintfoundry/run-state.json schema

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
  "quality_retry_count": 0,
  "merge_retry_count": 0
}
```

`current_version` — semver string, updated by Orchestrator after every SPRINT PASS.  
`sprint_origin` — set by Orchestrator at the moment a sprint is initiated (Rule 4/5/6); used to decide the version bump level.

Ownership:
- **Only the Orchestrator writes `.sprintfoundry/run-state.json`.**
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
- Never rewrite `.sprintfoundry/harness-audit.ndjson` — it is append-only.
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
