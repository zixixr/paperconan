"""The per-file memory guard: oversized workbooks are skipped (recorded), not loaded into RAM."""
import os

import pytest

import paperconan._audit as A


def _demo_dir():
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples", "demo_paper")


def test_oversized_file_is_skipped_and_recorded(tmp_path, monkeypatch):
    in_dir = _demo_dir()
    if not os.path.isdir(in_dir) or not any(f.endswith(".xlsx") for f in os.listdir(in_dir)):
        pytest.skip("examples/demo_paper xlsx not present")
    # Force a tiny cap so the demo files are treated as oversized.
    monkeypatch.setattr(A, "_MAX_FILE_BYTES", 100)
    monkeypatch.setattr(A, "_MAX_FILE_MB", 0.0001)
    scan = A.scan_dir(in_dir, str(tmp_path), write_html=False)
    # Scan still completes (no crash) and the oversized file is recorded, never silently clean.
    assert scan["scan_errors"], "oversized files must be recorded in scan_errors"
    assert any("oversized" in e["error"] for e in scan["scan_errors"])
    assert any(s.get("oversized") for s in scan["scan_stats"]["files"])


def test_normal_file_under_cap_is_audited(tmp_path):
    in_dir = _demo_dir()
    if not os.path.isdir(in_dir) or not any(f.endswith(".xlsx") for f in os.listdir(in_dir)):
        pytest.skip("examples/demo_paper xlsx not present")
    # Default 25 MB cap: the small demo files load and produce a normal scan.
    scan = A.scan_dir(in_dir, str(tmp_path), write_html=False)
    assert not any(s.get("oversized") for s in scan["scan_stats"]["files"])
