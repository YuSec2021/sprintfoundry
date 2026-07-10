# SPRINTFOUNDRY.md — Project Constitution & Cross-Sprint Constraints

> **Top-level, cross-sprint constraint layer.** Every sprint reads this file
> **before** anything else, alongside `AGENTS.md`, `CLAUDE.md`, and `MEMORY.md`.
> Where `AGENTS.md` defines *how the harness runs* (process), this file defines
> *what the project is and the bars it must meet* (architecture, testing,
> examples). On the architecture / testing / example dimensions, **this file
> outranks any individual sprint decision**: a sprint contract or implementation
> that conflicts with it must be rejected, not merged.

Read order for every sprint (contract → implement → evaluate):

```
SPRINTFOUNDRY.md → AGENTS.md → CLAUDE.md → MEMORY.md → planner-spec.json → sprint-contract.md
```

Who does what with this file:

| Role | Obligation toward SPRINTFOUNDRY.md |
|------|------------------------------------|
| **Planner** | Owns and keeps **§1 Architecture** accurate; scaffolds it for a new project and updates it on every `major_feature` / `replan`. Never proposes a plan that contradicts §1. |
| **Generator (Codex)** | Implements strictly within §1. Produces the tests required by §2 and the examples required by §3. Never drifts the architecture on its own. |
| **Evaluator** | Reads this file every CHECK. **SPRINT FAIL** on any §1 architecture drift, any missing §2b feature test, or any missing §3 example. |
| **Orchestrator** | Surfaces conflicts, pauses on architecture drift, and never merges a sprint the Evaluator failed against this file. |

Changing §1 is a deliberate act: architecture/tech changes require a
`change-request.md` of `Type: major_feature` or `replan`. Ad-hoc drift discovered
during a sprint is a **failure**, not an improvement.

---

## §1. Technology Selection & Architecture — 架构感知

> The authoritative registry of what this system is built from and how it is
> structured, so every agent stays architecture-aware. The Planner fills this in
> from `planner-spec.json` for a new project; humans may edit it as the source of
> truth. `planner-spec.json.tech_stack` and `verification` must stay consistent
> with this section — if they disagree, **this section wins**.

### Product

- **Name / one-liner**: _<fill in>_
- **Primary users & core value**: _<fill in>_

### Stack (pin exact versions)

| Layer | Choice | Version pin |
|-------|--------|-------------|
| Language(s) | _<e.g. Python>_ | _<e.g. 3.12>_ |
| Frontend | _<framework or "none">_ | _<version>_ |
| Backend | _<framework>_ | _<version>_ |
| Data store | _<db>_ | _<version>_ |
| Infra / runtime | _<container, queue, etc.>_ | _<version>_ |

### Architecture

- **Style**: _<e.g. layered service, hexagonal, modular monolith, microservices>_
- **Module / boundary map**: _<top-level modules and what each owns>_
- **Data model conventions**: _<naming, IDs, migrations, timestamps>_
- **API / interface conventions**: _<REST resource style, error envelope, auth>_
- **Allowed external dependencies**: _<explicit allow-list; anything else needs a change-request>_
- **Non-negotiables**: _<hard rules, e.g. "no ORM X", "all writes go through service layer">_

### Verification surface (mirrors planner-spec.json)

- **mode**: `browser | api | cli | job | library`
- **base_url / command**: _<e.g. http://localhost:3000 or `uv run --python 3.12 --with pytest pytest -q`>_

### Project layout (test & example locations — declare once, all agents follow)

```
sprint_tests_dir:   tests/sprint/            # §2a acceptance tests, per sprint criterion
feature_tests_dir:  tests/features/<feature> # §2b permanent feature/regression suites
examples_dir:       examples/<feature>       # §3 runnable feature cases
feature_gate:       on                       # quality-gate enforces §2b+§3 on feature sprints
```

Override any of these paths here if the project uses a different convention;
the Generator, Evaluator, and quality gate use whatever is declared above.

`feature_gate: on` (default) makes the quality gate deterministically fail a
**feature-type** sprint (`feature` / `minor_feature` / `major_feature` /
`replan`) that changes application source without also touching
`feature_tests_dir` (§2b) and `examples_dir` (§3). Set it to `off` during pure
scaffolding phases where a runnable feature/example does not yet exist; bugfix
sprints are never subject to this gate.

---

## §2. Behavioral Constraints — 行为约束（测试）

Every sprint must produce **two distinct, separately-located test layers**. They
are not interchangeable, and the second is easy to skip — so it is mandatory.

### §2a. Sprint completeness tests — 冲刺完整性测试（验收）

- **Purpose**: prove *this sprint's task is done* — one automated test per
  `sprint-contract.md` success criterion.
- **Location**: `sprint_tests_dir` (see §1).
- **Enforced by**: the contract schema (`Automated test:` per criterion), the
  quality gate's `test-presence` check, and the Evaluator's per-criterion run.
- **Lifetime**: tied to the sprint's acceptance; may be short-lived scaffolding
  around the specific deliverable.

### §2b. Feature regression tests — 功能常规测试（CRUD 等）  ← MANDATORY & SEPARATE

- **Purpose**: prove *the feature keeps working* as ongoing routine coverage —
  independent of any single sprint. For a data feature this means the **full
  CRUD matrix** (create / read / update / delete), plus edge cases and error
  paths. For a non-data feature, the equivalent routine behaviours and failure
  modes.
- **Location**: `feature_tests_dir` (see §1) — a **permanent** suite that grows
  over time and runs as regression on **every** sprint, not just the one that
  introduced the feature.
- **Separation rule**: §2b lives in a different directory from §2a and is written
  as general feature coverage, **not** phrased against a specific sprint's
  acceptance criteria. Reusing a §2a sprint test to satisfy §2b is not allowed.
- **Hard rule**: a sprint that **adds or changes a feature** must add or extend
  that feature's §2b suite. A feature touched without its regression suite being
  present and passing → **SPRINT FAIL** (Evaluator) and, when detectable, a
  quality-gate failure.

> Rule of thumb: §2a answers "did we build what the contract asked?"; §2b answers
> "does create/read/update/delete (and the unhappy paths) still work?".

---

## §3. Example / Case Constraints — 案例约束

- **Every completed feature ships a runnable example/case** under `examples_dir`
  (see §1) that demonstrates real end-to-end usage of the feature.
- Acceptable forms (pick what fits the verification mode): a runnable script,
  a sample request collection (`.http` / curl script), seed data + a walkthrough,
  a demo page, or a consumer snippet for a library.
- The example must run against the app started by `init.sh` and exercise the
  feature end-to-end (not a stub). Include a one-line "how to run" comment.
- **Hard rule**: a feature marked done with no accompanying example → **SPRINT FAIL**.

---

## §4. Enforcement summary — 谁检查什么

| Constraint | Author | Automatic gate | Semantic gate (Evaluator) |
|-----------|--------|----------------|---------------------------|
| §1 Architecture stays consistent | Planner | — | FAIL on drift from §1 (also `ARCHITECTURE DRIFT DETECTED`) |
| §2a Sprint acceptance tests | Generator | `test-presence` + per-criterion `Automated test:` | FAIL if any criterion's test missing/failing |
| §2b Feature regression tests (CRUD) | Generator | `test-presence` + `feature-gate` (feature sprint must touch `feature_tests_dir`) | FAIL if the touched feature has no separate, passing regression suite |
| §3 Feature example | Generator | `feature-gate` (feature sprint must touch `examples_dir`) | FAIL if the feature has no runnable example |

Two enforcement layers work together. The **quality gate**
(`references/quality-gate.md`) is deterministic: `test-presence` requires code
changes to ship *some* test, and `feature-gate` requires a feature-type sprint
that changes application source to also touch `feature_tests_dir` (§2b) and
`examples_dir` (§3). The **Evaluator** is the semantic gate for what a static
check cannot judge: §1 architecture drift, and whether the §2b tests / §3 example
actually exercise the feature and pass. A sprint must clear both.

---

## §5. Sprint definition-of-done checklist (all boxes required)

- [ ] Implementation stays within §1 architecture & tech pins.
- [ ] §2a: every contract criterion has its own passing automated test.
- [ ] §2b: the feature's separate regression/CRUD suite exists, is updated, and passes.
- [ ] §3: a runnable example/case for the feature exists and runs end-to-end.
- [ ] Quality gate PASS (lint / types / coverage / audit / test-presence).
- [ ] Evaluator black-box CHECK PASS with no §1/§2/§3 violation.
