# Version Update Workflows

Every sprint that reaches `SPRINT PASS` automatically triggers a version bump.
The Orchestrator decides the bump level — you never need to specify a version number.

## 0. Auto-version decision — how the Orchestrator decides

Each sprint carries a `sprint_origin` label set at the moment the sprint is initiated.
Together with keywords found in `sprint-contract.md` and `eval-result-N.md`, this
determines the semver bump applied immediately after `SPRINT PASS` is confirmed.

### Decision table

| sprint_origin | Contract/eval signals | Bump | Example |
|---------------|-----------------------|------|---------|
| `replan` | any | **major** | 1.3.2 → 2.0.0 |
| `major_feature` | any | **major** | 1.3.2 → 2.0.0 |
| any | contract contains "breaking / remove / deprecate / migrate / incompatible" | **major** | 1.3.2 → 2.0.0 |
| any | eval-result contains "ARCHITECTURE DRIFT DETECTED" | **major** | 1.3.2 → 2.0.0 |
| `bugfix` | contract has NO "new feature / add / introduce / new endpoint" | **patch** | 1.3.2 → 1.3.3 |
| `feature` / `minor_feature` / _(default)_ | _(anything else)_ | **minor** | 1.3.2 → 1.4.0 |

Rules are evaluated top-to-bottom; first match wins.

### Why this policy

- **Major** signals an intentional break in backwards compatibility or product direction.
  Users of the product or API need to be warned — a major bump is the conventional signal.
- **Minor** signals new capability that is backwards compatible — the typical output of
  a planned feature sprint.
- **Patch** signals a pure defect fix with no new surface — safe to adopt without review.

Marking `sprint_origin` at initiation time (not after the fact) ensures the decision
is deterministic even if the sprint contract is later modified during the retry cycle.

### What gets written

After every SPRINT PASS, the Orchestrator:
1. Runs the version bump script (in SKILL.md) → updates `VERSION` file
2. Appends a changelog entry to `CHANGELOG.md` (created if absent)
3. Commits both files: `chore(release): bump to vX.Y.Z after Sprint N PASS`
4. Tags the commit: `git tag -a vX.Y.Z`
5. Updates `run-state.json current_version`

`CHANGELOG.md` accumulates entries across the project lifetime — one entry per sprint PASS.

---

## Table of contents

1. [How to classify the update](#1-how-to-classify-the-update)
2. [Type: bugfix](#2-type-bugfix)
3. [Type: minor_feature](#3-type-minor_feature)
4. [Type: major_feature](#4-type-major_feature)
5. [Type: replan](#5-type-replan)
6. [Dependency & toolchain upgrades](#6-dependency--toolchain-upgrades)
7. [Semver tagging & changelog](#7-semver-tagging--changelog)
8. [Breaking changes across already-PASS sprints](#8-breaking-changes-across-already-pass-sprints)

---

Every update type — feature addition, dependency upgrade, full replan — passes
through the same four-phase sprint gate used for regular development:

```
CONTRACT → APPROVAL → IMPLEMENTATION → EVALUATION
```

The only difference is the **preparation step** that runs before the sprint
contract is proposed. This document covers each preparation path in detail.

---

## Table of contents

1. [How to classify the update](#1-how-to-classify-the-update)
2. [Type: bugfix](#2-type-bugfix)
3. [Type: minor_feature](#3-type-minor_feature)
4. [Type: major_feature](#4-type-major_feature)
5. [Type: replan](#5-type-replan)
6. [Dependency & toolchain upgrades](#6-dependency--toolchain-upgrades)
7. [Semver tagging & changelog](#7-semver-tagging--changelog)
8. [Breaking changes across already-PASS sprints](#8-breaking-changes-across-already-pass-sprints)

---

## 1. How to classify the update

Choose the type by answering these questions in order:

| Question | Yes → | No → continue |
|----------|-------|---------------|
| Is this fixing a regression in already-shipped behaviour? | `bugfix` | ↓ |
| Does the scope fit entirely within one sprint without restructuring the spec? | `minor_feature` | ↓ |
| Does it require adding 2+ new sprints but the overall product direction stays the same? | `major_feature` | ↓ |
| Does the product direction itself change, requiring existing unstarted sprints to be discarded or replaced? | `replan` | pause for human |

Create `change-request.md` in the project root:

```markdown
# Change Request

Type: bugfix | minor_feature | major_feature | replan

## Summary
One or two sentences describing what needs to change and why.

## Motivation
Business or technical reason for the update.

## Scope notes (optional)
Any constraints: which modules, which API surfaces, any hard "do not touch" areas.
```

---

## 2. Type: bugfix

**Use when**: a regression or defect is observed in already-shipped behaviour.

Prefer `bug-report.md` (Rule 4) for defects. Use `change-request.md Type: bugfix`
only when the issue is part of a broader iteration, not a pure regression.

### Preparation
None — Codex reads the defect description and proposes a tightly scoped contract.

### Codex prompt (Orchestrator sends)
```
Read planner-spec.json and change-request.md.
Add a new sprint entry to planner-spec.json for this bugfix (next available sprint ID).
Propose sprint-contract.md for that sprint.
Limit scope strictly to the reported defect — do not refactor unrelated code.
Delete change-request.md after writing the contract.
Stop after writing the file. Follow AGENTS.md Generator rules.
```

### Sprint contract requirements
- Success criteria must be observable regressions: "endpoint X now returns 200" not "fixed null pointer"
- Evaluator test steps must reproduce the original failure before asserting the fix

### After SPRINT PASS
Orchestrator deletes `sprint-contract.md`, `sprint-fence.json`, `eval-trigger.txt` as normal.

---

## 3. Type: minor_feature

**Use when**: adding a bounded new capability that fits in one sprint and does
not require restructuring `planner-spec.json` beyond appending one new sprint entry.

Includes: dependency version bumps, small UI additions, new CLI flags,
single new API endpoints, configuration options.

### Preparation
None for Planner — Codex appends the sprint and proposes the contract in one step.

### Codex prompt (Orchestrator sends)
```
Read planner-spec.json and change-request.md.
Determine the next available sprint ID (max existing ID + 1).
Append a new sprint entry to planner-spec.json:
  { "id": N, "title": "<feature title>", "features": ["<from change-request>"] }
Propose sprint-contract.md for Sprint N following AGENTS.md schema constraints.
Delete change-request.md after writing the contract.
Stop after writing the file. Follow AGENTS.md Generator rules.
```

### Sprint contract requirements
Same as all sprints — every criterion must be externally verifiable through
the project's configured `verification.mode`.

For dependency upgrades specifically, criteria must cite version evidence:
```markdown
- [ ] `npm list react` reports react@18.x.x or higher
  Evaluator steps:
  1. bash init.sh
  2. npm list react --depth=0
  3. Assert output contains "react@18."
```

### After SPRINT PASS
Normal cleanup. `planner-spec.json` already has the new sprint recorded.

---

## 4. Type: major_feature

**Use when**: the feature is large enough to require 2+ new sprints, or it
touches multiple systems in ways a single contract cannot capture cleanly.
The overall product direction does not change — this is additive scope.

### Preparation: Planner revises spec

Before any sprint contract is proposed, the Planner must update `planner-spec.json`
to add the new sprints.

**Orchestrator prompt to Planner:**
```
Read planner-spec.json and change-request.md.
Add new sprint entries for the requested major feature.
Rules:
- New sprint IDs must be higher than the highest existing sprint ID.
- Do NOT renumber or remove any existing sprint entry.
- Do NOT touch eval-result files or run-state.json.
- Add features to the top-level "features" list if new capabilities are introduced.
- Update design_language if the feature requires new UI patterns.
Delete change-request.md after updating planner-spec.json.
Stop after writing planner-spec.json.
```

**Planner must not:**
- Renumber completed sprints
- Mark completed sprints as skipped (they stay as-is)
- Write any implementation code

### After Planner completes
Orchestrator runs sprint history audit, then routes to Rule 6 (next unfinished sprint).
The newly added sprints will be picked up in order.

### Example planner-spec.json delta
```json
// Before
{ "sprints": [
    { "id": 1, "title": "Auth", "features": ["Login"] },
    { "id": 2, "title": "Dashboard", "features": ["Chart"] }
  ]
}

// After (major_feature adds sprints 3–4)
{ "sprints": [
    { "id": 1, "title": "Auth", "features": ["Login"] },
    { "id": 2, "title": "Dashboard", "features": ["Chart"] },
    { "id": 3, "title": "Export", "features": ["CSV export", "PDF export"] },
    { "id": 4, "title": "Notifications", "features": ["Email alerts"] }
  ]
}
```

---

## 5. Type: replan

**Use when**: the product direction changes substantially — existing unstarted
sprints are no longer relevant, or the architecture needs restructuring before
more code is written.

This is the most disruptive update type. Proceed carefully.

### Pre-flight checks (Orchestrator does these before invoking Planner)

```bash
# 1. Confirm no sprint is currently in-flight
ls sprint-contract.md 2>/dev/null && echo "WARNING: sprint in progress — complete it first"
ls eval-trigger.txt   2>/dev/null && echo "WARNING: pending eval — resolve it first"

# 2. Snapshot current state for audit trail
python3 scripts/harness-log.py note --text "replan initiated — change-request: $(cat change-request.md | head -5)"
```

If a sprint is in-flight (contract or eval pending), **do not replan yet**. Complete
or explicitly abandon the current sprint first.

### Preparation: Planner rewrites spec

**Orchestrator prompt to Planner:**
```
Read planner-spec.json and change-request.md.
Revise planner-spec.json for the new product direction.
Rules:
- Preserve all sprint entries that have a corresponding eval-result-N.md
  containing "SPRINT PASS" — set them to skipped: false (they are history, keep them).
- For any sprint that has NOT been completed (no SPRINT PASS), you may:
  - Mark it "skipped": true if it is no longer needed, OR
  - Revise its title/features to align with the new direction.
- New sprint IDs must be higher than the highest existing sprint ID — never reuse IDs.
- Update product, design_language, tech_stack, and features as needed.
- Do NOT delete eval-result files, run-state.json, or claude-progress.txt.
Delete change-request.md after writing the updated spec.
Append a note to claude-progress.txt: "Replan completed — {one-line summary}".
Stop after writing planner-spec.json.
```

### Sprint ID invariant during replan

```
Existing PASS sprints: IDs 1, 2, 3  → preserved unchanged
Old sprints 4, 5 (not started)      → marked skipped: true (or revised in-place)
New direction sprints                → IDs 6, 7, 8, … (always higher)
```

Never renumber. The sprint history audit depends on stable IDs matching eval-result filenames.

### After Planner completes

1. Run sprint history audit (it will now see `skipped: true` sprints and ignore them).
2. Update `run-state.json`:
   ```json
   {
     "mode": "planning",
     "current_sprint": <next unfinished non-skipped sprint ID>,
     "last_successful_sprint": <highest PASS sprint ID>,
     "retry_count": 0
   }
   ```
3. Resume at Rule 6.

### Skipped sprint handling in the audit script

The audit script in SKILL.md already handles `skipped: true`:
```python
for s in sorted(int(x["id"]) for x in spec.get("sprints", []) if not x.get("skipped")):
```
Skipped sprints are excluded from the "must have SPRINT PASS" check.

---

## 6. Dependency & toolchain upgrades

Classify as `Type: minor_feature` unless the upgrade requires architectural changes
(e.g., a major framework version that changes the API surface — then `major_feature`).

### Writing the sprint contract for an upgrade

The contract must specify **externally observable version evidence** for each
upgraded dependency. "Upgraded React to 18" is not a valid criterion — the
Evaluator must be able to verify it through the project's verification surface.

**For `cli` verification mode:**
```markdown
- [ ] Application runs on Node 22 LTS
  Evaluator steps:
  1. bash init.sh
  2. node --version
  3. Assert output starts with "v22."
  4. npm test — all tests pass
```

**For `browser` verification mode:**
```markdown
- [ ] Application loads without console errors after React 18 upgrade
  Evaluator steps:
  1. bash init.sh
  2. Navigate to http://localhost:3000
  3. Open browser console — assert zero errors
  4. Interact with the main user flow — assert no regression
```

**For `api` verification mode:**
```markdown
- [ ] API responds correctly after Python 3.12 upgrade
  Evaluator steps:
  1. bash init.sh
  2. python3 --version → assert "Python 3.12"
  3. curl http://localhost:8000/health → assert 200 OK
  4. curl http://localhost:8000/api/main-endpoint → assert correct shape
```

### Upgrade scope rule

An upgrade sprint must not silently fix unrelated bugs or add new features.
Evaluator will flag scope violations as Craft defects.
If the upgrade surface is large (e.g., major framework migration), split into:
- Sprint N: upgrade + fix breaking changes only
- Sprint N+1: restore and verify each major feature area

---

## 7. Semver tagging & changelog

### When to tag

Tag after **all planned non-skipped sprints reach SPRINT PASS** (Rule 7), or
after a designated milestone sprint that the team has agreed constitutes a release.

### Version number decision

The Orchestrator surfaces the version choice to the user — do not pick it autonomously:

```
"All sprints complete. What version number should I tag this release as?
 Suggestion: v1.0.0 for the first full product release, or v0.N.0 for milestones."
```

### Changelog generation

```bash
python3 - <<'PY'
import pathlib, re

results = sorted(
    pathlib.Path(".").glob("eval-result-*.md"),
    key=lambda p: int(re.search(r"\d+", p.stem).group())
)

lines = ["# Changelog\n"]
for r in results:
    text = r.read_text(errors="ignore")
    sprint_match = re.search(r"Sprint (\d+)", text)
    sprint_id = sprint_match.group(1) if sprint_match else "?"
    verdict = "PASS" if "SPRINT PASS" in text else "FAIL"
    title_match = re.search(r"## Sprint \d+: (.+)", text)
    title = title_match.group(1) if title_match else ""

    lines.append(f"## Sprint {sprint_id}{' — ' + title if title else ''} [{verdict}]")
    for obs in re.findall(r"Observation: (.+)", text):
        lines.append(f"- {obs.strip()}")
    lines.append("")

pathlib.Path("CHANGELOG.md").write_text("\n".join(lines))
print("CHANGELOG.md written")
PY
```

### Tagging procedure (Orchestrator runs, after user confirms version)

```bash
VERSION="vX.Y.Z"   # filled in by Orchestrator from user's answer

git add CHANGELOG.md
git commit -m "chore: changelog and release notes for ${VERSION}"
git tag -a "${VERSION}" -m "Release ${VERSION}"
git push origin "${VERSION}"

# Log to audit trail
python3 scripts/harness-log.py note --text "Release tagged: ${VERSION}"
```

**Orchestrator writes the tag commit. Generator (Codex) does not tag releases.**

### Patch releases (post-launch bugfixes)

Use `bug-report.md` or `change-request.md Type: bugfix` as normal. After the
bugfix sprint passes, tag a patch release (e.g., `v1.0.1`) using the same
procedure. The sprint ID continues incrementing — it does not reset per release.

---

## 8. Breaking changes across already-PASS sprints

Sometimes a `major_feature` or `replan` introduces behaviour that conflicts with
a previously passed sprint's contract (e.g., an API response shape that a passing
test relied on).

### Decision tree

```
Does the new feature change the external surface of an already-PASS sprint?
  │
  ├─ No (additive only, old surface unchanged)
  │    → proceed normally, no special handling needed
  │
  └─ Yes (breaking change)
       │
       ├─ Is the old sprint's contract intentionally superseded?
       │    → Planner marks old sprint "skipped": true in planner-spec.json
       │      and documents the incompatibility in claude-progress.txt.
       │      Old eval-result-N.md is NEVER deleted — it remains as audit record.
       │
       └─ Is the old sprint's contract still valid but the implementation drifted?
            → Treat as architecture drift. Pause with needs_human=true.
              Human decides: replan OR fix the new feature to preserve compatibility.
```

### What "skipped: true" means for an old PASS sprint

The `skipped: true` flag tells the audit to stop checking that sprint for SPRINT PASS.
It does **not** erase history — the eval-result file remains intact.

This is the correct signal when the product has deliberately moved past a feature
(e.g., Sprint 3 added a REST API that Sprint 8 replaced with GraphQL).

### Documentation requirement

Whenever a sprint is retroactively skipped due to a breaking change, Planner must
append to `claude-progress.txt`:

```
Sprint <N> marked skipped: superseded by Sprint <M>.
Reason: <one sentence — what changed and why the old contract no longer applies>.
```

This ensures the audit trail is human-readable without needing to reconstruct
the history from eval-result files alone.
