#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/check-agent-sync.sh
# Fails when the agent-definition copies drift apart.
#
# plugins/sprintfoundry/agents/ is the source of truth; .claude/agents/ is the
# local-dev fallback and must be byte-identical for planner/evaluator/generator.
# (.claude/agents/orchestrator.md is deprecated and exempt.)
#
# Used by package_plugin.sh and CI. Run manually:
#   bash scripts/check-agent-sync.sh          # check
#   bash scripts/check-agent-sync.sh --fix    # copy plugin → .claude
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC="$ROOT/plugins/sprintfoundry/agents"
DST="$ROOT/.claude/agents"
FIX="${1:-}"

STATUS=0
for agent in planner evaluator generator; do
    if [[ ! -f "$SRC/$agent.md" ]]; then
        echo "MISSING source: plugins/sprintfoundry/agents/$agent.md"
        STATUS=1
        continue
    fi
    if [[ ! -f "$DST/$agent.md" ]] || ! cmp -s "$SRC/$agent.md" "$DST/$agent.md"; then
        if [[ "$FIX" == "--fix" ]]; then
            cp "$SRC/$agent.md" "$DST/$agent.md"
            echo "SYNCED  .claude/agents/$agent.md ← plugins/sprintfoundry/agents/$agent.md"
        else
            echo "DRIFT   .claude/agents/$agent.md differs from plugins/sprintfoundry/agents/$agent.md"
            echo "        fix: bash scripts/check-agent-sync.sh --fix"
            STATUS=1
        fi
    fi
done

# The orchestrator skill ships copies of the harness scripts (the plugin is
# installed from this directory by the marketplace). They must match scripts/.
SKILL_SCRIPTS="$ROOT/plugins/sprintfoundry/skills/sprintfoundry-orchestrator/scripts"
for s in orchestrate.py run-codex.sh harness-log.py; do
    if [[ ! -f "$SKILL_SCRIPTS/$s" ]] || ! cmp -s "$ROOT/scripts/$s" "$SKILL_SCRIPTS/$s"; then
        if [[ "$FIX" == "--fix" ]]; then
            mkdir -p "$SKILL_SCRIPTS"
            cp "$ROOT/scripts/$s" "$SKILL_SCRIPTS/$s"
            echo "SYNCED  skill scripts/$s ← scripts/$s"
        else
            echo "DRIFT   skill scripts/$s differs from scripts/$s"
            echo "        fix: bash scripts/check-agent-sync.sh --fix"
            STATUS=1
        fi
    fi
done

if [[ $STATUS -eq 0 ]]; then
    echo "agent-sync: OK (agents + shipped scripts identical)"
fi
exit $STATUS
