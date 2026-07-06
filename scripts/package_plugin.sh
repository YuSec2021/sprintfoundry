#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/package_plugin.sh
# Build sprintfoundry.plugin from plugins/sprintfoundry/ and sync versions.
#
# Usage:
#   bash scripts/package_plugin.sh               # build only
#   bash scripts/package_plugin.sh --bump patch  # patch version, then build
#   bash scripts/package_plugin.sh --bump minor  # minor version, then build
#   bash scripts/package_plugin.sh --bump major  # major version, then build
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLUGIN_SRC="$PROJECT_ROOT/plugins/sprintfoundry"
PLUGIN_JSON="$PLUGIN_SRC/.claude-plugin/plugin.json"
MARKET_JSON="$PROJECT_ROOT/.claude-plugin/marketplace.json"
OUTPUT="$PROJECT_ROOT/sprintfoundry.plugin"

BUMP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --bump) BUMP="$2"; shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# ── Version bump (sync both plugin.json and marketplace.json) ─────────────────
if [[ -n "$BUMP" ]]; then
  python3 - "$PLUGIN_JSON" "$MARKET_JSON" "$BUMP" << 'PY'
import json, sys, pathlib, re

pj_path = pathlib.Path(sys.argv[1])
mj_path = pathlib.Path(sys.argv[2])
bump    = sys.argv[3]
assert bump in ("major", "minor", "patch"), f"invalid bump: {bump}"

def semver_bump(ver, bump):
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", ver)
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if   bump == "major": major += 1; minor = 0; patch = 0
    elif bump == "minor": minor += 1; patch = 0
    elif bump == "patch": patch += 1
    return f"{major}.{minor}.{patch}"

# Bump plugin.json
pj = json.loads(pj_path.read_text())
old = pj.get("version", "0.0.0")
new = semver_bump(old, bump)
pj["version"] = new
pj_path.write_text(json.dumps(pj, indent=2) + "\n")

# Sync marketplace.json (both metadata.version and plugin entry version)
mj = json.loads(mj_path.read_text())
if "metadata" in mj:
    mj["metadata"]["version"] = new
for p in mj.get("plugins", []):
    if p.get("name") == "sprintfoundry":
        p["version"] = new
mj_path.write_text(json.dumps(mj, indent=2) + "\n")

print(f"Version bumped: {old} → {new}  (plugin.json + marketplace.json)")
PY
fi

# ── Agent-copy consistency gate ───────────────────────────────────────────────
bash "$SCRIPT_DIR/check-agent-sync.sh" || {
  echo "package_plugin: agent copies have drifted — aborting build."
  exit 1
}

# ── Ship harness scripts with the orchestrator skill ─────────────────────────
# The plugin is the only thing installed in target projects; orchestrate.py is
# the single source of truth for routing, so it must travel with the skill.
SKILL_SCRIPTS="$PLUGIN_SRC/skills/sprintfoundry-orchestrator/scripts"
mkdir -p "$SKILL_SCRIPTS"
for s in orchestrate.py run-codex.sh harness-log.py; do
  cp "$SCRIPT_DIR/$s" "$SKILL_SCRIPTS/$s"
done
echo "Shipped scripts into skill."

# ── Validate ──────────────────────────────────────────────────────────────────
python3 - "$PLUGIN_JSON" << 'PY'
import json, re, pathlib, sys

path   = pathlib.Path(sys.argv[1])
plugin = path.parent.parent

data = json.loads(path.read_text())
name = data.get("name", "")
errors = []

if not re.match(r'^[a-z0-9-]+$', name):
    errors.append(f'name "{name}" must be kebab-case')

for skill in (plugin / "skills").iterdir():
    if not (skill / "SKILL.md").exists():
        errors.append(f"skills/{skill.name}/SKILL.md missing")

if not list((plugin / "agents").glob("*.md")):
    errors.append("agents/ has no .md files")

if errors:
    print("VALIDATION ERRORS:")
    for e in errors: print(" -", e)
    sys.exit(1)

version = data.get("version", "?")
skills  = [s.name for s in sorted((plugin / "skills").iterdir())]
agents  = [a.name for a in sorted((plugin / "agents").iterdir())]
print(f"Validation: PASS  ({name} v{version})")
print(f"  skills: {skills}")
print(f"  agents: {agents}")
PY

# ── Package ───────────────────────────────────────────────────────────────────
TMP_ZIP="/tmp/sprintfoundry.plugin"
rm -f "$TMP_ZIP"
(cd "$PLUGIN_SRC" && zip -r "$TMP_ZIP" . -x "*.DS_Store" -x "__pycache__/*" -q)
cp "$TMP_ZIP" "$OUTPUT"
SIZE=$(du -sh "$OUTPUT" | cut -f1)
VERSION=$(python3 -c "import json; print(json.load(open('$PLUGIN_JSON'))['version'])")
echo "Built: $OUTPUT  ($SIZE, v$VERSION)"
echo ""
echo "To distribute via GitHub marketplace:"
echo "  git add -A && git commit -m 'release: v$VERSION' && git tag v$VERSION && git push --tags"
