---
name: harness-branching
description: Manage the one-branch-per-sprint Git workflow for the Claude + Codex sprint harness. Use when a sprint needs a dedicated branch, when unattended mode must track active_branch/base_branch, when retries must stay on the same sprint branch, or when deciding whether a sprint branch is ready to merge after SPRINT PASS.
---

# Harness Branching

Use this skill when the sprint harness needs Git branch lifecycle management.

This skill covers:

- creating a branch for a new sprint
- switching to the correct sprint branch
- keeping retries on the same sprint branch
- recording `active_branch` and `base_branch` in `run-state.json`
- deciding when a sprint branch is ready to merge

This skill is for harness Git workflow, not for product code.

## When to use

Use this skill when:

- a new sprint is about to move from contract to implementation
- unattended mode needs to resume work on the correct branch
- a sprint has failed and needs another fix cycle on the same branch
- a sprint has passed and you need to decide whether it can merge into `main`

## Naming convention

Preferred branch name:

- `codex/sprint-<N>-<short-slug>`

Fallback if no slug is available:

- `codex/sprint-<N>`

Use a short slug derived from the sprint title in `planner-spec.json`.

## Core workflow

### 1. Determine branch identity

Read:

- `planner-spec.json`
- `run-state.json` if present
- current Git branch

Determine:

- current sprint number
- branch slug
- target branch name
- base branch, usually `main`

### 2. Prepare branch for implementation

Before implementation:

- if the sprint branch does not exist, create it from the base branch
- if it exists, switch to it
- confirm `git branch --show-current` matches the target branch

If unattended mode is active, update `run-state.json`:

- `active_branch`
- `base_branch`
- `current_sprint`

### 3. Retry behavior

If the sprint failed:

- stay on the same sprint branch
- do not create a new branch for the retry
- do not rename the branch mid-sprint

### 4. New sprint behavior

When the next sprint starts:

- create a new branch
- do not reuse the previous sprint branch
- update `run-state.json` to the new branch

### 5. Merge readiness

A sprint branch is merge-ready only when:

- the evaluator result for that sprint is `SPRINT PASS`
- the branch still corresponds to the same sprint scope
- there is no unresolved pause or escalation state

If a sprint is re-planned instead of completed:

- preserve the old branch for audit
- do not silently reuse it for a different sprint

## Output rules

- Prefer explicit branch naming over implicit current-branch assumptions
- Keep branch state synchronized with `run-state.json`
- Never implement a new sprint directly on `main`
- Never merge a sprint branch before evaluator approval
- Never create multiple concurrent branches for the same sprint unless the harness explicitly introduces parallel sprint execution
