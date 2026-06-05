from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "plugins" / "sprintfoundry" / "skills" / "sprintfoundry-orchestrator" / "SKILL.md"


def extract_version_bump_script() -> str:
    text = SKILL.read_text(encoding="utf-8")
    section = text.split("### Version bump script", 1)[1]
    match = re.search(r"python3 - <<'PY'\n(?P<script>.*?)\nPY\n```", section, re.DOTALL)
    assert match is not None
    return match.group("script")


def run_version_bump(project_dir: Path, contract: str) -> str:
    state_dir = project_dir / ".sprintfoundry"
    eval_dir = state_dir / "eval-results"
    eval_dir.mkdir(parents=True)
    (project_dir / "VERSION").write_text("1.1.7\n", encoding="utf-8")
    (project_dir / "sprint-contract.md").write_text(contract, encoding="utf-8")
    (eval_dir / "eval-result-2.md").write_text(
        "# Eval Result - Sprint 2\n\nSPRINT PASS\n",
        encoding="utf-8",
    )
    (state_dir / "run-state.json").write_text(
        json.dumps(
            {
                "current_sprint": 2,
                "current_version": "1.1.7",
                "sprint_origin": "feature",
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["python3", "-c", extract_version_bump_script()],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_contract_title_without_breaking_tests_does_not_force_major(tmp_path: Path) -> None:
    output = run_version_bump(
        tmp_path,
        "## Sprint 2: Without Breaking Tests\n\n"
        "### Features\n"
        "- Keep the existing test suite passing while updating protocol wording.\n",
    )

    assert (tmp_path / "VERSION").read_text(encoding="utf-8").strip() == "1.2.0"
    assert "(minor)" in output


def test_explicit_breaking_change_declaration_forces_major(tmp_path: Path) -> None:
    output = run_version_bump(
        tmp_path,
        "## Sprint 2: Replace legacy API\n\n"
        "Breaking changes: yes\n\n"
        "### Features\n"
        "- Remove the old public endpoint.\n",
    )

    assert (tmp_path / "VERSION").read_text(encoding="utf-8").strip() == "2.0.0"
    assert "(major)" in output
