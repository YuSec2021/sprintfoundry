from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_attestation(tmp_path_factory, monkeypatch):
    """Keep attestation keys AND stores out of the real ~/.sprintfoundry.

    The env vars are inherited by the orchestrator subprocesses the tests
    spawn, so in-process and subprocess code paths share the same throwaway
    locations.
    """
    base = tmp_path_factory.mktemp("attest")
    monkeypatch.setenv("SPRINTFOUNDRY_ATTEST_KEY_FILE", str(base / "attest.key"))
    monkeypatch.setenv("SPRINTFOUNDRY_ATTEST_DIR", str(base / "stores"))
