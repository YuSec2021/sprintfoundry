#!/usr/bin/env python3
"""State-driven orchestrator for planning, iteration, and bugfix flows."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RETRY_LIMIT = 2
SPRINT_PASS = "SPRINT PASS"
SPRINT_FAIL = "SPRINT FAIL"
CONTRACT_APPROVED = "CONTRACT APPROVED"
CODEX_EXEC_MODERN_MIN_VERSION = (0, 120, 0)
HARNESS_DIR = ".sprintfoundry"
EVAL_RESULTS_DIR = f"{HARNESS_DIR}/eval-results"
LOGS_DIR = f"{HARNESS_DIR}/logs"
SPRINT_PROMPT_DIR = f"{HARNESS_DIR}/sprint_prompt"


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def append_ndjson(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _has_multi_paragraph_narrative(lines: list[str]) -> bool:
    """Detect multi-paragraph narrative: 3+ consecutive non-empty lines separated by blank lines."""
    paragraphs = 0
    in_paragraph = False
    for line in lines:
        if line.strip():
            if not in_paragraph:
                paragraphs += 1
                in_paragraph = True
        else:
            in_paragraph = False
    return paragraphs > 6


def _extract_project_summary(lines: list[str], sprint_headers: list[int]) -> list[str]:
    """Return lines before the first sprint header as the project summary (max 5 non-empty)."""
    boundary = sprint_headers[0] if sprint_headers else len(lines)
    preamble = [line for line in lines[:boundary] if line.strip()]
    if preamble:
        return preamble[:5]
    return ["Project summary compressed by orchestrator."]


def compress_progress(path: Path) -> None:
    if not path.exists():
        return
    lines = read_text(path).splitlines()
    sprint_headers = [idx for idx, line in enumerate(lines) if line.startswith("## Sprint ")]
    should_compress = (
        len(lines) > 60
        or len(sprint_headers) > 3
        or any("Traceback" in line or "FAILED" in line for line in lines)
        or _has_multi_paragraph_narrative(lines)
    )
    if not should_compress:
        return

    summary = _extract_project_summary(lines, sprint_headers)

    entries: list[str] = []
    for header_index in sprint_headers[-3:]:
        next_headers = [idx for idx in sprint_headers if idx > header_index]
        end_index = next_headers[0] if next_headers else len(lines)
        entries.extend([line for line in lines[header_index:end_index] if line.strip()][:5])
        entries.append("")

    write_text(path, "\n".join(summary + [""] + entries).rstrip() + "\n")


def extract_sprint_id(value: str) -> int | None:
    match = re.search(r"sprint=(\d+)", value)
    return int(match.group(1)) if match else None


def eval_sprint_id(path: Path) -> int | None:
    match = re.search(r"eval-result-(\d+)", path.name)
    return int(match.group(1)) if match else None


def eval_result_paths(root: Path) -> list[Path]:
    """Return eval-result files from the hidden harness dir plus legacy root files.

    New projects write evaluator verdicts under .sprintfoundry/eval-results/ so
    long sprint histories do not clutter the project root. Root-level files are
    still read for backwards compatibility with older harness runs.
    """
    candidates = [
        *root.glob(f"{EVAL_RESULTS_DIR}/eval-result-*.md"),
        *root.glob("eval-result-*.md"),
    ]
    return sorted({path.resolve(): path for path in candidates}.values())


def eval_result_path(root: Path, sprint_id: int) -> Path:
    return root / EVAL_RESULTS_DIR / f"eval-result-{sprint_id}.md"


def parse_semver(version: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def codex_version_tuple() -> tuple[int, int, int] | None:
    try:
        result = subprocess.run(
            ["codex", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return parse_semver(f"{result.stdout}\n{result.stderr}")


def codex_command(prompt_file: str) -> str:
    wrapper_prompt = (
        f"Read the local SprintFoundry prompt file at {prompt_file} and follow it exactly. "
        "The file content is the authoritative prompt for this Codex run."
    )
    quoted_prompt = shlex.quote(wrapper_prompt)
    version = codex_version_tuple()
    if version is not None and version >= CODEX_EXEC_MODERN_MIN_VERSION:
        # --sandbox workspace-write keeps writes restricted to the workspace.
        # Codex writes project files only; Orchestrator owns Git metadata.
        # shell_environment_policy.inherit=all keeps project-root/env hints available.
        return (
            "codex exec --sandbox workspace-write"
            " -c 'sandbox_permissions=[\"disk-full-read-access\"]'"
            " -c 'shell_environment_policy.inherit=all'"
            f" --skip-git-repo-check {quoted_prompt}"
        )
    return f"codex -a never exec --skip-git-repo-check {quoted_prompt}"


def parse_key(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}\s*:\s*(.+)$", text, flags=re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else None


@dataclass
class RouteDecision:
    rule: str
    action: str
    rationale: str
    mode: str
    current_sprint: int
    command: str | None = None
    codex_prompt: str | None = None
    prompt_file: str | None = None
    prompt: str | None = None
    needs_human: bool = False
    last_failure_reason: str = ""
    cleanup_eval_trigger: bool = False
    # When True, sprint-contract.md and .sprintfoundry/sprint-fence.json are deleted so the
    # next sprint must go through full contract negotiation before coding starts.
    cleanup_contract: bool = False
    # When True, eval-result-{current_sprint}.md is deleted *before* Codex runs
    # its retry. Without this the orchestrator loops on the stale FAIL verdict:
    # a retry handoff that preserves .sprintfoundry/eval-trigger.txt on the same sprint leaves the
    # file system indistinguishable from the pre-retry state, so every
    # subsequent round routes to `invoke_codex_for_retry` again while the
    # Evaluator never gets a chance to re-verify the fix. The retry prompt
    # inlines the full FAIL body so Codex has complete context even after
    # the file is gone; the next orchestrator round then sees the missing
    # eval-result and correctly routes to invoke_evaluator.
    cleanup_eval_result: bool = False
    active_branch: str = ""
    base_branch: str = ""


@dataclass(frozen=True)
class SprintAuditFinding:
    """One inconsistency between declared state (.sprintfoundry/run-state.json, progress log,
    git history) and the authoritative source (eval-result-{N}.md)."""

    kind: str
    sprint: int
    detail: str

    def format(self) -> str:
        return f"[{self.kind}] Sprint {self.sprint}: {self.detail}"


def audit_sprint_history(project: "HarnessProject") -> list[SprintAuditFinding]:
    """Enforce the monotonic-PASS invariant.

    The only authoritative signal that Sprint N is complete is:
        eval-result-{N}.md exists AND contains "SPRINT PASS"

    Everything else (.sprintfoundry/run-state.json.last_successful_sprint, .sprintfoundry/claude-progress.txt,
    branch state) is derived state. This audit detects the historical failure
    modes seen in this repo:

      A. Sprint bootstrap bypass — Codex committed sprint code without ever
         going through contract/eval-trigger, so eval-result-{N}.md was never
         written even though later sprints proceeded.
      B. Manual FAIL override — a human chore commit updated .sprintfoundry/run-state.json
         to advance past a sprint whose eval-result still says SPRINT FAIL.

    Findings are *blocking*: the orchestrator must pause until a human either
    fixes the state (delete the stray later work / re-run Evaluator) or
    explicitly acknowledges by editing .sprintfoundry/run-state.json.needs_human back to false.
    """
    if not project.spec_path.exists():
        return []

    spec = project.planner_spec()
    run_state = project.load_run_state()
    declared_last = int(run_state.get("last_successful_sprint", 0) or 0)

    findings: list[SprintAuditFinding] = []

    planned_ids = [
        int(sprint["id"])
        for sprint in spec.get("sprints", [])
        if not sprint.get("skipped")
    ]
    planned_ids.sort()
    if not planned_ids:
        return []

    passed_ids: set[int] = set()
    failed_ids: set[int] = set()
    for path in project.eval_results():
        sid = eval_sprint_id(path)
        if sid is None:
            continue
        text = read_text(path)
        if SPRINT_PASS in text:
            passed_ids.add(sid)
        elif SPRINT_FAIL in text:
            failed_ids.add(sid)

    # A. .sprintfoundry/run-state.json claims higher success than eval-result files support.
    if declared_last > 0 and declared_last not in passed_ids:
        findings.append(
            SprintAuditFinding(
                kind="run_state_unsupported",
                sprint=declared_last,
                detail=(
                    f".sprintfoundry/run-state.json says last_successful_sprint={declared_last} "
                    f"but eval-result-{declared_last}.md is missing or does not "
                    f"contain SPRINT PASS"
                ),
            )
        )

    # B. Monotonic invariant: every planned sprint strictly below declared_last
    #    must carry a SPRINT PASS. Also flag sprints below declared_last whose
    #    eval-result is explicitly SPRINT FAIL (bypass).
    for sid in planned_ids:
        if sid > declared_last:
            continue
        if sid not in passed_ids:
            if sid in failed_ids:
                findings.append(
                    SprintAuditFinding(
                        kind="fail_bypassed",
                        sprint=sid,
                        detail=(
                            f"eval-result-{sid}.md contains SPRINT FAIL but "
                            f".sprintfoundry/run-state.json claims Sprint {declared_last} "
                            f"already succeeded; FAIL was never resolved by Evaluator"
                        ),
                    )
                )
            else:
                findings.append(
                    SprintAuditFinding(
                        kind="evaluator_skipped",
                        sprint=sid,
                        detail=(
                            f"eval-result-{sid}.md is missing but .sprintfoundry/run-state.json "
                            f"claims Sprint {declared_last} already succeeded; "
                            f"Evaluator never ran for Sprint {sid}"
                        ),
                    )
                )

    # C. Evaluator has scored later sprints as PASS while earlier sprints never
    #    passed — the "gap" case. Detects the scenario where PASS exists for
    #    Sprint K but some Sprint M < K has no eval-result-{M}.md with PASS.
    if passed_ids:
        max_passed = max(passed_ids)
        for sid in planned_ids:
            if sid >= max_passed:
                break
            if sid in passed_ids:
                continue
            already_flagged = any(
                f.sprint == sid and f.kind in {"evaluator_skipped", "fail_bypassed"}
                for f in findings
            )
            if already_flagged:
                continue
            if sid in failed_ids:
                findings.append(
                    SprintAuditFinding(
                        kind="fail_bypassed",
                        sprint=sid,
                        detail=(
                            f"Sprint {max_passed} has SPRINT PASS but "
                            f"eval-result-{sid}.md still contains SPRINT FAIL"
                        ),
                    )
                )
            else:
                findings.append(
                    SprintAuditFinding(
                        kind="evaluator_skipped",
                        sprint=sid,
                        detail=(
                            f"Sprint {max_passed} has SPRINT PASS but "
                            f"eval-result-{sid}.md is missing"
                        ),
                    )
                )

    return findings


class HarnessProject:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.state_dir = self.root / HARNESS_DIR
        self.logs_dir = self.root / LOGS_DIR
        self.sprint_prompt_dir = self.root / SPRINT_PROMPT_DIR
        self.spec_path = self.root / "planner-spec.json"
        self.contract_path = self.root / "sprint-contract.md"
        self.eval_trigger_path = self.state_dir / "eval-trigger.txt"
        self.progress_path = self.state_dir / "claude-progress.txt"
        self.run_state_path = self.state_dir / "run-state.json"
        self.log_path = self.logs_dir / "orchestrator-log.ndjson"
        self.events_path = self.logs_dir / "run-events.ndjson"
        # Single append-only forensic audit log — the authoritative timeline of
        # every harness operation. Unlike orchestrator-log/run-events (which
        # only capture orchestrator-internal decisions), this file also records
        # state transitions, git commits, hook allow/block outcomes, and manual
        # human annotations. Never rewritten, never truncated.
        self.audit_path = self.state_dir / "harness-audit.ndjson"
        self.scope_classification_path = self.state_dir / "scope-classification.json"
        self.change_request_path = self.root / "change-request.md"
        self.bug_report_path = self.root / "bug-report.md"
        # Sprint fence: records expected sprint + git HEAD at implementation start.
        # Acts like a page-protection entry — any commit outside the fenced sprint
        # triggers a boundary-violation pause instead of silently continuing.
        self.sprint_fence_path = self.state_dir / "sprint-fence.json"
        self.contract_tampered_path = self.state_dir / "contract-tampered.flag"
        self._migrate_legacy_runtime_files()

    def _migrate_legacy_runtime_files(self) -> None:
        """Move legacy root-level machine state into .sprintfoundry/.

        Human-facing control files such as planner-spec.json, sprint-contract.md,
        change-request.md, and bug-report.md intentionally stay at the project
        root for discoverability.
        """
        migrations = {
            "run-state.json": self.run_state_path,
            "eval-trigger.txt": self.eval_trigger_path,
            "sprint-fence.json": self.sprint_fence_path,
            "claude-progress.txt": self.progress_path,
            "contract-tampered.flag": self.contract_tampered_path,
            "harness-audit.ndjson": self.audit_path,
            "orchestrator-log.ndjson": self.log_path,
            "run-events.ndjson": self.events_path,
            "scope-classification.json": self.scope_classification_path,
        }
        for legacy_name, target in migrations.items():
            legacy = self.root / legacy_name
            if not legacy.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.is_file():
                if legacy.suffix == ".ndjson":
                    existing = target.read_text(encoding="utf-8")
                    incoming = legacy.read_text(encoding="utf-8")
                    separator = "" if not existing or existing.endswith("\n") else "\n"
                    target.write_text(existing + separator + incoming, encoding="utf-8")
                legacy.unlink()
            else:
                legacy.rename(target)

    def append_audit(self, event: str, actor: str, payload: dict[str, Any] | None = None,
                     sprint: int | None = None) -> None:
        """Append a single line to .sprintfoundry/harness-audit.ndjson.

        Best-effort: audit logging must never break the orchestrator itself.
        Permission errors or disk-full conditions are swallowed silently so
        the control-path keeps running; the caller should still surface the
        underlying state change through its normal return channel.
        """
        record: dict[str, Any] = {
            "ts": iso_now(),
            "event": event,
            "actor": actor,
        }
        if sprint is not None:
            record["sprint"] = sprint
        record["payload"] = payload or {}
        try:
            append_ndjson(self.audit_path, record)
        except OSError:
            pass

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=str(self.root),
            check=False,
        )

    def is_git_repo(self) -> bool:
        try:
            result = self._git("rev-parse", "--is-inside-work-tree")
        except OSError:
            return False
        return result.returncode == 0 and result.stdout.strip() == "true"

    def current_branch(self) -> str:
        try:
            result = self._git("branch", "--show-current")
        except OSError:
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    def branch_exists(self, branch: str) -> bool:
        try:
            result = self._git("rev-parse", "--verify", "--quiet", branch)
        except OSError:
            return False
        return result.returncode == 0

    def sprint_branch_name(self, sprint: int) -> str:
        title = ""
        if self.spec_path.exists():
            spec = self.planner_spec()
            for item in spec.get("sprints", []):
                if int(item.get("id", 0)) == sprint:
                    title = str(item.get("title", ""))
                    break
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        slug = slug[:40].strip("-")
        return f"codex/sprint-{sprint}-{slug}" if slug else f"codex/sprint-{sprint}"

    def prepare_sprint_branch(self, sprint: int) -> tuple[bool, str, str, str]:
        """Create or switch to the sprint branch.

        Returns (ok, active_branch, base_branch, error). Non-git project dirs are
        treated as ok/no-op so route tests and protocol-only projects still work.
        """
        if not self.is_git_repo():
            return True, "", "", ""

        run_state = self.load_run_state()
        current = self.current_branch()
        base = str(run_state.get("base_branch") or current or "main")
        target = str(run_state.get("active_branch") or "")
        if not re.match(rf"^codex/sprint-{sprint}($|-)", target):
            target = self.sprint_branch_name(sprint)

        if current == target:
            return True, target, base, ""

        try:
            if self.branch_exists(target):
                result = self._git("switch", target)
            else:
                result = self._git("switch", "-c", target, base)
        except OSError as exc:
            return False, target, base, str(exc)

        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            return False, target, base, message or "git switch failed"
        return True, target, base, ""

    def switch_to_active_branch(self) -> tuple[bool, str, str]:
        if not self.is_git_repo():
            return True, "", ""
        active = str(self.load_run_state().get("active_branch") or "")
        if not active:
            return True, "", ""
        if self.current_branch() == active:
            return True, active, ""
        try:
            result = self._git("switch", active)
        except OSError as exc:
            return False, active, str(exc)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            return False, active, message or "git switch failed"
        return True, active, ""

    def _git_head(self) -> str:
        try:
            result = self._git("rev-parse", "HEAD")
            return result.stdout.strip() if result.returncode == 0 else ""
        except OSError:
            return ""

    def write_sprint_fence(self, sprint: int) -> None:
        """Write a fence file before Codex starts implementing a sprint.

        Analogous to marking a memory page read-only before a CoW fork:
        any .sprintfoundry/eval-trigger.txt that reports a *different* sprint number is
        treated as an out-of-bounds write and causes an immediate pause.
        """
        payload = {
            "sprint": sprint,
            "base_commit": self._git_head(),
            "started_at": iso_now(),
        }
        write_text(self.sprint_fence_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def sprint_prompt_rel_path(self, sprint: int, action: str) -> str:
        action_slug = re.sub(r"[^a-z0-9]+", "-", action.lower()).strip("-")
        action_slug = action_slug or "codex"
        return f"{SPRINT_PROMPT_DIR}/sprint-{sprint}-{action_slug}.md"

    def write_sprint_prompt(self, decision: RouteDecision) -> str | None:
        if not decision.codex_prompt:
            return None
        rel_path = decision.prompt_file or self.sprint_prompt_rel_path(
            decision.current_sprint,
            decision.action,
        )
        path = self.root / rel_path
        content = (
            "# SprintFoundry Codex Prompt\n\n"
            f"- Action: `{decision.action}`\n"
            f"- Sprint: `{decision.current_sprint}`\n"
            f"- Generated at: `{iso_now()}`\n\n"
            "## Instructions\n\n"
            f"{decision.codex_prompt.rstrip()}\n"
        )
        write_text(path, content)
        decision.prompt_file = rel_path
        return rel_path

    def read_sprint_fence(self) -> dict[str, Any] | None:
        if not self.sprint_fence_path.exists():
            return None
        try:
            return json.loads(read_text(self.sprint_fence_path))
        except (json.JSONDecodeError, KeyError):
            return None

    def load_run_state(self) -> dict[str, Any]:
        if not self.run_state_path.exists():
            return {
                "mode": "planning",
                "current_sprint": 0,
                "retry_count": 0,
                "last_successful_sprint": 0,
                "last_failure_reason": "",
                "needs_human": False,
                "active_branch": "",
                "base_branch": "",
                "last_run_at": "",
                "request_kind": "",
            }
        return json.loads(read_text(self.run_state_path))

    def save_run_state(self, state: dict[str, Any]) -> None:
        write_text(self.run_state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")

    def planner_spec(self) -> dict[str, Any]:
        return json.loads(read_text(self.spec_path))

    def eval_results(self) -> list[Path]:
        return eval_result_paths(self.root)

    def eval_result_path(self, sprint_id: int) -> Path:
        return eval_result_path(self.root, sprint_id)

    def passing_sprints(self) -> set[int]:
        passed: set[int] = set()
        for path in self.eval_results():
            sprint_id = eval_sprint_id(path)
            if sprint_id is not None and SPRINT_PASS in read_text(path):
                passed.add(sprint_id)
        return passed

    def current_sprint(self) -> int:
        spec = self.planner_spec()
        passed = self.passing_sprints()
        for sprint in spec.get("sprints", []):
            sprint_id = int(sprint["id"])
            if sprint.get("skipped"):
                continue
            if sprint_id not in passed:
                return sprint_id
        return 0

    def all_sprints_complete(self) -> bool:
        spec = self.planner_spec()
        passed = self.passing_sprints()
        for sprint in spec.get("sprints", []):
            sprint_id = int(sprint["id"])
            if sprint.get("skipped"):
                continue
            if sprint_id not in passed:
                return False
        return True

    def latest_failed_eval(self, sprint_id: int) -> Path | None:
        candidates = [
            path for path in self.eval_results()
            if eval_sprint_id(path) == sprint_id
        ]
        for path in reversed(candidates):
            if SPRINT_FAIL in read_text(path):
                return path
        return None

    def observed_state(self) -> dict[str, Any]:
        run_state = self.load_run_state()
        observed: dict[str, Any] = {
            "project_dir": str(self.root),
            "has_spec": self.spec_path.exists(),
            "has_contract": self.contract_path.exists(),
            "contract_approved": CONTRACT_APPROVED in read_text(self.contract_path),
            "has_eval_trigger": self.eval_trigger_path.exists(),
            "has_run_state": self.run_state_path.exists(),
            "has_change_request": self.change_request_path.exists(),
            "change_request_type": parse_key(read_text(self.change_request_path), "Type"),
            "has_bug_report": self.bug_report_path.exists(),
            "retry_count": int(run_state.get("retry_count", 0) or 0),
            "trigger_sprint": extract_sprint_id(read_text(self.eval_trigger_path)),
        }
        if observed["has_spec"]:
            observed["current_sprint"] = self.current_sprint()
            observed["all_sprints_complete"] = self.all_sprints_complete()
        return observed


def decide_route(project: HarnessProject, user_prompt: str, emit_audit: bool = True) -> RouteDecision:
    observed = project.observed_state()
    run_state = project.load_run_state()
    retry_count = int(run_state.get("retry_count", 0) or 0)

    if run_state.get("needs_human"):
        current_sprint = int(run_state.get("current_sprint", 0) or observed.get("current_sprint", 0) or 0)
        reason = str(run_state.get("last_failure_reason") or ".sprintfoundry/run-state.json has needs_human=true")
        return RouteDecision(
            rule="needs_human_set",
            action="pause_for_human",
            rationale=(
                ".sprintfoundry/run-state.json has needs_human=true; human action is required "
                "before the orchestrator may route another agent"
            ),
            mode="paused",
            current_sprint=current_sprint,
            needs_human=True,
            last_failure_reason=reason,
        )

    # Sprint-history audit runs BEFORE any other rule. It is the system-wide
    # invariant enforcement point: if declared state disagrees with the
    # authoritative eval-result-{N}.md files, pause unconditionally. A single
    # needs_human=true here blocks every other rule below.
    if observed["has_spec"]:
        audit_findings = audit_sprint_history(project)
        if emit_audit:
            for finding in audit_findings:
                project.append_audit(
                    event="audit_finding",
                    actor="orchestrator",
                    sprint=finding.sprint,
                    payload={"kind": finding.kind, "detail": finding.detail},
                )
        if audit_findings:
            worst = audit_findings[0]
            reason_summary = "; ".join(f.format() for f in audit_findings[:3])
            if len(audit_findings) > 3:
                reason_summary += f" (+{len(audit_findings) - 3} more)"
            return RouteDecision(
                rule="sprint_history_inconsistent",
                action="pause_for_human",
                rationale=(
                    "sprint-history audit found state that contradicts eval-result files: "
                    + reason_summary
                ),
                mode="paused",
                current_sprint=worst.sprint,
                needs_human=True,
                last_failure_reason=reason_summary,
            )

    if not observed["has_spec"]:
        return RouteDecision(
            rule="no_spec_yet",
            action="invoke_planner",
            rationale="planner-spec.json is missing at session start",
            mode="planning",
            current_sprint=0,
            prompt=(
                f"New project: {user_prompt}. First write .sprintfoundry/scope-classification.json "
                "with planning_mode=standard or large_system. Then write "
                "planner-spec.json and init.sh."
            ),
        )

    current_sprint = int(observed.get("current_sprint", 0) or 0)

    if observed["has_eval_trigger"]:
        trigger_sprint = observed["trigger_sprint"] or current_sprint

        # Sprint boundary check — analogous to a page-fault handler.
        # If the fenced sprint and the triggered sprint disagree, Codex has
        # written past its allocation boundary.  Pause instead of silently
        # advancing to avoid compounding multi-sprint drift.
        fence = project.read_sprint_fence()
        if fence is not None and trigger_sprint != fence.get("sprint"):
            return RouteDecision(
                rule="sprint_boundary_violation",
                action="pause_for_human",
                rationale=(
                    f".sprintfoundry/eval-trigger.txt reports sprint {trigger_sprint} but "
                    f".sprintfoundry/sprint-fence.json expected sprint {fence['sprint']} — "
                    "possible multi-sprint drift detected"
                ),
                mode="paused",
                current_sprint=trigger_sprint,
                needs_human=True,
                last_failure_reason=(
                    f"Sprint boundary violation: trigger={trigger_sprint}, fence={fence['sprint']}"
                ),
            )

        has_pass = any(
            eval_sprint_id(path) == trigger_sprint and SPRINT_PASS in read_text(path)
            for path in project.eval_results()
        )
        failed_eval = project.latest_failed_eval(trigger_sprint)
        if has_pass:
            return RouteDecision(
                rule="eval_trigger_has_pass",
                action="clear_eval_trigger_and_continue",
                rationale=".sprintfoundry/eval-trigger.txt exists but the sprint already has SPRINT PASS",
                mode="contract",
                current_sprint=current_sprint,
                cleanup_eval_trigger=True,
                # Delete sprint-contract.md and .sprintfoundry/sprint-fence.json so the next
                # sprint cannot skip contract negotiation — same principle as
                # clearing page-table write bits after a CoW copy completes.
                cleanup_contract=True,
            )
        if failed_eval is not None:
            if retry_count > RETRY_LIMIT:
                return RouteDecision(
                    rule="retry_limit_exceeded",
                    action="pause_for_human",
                    rationale="the same sprint already exceeded the retry limit",
                    mode="paused",
                    current_sprint=trigger_sprint,
                    needs_human=True,
                    last_failure_reason=f"Sprint {trigger_sprint} exceeded retry limit",
                )
            # Inline the failure body into the prompt so Codex still has the
            # cited issues after cleanup_eval_result deletes the source file.
            # The deletion is what forces the next round to re-invoke the
            # Evaluator instead of routing to another retry on stale state.
            failed_body = read_text(failed_eval).strip()
            return RouteDecision(
                rule="eval_trigger_with_fail",
                action="invoke_codex_for_retry",
                rationale="evaluator requested a targeted retry of committed sprint output",
                mode="implementing",
                current_sprint=trigger_sprint,
                codex_prompt=(
                    f"Sprint {trigger_sprint} failed. Fix ONLY the cited issues from the "
                    f"Evaluator verdict below; do not add unrelated changes.\n\n"
                    f"=== Evaluator verdict ({failed_eval.name}) ===\n"
                    f"{failed_body}\n"
                    f"=== end verdict ===\n\n"
                    f"Write .sprintfoundry/commit-requests/sprint-{trigger_sprint}.json "
                    "with attempt='retry'. Do not run git commit or write .sprintfoundry/eval-trigger.txt. "
                    "STOP after updating .sprintfoundry/claude-progress.txt. Do NOT advance to any later sprint. "
                    "Follow AGENTS.md Generator rules."
                ),
                cleanup_eval_result=True,
            )
        return RouteDecision(
            rule="eval_trigger_exists",
            action="invoke_evaluator",
            rationale="generator signaled that sprint output is ready for live CHECK",
            mode="checking",
            current_sprint=trigger_sprint,
            prompt=f"Run CHECK for Sprint {trigger_sprint}. Read sprint-contract.md and .sprintfoundry/eval-trigger.txt.",
        )

    if observed["has_contract"]:
        if observed["contract_approved"]:
            return RouteDecision(
                rule="approved_contract_phase",
                action="invoke_codex_for_implementation",
                rationale="sprint-contract.md is approved and ready for implementation",
                mode="implementing",
                current_sprint=current_sprint,
                codex_prompt=(
                    f"sprint-contract.md is approved. Implement Sprint {current_sprint} ONLY. "
                    f"Write .sprintfoundry/commit-requests/sprint-{current_sprint}.json "
                    "for Orchestrator commit. Do not run git commit or write .sprintfoundry/eval-trigger.txt. "
                    "STOP IMMEDIATELY after updating .sprintfoundry/claude-progress.txt. "
                    f"Do NOT read planner-spec.json to find Sprint {current_sprint + 1} or any later sprint. "
                    "Do NOT create a new branch or implement any other sprint. "
                    "Follow AGENTS.md Generator rules."
                ),
            )
        return RouteDecision(
            rule="contract_review_phase",
            action="invoke_evaluator_contract_review",
            rationale="a sprint contract exists but has not been approved yet",
            mode="contract",
            current_sprint=current_sprint,
            prompt="Review sprint-contract.md. Approve or return required changes.",
        )

    if observed["has_bug_report"]:
        return RouteDecision(
            rule="bug_report_ready",
            action="invoke_codex_for_bugfix_contract",
            rationale="bug-report.md exists, so this request should become a dedicated bugfix sprint",
            mode="contract",
            current_sprint=current_sprint,
            codex_prompt=(
                "Read planner-spec.json and bug-report.md. Propose sprint-contract.md for a bugfix sprint. "
                "Limit scope to the reported regression only, include browser-verifiable success criteria, "
                "and stop after writing the file."
            ),
        )

    if observed["has_change_request"]:
        change_type = (observed["change_request_type"] or "").lower()
        if change_type == "bugfix":
            return RouteDecision(
                rule="change_request_bugfix",
                action="invoke_codex_for_bugfix_contract",
                rationale="change-request.md marks this work as a bugfix",
                mode="contract",
                current_sprint=current_sprint,
                codex_prompt=(
                    "Read planner-spec.json and change-request.md. Propose sprint-contract.md for a bugfix sprint. "
                    "Limit scope to the requested fix and stop after writing the file."
                ),
            )
        if change_type == "minor_feature":
            return RouteDecision(
                rule="change_request_minor_feature",
                action="invoke_codex_for_iteration_contract",
                rationale="change-request.md marks this work as a bounded iteration",
                mode="contract",
                current_sprint=current_sprint,
                codex_prompt=(
                    "Read planner-spec.json and change-request.md. Identify the next iteration sprint and propose "
                    "sprint-contract.md for this minor feature. Keep the current architecture and VDL, and stop after writing the file."
                ),
            )
        if change_type in {"major_feature", "replan"}:
            return RouteDecision(
                rule="change_request_replan",
                action="invoke_planner_replan",
                rationale="change-request.md requires spec revision before a new sprint can be contracted",
                mode="planning",
                current_sprint=current_sprint,
                prompt=(
                    "Existing product change request: read planner-spec.json and change-request.md. "
                    "Revise planner-spec.json for this larger iteration before any coding begins."
                ),
            )
        return RouteDecision(
            rule="change_request_invalid",
            action="pause_for_human",
            rationale="change-request.md exists but its Type field is missing or invalid",
            mode="paused",
            current_sprint=current_sprint,
            needs_human=True,
            last_failure_reason="Invalid change-request.md Type",
        )

    if observed.get("all_sprints_complete"):
        return RouteDecision(
            rule="all_sprints_complete",
            action="complete",
            rationale="every sprint in planner-spec.json already has SPRINT PASS",
            mode="complete",
            current_sprint=0,
        )

    # Defence-in-depth: even if the pre-route audit was bypassed, refuse to
    # start Sprint N when any earlier sprint in planner-spec.json has not
    # produced a SPRINT PASS eval-result. Prevents silent "skip" of sprints
    # whose Evaluator was never run.
    passed = project.passing_sprints()
    prior_gaps = [
        int(sprint["id"])
        for sprint in project.planner_spec().get("sprints", [])
        if not sprint.get("skipped")
        and int(sprint["id"]) < current_sprint
        and int(sprint["id"]) not in passed
    ]
    if prior_gaps:
        return RouteDecision(
            rule="prior_sprint_not_passed",
            action="pause_for_human",
            rationale=(
                f"cannot start Sprint {current_sprint}: prior sprints {prior_gaps} "
                "have no eval-result-{N}.md with SPRINT PASS"
            ),
            mode="paused",
            current_sprint=current_sprint,
            needs_human=True,
            last_failure_reason=(
                f"Cannot advance to Sprint {current_sprint}; "
                f"Sprint(s) {prior_gaps} never received Evaluator PASS"
            ),
        )

    return RouteDecision(
        rule="ready_for_next_sprint",
        action="invoke_codex_for_contract",
        rationale="spec exists and no active contract, evaluation trigger, bug report, or change request is present",
        mode="contract",
        current_sprint=current_sprint,
        codex_prompt=(
            f"Read planner-spec.json. Propose sprint-contract.md for Sprint {current_sprint}. "
            "Follow AGENTS.md Generator rules. Stop after writing the file."
        ),
    )


def update_run_state(project: HarnessProject, decision: RouteDecision) -> None:
    previous = project.load_run_state()
    state = dict(previous)
    state["mode"] = decision.mode
    state["current_sprint"] = decision.current_sprint
    state["needs_human"] = decision.needs_human
    state["last_failure_reason"] = decision.last_failure_reason
    state["last_run_at"] = iso_now()
    if decision.active_branch:
        state["active_branch"] = decision.active_branch
    if decision.base_branch:
        state["base_branch"] = decision.base_branch
    if decision.action == "invoke_codex_for_retry":
        state["retry_count"] = int(state.get("retry_count", 0) or 0) + 1
    elif decision.action not in {"pause_for_human", "invoke_evaluator"}:
        # invoke_evaluator can legitimately appear mid retry cycle — right
        # after Codex has fixed the cited issues and is waiting for re-CHECK.
        # Resetting retry_count there would silently grant an infinite retry
        # budget for the same sprint. Only genuine progress (sprint PASS,
        # starting the next sprint, contract/planner phases) clears it.
        state["retry_count"] = 0

    if decision.action == "invoke_codex_for_bugfix_contract":
        state["request_kind"] = "bugfix"
    elif decision.action == "invoke_codex_for_iteration_contract":
        state["request_kind"] = "iteration"
    elif decision.action == "invoke_planner_replan":
        state["request_kind"] = "replan"
    elif decision.action not in {"pause_for_human", "invoke_codex_for_retry"}:
        state["request_kind"] = ""

    project.save_run_state(state)

    # Emit a state_transition event for every field whose value changed.
    # last_run_at is excluded because it changes on every invocation and would
    # flood the audit log with noise.
    tracked_keys = (
        "mode",
        "current_sprint",
        "retry_count",
        "last_successful_sprint",
        "last_failure_reason",
        "needs_human",
        "active_branch",
        "base_branch",
        "request_kind",
    )
    changes: dict[str, list[Any]] = {}
    for key in tracked_keys:
        old = previous.get(key)
        new = state.get(key)
        if old != new:
            changes[key] = [old, new]
    if changes:
        project.append_audit(
            event="state_transition",
            actor="orchestrator",
            sprint=decision.current_sprint or None,
            payload={"changes": changes, "triggered_by": decision.rule},
        )


def log_decision(project: HarnessProject, decision: RouteDecision) -> None:
    observed = project.observed_state()
    ts = iso_now()
    append_ndjson(
        project.log_path,
        {
            "ts": ts,
            "observed": observed,
            "rule": decision.rule,
            "action": decision.action,
            "rationale": decision.rationale,
        },
    )
    action_to_event = {
        "invoke_planner": "planner_requested",
        "invoke_planner_replan": "planner_replan_requested",
        "invoke_codex_for_contract": "contract_requested",
        "invoke_codex_for_bugfix_contract": "bugfix_contract_requested",
        "invoke_codex_for_iteration_contract": "iteration_contract_requested",
        "invoke_evaluator_contract_review": "contract_review_requested",
        "invoke_codex_for_implementation": "generator_requested",
        "invoke_evaluator": "evaluator_requested",
        "invoke_codex_for_retry": "generator_retry_requested",
        "pause_for_human": "orchestrator_paused",
        "complete": "orchestrator_completed",
        "clear_eval_trigger_and_continue": "eval_trigger_cleaned",
        # Emitted when .sprintfoundry/eval-trigger.txt names a sprint that does not match the
        # fenced sprint — indicates Codex drifted past its implementation boundary.
        "sprint_boundary_violation": "sprint_boundary_violated",
    }
    append_ndjson(
        project.events_path,
        {
            "ts": ts,
            "event": action_to_event[decision.action],
            "mode": decision.mode,
            "current_sprint": decision.current_sprint,
        },
    )

    # Unified audit trail — one line per orchestrator run, plus an observation
    # snapshot of every eval-result verdict so offline auditors can reconstruct
    # the project state at any point in time from this file alone.
    project.append_audit(
        event="orchestrator_run",
        actor="orchestrator",
        sprint=decision.current_sprint or None,
        payload={
            "rule": decision.rule,
            "action": decision.action,
            "mode": decision.mode,
            "needs_human": decision.needs_human,
            "rationale": decision.rationale,
            "retry_count": observed.get("retry_count", 0),
            "trigger_sprint": observed.get("trigger_sprint"),
            "has_contract": observed.get("has_contract", False),
            "contract_approved": observed.get("contract_approved", False),
            "has_eval_trigger": observed.get("has_eval_trigger", False),
            "prompt_file": decision.prompt_file,
        },
    )
    for path in sorted(project.eval_results()):
        sid = eval_sprint_id(path)
        if sid is None:
            continue
        text = read_text(path)
        verdict = (
            SPRINT_PASS if SPRINT_PASS in text else
            SPRINT_FAIL if SPRINT_FAIL in text else
            "UNKNOWN"
        )
        project.append_audit(
            event="eval_result_observed",
            actor="orchestrator",
            sprint=sid,
            payload={
                "verdict": verdict,
                "file": str(path.relative_to(project.root)),
            },
        )


def maybe_cleanup_sprint_artifacts(project: HarnessProject, decision: RouteDecision) -> None:
    if decision.cleanup_eval_trigger and project.eval_trigger_path.exists():
        project.eval_trigger_path.unlink()
    if decision.cleanup_contract:
        # Remove both contract and fence so the next sprint must start from
        # scratch: propose contract → Evaluator approves → write new fence →
        # implement.  Mirrors clearing page-protection bits between epochs.
        for path in (project.contract_path, project.sprint_fence_path):
            if path.exists():
                path.unlink()
    if decision.cleanup_eval_result and decision.current_sprint:
        # Drop the stale FAIL verdict so the next orchestrator round sees
        # .sprintfoundry/eval-trigger.txt without any eval-result and routes to the
        # Evaluator instead of another Codex retry. The retry prompt already
        # inlined the verdict body, so Codex does not need the file.
        for stale in (
            project.eval_result_path(decision.current_sprint),
            project.root / f"eval-result-{decision.current_sprint}.md",
        ):
            if stale.exists():
                stale.unlink()


def prepare_branch_for_decision(project: HarnessProject, decision: RouteDecision) -> RouteDecision:
    if decision.action == "invoke_codex_for_implementation":
        ok, active, base, error = project.prepare_sprint_branch(decision.current_sprint)
        if ok:
            decision.active_branch = active
            decision.base_branch = base
            return decision
        return RouteDecision(
            rule="sprint_branch_prepare_failed",
            action="pause_for_human",
            rationale=f"could not create or switch to sprint branch {active}: {error}",
            mode="paused",
            current_sprint=decision.current_sprint,
            needs_human=True,
            last_failure_reason=f"Sprint branch prepare failed: {error}",
        )

    if decision.action == "invoke_codex_for_retry":
        ok, active, error = project.switch_to_active_branch()
        if ok:
            decision.active_branch = active
            return decision
        return RouteDecision(
            rule="sprint_branch_switch_failed",
            action="pause_for_human",
            rationale=f"could not switch to active sprint branch {active}: {error}",
            mode="paused",
            current_sprint=decision.current_sprint,
            needs_human=True,
            last_failure_reason=f"Sprint branch switch failed: {error}",
        )

    return decision


def attach_codex_prompt_file(project: HarnessProject, decision: RouteDecision, write_file: bool) -> None:
    if not decision.codex_prompt:
        return
    if decision.prompt_file is None:
        decision.prompt_file = project.sprint_prompt_rel_path(decision.current_sprint, decision.action)
    if write_file:
        project.write_sprint_prompt(decision)
    decision.command = codex_command(decision.prompt_file)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", default=".", help="Target project directory.")
    parser.add_argument("--user-prompt", default="", help="Initial prompt for a brand-new product.")
    parser.add_argument("--run-generator", action="store_true", help="Execute Codex CLI automatically.")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Compute the routing decision without writing logs, state, cleanup files, or switching branches.",
    )
    parser.add_argument("--json", action="store_true", help="Print decision as JSON.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    project = HarnessProject(Path(args.project_dir))
    if not args.check_only:
        compress_progress(project.progress_path)
    decision = decide_route(project, args.user_prompt, emit_audit=not args.check_only)
    if not args.check_only:
        decision = prepare_branch_for_decision(project, decision)
        maybe_cleanup_sprint_artifacts(project, decision)
        # Write sprint fence before handing off to Codex so the boundary-violation
        # check in the next orchestrator call has a reference point.
        if decision.action == "invoke_codex_for_implementation":
            project.write_sprint_fence(decision.current_sprint)
        attach_codex_prompt_file(project, decision, write_file=True)
        update_run_state(project, decision)
        log_decision(project, decision)
    else:
        attach_codex_prompt_file(project, decision, write_file=False)

    if args.run_generator and decision.command and not args.check_only:
        return subprocess.run(decision.command, cwd=str(project.root), shell=True).returncode

    payload = {
        "project_dir": str(project.root),
        "rule": decision.rule,
        "action": decision.action,
        "mode": decision.mode,
        "current_sprint": decision.current_sprint,
        "rationale": decision.rationale,
        "command": decision.command,
        "prompt_file": decision.prompt_file,
        "prompt": decision.prompt,
        "needs_human": decision.needs_human,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    return 2 if decision.needs_human else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
