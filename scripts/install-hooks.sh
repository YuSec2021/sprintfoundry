#!/usr/bin/env bash
# Install the harness pre-commit hook by pointing core.hooksPath at .githooks/.
# Idempotent: safe to re-run.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

chmod +x .githooks/pre-commit .githooks/post-commit
chmod +x scripts/harness-log.py

git config core.hooksPath .githooks

echo "Installed harness git hooks (core.hooksPath=.githooks):"
echo "  pre-commit  — blocks sprint-advance commits when audit fails"
echo "  post-commit — records every commit into .sprintfoundry/logs/harness-audit.ndjson"
echo ""
echo "Useful commands:"
echo "  python3 scripts/harness-log.py tail -n 30"
echo "  python3 scripts/harness-log.py verify"
echo "  python3 scripts/harness-log.py note --text 'reason for manual change'"
echo ""
echo "Emergency bypass (audited): HARNESS_BYPASS=1 git commit ..."
