# Generator Rules (Codex CLI)

The Generator is always **Codex CLI** invoked via Bash. Never a Claude sub-agent.

Codex reads `AGENTS.md` in the project directory. This file is the definitive
Generator instruction set. The rules below are a reference for the Orchestrator
when building prompts or debugging Codex output.

---

## Session startup (Codex does this automatically from AGENTS.md)

```bash
cat .sprintfoundry/claude-progress.txt   # read last handoff
git log --oneline -10     # orient in history
bash init.sh              # start dev server
```

After `init.sh`, Codex runs one smoke test before touching any code.

---

## Sprint workflow (four steps)

### Step 1 — Identify current sprint
Lowest-numbered sprint in `planner-spec.json` with no
`.sprintfoundry/eval-results/eval-result-N.md` containing `SPRINT PASS`.
Legacy root-level eval-result files may be read during migration, but new
Evaluator output belongs in `.sprintfoundry/eval-results/`.

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
- Check that implementation branch matches `.sprintfoundry/run-state.json active_branch`

**Step 4a — 静态分析（强制，失败则不得请求 commit）**

根据项目栈运行对应工具，全部通过才能继续：

```bash
# JavaScript / TypeScript 项目
npx eslint . --ext .js,.jsx,.ts,.tsx --max-warnings=0
npx tsc --noEmit

# Python 项目
uv run --python <project-python-version> --with flake8 flake8 . --max-line-length=100 --exclude=.git,__pycache__,venv,.venv
uv run --python <project-python-version> --with mypy mypy . --ignore-missing-imports --no-error-summary
```

运行前必须解析 `<project-python-version>`：优先读取 `SPRINTFOUNDRY_PYTHON_VERSION`、
`.python-version`、`runtime.txt`、`pyproject.toml requires-python`。commit request
里记录实际执行的版本号（例如 `3.11`），不要保留占位符。

lint/type 错误必须修复，不可用 `// eslint-disable` 或 `# type: ignore` 绕过，
除非该行本身有充分理由（须在 commit request 的 message 中说明）。

**Step 4b — 单测与覆盖率（强制，低于阈值则不得请求 commit）**

```bash
# JavaScript
npx jest --coverage --coverageThreshold='{"global":{"lines":THRESHOLD}}'

# Python
uv run --python <project-python-version> --with pytest --with pytest-cov pytest --cov=. --cov-fail-under=THRESHOLD -q
```

覆盖率阈值（读取 `.sprintfoundry/run-state.json current_sprint` 和 `sprint_origin`）：
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

**只有 4a + 4b + 4c + 4d 全部通过，才可执行 Step 5（Commit Request）。**

### Step 5 — Commit request (Orchestrator owns Git)

Codex may be unable to write `.git/index.lock` inside the sandbox. It must not
run `git add`, `git commit`, or write `.sprintfoundry/eval-trigger.txt`. Instead it prepares a
commit request:

```bash
mkdir -p .sprintfoundry/commit-requests
CONTRACT_SHA="$(cut -d' ' -f1 sprint-contract.md.sha256)"
cat > ".sprintfoundry/commit-requests/sprint-<N>.json" <<JSON
{
  "sprint": <N>,
  "attempt": "initial",
  "contract_sha256": "$CONTRACT_SHA",
  "commit_message": "feat(sprint-<N>): <imperative description, 72 chars max>",
  "changed_files": ["<relative paths>"],
  "tests": [{"command": "uv run --python <project-python-version> --with pytest pytest -q", "status": "passed"}]
}
JSON
rm -f sprint-contract.md.sha256
echo "## Sprint <N> — $(date '+%Y-%m-%d %H:%M')" >> .sprintfoundry/claude-progress.txt
echo "Status: implementation ready, pending Orchestrator commit" >> .sprintfoundry/claude-progress.txt
```

**Stop immediately after writing the commit request and progress update.** Do
not read `planner-spec.json` for the next sprint. Do not create a new branch.
The Orchestrator commits, writes `.sprintfoundry/eval-trigger.txt`, and advances routing.

---

## Handling SPRINT FAIL (retry invocation)

1. Read the retry instructions from `.sprintfoundry/sprint_prompt/sprint-N-invoke-codex-for-retry.md` (Orchestrator inlines the Evaluator verdict there before deleting the stale eval-result)
2. Fix **only** what the Evaluator cited
3. Write `.sprintfoundry/commit-requests/sprint-N.json` with `attempt: "retry"` and `commit_message: "fix(sprint-N): address evaluator failure"`
4. Update `.sprintfoundry/claude-progress.txt` with "pending Orchestrator commit"
5. Stop immediately

---

## .sprintfoundry/sprint-fence.json

Before implementation, the Orchestrator writes `.sprintfoundry/sprint-fence.json`:
```json
{ "sprint": N, "base_commit": "<sha>" }
```

If this file exists, Codex is authorised to implement **only** the sprint named in it. Stop without writing code if asked to implement a different sprint.

---

## Commit request convention

```json
{
  "sprint": N,
  "attempt": "initial | retry",
  "contract_sha256": "<approved contract sha256 when available>",
  "commit_message": "feat(sprint-<N>): <imperative description, 72 chars max>",
  "changed_files": ["<relative paths>"],
  "tests": [{"command": "uv run --python <project-python-version> --with pytest pytest -q", "status": "passed"}]
}
```

The Orchestrator confirms the active sprint branch before committing and writing
`.sprintfoundry/eval-trigger.txt`.

---

## Hard rules for Generator

- Never evaluate its own output
- Never write "SPRINT PASS" or "SPRINT FAIL"
- Never begin coding before "CONTRACT APPROVED" is in `sprint-contract.md`
- Never remove or modify existing tests
- Never request a commit with failing tests
- Never run `git add`, `git commit`, or write `.sprintfoundry/eval-trigger.txt`
- Use `git revert` (not patches) to recover from broken state
- Never write to `.sprintfoundry/run-state.json`
- Never merge a sprint branch into `main` before Evaluator approval
- Never start a new sprint on the previous sprint's branch
