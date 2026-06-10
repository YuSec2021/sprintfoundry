#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "Missing required tool: uv" >&2
  exit 127
fi

normalize_python_version() {
  local raw="$1"
  if [[ "$raw" =~ ([0-9]+[.][0-9]+([.][0-9]+)?) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

detect_python_version() {
  if [[ -n "${SPRINTFOUNDRY_PYTHON_VERSION:-}" ]]; then
    normalize_python_version "$SPRINTFOUNDRY_PYTHON_VERSION"
    return
  fi

  if [[ -f ".python-version" ]]; then
    normalize_python_version "$(head -n 1 .python-version)"
    return
  fi

  if [[ -f "runtime.txt" ]]; then
    normalize_python_version "$(head -n 1 runtime.txt)"
    return
  fi

  if [[ -f "pyproject.toml" ]]; then
    local requires
    requires="$(grep -E '^[[:space:]]*requires-python[[:space:]]*=' pyproject.toml | head -n 1 || true)"
    if [[ -n "$requires" ]]; then
      normalize_python_version "$requires"
      return
    fi
  fi

  python3 - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
}

PYTHON_VERSION="$(detect_python_version)"

if [[ $# -eq 0 ]]; then
  set -- -q
fi

echo "Running pytest with uv-managed Python ${PYTHON_VERSION}"
exec uv run --python "$PYTHON_VERSION" --with pytest --with pytest-cov pytest "$@"
