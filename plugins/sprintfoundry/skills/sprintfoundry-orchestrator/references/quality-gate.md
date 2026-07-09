# Quality Gate — 代码质量门禁

质量门禁是位于 **Orchestrator 提交 Generator 产物之后、Evaluator 黑盒验证之前** 的独立检查阶段。
它由 Orchestrator 通过 Bash 运行，不依赖任何 agent 的主观判断。

目标：把"代码内部质量"与"黑盒功能验证"分离，各自有独立的失败通道和修复循环。

**硬性约束（对所有 sprint、每一项更新生效）**：除静态分析（lint/type/coverage/audit）
和 Evaluator 的代码 review 之外，任何改动了应用源码的 sprint 都**必须**附带对应的
自动化测试脚本。质量门禁内置的 **test-presence** 检查会对 sprint 的 diff 做静态判定：
源码有改动但没有新增/修改任何测试文件 → 直接 FAIL。纯文档/配置/标记（md、json、
yaml、html、css 等）改动豁免——它们没有可测的行为，且各自有独立的 lint 门禁。

---

## 目录

1. [在 Sprint 门控中的位置](#1-在-sprint-门控中的位置)
2. [质量门禁脚本（Orchestrator 运行）](#2-质量门禁脚本)
3. [各语言工具配置](#3-各语言工具配置)
4. [覆盖率阈值](#4-覆盖率阈值)
5. [安全审计](#5-安全审计)
6. [.sprintfoundry/results/quality/quality-gate-N.md 格式](#6-sprintfoundryquality-gatesquality-gate-nmd-格式)
7. [失败处理](#7-失败处理)
8. [Evaluator 如何使用质量门禁结果](#8-evaluator-如何使用质量门禁结果)

---

## 1. 在 Sprint 门控中的位置

```
③ IMPLEMENT (Codex writes commit request; Orchestrator commits + writes .sprintfoundry/signals/eval-trigger.txt)
        │
        ▼
   Rule 2.1: QUALITY GATE  ◀── lint / type / coverage / audit
        │                       + test-presence（源码改动必须带测试）
   PASS ├──────────────────▶ ④ EVALUATE (Evaluator 黑盒验证)
        │
   FAIL └──────────────────▶ Codex 修复质量问题（含补测试脚本）
                              quality_retry_count++
                              写新的 commit request
                              (不消耗 Evaluator retry_count)
```

质量门禁失败走独立的修复循环，**不计入** Evaluator 的 `retry_count`。
超过 `quality_retry_count > 2` → pause，`needs_human=true`。

---

## 2. 质量门禁脚本

Orchestrator 在检测到 `.sprintfoundry/signals/eval-trigger.txt` 后、调用 Evaluator 前，运行此脚本：

```bash
python3 - <<'PY'
import json, os, pathlib, subprocess, sys, re, shutil

spec = json.loads(pathlib.Path("planner-spec.json").read_text()) \
       if pathlib.Path("planner-spec.json").exists() else {}
stack = spec.get("tech_stack", {})
frontend = stack.get("frontend", "").lower()
backend  = stack.get("backend",  "").lower()

results = {}   # tool -> {"passed": bool, "output": str}

# Directories that must never be scanned by any linter.
SKIP_DIRS = (".git", "node_modules", ".venv", "venv", "dist", "build",
             "__pycache__", ".sprintfoundry", ".next", "coverage")

def run(cmd, **kwargs):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)
    return r.returncode, (r.stdout + r.stderr).strip()

def has_files(*exts):
    """True if the project contains at least one file with any of these
    extensions, ignoring vendored/build directories."""
    for ext in exts:
        for p in pathlib.Path(".").rglob(f"*{ext}"):
            if not any(part in SKIP_DIRS for part in p.parts):
                return True
    return False

trigger_path = pathlib.Path(".sprintfoundry/signals/eval-trigger.txt")
if not trigger_path.exists():
    trigger_path = pathlib.Path("eval-trigger.txt")  # legacy compatibility

sprint_n = "?"
if trigger_path.exists():
    m = re.search(r"sprint=(\d+)", trigger_path.read_text())
    sprint_n = m.group(1) if m else "?"

# ── JavaScript / TypeScript ──────────────────────────────────────────────────
if any(x in frontend for x in ["react", "next", "vue", "node", "express"]) or \
   any(x in backend  for x in ["node", "express", "fastify", "nest"]):
    rc, out = run("npx eslint . --ext .js,.jsx,.ts,.tsx --max-warnings=0 2>&1 | tail -20")
    results["eslint"] = {"passed": rc == 0, "output": out}

    rc, out = run("npx tsc --noEmit 2>&1 | tail -30")
    results["tsc"] = {"passed": rc == 0, "output": out}

    rc, out = run("npx jest --coverage --coverageThreshold='{\"global\":{\"lines\":70}}' 2>&1 | tail -30")
    results["jest-coverage"] = {"passed": rc == 0, "output": out}

    rc, out = run("npm audit --audit-level=high 2>&1 | tail -20")
    results["npm-audit"] = {"passed": rc == 0, "output": out}

# ── Python ───────────────────────────────────────────────────────────────────
if any(x in backend for x in ["python", "fastapi", "flask", "django", "poetry"]):
    def detect_python_version():
        if os.environ.get("SPRINTFOUNDRY_PYTHON_VERSION"):
            raw = os.environ["SPRINTFOUNDRY_PYTHON_VERSION"]
        elif pathlib.Path(".python-version").exists():
            raw = pathlib.Path(".python-version").read_text().splitlines()[0]
        elif pathlib.Path("runtime.txt").exists():
            raw = pathlib.Path("runtime.txt").read_text().splitlines()[0]
        elif pathlib.Path("pyproject.toml").exists():
            match = re.search(
                r"(?m)^\s*requires-python\s*=\s*[\"']([^\"']+)[\"']",
                pathlib.Path("pyproject.toml").read_text(errors="ignore"),
            )
            raw = match.group(1) if match else ""
        else:
            probe = subprocess.run(
                "python3 -c 'import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")'",
                shell=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            raw = probe.stdout.strip()

        match = re.search(r"(?<!\d)(\d+\.\d+(?:\.\d+)?)(?!\d)", raw)
        return match.group(1) if match else "3.9"

    py = detect_python_version()
    uv_prefix = f"uv run --python {py}"
    results["python-env"] = {"passed": True, "output": f"Using uv-managed Python {py}"}

    rc, out = run(f"{uv_prefix} --with flake8 flake8 . --max-line-length=100 --exclude=.git,__pycache__,venv,.venv 2>&1 | tail -30")
    results["flake8"] = {"passed": rc == 0, "output": out}

    rc, out = run(f"{uv_prefix} --with mypy mypy . --ignore-missing-imports --no-error-summary 2>&1 | tail -30")
    results["mypy"] = {"passed": rc == 0, "output": out}

    rc, out = run(f"{uv_prefix} --with pytest --with pytest-cov pytest --cov=. --cov-fail-under=70 -q 2>&1 | tail -20")
    results["pytest-coverage"] = {"passed": rc == 0, "output": out}

    rc, out = run(f"{uv_prefix} --with pip-audit pip-audit --desc 2>&1 | tail -20")
    results["pip-audit"] = {"passed": rc == 0, "output": out}

# ── Frontend assets: HTML / CSS / vanilla JS ─────────────────────────────────
# These run by *file presence*, not tech_stack keyword, so plain static sites
# (no framework) are still covered. Tools are fetched on demand via `npx --yes`.

# HTML — htmlhint ships sane default rules, so it needs no project config.
if has_files(".html", ".htm"):
    rc, out = run(
        'npx --yes htmlhint "**/*.html" "**/*.htm" '
        '--ignore "node_modules/**" --ignore "dist/**" --ignore "build/**" '
        '2>&1 | tail -20'
    )
    results["htmlhint"] = {"passed": rc == 0, "output": out}

# CSS — stylelint requires a config. Use the project's if present; otherwise
# write a self-contained ruleset (no `extends`, so no extra packages needed)
# under .sprintfoundry/ to avoid polluting the project root.
if has_files(".css"):
    project_cfgs = [".stylelintrc", ".stylelintrc.json", ".stylelintrc.js",
                    ".stylelintrc.cjs", ".stylelintrc.yaml", ".stylelintrc.yml",
                    "stylelint.config.js", "stylelint.config.cjs"]
    cfg_flag = ""
    if not any(pathlib.Path(c).exists() for c in project_cfgs):
        cfg = pathlib.Path(".sprintfoundry/results/quality/.stylelintrc.json")
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({
            "rules": {
                "color-no-invalid-hex": True,
                "block-no-empty": True,
                "no-duplicate-selectors": True,
                "no-invalid-double-slash-comments": True,
                "property-no-unknown": True,
                "unit-no-unknown": True,
                "declaration-block-no-duplicate-properties": True,
                "declaration-block-no-shorthand-property-overrides": True
            }
        }))
        cfg_flag = f'--config "{cfg}"'
    rc, out = run(
        f'npx --yes stylelint "**/*.css" {cfg_flag} '
        '--ignore-pattern "node_modules/**" --ignore-pattern "dist/**" '
        '--ignore-pattern "build/**" 2>&1 | tail -20'
    )
    results["stylelint"] = {"passed": rc == 0, "output": out}

# Vanilla JS — only when the framework branch above did NOT already lint JS.
# Relies on the project's ESLint config (Generator establishes one in Sprint 1);
# if absent, ESLint reports it and the gate fails loudly rather than silently
# skipping JS quality.
if has_files(".js", ".mjs", ".cjs") and "eslint" not in results:
    rc, out = run(
        "npx --yes eslint . --ext .js,.mjs,.cjs --max-warnings=0 2>&1 | tail -20"
    )
    results["eslint-js"] = {"passed": rc == 0, "output": out}

# ── Test-presence gate (MANDATORY for every sprint / every code change) ──────
# Every sprint update must ship a corresponding automated test script — this is
# a hard requirement on top of static analysis and the Evaluator's code review.
# Compare the sprint's diff against its base: if application source code changed
# but no test file was added or modified, FAIL. Pure docs / config / markup
# changes are exempt (nothing behavioural to test; they have their own lint
# gates). This runs for EVERY stack, including ones not otherwise recognised.
def sprint_diff_files():
    def sh(cmd):
        code, out = run(cmd)
        return out.strip() if code == 0 else ""

    head = sh("git rev-parse HEAD 2>/dev/null")
    base = ""
    fence = pathlib.Path(".sprintfoundry/state/sprint-fence.json")
    if fence.exists():
        try:
            base = json.loads(fence.read_text()).get("base_commit", "") or ""
        except Exception:
            base = ""
    if not base:
        base_branch = "main"
        rs = pathlib.Path(".sprintfoundry/state/run-state.json")
        if rs.exists():
            try:
                base_branch = json.loads(rs.read_text()).get("base_branch", "main") or "main"
            except Exception:
                base_branch = "main"
        for cand in (base_branch, "main", "master"):
            mb = sh(f"git merge-base HEAD {cand} 2>/dev/null")
            # Ignore a base that resolves to HEAD itself (we are on/behind the
            # base branch) — that would yield an empty diff and hide the change.
            if mb and mb != head:
                base = mb
                break
    if base and base != head:
        _, out = run(f"git diff --name-only {base}..HEAD")
    else:
        parent = sh("git rev-parse --verify --quiet HEAD~1 2>/dev/null")
        if parent:  # no distinct base recorded — compare against the parent commit
            _, out = run("git diff --name-only HEAD~1..HEAD")
        else:  # parentless root commit — --root lists its files as all-added
            _, out = run("git diff-tree --root --no-commit-id --name-only -r HEAD")
    return [f for f in out.splitlines() if f.strip()]

def is_test_file(path):
    name = path.lower().rsplit("/", 1)[-1]
    if any(seg in f"/{path.lower()}" for seg in
           ("/tests/", "/test/", "/__tests__/", "/spec/", "/e2e/", "/testing/")):
        return True
    return (
        name.startswith("test_") or name.endswith("_test.py") or name.endswith("_test.go")
        or ".test." in name or ".spec." in name
        or name.endswith(("test.js", "spec.js", "test.ts", "spec.ts", "test.tsx", "spec.tsx"))
    )

CODE_EXTS = (
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java",
    ".rb", ".php", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".kt", ".swift",
    ".scala", ".vue", ".svelte", ".dart",
)
changed_paths = [
    f for f in sprint_diff_files()
    if not any(part in SKIP_DIRS for part in pathlib.Path(f).parts)
]
code_changed = [
    f for f in changed_paths
    if pathlib.Path(f).suffix.lower() in CODE_EXTS and not is_test_file(f)
]
test_changed = [f for f in changed_paths if is_test_file(f)]
if code_changed:
    if test_changed:
        results["test-presence"] = {
            "passed": True,
            "output": "Code changes ship tests:\n  " + "\n  ".join(test_changed[:20]),
        }
    else:
        results["test-presence"] = {
            "passed": False,
            "output": (
                "No test script accompanies the code changes. Every sprint update "
                "must ship a corresponding automated test.\n"
                "Add/extend tests for these changed source files:\n  "
                + "\n  ".join(code_changed[:30])
            ),
        }

# ── 兜底：如果未能识别任何栈，只跑 git diff stat ─────────────────────────────
if not results:
    rc, out = run("git diff HEAD~1..HEAD --stat 2>&1")
    results["git-diff-stat"] = {"passed": True, "output": out}

# 写结果文件。新文件统一放在 .sprintfoundry/results/quality/，避免污染项目根目录。
passed_all = all(v["passed"] for v in results.values())
lines = [f"# Quality Gate — Sprint {sprint_n}"]
lines.append(f"\n**Verdict: {'PASS' if passed_all else 'FAIL'}**\n")
for tool, res in results.items():
    icon = "✅" if res["passed"] else "❌"
    lines.append(f"\n## {icon} {tool}\n```\n{res['output'][:800]}\n```")

out_dir = pathlib.Path(".sprintfoundry") / "quality-gates"
out_dir.mkdir(parents=True, exist_ok=True)
for legacy in pathlib.Path(".").glob("quality-gate-*.md"):
    target = out_dir / legacy.name
    if not target.exists():
        shutil.move(str(legacy), str(target))
(out_dir / f"quality-gate-{sprint_n}.md").write_text("\n".join(lines))
print("PASS" if passed_all else "FAIL")
sys.exit(0 if passed_all else 1)
PY
```

脚本退出码 0 = PASS，1 = FAIL。

---

## 3. 各语言工具配置

### JavaScript / TypeScript

| 工具 | 用途 | 失败条件 |
|------|------|---------|
| ESLint | 语法/风格检查 | 任何 warning（`--max-warnings=0`）|
| tsc | 类型检查 | 任何类型错误（`--noEmit`）|
| jest + coverage | 单测 + 覆盖率 | 行覆盖率 < 70% |
| npm audit | 依赖安全 | high 或 critical 漏洞 |

推荐 `.eslintrc` 最低配置（若项目无配置，Generator 须在 Sprint 1 建立）：
```json
{
  "extends": ["eslint:recommended"],
  "rules": { "no-unused-vars": "error", "no-console": "warn" }
}
```

### Python

| 工具 | 用途 | 失败条件 |
|------|------|---------|
| flake8 | PEP8 + 语法 | 任何 error/warning |
| mypy | 类型检查 | 任何类型错误 |
| pytest --cov | 单测 + 覆盖率 | 行覆盖率 < 70% |
| pip-audit | 依赖安全 | 任何已知漏洞 |

Python 工具必须通过本地 `uv` 运行。质量门禁先读取项目声明的 Python 版本：
`SPRINTFOUNDRY_PYTHON_VERSION`、`.python-version`、`runtime.txt`、
`pyproject.toml [project].requires-python`，最后才兜底到当前 `python3`
的 major.minor。随后用同版本执行：

```bash
uv run --python <version> --with pytest --with pytest-cov pytest --cov=. --cov-fail-under=THRESHOLD -q
```

`flake8`、`mypy`、`pip-audit` 同样用 `uv run --python <version> --with <tool>`，
不要安装到系统 Python，也不要使用 `--break-system-packages`。

### 前端静态资源（HTML / CSS / 原生 JavaScript）

这一组检查**按文件存在性触发**（而非 `tech_stack` 关键词），因此即便是不带
框架的纯静态站点也能覆盖。工具通过 `npx --yes` 按需拉取。

| 工具 | 用途 | 触发条件 | 失败条件 |
|------|------|---------|---------|
| htmlhint | HTML 结构/属性检查 | 存在 `*.html` / `*.htm` | 任何 htmlhint 默认规则报错 |
| stylelint | CSS 语法/规则检查 | 存在 `*.css` | 任何 error 级规则报错 |
| eslint (vanilla) | 原生 JS 检查（`.js/.mjs/.cjs`）| 存在 JS 文件且上文框架分支**未**跑过 ESLint | 任何 warning（`--max-warnings=0`）|

要点：

- **htmlhint** 自带默认规则集，无需项目配置即可运行。
- **stylelint** 需要配置：优先使用项目自带的 `.stylelintrc*` / `stylelint.config.*`；
  若不存在，门禁会在 `.sprintfoundry/results/quality/.stylelintrc.json` 写入一份
  自包含规则集（不含 `extends`，无需额外安装配置包），不污染项目根目录。
- **原生 JS 的 ESLint** 复用项目的 ESLint 配置（Generator 应在 Sprint 1 建立）。
  与框架分支的 ESLint 互斥：若框架分支已跑过 `eslint`，这里不再重复执行。
- 所有前端检查都会跳过 `node_modules`、`dist`、`build`、`.git` 等目录。

### 其他栈

若某栈既不属于上述 Python / JS-TS / 前端三类（例如 Go、Rust、Java），
Orchestrator 记录 `.sprintfoundry/results/quality/quality-gate-N.md` 为"栈未识别，跳过静态分析"，
**不因此失败**，但 Evaluator 须在 Craft 评分中注记"缺少静态分析覆盖"并适当扣分。
注意：即使栈未识别，下面的 **test-presence** 门禁仍然生效。

### test-presence（测试脚本存在性门禁，对所有 sprint 强制）

| 项 | 说明 |
|------|------|
| 触发条件 | **每个 sprint 都跑**，与栈无关 |
| 判定方式 | 静态比对 sprint 的 diff（fence 的 `base_commit` → 与 base 分支的 merge-base → 首提交回退 `git diff-tree HEAD`） |
| 失败条件 | diff 改动了应用源码（`.py/.js/.ts/.jsx/.tsx/.go/.rs/.java/.vue/.svelte/...`）但**没有**新增或修改任何测试文件 |
| 豁免 | 纯文档/配置/标记改动（md、json、yaml、toml、html、css、图片等）——无可测行为，另有各自 lint 门禁 |

测试文件识别规则：路径含 `tests/`、`test/`、`__tests__/`、`spec/`、`e2e/`；或文件名匹配
`test_*`、`*_test.py`、`*_test.go`、`*.test.*`、`*.spec.*`。

失败时 Codex 走标准 `quality_retry` 循环补写测试脚本（不消耗 Evaluator 的 `retry_count`）。
因为比对的是**整段 sprint diff**（base..HEAD），后续只修 lint 的 quality-retry 不会因此反复失败——
只要该 sprint 累计已包含测试即通过。

> test-presence 只保证"改动带测试文件"。**每一条 success criterion 是否都有对应测试**由
> 契约 schema（Generator 写 `Automated test:`）和 Evaluator 的 CHECK 联合把关，见 AGENTS.md
> 与 `references/evaluator-agent.md`。

---

## 4. 覆盖率阈值

| 阶段 | 最低行覆盖率 | 说明 |
|------|------------|------|
| Sprint 1–3 | 50% | 早期搭建阶段宽松 |
| Sprint 4+ | 70% | 核心功能稳定后收紧 |
| bugfix sprint | 80% | 修复代码必须有对应测试 |

Sprint 编号从 `.sprintfoundry/state/run-state.json.current_sprint` 读取。
阈值判断逻辑内嵌于质量门禁脚本：

```python
sprint_num = int(run_state.get("current_sprint", 99))
origin     = run_state.get("sprint_origin", "feature")
threshold  = 80 if origin == "bugfix" else (50 if sprint_num <= 3 else 70)
```

---

## 5. 安全审计

### npm audit（Node.js 项目）
```bash
npm audit --audit-level=high
# 只在 high / critical 漏洞时失败；moderate 及以下只记录，不阻塞
```

### pip-audit（Python 项目）
```bash
uv run --python <project-python-version> --with pip-audit pip-audit --desc
# 任何已知 CVE 均失败
```

### 失败处理
安全漏洞失败 **不计入 quality_retry_count**——因为漏洞修复通常需要升级依赖，
可能超出当前 sprint 范围。Orchestrator 应：
1. 记录漏洞详情到 `.sprintfoundry/results/quality/quality-gate-N.md`
2. 自动生成 `change-request.md Type: minor_feature` 描述需要升级的包
3. 将当前 sprint 标记为通过（漏洞单独处理）
4. 在 `.sprintfoundry/claude-progress.txt` 注记：`[SECURITY] Sprint N 遗留漏洞，已创建 change-request`

---

## 6. .sprintfoundry/results/quality/quality-gate-N.md 格式

```markdown
# Quality Gate — Sprint {N}

**Verdict: PASS / FAIL**

## ✅/❌ eslint
```
{工具输出，最多 800 字符}
```

## ✅/❌ tsc
```
{工具输出}
```

## ✅/❌ jest-coverage
```
{覆盖率报告摘要}
```

## ✅/❌ npm-audit
```
{漏洞报告}
```
```

Orchestrator 将此文件路径传递给 Evaluator，Evaluator 读取后纳入 Craft 评分。

---

## 7. 失败处理

### quality_retry_count 与 retry_count 的区别

| 计数器 | 归属 | 计什么 | 上限 |
|--------|------|--------|------|
| `quality_retry_count` | Orchestrator | Quality Gate 失败次数（同一 sprint） | 2 |
| `retry_count` | Orchestrator | Evaluator SPRINT FAIL 次数 | 2 |

两者独立。Quality Gate 失败不增加 `retry_count`，反之亦然。

### Quality Gate FAIL → Codex 修复提示词

```
Sprint {N} 的代码质量检查失败。
请阅读 .sprintfoundry/results/quality/quality-gate-{N}.md，修复所有标记为 ❌ 的问题：
- lint 错误：修复所有报告的代码风格和语法问题
- 类型错误：补全缺失的类型标注，修复类型不匹配
- 覆盖率不足：为未覆盖的分支补写单测
不要修改已通过的功能逻辑，只修复质量问题。
修复完成后写 .sprintfoundry/signals/commit-requests/sprint-{N}.json，
attempt 使用 "quality_retry"。不要运行 git commit，不要改 `.sprintfoundry/signals/eval-trigger.txt`。
STOP 后不要执行其他操作。
```

### Quality Gate 超过重试上限

```
quality_retry_count > 2
→ set `.sprintfoundry/state/run-state.json`: mode="paused", needs_human=true
  last_failure_reason="quality gate failed after 2 retries — sprint {N}"
  append to `.sprintfoundry/claude-progress.txt`: "PAUSED: 质量门禁连续失败，需人工介入"
```

---

## 8. Evaluator 如何使用质量门禁结果

Evaluator 在 CHECK 阶段开始前读取 `.sprintfoundry/results/quality/quality-gate-{N}.md`（若存在）。
质量门禁脚本会把旧版根目录 `quality-gate-*.md` 迁移到该目录；旧根目录读取仅作为迁移兼容兜底，新文件不得再写到项目根目录。

```bash
cat .sprintfoundry/results/quality/quality-gate-{N}.md 2>/dev/null \
  || cat quality-gate-{N}.md 2>/dev/null \
  || echo "[no quality gate result]"
```

**Craft 评分影响规则：**

| 质量门禁状态 | 对 Craft 评分的影响 |
|------------|-------------------|
| PASS（所有工具通过） | 无额外扣分 |
| PASS（部分工具跳过，栈未识别） | 记录"缺少静态分析"，Craft 上限降为 8/10 |
| FAIL（不应发生，但若 Orchestrator 跳过了质量门禁） | Craft 直接 ≤ 5/10，注明"未经质量门禁" |

Evaluator **不重新运行**静态分析工具——它信任 quality gate 结果文件，
只将其作为 Craft 评分的上下文输入。黑盒功能验证保持不变。
