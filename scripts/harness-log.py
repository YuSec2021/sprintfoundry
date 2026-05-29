#!/usr/bin/env python3
"""Append-only audit log CLI for the Claude + Codex sprint harness.

The authoritative timeline lives in `.sprintfoundry/harness-audit.ndjson`. This tool is a
thin wrapper that makes it easy for humans and hooks to write well-formed
entries and to read the log back with structured filters.

Subcommands
-----------
  note    Append a free-form annotation (use when a human takes a manual action).
  event   Append a structured event (used by git hooks and rescue scripts).
  tail    Print the last N entries (default 20) in human-readable form.
  filter  Print entries matching --event, --sprint, or --actor filters.
  verify  Re-derive passing-sprint set from eval-result files; print summary.

This tool never truncates the log. To rotate, copy the file elsewhere and let
a new one be created on the next append; do not overwrite in place.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUDIT_FILE = ".sprintfoundry/harness-audit.ndjson"
EVAL_RESULTS_DIR = ".sprintfoundry/eval-results"
RUN_STATE_FILE = ".sprintfoundry/run-state.json"


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def append_record(project_dir: Path, record: dict[str, Any]) -> None:
    record.setdefault("ts", iso_now())
    path = project_dir / AUDIT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_records(project_dir: Path) -> list[dict[str, Any]]:
    path = project_dir / AUDIT_FILE
    if not path.exists():
        path = project_dir / "harness-audit.ndjson"
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # Preserve the corruption marker so auditors see it but keep going.
            records.append({"ts": "?", "event": "corrupted_line", "raw": line})
    return records


def format_record(record: dict[str, Any]) -> str:
    ts = record.get("ts", "?")
    event = record.get("event", "?")
    actor = record.get("actor", "?")
    sprint = record.get("sprint")
    sprint_str = f"sprint={sprint}" if sprint is not None else "-"
    payload = record.get("payload", {})

    # Short human-readable summary per event type.
    if event == "orchestrator_run":
        summary = f"rule={payload.get('rule')} action={payload.get('action')}"
        if payload.get("needs_human"):
            summary += " [NEEDS_HUMAN]"
    elif event == "audit_finding":
        summary = f"{payload.get('kind')}: {payload.get('detail', '')[:90]}"
    elif event == "state_transition":
        changes = payload.get("changes", {})
        summary = "; ".join(f"{k}:{v[0]}→{v[1]}" for k, v in changes.items())
    elif event == "eval_result_observed":
        summary = f"verdict={payload.get('verdict')}"
    elif event == "commit_recorded":
        summary = f"{payload.get('sha', '')[:8]} {payload.get('subject', '')[:80]}"
    elif event == "commit_blocked":
        summary = f"[{payload.get('rule')}] {payload.get('subject', '')[:70]}"
    elif event == "commit_bypassed":
        summary = f"BYPASS {payload.get('subject', '')[:80]}"
    elif event == "note":
        summary = payload.get("text", "")
    else:
        summary = json.dumps(payload, ensure_ascii=False)[:120]

    return f"{ts}  {event:<22} {actor:<12} {sprint_str:<10} {summary}"


def cmd_note(args: argparse.Namespace) -> int:
    if not args.text:
        print("error: --text is required for 'note'", file=sys.stderr)
        return 2
    append_record(
        args.project_dir,
        {
            "event": "note",
            "actor": args.actor or os.environ.get("USER") or "human",
            "sprint": args.sprint,
            "payload": {"text": args.text},
        },
    )
    return 0


def cmd_event(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {}
    if args.payload:
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as exc:
            print(f"error: --payload must be valid JSON ({exc})", file=sys.stderr)
            return 2
    append_record(
        args.project_dir,
        {
            "event": args.event,
            "actor": args.actor or "cli",
            "sprint": args.sprint,
            "payload": payload,
        },
    )
    return 0


def cmd_tail(args: argparse.Namespace) -> int:
    records = read_records(args.project_dir)
    n = max(1, args.count)
    for record in records[-n:]:
        print(format_record(record))
    return 0


def cmd_filter(args: argparse.Namespace) -> int:
    records = read_records(args.project_dir)
    wanted_events = set(args.event or [])
    wanted_actors = set(args.actor or [])
    wanted_sprint = args.sprint
    for record in records:
        if wanted_events and record.get("event") not in wanted_events:
            continue
        if wanted_actors and record.get("actor") not in wanted_actors:
            continue
        if wanted_sprint is not None and record.get("sprint") != wanted_sprint:
            continue
        if args.json:
            print(json.dumps(record, ensure_ascii=False))
        else:
            print(format_record(record))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Print a reconciliation report without writing anything.

    Useful when diagnosing a pause: shows declared state (.sprintfoundry/run-state.json) next
    to the authoritative eval-result verdicts and highlights any gaps.
    """
    root = args.project_dir
    spec_path = root / "planner-spec.json"
    run_state_path = root / RUN_STATE_FILE
    if not run_state_path.exists():
        run_state_path = root / "run-state.json"
    if not spec_path.exists():
        print("no planner-spec.json; nothing to verify")
        return 0

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    run_state = (
        json.loads(run_state_path.read_text(encoding="utf-8"))
        if run_state_path.exists() else {}
    )

    verdicts: dict[int, str] = {}
    eval_paths = [
        *root.glob(f"{EVAL_RESULTS_DIR}/eval-result-*.md"),
        *root.glob("eval-result-*.md"),
    ]
    for path in sorted({p.resolve(): p for p in eval_paths}.values()):
        stem_tail = path.stem.split("-")[-1]
        if not stem_tail.isdigit():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        verdict = (
            "SPRINT PASS" if "SPRINT PASS" in text else
            "SPRINT FAIL" if "SPRINT FAIL" in text else
            "UNKNOWN"
        )
        verdicts[int(stem_tail)] = verdict

    declared_last = int(run_state.get("last_successful_sprint", 0) or 0)
    mode = run_state.get("mode", "?")
    needs_human = run_state.get("needs_human", False)

    print(f".sprintfoundry/run-state.json  mode={mode}  last_successful_sprint={declared_last}"
          f"  needs_human={needs_human}")
    print("eval-result verdicts:")
    for sprint in spec.get("sprints", []):
        sid = int(sprint["id"])
        v = verdicts.get(sid, "MISSING")
        marker = ""
        if v == "MISSING" and sid <= declared_last:
            marker = "  ← gap"
        elif v == "SPRINT FAIL" and sid <= declared_last:
            marker = "  ← bypassed FAIL"
        print(f"  Sprint {sid:>2}: {v}{marker}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path.cwd(),
        help="Project root (default: current directory).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_note = sub.add_parser("note", help="Append a free-form human note.")
    p_note.add_argument("--text", required=True, help="Note body.")
    p_note.add_argument("--sprint", type=int, help="Associated sprint (optional).")
    p_note.add_argument("--actor", help="Who is writing this (default: $USER).")
    p_note.set_defaults(func=cmd_note)

    p_event = sub.add_parser("event", help="Append a structured event (used by hooks).")
    p_event.add_argument("--event", required=True, help="Event type string.")
    p_event.add_argument("--actor", help="Emitter identifier (default: cli).")
    p_event.add_argument("--sprint", type=int, help="Associated sprint (optional).")
    p_event.add_argument("--payload", help="JSON-encoded payload object.")
    p_event.set_defaults(func=cmd_event)

    p_tail = sub.add_parser("tail", help="Print the last N audit entries.")
    p_tail.add_argument("--count", "-n", type=int, default=20)
    p_tail.set_defaults(func=cmd_tail)

    p_filter = sub.add_parser("filter", help="Print entries matching filters.")
    p_filter.add_argument("--event", action="append", help="Event type (repeatable).")
    p_filter.add_argument("--actor", action="append", help="Actor name (repeatable).")
    p_filter.add_argument("--sprint", type=int, help="Exact sprint number.")
    p_filter.add_argument("--json", action="store_true", help="Raw JSON output.")
    p_filter.set_defaults(func=cmd_filter)

    p_verify = sub.add_parser(
        "verify", help="Reconcile .sprintfoundry/run-state.json against eval-result verdicts."
    )
    p_verify.set_defaults(func=cmd_verify)

    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    args.project_dir = args.project_dir.resolve()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
