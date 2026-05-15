from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "orchestrate.py"
EVAL_RESULTS_DIR = Path(".sprintfoundry") / "eval-results"


def run_orchestrator(project_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project-dir", str(project_dir), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def run_git(project_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        check=False,
    )


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_eval_result(project_dir: Path, sprint: int, body: str) -> Path:
    path = project_dir / EVAL_RESULTS_DIR / f"eval-result-{sprint}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def write_spec(path: Path) -> None:
    write_json(
        path,
        {
            "product": "Test product",
            "design_language": {},
            "tech_stack": {},
            "features": [],
            "sprints": [{"id": 1, "title": "Sprint One", "features": ["F1"]}],
        },
    )


def test_routes_to_planner_when_spec_missing(tmp_path: Path) -> None:
    result = run_orchestrator(tmp_path, "--user-prompt", "Build a writing app", "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["rule"] == "no_spec_yet"
    assert payload["action"] == "invoke_planner"


def test_routes_to_bugfix_contract_when_bug_report_exists(tmp_path: Path) -> None:
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "bug-report.md").write_text(
        "# Bug Report\n\nTitle: Login fails\nExpected: success\nActual: error\n",
        encoding="utf-8",
    )
    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["rule"] == "bug_report_ready"
    assert payload["action"] == "invoke_codex_for_bugfix_contract"


def test_routes_to_iteration_contract_for_minor_feature(tmp_path: Path) -> None:
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "change-request.md").write_text(
        "# Change Request\n\nType: minor_feature\nTitle: Add quick filters\n",
        encoding="utf-8",
    )
    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["rule"] == "change_request_minor_feature"
    assert payload["action"] == "invoke_codex_for_iteration_contract"


def test_routes_to_replan_for_major_feature(tmp_path: Path) -> None:
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "change-request.md").write_text(
        "# Change Request\n\nType: major_feature\nTitle: Mobile app support\n",
        encoding="utf-8",
    )
    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["rule"] == "change_request_replan"
    assert payload["action"] == "invoke_planner_replan"


def test_pauses_when_change_request_type_is_invalid(tmp_path: Path) -> None:
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "change-request.md").write_text(
        "# Change Request\n\nTitle: Missing type field\n",
        encoding="utf-8",
    )
    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload["rule"] == "change_request_invalid"
    assert payload["action"] == "pause_for_human"


def test_needs_human_hard_stops_before_any_routing(tmp_path: Path) -> None:
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "bug-report.md").write_text("# Bug\n\nActual: broken\n", encoding="utf-8")
    write_json(
        tmp_path / "run-state.json",
        {
            "mode": "paused",
            "current_sprint": 1,
            "retry_count": 0,
            "last_successful_sprint": 0,
            "last_failure_reason": "manual review required",
            "needs_human": True,
            "active_branch": "",
            "base_branch": "",
            "last_run_at": "",
            "request_kind": "",
        },
    )

    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    run_state = json.loads((tmp_path / "run-state.json").read_text(encoding="utf-8"))

    assert result.returncode == 2
    assert payload["rule"] == "needs_human_set"
    assert payload["action"] == "pause_for_human"
    assert payload["needs_human"] is True
    assert run_state["needs_human"] is True
    assert run_state["mode"] == "paused"
    assert run_state["last_failure_reason"] == "manual review required"


def test_codex_command_uses_modern_exec_when_version_is_new(monkeypatch) -> None:
    from scripts import orchestrate

    monkeypatch.setattr(orchestrate, "codex_version_tuple", lambda: (0, 120, 0))
    command = orchestrate.codex_command("Implement sprint")
    assert "codex exec --full-auto" in command
    assert "disk-full-read-access" in command
    assert "shell_environment_policy.inherit=all" in command
    assert "--skip-git-repo-check" in command


def test_codex_command_uses_legacy_exec_when_version_is_old(monkeypatch) -> None:
    from scripts import orchestrate

    monkeypatch.setattr(orchestrate, "codex_version_tuple", lambda: (0, 119, 9))
    command = orchestrate.codex_command("Implement sprint")
    assert command.startswith("codex -a never exec --skip-git-repo-check ")


def test_codex_command_uses_legacy_exec_when_version_is_unknown(monkeypatch) -> None:
    from scripts import orchestrate

    monkeypatch.setattr(orchestrate, "codex_version_tuple", lambda: None)
    command = orchestrate.codex_command("Implement sprint")
    assert command.startswith("codex -a never exec --skip-git-repo-check ")


def test_codex_command_quotes_prompt(monkeypatch) -> None:
    from scripts import orchestrate

    monkeypatch.setattr(orchestrate, "codex_version_tuple", lambda: (0, 120, 0))
    command = orchestrate.codex_command("Implement 'sprint' && rm -rf /")
    assert "Implement 'sprint' && rm -rf /" not in command
    assert "codex exec --full-auto" in command
    assert "--skip-git-repo-check" in command


# --- compress_progress tests ---

def test_compress_not_triggered_when_file_is_small(tmp_path: Path) -> None:
    from scripts.orchestrate import compress_progress

    progress = tmp_path / "claude-progress.txt"
    content = "Project: test\n\n## Sprint 1 — 2026-01-01\nStatus: committed\n"
    progress.write_text(content, encoding="utf-8")
    compress_progress(progress)
    assert progress.read_text(encoding="utf-8") == content


def test_compress_triggered_when_too_many_sprints(tmp_path: Path) -> None:
    from scripts.orchestrate import compress_progress

    progress = tmp_path / "claude-progress.txt"
    lines = ["Project summary\n"]
    for i in range(1, 6):
        lines.append(f"## Sprint {i} — 2026-01-0{i}\nStatus: done\nKey: file{i}.py\n\n")
    progress.write_text("".join(lines), encoding="utf-8")
    compress_progress(progress)
    result = progress.read_text(encoding="utf-8")
    # Only the last 3 sprint headers should remain
    assert result.count("## Sprint ") <= 3


def test_compress_triggered_when_over_60_lines(tmp_path: Path) -> None:
    from scripts.orchestrate import compress_progress

    progress = tmp_path / "claude-progress.txt"
    lines = ["Project summary\n"] + [f"line {i}\n" for i in range(65)]
    progress.write_text("".join(lines), encoding="utf-8")
    compress_progress(progress)
    result = progress.read_text(encoding="utf-8")
    assert len(result.splitlines()) < 65


def test_compress_triggered_by_traceback(tmp_path: Path) -> None:
    from scripts.orchestrate import compress_progress

    progress = tmp_path / "claude-progress.txt"
    progress.write_text(
        "Project summary\n\n## Sprint 1 — 2026-01-01\nTraceback (most recent call last):\n  File foo.py\n",
        encoding="utf-8",
    )
    original_len = len(progress.read_text(encoding="utf-8").splitlines())
    compress_progress(progress)
    assert len(progress.read_text(encoding="utf-8").splitlines()) <= original_len


def test_compress_summary_does_not_include_sprint_header(tmp_path: Path) -> None:
    from scripts.orchestrate import compress_progress

    progress = tmp_path / "claude-progress.txt"
    lines = ["Project: my app\nStack: Next.js\n\n"]
    for i in range(1, 6):
        lines.append(f"## Sprint {i} — 2026-01-0{i}\nStatus: done\n\n")
    progress.write_text("".join(lines), encoding="utf-8")
    compress_progress(progress)
    result_lines = progress.read_text(encoding="utf-8").splitlines()
    # Summary section (before first blank line after header) must not start with a sprint header
    assert not result_lines[0].startswith("## Sprint")


# --- sprint gate / boundary enforcement tests ---

def test_clears_contract_and_fence_after_sprint_pass(tmp_path: Path) -> None:
    """After SPRINT PASS the Orchestrator must delete sprint-contract.md and
    sprint-fence.json so the next sprint cannot bypass contract negotiation."""
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "sprint-contract.md").write_text(
        "## Sprint 1\nCONTRACT APPROVED\n", encoding="utf-8"
    )
    (tmp_path / "sprint-fence.json").write_text(
        '{"sprint": 1, "base_commit": "abc123", "started_at": "2026-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )
    (tmp_path / "eval-trigger.txt").write_text("sprint=1", encoding="utf-8")
    write_eval_result(tmp_path, 1, "## Verdict: SPRINT PASS\n")

    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    assert payload["rule"] == "eval_trigger_has_pass"
    assert not (tmp_path / "sprint-contract.md").exists(), (
        "sprint-contract.md must be deleted after SPRINT PASS"
    )
    assert not (tmp_path / "sprint-fence.json").exists(), (
        "sprint-fence.json must be deleted after SPRINT PASS"
    )


def test_legacy_root_eval_result_files_still_work(tmp_path: Path) -> None:
    """Existing projects may still have root-level eval-result files."""
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "eval-trigger.txt").write_text("sprint=1", encoding="utf-8")
    (tmp_path / "eval-result-1.md").write_text("## Verdict: SPRINT PASS\n", encoding="utf-8")

    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["rule"] == "eval_trigger_has_pass"


def test_next_sprint_requires_new_contract_after_pass(tmp_path: Path) -> None:
    """After cleanup the next orchestrator call must route to contract proposal,
    not directly to implementation — verifying the gate is not bypassed."""
    write_spec(tmp_path / "planner-spec.json")
    # Sprint 1 already passed; no contract or fence present (simulating post-cleanup state)
    (tmp_path / "eval-result-1.md").write_text("## Verdict: SPRINT PASS\n", encoding="utf-8")
    # planner-spec has only sprint 1, so add sprint 2 so there is work left
    spec = json.loads((tmp_path / "planner-spec.json").read_text(encoding="utf-8"))
    spec["sprints"].append({"id": 2, "title": "Sprint Two", "features": ["F2"]})
    (tmp_path / "planner-spec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    # Must ask Codex to propose a new contract, NOT jump straight to implementation
    assert payload["action"] == "invoke_codex_for_contract", (
        f"Expected invoke_codex_for_contract, got {payload['action']}"
    )


def test_pauses_on_sprint_boundary_violation(tmp_path: Path) -> None:
    """If eval-trigger.txt names a different sprint than sprint-fence.json the
    Orchestrator must pause immediately instead of routing to the Evaluator."""
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "sprint-contract.md").write_text(
        "## Sprint 1\nCONTRACT APPROVED\n", encoding="utf-8"
    )
    # Fence says sprint 1 was the authorised sprint ...
    (tmp_path / "sprint-fence.json").write_text(
        '{"sprint": 1, "base_commit": "abc123", "started_at": "2026-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )
    # ... but Codex wrote sprint=2, revealing it implemented two sprints at once
    (tmp_path / "eval-trigger.txt").write_text("sprint=2", encoding="utf-8")

    result = run_orchestrator(tmp_path, "--json")
    assert result.returncode == 2, "Orchestrator must exit 2 (needs_human) on boundary violation"
    payload = json.loads(result.stdout)
    assert payload["rule"] == "sprint_boundary_violation"
    assert payload["action"] == "pause_for_human"
    assert payload["needs_human"] is True


def test_writes_sprint_fence_before_implementation(tmp_path: Path) -> None:
    """Orchestrator must write sprint-fence.json when routing to implementation."""
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "sprint-contract.md").write_text(
        "## Sprint 1\nCONTRACT APPROVED\n", encoding="utf-8"
    )

    run_orchestrator(tmp_path, "--json")

    fence_path = tmp_path / "sprint-fence.json"
    assert fence_path.exists(), "sprint-fence.json must be written before Codex is invoked"
    fence = json.loads(fence_path.read_text(encoding="utf-8"))
    assert fence["sprint"] == 1
    assert "base_commit" in fence
    assert "started_at" in fence


def test_implementation_routes_on_dedicated_sprint_branch(tmp_path: Path) -> None:
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "sprint-contract.md").write_text(
        "## Sprint 1\nCONTRACT APPROVED\n", encoding="utf-8"
    )
    assert run_git(tmp_path, "init", "-b", "main").returncode == 0
    assert run_git(tmp_path, "add", ".").returncode == 0
    commit = run_git(
        tmp_path,
        "-c", "user.name=Test",
        "-c", "user.email=test@example.com",
        "commit", "-m", "initial",
    )
    assert commit.returncode == 0, commit.stderr

    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    run_state = json.loads((tmp_path / "run-state.json").read_text(encoding="utf-8"))
    branch = run_git(tmp_path, "branch", "--show-current").stdout.strip()

    assert payload["action"] == "invoke_codex_for_implementation"
    assert branch == "codex/sprint-1-sprint-one"
    assert run_state["active_branch"] == "codex/sprint-1-sprint-one"
    assert run_state["base_branch"] == "main"
    assert (tmp_path / "sprint-fence.json").exists()


def test_implementation_prompt_includes_stop_instruction(tmp_path: Path) -> None:
    """The Codex prompt must explicitly tell it to stop after eval-trigger.txt."""
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "sprint-contract.md").write_text(
        "## Sprint 1\nCONTRACT APPROVED\n", encoding="utf-8"
    )

    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    assert payload["action"] == "invoke_codex_for_implementation"
    command = payload["command"] or ""
    assert "STOP" in command, "Implementation prompt must include explicit STOP instruction"
    assert "ONLY" in command, "Implementation prompt must say 'Sprint N ONLY'"


def test_check_only_does_not_write_state_logs_or_fence(tmp_path: Path) -> None:
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "sprint-contract.md").write_text(
        "## Sprint 1\nCONTRACT APPROVED\n", encoding="utf-8"
    )

    result = run_orchestrator(tmp_path, "--check-only", "--json")
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["action"] == "invoke_codex_for_implementation"
    assert not (tmp_path / "run-state.json").exists()
    assert not (tmp_path / "orchestrator-log.ndjson").exists()
    assert not (tmp_path / "run-events.ndjson").exists()
    assert not (tmp_path / "harness-audit.ndjson").exists()
    assert not (tmp_path / "sprint-fence.json").exists()


def test_pre_commit_uses_read_only_orchestrator_check() -> None:
    hook = (ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    assert "scripts/orchestrate.py --project-dir . --check-only --json" in hook


# --- retry → evaluator handoff ------------------------------------------------
#
# Historical bug: the orchestrator routed to Codex retry whenever
# eval-result-{N}.md contained SPRINT FAIL, regardless of whether the Evaluator
# had *already* re-checked the latest retry. Because Codex rewriting
# eval-trigger.txt with the same sprint number left the file system
# indistinguishable from the pre-retry state, every subsequent orchestrator
# round saw the same stale FAIL and launched another Codex retry — burning
# retry budget without the Evaluator ever verifying the fix.
#
# Fix (Option A): after routing to invoke_codex_for_retry, delete the stale
# eval-result-{N}.md. The retry prompt inlines the FAIL details so Codex still
# has full context even though the file is gone. On the next orchestrator
# round the missing eval-result forces the Evaluator to re-CHECK the fix.


def test_retry_deletes_stale_eval_result_so_evaluator_can_recheck(tmp_path: Path) -> None:
    """Routing to invoke_codex_for_retry must remove eval-result-{N}.md so the
    Evaluator is forced to re-verify the next commit."""
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "sprint-contract.md").write_text(
        "## Sprint 1\nCONTRACT APPROVED\n", encoding="utf-8"
    )
    (tmp_path / "sprint-fence.json").write_text(
        '{"sprint": 1, "base_commit": "abc123", "started_at": "2026-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )
    (tmp_path / "eval-trigger.txt").write_text("sprint=1", encoding="utf-8")
    eval_path = write_eval_result(
        tmp_path,
        1,
        "## Verdict: SPRINT FAIL\n\nRequired fixes:\n1. Add CTA button\n",
    )

    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    assert payload["action"] == "invoke_codex_for_retry"
    assert not eval_path.exists(), (
        ".sprintfoundry/eval-results/eval-result-1.md must be deleted after routing to retry so the next "
        "orchestrator round routes back to the Evaluator"
    )


def test_retry_prompt_inlines_eval_result_fail_details(tmp_path: Path) -> None:
    """Because the stale eval-result is deleted before Codex runs, the retry
    prompt itself must carry every line Codex needs to fix."""
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "eval-trigger.txt").write_text("sprint=1", encoding="utf-8")
    (tmp_path / "eval-result-1.md").write_text(
        "## Verdict: SPRINT FAIL\n\nRequired fixes:\n1. Add CTA button on /home\n",
        encoding="utf-8",
    )

    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    command = payload["command"] or ""
    assert "Add CTA button on /home" in command, (
        "retry prompt must inline the eval-result body so Codex has the "
        "cited fixes even after the file is deleted"
    )
    assert "STOP" in command, "retry prompt must still include STOP after eval-trigger.txt"


def test_next_round_after_retry_routes_to_evaluator(tmp_path: Path) -> None:
    """End-to-end: once Codex has rewritten eval-trigger.txt after a retry, the
    NEXT orchestrator call must invoke the Evaluator for live re-CHECK
    rather than looping on another Codex retry."""
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "sprint-contract.md").write_text(
        "## Sprint 1\nCONTRACT APPROVED\n", encoding="utf-8"
    )
    (tmp_path / "sprint-fence.json").write_text(
        '{"sprint": 1, "base_commit": "abc123", "started_at": "2026-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )
    (tmp_path / "eval-trigger.txt").write_text("sprint=1", encoding="utf-8")
    (tmp_path / "eval-result-1.md").write_text(
        "## Verdict: SPRINT FAIL\n\nFix: add button\n", encoding="utf-8"
    )

    # Round 1: FAIL → retry (deletes eval-result-1.md as part of cleanup)
    first = run_orchestrator(tmp_path, "--json")
    first_payload = json.loads(first.stdout)
    assert first_payload["action"] == "invoke_codex_for_retry"

    # Simulate Codex having finished the retry: commit happened, trigger
    # rewritten with the same sprint=1 content. eval-result-1.md is still
    # absent because the orchestrator deleted it before the Codex invocation.
    (tmp_path / "eval-trigger.txt").write_text("sprint=1", encoding="utf-8")

    # Round 2: must now route to Evaluator, NOT another Codex retry
    second = run_orchestrator(tmp_path, "--json")
    second_payload = json.loads(second.stdout)
    assert second_payload["rule"] == "eval_trigger_exists"
    assert second_payload["action"] == "invoke_evaluator", (
        f"expected invoke_evaluator after Codex retry, got "
        f"{second_payload['action']} (rule={second_payload['rule']})"
    )
    run_state = json.loads((tmp_path / "run-state.json").read_text(encoding="utf-8"))
    assert run_state["retry_count"] == 1, (
        "retry_count should still be 1 — invoking Evaluator must not consume "
        "another retry slot"
    )


def test_retry_budget_exhausts_across_full_evaluator_retry_cycles(tmp_path: Path) -> None:
    """Full state-machine walk: every Codex retry must count against the budget
    even when it is interleaved with Evaluator re-CHECK rounds. After the
    retry limit (2) has been exceeded the orchestrator must pause."""
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "sprint-contract.md").write_text(
        "## Sprint 1\nCONTRACT APPROVED\n", encoding="utf-8"
    )
    (tmp_path / "sprint-fence.json").write_text(
        '{"sprint": 1, "base_commit": "abc123", "started_at": "2026-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )
    (tmp_path / "eval-trigger.txt").write_text("sprint=1", encoding="utf-8")

    def simulate_evaluator_fail() -> None:
        (tmp_path / "eval-result-1.md").write_text(
            "## Verdict: SPRINT FAIL\n\nFix: stubborn defect\n", encoding="utf-8"
        )

    def simulate_codex_retry() -> None:
        # Codex re-commits and rewrites eval-trigger.txt, same sprint number.
        (tmp_path / "eval-trigger.txt").write_text("sprint=1", encoding="utf-8")

    observed_retry_counts: list[int] = []
    observed_actions: list[str] = []

    # Drive the state machine for enough rounds that, with a correctly
    # enforced retry_count, the orchestrator eventually pauses.
    for round_idx in range(10):
        result = run_orchestrator(tmp_path, "--json")
        payload = json.loads(result.stdout)
        run_state = json.loads(
            (tmp_path / "run-state.json").read_text(encoding="utf-8")
        )
        observed_actions.append(payload["action"])
        observed_retry_counts.append(run_state["retry_count"])

        if payload["action"] == "pause_for_human":
            break
        if payload["action"] == "invoke_evaluator":
            simulate_evaluator_fail()
        elif payload["action"] == "invoke_codex_for_retry":
            simulate_codex_retry()
        else:
            raise AssertionError(
                f"unexpected action {payload['action']} in retry cycle"
            )
    else:
        raise AssertionError(
            f"retry budget never exhausted after 10 rounds; actions={observed_actions}, "
            f"retry_counts={observed_retry_counts}"
        )

    assert payload["rule"] == "retry_limit_exceeded", (
        f"expected retry_limit_exceeded, got rule={payload['rule']}, "
        f"actions={observed_actions}, retry_counts={observed_retry_counts}"
    )
    # With RETRY_LIMIT=2 we expect exactly 3 Codex retry invocations before the
    # pause: retry_count 1 → 2 → 3, then the next round with retry_count=3
    # trips the `> RETRY_LIMIT` guard.
    retry_actions = [a for a in observed_actions if a == "invoke_codex_for_retry"]
    assert len(retry_actions) == 3, (
        f"expected exactly 3 retries, got {len(retry_actions)}: {observed_actions}"
    )


def test_compress_triggered_by_multi_paragraph_narrative(tmp_path: Path) -> None:
    from scripts.orchestrate import compress_progress, _has_multi_paragraph_narrative

    # Build a file with 7 paragraphs (well above threshold of 6) but under 60 lines
    paras = [f"This is paragraph {i} with some content here.\n" for i in range(7)]
    lines = "\n".join(paras).splitlines()
    assert _has_multi_paragraph_narrative(lines)

    progress = tmp_path / "claude-progress.txt"
    progress.write_text("\n\n".join(paras), encoding="utf-8")
    compress_progress(progress)
    # File should have been compressed (rewritten)
    result = progress.read_text(encoding="utf-8")
    assert len(result.splitlines()) < len(lines)


# --- monotonic-PASS invariant tests -----------------------------------------
#
# Reproduces the two historical failure modes fixed by audit_sprint_history:
#   A. Sprint 1/2 bootstrap bypass — later sprints PASS while earlier sprints
#      have no eval-result file at all.
#   B. Sprint 3 manual FAIL override — run-state.json advances past a sprint
#      whose eval-result-{N}.md still contains SPRINT FAIL.

def _write_multi_sprint_spec(path: Path, n: int) -> None:
    write_json(
        path,
        {
            "product": "test",
            "design_language": {},
            "tech_stack": {},
            "features": [],
            "sprints": [
                {"id": i, "title": f"Sprint {i}", "features": [f"F{i}"]}
                for i in range(1, n + 1)
            ],
        },
    )


def test_audit_detects_bootstrap_bypass(tmp_path: Path) -> None:
    """Sprint 1/2 history replay: later sprints have PASS but Sprints 1+2
    have no eval-result files. Orchestrator must pause."""
    _write_multi_sprint_spec(tmp_path / "planner-spec.json", 4)
    (tmp_path / "eval-result-3.md").write_text("## Verdict: SPRINT PASS\n", encoding="utf-8")
    (tmp_path / "eval-result-4.md").write_text("## Verdict: SPRINT PASS\n", encoding="utf-8")
    write_json(
        tmp_path / "run-state.json",
        {
            "mode": "complete",
            "current_sprint": 4,
            "retry_count": 0,
            "last_successful_sprint": 4,
            "last_failure_reason": "",
            "needs_human": False,
            "active_branch": "main",
            "base_branch": "main",
            "last_run_at": "",
            "request_kind": "",
        },
    )

    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload["rule"] == "sprint_history_inconsistent"
    assert payload["action"] == "pause_for_human"
    assert payload["needs_human"] is True


def test_audit_detects_fail_override(tmp_path: Path) -> None:
    """Sprint 3 history replay: eval-result-3.md still says SPRINT FAIL
    but run-state.json claims Sprint 4 has already succeeded."""
    _write_multi_sprint_spec(tmp_path / "planner-spec.json", 4)
    for n in (1, 2):
        (tmp_path / f"eval-result-{n}.md").write_text(
            "## Verdict: SPRINT PASS\n", encoding="utf-8"
        )
    (tmp_path / "eval-result-3.md").write_text(
        "## Verdict: SPRINT FAIL\nFunctionality 7/10\n", encoding="utf-8"
    )
    (tmp_path / "eval-result-4.md").write_text(
        "## Verdict: SPRINT PASS\n", encoding="utf-8"
    )
    write_json(
        tmp_path / "run-state.json",
        {
            "mode": "complete",
            "current_sprint": 4,
            "retry_count": 0,
            "last_successful_sprint": 4,
            "last_failure_reason": "",
            "needs_human": False,
            "active_branch": "main",
            "base_branch": "main",
            "last_run_at": "",
            "request_kind": "",
        },
    )

    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert payload["rule"] == "sprint_history_inconsistent"
    assert "fail_bypassed" in payload["rationale"].lower() or "Sprint 3" in payload["rationale"]


def test_audit_passes_for_clean_history(tmp_path: Path) -> None:
    """Happy path: every sprint 1..3 has PASS, run-state matches."""
    _write_multi_sprint_spec(tmp_path / "planner-spec.json", 4)
    for n in (1, 2, 3):
        (tmp_path / f"eval-result-{n}.md").write_text(
            "## Verdict: SPRINT PASS\n", encoding="utf-8"
        )
    write_json(
        tmp_path / "run-state.json",
        {
            "mode": "contract",
            "current_sprint": 4,
            "retry_count": 0,
            "last_successful_sprint": 3,
            "last_failure_reason": "",
            "needs_human": False,
            "active_branch": "main",
            "base_branch": "main",
            "last_run_at": "",
            "request_kind": "",
        },
    )

    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    # Clean state → should fall through to ready_for_next_sprint / contract proposal.
    assert payload["rule"] == "ready_for_next_sprint"
    assert payload["action"] == "invoke_codex_for_contract"
    assert payload["current_sprint"] == 4


# --- harness-audit.ndjson emission tests -------------------------------------

def _load_audit(project_dir: Path) -> list[dict]:
    path = project_dir / "harness-audit.ndjson"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_audit_log_emits_orchestrator_run_and_state_transition(tmp_path: Path) -> None:
    write_spec(tmp_path / "planner-spec.json")
    (tmp_path / "sprint-contract.md").write_text(
        "## Sprint 1\nCONTRACT APPROVED\n", encoding="utf-8"
    )

    run_orchestrator(tmp_path, "--json")

    records = _load_audit(tmp_path)
    events = [r["event"] for r in records]
    assert "orchestrator_run" in events, f"missing orchestrator_run in {events}"
    assert "state_transition" in events, f"missing state_transition in {events}"

    run_record = next(r for r in records if r["event"] == "orchestrator_run")
    assert run_record["actor"] == "orchestrator"
    assert run_record["sprint"] == 1
    assert run_record["payload"]["action"] == "invoke_codex_for_implementation"


def test_audit_log_emits_audit_finding_on_inconsistent_state(tmp_path: Path) -> None:
    """Every finding from audit_sprint_history must appear as its own event so
    a human can see each violation individually instead of a blob rationale."""
    _write_multi_sprint_spec(tmp_path / "planner-spec.json", 4)
    (tmp_path / "eval-result-3.md").write_text(
        "## Verdict: SPRINT FAIL\n", encoding="utf-8"
    )
    (tmp_path / "eval-result-4.md").write_text(
        "## Verdict: SPRINT PASS\n", encoding="utf-8"
    )
    write_json(
        tmp_path / "run-state.json",
        {
            "mode": "complete",
            "current_sprint": 4,
            "retry_count": 0,
            "last_successful_sprint": 4,
            "last_failure_reason": "",
            "needs_human": False,
            "active_branch": "main",
            "base_branch": "main",
            "last_run_at": "",
            "request_kind": "",
        },
    )

    run_orchestrator(tmp_path, "--json")
    records = _load_audit(tmp_path)

    findings = [r for r in records if r["event"] == "audit_finding"]
    # Expect at least: missing sprint 1, missing sprint 2, fail_bypassed sprint 3
    kinds = {(r["sprint"], r["payload"]["kind"]) for r in findings}
    assert (1, "evaluator_skipped") in kinds
    assert (2, "evaluator_skipped") in kinds
    assert (3, "fail_bypassed") in kinds


def test_audit_log_emits_eval_result_observed_snapshot(tmp_path: Path) -> None:
    """Every orchestrator run should record the current verdict of every
    eval-result-{N}.md so the timeline is reconstructable from the log alone."""
    _write_multi_sprint_spec(tmp_path / "planner-spec.json", 3)
    for n in (1, 2, 3):
        (tmp_path / f"eval-result-{n}.md").write_text(
            "## Verdict: SPRINT PASS\n", encoding="utf-8"
        )
    write_json(
        tmp_path / "run-state.json",
        {
            "mode": "complete",
            "current_sprint": 3,
            "retry_count": 0,
            "last_successful_sprint": 3,
            "last_failure_reason": "",
            "needs_human": False,
            "active_branch": "main",
            "base_branch": "main",
            "last_run_at": "",
            "request_kind": "",
        },
    )

    run_orchestrator(tmp_path, "--json")
    records = _load_audit(tmp_path)
    snapshots = {
        r["sprint"]: r["payload"]["verdict"]
        for r in records if r["event"] == "eval_result_observed"
    }
    assert snapshots == {1: "SPRINT PASS", 2: "SPRINT PASS", 3: "SPRINT PASS"}


def test_harness_log_cli_note_and_tail(tmp_path: Path) -> None:
    cli = ROOT / "scripts" / "harness-log.py"

    # Append a note.
    result = subprocess.run(
        [
            sys.executable, str(cli),
            "--project-dir", str(tmp_path),
            "note", "--text", "manual FAIL-bypass acknowledged", "--sprint", "3",
            "--actor", "operator",
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr

    # Tail should show exactly one human-formatted line.
    result = subprocess.run(
        [sys.executable, str(cli), "--project-dir", str(tmp_path), "tail", "-n", "10"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "note" in result.stdout
    assert "operator" in result.stdout
    assert "manual FAIL-bypass acknowledged" in result.stdout

    # Filter by event should return the same note as JSON.
    result = subprocess.run(
        [
            sys.executable, str(cli),
            "--project-dir", str(tmp_path),
            "filter", "--event", "note", "--sprint", "3", "--json",
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    record = json.loads(result.stdout.strip().splitlines()[0])
    assert record["event"] == "note"
    assert record["sprint"] == 3
    assert record["payload"]["text"] == "manual FAIL-bypass acknowledged"


def test_harness_log_cli_verify_highlights_gap(tmp_path: Path) -> None:
    cli = ROOT / "scripts" / "harness-log.py"
    _write_multi_sprint_spec(tmp_path / "planner-spec.json", 3)
    # Sprint 3 FAIL but run-state declares Sprint 3 passed.
    (tmp_path / "eval-result-3.md").write_text("## Verdict: SPRINT FAIL\n", encoding="utf-8")
    write_json(
        tmp_path / "run-state.json",
        {
            "mode": "complete", "current_sprint": 3, "retry_count": 0,
            "last_successful_sprint": 3, "last_failure_reason": "",
            "needs_human": False, "active_branch": "main", "base_branch": "main",
            "last_run_at": "", "request_kind": "",
        },
    )

    result = subprocess.run(
        [sys.executable, str(cli), "--project-dir", str(tmp_path), "verify"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "bypassed FAIL" in result.stdout  # Sprint 3
    assert "gap" in result.stdout            # Sprints 1 & 2 missing


def test_refuses_to_start_sprint_when_prior_not_passed(tmp_path: Path) -> None:
    """Even if audit somehow didn't fire (e.g. run-state.json matches reality),
    Rule 6 must still refuse to contract Sprint N when prior sprints lack PASS.
    Simulated by NOT having run-state declare success past the gap, so the
    audit does not trip, but current_sprint still computes to 3.
    Actually current_sprint() returns first-unpassed sprint, which IS the gap.
    In that case we expect 'ready_for_next_sprint' on the GAP sprint itself,
    which is correct behaviour. This test is a regression guard: if somebody
    changes current_sprint() later, this must still pause instead of skipping."""
    _write_multi_sprint_spec(tmp_path / "planner-spec.json", 4)
    # Sprint 1 missing eval-result, but Sprint 2 and 3 PASS — pathological case
    (tmp_path / "eval-result-2.md").write_text("## Verdict: SPRINT PASS\n", encoding="utf-8")
    (tmp_path / "eval-result-3.md").write_text("## Verdict: SPRINT PASS\n", encoding="utf-8")
    # run-state declares sprint 3 succeeded — this triggers the audit
    write_json(
        tmp_path / "run-state.json",
        {
            "mode": "contract",
            "current_sprint": 4,
            "retry_count": 0,
            "last_successful_sprint": 3,
            "last_failure_reason": "",
            "needs_human": False,
            "active_branch": "main",
            "base_branch": "main",
            "last_run_at": "",
            "request_kind": "",
        },
    )

    result = run_orchestrator(tmp_path, "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 2, "must pause, not proceed past a gap"
    assert payload["rule"] == "sprint_history_inconsistent"
    assert "Sprint 1" in payload["rationale"]
