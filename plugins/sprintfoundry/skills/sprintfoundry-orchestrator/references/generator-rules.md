# Generator Rules (Codex CLI)

The Generator is always **Codex CLI** invoked via Bash. Never a Claude sub-agent.

Codex reads `AGENTS.md` in the project directory. This file is the definitive
Generator instruction set. The rules below are a reference for the Orchestrator
when building prompts or debugging Codex output.

---

## Session startup (Codex does this automatically from AGENTS.md)

```bash
cat claude-progress.txt   # read last handoff
git log --oneline -10     # orient in history
bash init.sh              # start dev server
```

After `init.sh`, Codex runs one smoke test before touching any code.

---

## Sprint workflow (four steps)

### Step 1 — Identify current sprint
Lowest-numbered sprint in `planner-spec.json` with no `eval-result-N.md` containing `SPRINT PASS`.

### Step 2 — Propose sprint contract (if `sprint-contract.md` absent)

Schema constraints the Evaluator will enforce:
- Every criterion must be **observable** through the configured verification mode (not an implementation detail)
- Every criterion must include its own `Evaluator steps:` block
- Every criterion needs **≥ 2 Evaluator test steps**
- Every step requiring navigation/HTTP must include a full URL path
- Contract needs **≥ 1** success criterion and **≥ 3** total test steps

After writing `sprint-contract.md`, stop. Orchestrator routes to Evaluator for contract review.

### Step 3 — Implement (only after `sprint-contract.md` contains "CONTRACT APPROVED")

Before writing any code:
```bash
sha256sum sprint-contract.md > sprint-contract.md.sha256
```

If `sprint-contract.md` changes after this checksum (mismatch detected), stop and surface to Orchestrator.

- Read `planner-spec.json` for VDL and architecture constraints
- Write tests alongside implementation — never after
- No inline styles in React/frontend components
- Do not carry forward abstractions unless required by current sprint
- Check that implementation branch matches `run-state.json active_branch`

**Step 4a — 静态分析（强制，失败则不得 commit）**

根据项目栈运行对应工具，全部通过才能继续：

```bash
# JavaScript / TypeScript 项目
npx eslint . --ext .js,.jsx,.ts,.tsx --max-warnings=0
npx tsc --noEmit

# Python 项目
python3 -m flake8 . --max-line-length=100 --exclude=.git,__pycache__,venv
python3 -m mypy . --ignore-missing-imports --no-error-summary
```

lint/type 错误必须修复，不可用 `// eslint-disable` 或 `# type: ignore` 绕过，
除非该行本身有充分理由（须在 commit message 中说明）。

**Step 4b — 单测与覆盖率（强制，低于阈值则不得 commit）**

```bash
# JavaScript
npx jest --coverage --coverageThreshold='{"global":{"lines":THRESHOLD}}'

# Python
python3 -m pytest --cov=. --cov-fail-under=THRESHOLD -q
```

覆盖率阈值（读取 `run-state.json current_sprint` 和 `sprint_origin`）：
- Sprint 1–3：50% · Sprint 4+：70% · bugfix sprint：80%

覆盖率不达标时补写单测，不得降低阈值。

**Step 4c — 上下文卫生（强制）**

```bash
git diff --stat     # 确认变更范围未越界
```

- 删除本 sprint 引入的 dead code 和临时调试输出（`console.log`、`print`、`debugger`）
- 合并本 sprint 产生的重复逻辑
- 确认 diff 范围仍属于已批准的 sprint 范围

**Step 4d — 功能自验**

对 `sprint-contract.md` 中每条成功标准手动验证一遍。

**只有 4a + 4b + 4c + 4d 全部通过，才可执行 Step 5（Commit）。**

### Step 4 — Signal (write eval-trigger BEFORE progress log)

```bash
# 1. Write trigger first — authoritative signal
echo "sprint=<N>" > eval-trigger.txt

# 2. Update progress log after trigger is safely on disk
echo "## Sprint <N> — $(date '+%Y-%m-%d %H:%M')" >> claude-progress.txt
echo "Status: committed, pending Evaluator CHECK" >> claude-progress.txt
```

**Stop immediately after writing `eval-trigger.txt`.** Do not read `planner-spec.json` for the next sprint. Do not create a new branch. The Orchestrator is the only entity that may advance the sprint counter.

---

## Handling SPRINT FAIL (retry invocation)

1. Read `eval-result-N.md` fully (Orchestrator inlines it into the prompt)
2. Fix **only** what the Evaluator cited
3. `git commit -m "fix(sprint-N): address evaluator failure"`
4. Write `eval-trigger.txt` with `sprint=N-retry` **before** updating progress log
5. Stop immediately

---

## sprint-fence.json

Before implementation, the Orchestrator writes `sprint-fence.json`:
```json
{ "sprint": N, "base_commit": "<sha>" }
```

If this file exists, Codex is authorised to implement **only** the sprint named in it. Stop without writing code if asked to implement a different sprint.

---

## Git commit convention

```bash
git add -A
git commit -m "feat(sprint-<N>): <imperative description, 72 chars max>"
```

Confirm commit is on the active sprint branch before writing `eval-trigger.txt`.

---

## Hard rules for Generator

- Never evaluate its own output
- Never write "SPRINT PASS" or "SPRINT FAIL"
- Never begin coding before "CONTRACT APPROVED" is in `sprint-contract.md`
- Never remove or modify existing tests
- Never commit with failing tests
- Use `git revert` (not patches) to recover from broken state
- Never write to `run-state.json`
- Never merge a sprint branch into `main` before Evaluator approval
- Never start a new sprint on the previous sprint's branch
