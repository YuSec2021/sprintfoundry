# Quality Gate — 代码质量门禁

质量门禁是位于 **Generator 提交之后、Evaluator 黑盒验证之前** 的独立检查阶段。
它由 Orchestrator 通过 Bash 运行，不依赖任何 agent 的主观判断。

目标：把"代码内部质量"与"黑盒功能验证"分离，各自有独立的失败通道和修复循环。

---

## 目录

1. [在 Sprint 门控中的位置](#1-在-sprint-门控中的位置)
2. [质量门禁脚本（Orchestrator 运行）](#2-质量门禁脚本)
3. [各语言工具配置](#3-各语言工具配置)
4. [覆盖率阈值](#4-覆盖率阈值)
5. [安全审计](#5-安全审计)
6. [quality-gate-N.md 格式](#6-quality-gate-nmd-格式)
7. [失败处理](#7-失败处理)
8. [Evaluator 如何使用质量门禁结果](#8-evaluator-如何使用质量门禁结果)

---

## 1. 在 Sprint 门控中的位置

```
③ IMPLEMENT (Codex commits + writes eval-trigger.txt)
        │
        ▼
   Rule 2.1: QUALITY GATE  ◀── 新增阶段
        │
   PASS ├──────────────────▶ ④ EVALUATE (Evaluator 黑盒验证)
        │
   FAIL └──────────────────▶ Codex 修复质量问题
                              quality_retry_count++
                              重新 commit + eval-trigger.txt
                              (不消耗 Evaluator retry_count)
```

质量门禁失败走独立的修复循环，**不计入** Evaluator 的 `retry_count`。
超过 `quality_retry_count > 2` → pause，`needs_human=true`。

---

## 2. 质量门禁脚本

Orchestrator 在检测到 `eval-trigger.txt` 后、调用 Evaluator 前，运行此脚本：

```bash
python3 - <<'PY'
import json, pathlib, subprocess, sys, re

spec = json.loads(pathlib.Path("planner-spec.json").read_text()) \
       if pathlib.Path("planner-spec.json").exists() else {}
stack = spec.get("tech_stack", {})
frontend = stack.get("frontend", "").lower()
backend  = stack.get("backend",  "").lower()

results = {}   # tool -> {"passed": bool, "output": str}

def run(cmd, **kwargs):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)
    return r.returncode, (r.stdout + r.stderr).strip()

sprint_n = "?"
if pathlib.Path("eval-trigger.txt").exists():
    m = re.search(r"sprint=(\d+)", pathlib.Path("eval-trigger.txt").read_text())
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
    rc, out = run("python3 -m flake8 . --max-line-length=100 --exclude=.git,__pycache__,venv 2>&1 | tail -30")
    results["flake8"] = {"passed": rc == 0, "output": out}

    rc, out = run("python3 -m mypy . --ignore-missing-imports --no-error-summary 2>&1 | tail -30")
    results["mypy"] = {"passed": rc == 0, "output": out}

    rc, out = run("python3 -m pytest --cov=. --cov-fail-under=70 -q 2>&1 | tail -20")
    results["pytest-coverage"] = {"passed": rc == 0, "output": out}

    rc, out = run("pip-audit --desc 2>&1 | tail -20")
    results["pip-audit"] = {"passed": rc == 0, "output": out}

# ── 兜底：如果未能识别任何栈，只跑 git diff stat ─────────────────────────────
if not results:
    rc, out = run("git diff HEAD~1..HEAD --stat 2>&1")
    results["git-diff-stat"] = {"passed": True, "output": out}

# 写结果文件
passed_all = all(v["passed"] for v in results.values())
lines = [f"# Quality Gate — Sprint {sprint_n}"]
lines.append(f"\n**Verdict: {'PASS' if passed_all else 'FAIL'}**\n")
for tool, res in results.items():
    icon = "✅" if res["passed"] else "❌"
    lines.append(f"\n## {icon} {tool}\n```\n{res['output'][:800]}\n```")

pathlib.Path(f"quality-gate-{sprint_n}.md").write_text("\n".join(lines))
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

若 `mypy` 未安装：`pip install mypy --break-system-packages`  
若 `pip-audit` 未安装：`pip install pip-audit --break-system-packages`

### 其他栈

若 `planner-spec.json` 中的 `tech_stack` 未覆盖以上两类，Orchestrator 记录
`quality-gate-N.md` 为"栈未识别，跳过静态分析"，**不因此失败**，但 Evaluator
须在 Craft 评分中注记"缺少静态分析覆盖"并适当扣分。

---

## 4. 覆盖率阈值

| 阶段 | 最低行覆盖率 | 说明 |
|------|------------|------|
| Sprint 1–3 | 50% | 早期搭建阶段宽松 |
| Sprint 4+ | 70% | 核心功能稳定后收紧 |
| bugfix sprint | 80% | 修复代码必须有对应测试 |

Sprint 编号从 `run-state.json.current_sprint` 读取。
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
pip-audit --desc
# 任何已知 CVE 均失败
```

### 失败处理
安全漏洞失败 **不计入 quality_retry_count**——因为漏洞修复通常需要升级依赖，
可能超出当前 sprint 范围。Orchestrator 应：
1. 记录漏洞详情到 `quality-gate-N.md`
2. 自动生成 `change-request.md Type: minor_feature` 描述需要升级的包
3. 将当前 sprint 标记为通过（漏洞单独处理）
4. 在 `claude-progress.txt` 注记：`[SECURITY] Sprint N 遗留漏洞，已创建 change-request`

---

## 6. quality-gate-N.md 格式

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
请阅读 quality-gate-{N}.md，修复所有标记为 ❌ 的问题：
- lint 错误：修复所有报告的代码风格和语法问题
- 类型错误：补全缺失的类型标注，修复类型不匹配
- 覆盖率不足：为未覆盖的分支补写单测
不要修改已通过的功能逻辑，只修复质量问题。
修复完成后重新 commit，并重写 eval-trigger.txt（内容不变：sprint={N}）。
STOP 后不要执行其他操作。
```

### Quality Gate 超过重试上限

```
quality_retry_count > 2
→ set run-state.json: mode="paused", needs_human=true
  last_failure_reason="quality gate failed after 2 retries — sprint {N}"
  append to claude-progress.txt: "PAUSED: 质量门禁连续失败，需人工介入"
```

---

## 8. Evaluator 如何使用质量门禁结果

Evaluator 在 CHECK 阶段开始前读取 `quality-gate-{N}.md`（若存在）：

```bash
cat quality-gate-{N}.md 2>/dev/null || echo "[no quality gate result]"
```

**Craft 评分影响规则：**

| 质量门禁状态 | 对 Craft 评分的影响 |
|------------|-------------------|
| PASS（所有工具通过） | 无额外扣分 |
| PASS（部分工具跳过，栈未识别） | 记录"缺少静态分析"，Craft 上限降为 8/10 |
| FAIL（不应发生，但若 Orchestrator 跳过了质量门禁） | Craft 直接 ≤ 5/10，注明"未经质量门禁" |

Evaluator **不重新运行**静态分析工具——它信任 `quality-gate-N.md` 的结果，
只将其作为 Craft 评分的上下文输入。黑盒功能验证保持不变。
