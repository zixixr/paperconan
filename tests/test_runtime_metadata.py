"""Runtime metadata (wall-clock timestamp + elapsed times) is opt-in.

By default paperconan omits every non-deterministic timing value so that
``scan.json`` is byte-reproducible for identical input; ``--runtime-metadata``
(``scan_dir(runtime_metadata=True)``) records them explicitly.
"""

import json
import os
import subprocess
import sys

from paperconan._audit import scan_dir


_CSV = "a,b,c\n1.11,2.22,3.33\n4.44,5.55,6.66\n7.77,8.88,9.99\n10.1,11.2,12.3\n"


def _write_input(tmp_path):
    src = tmp_path / "data.csv"
    src.write_text(_CSV)
    return src


def _scan(tmp_path, out_name, **kwargs):
    out = tmp_path / out_name
    scan_dir(
        str(tmp_path),
        str(out),
        write_md=False,
        write_html=False,
        write_json=True,
        **kwargs,
    )
    return json.loads((out / "scan.json").read_text())


def test_runtime_metadata_omitted_by_default(tmp_path):
    _write_input(tmp_path)
    scan = _scan(tmp_path, "audit")

    assert scan["scanned_at"] is None
    assert scan["scan_stats"]["elapsed_ms"] is None
    for file_stat in scan["scan_stats"]["files"]:
        assert file_stat["elapsed_ms"] is None
    for sheet_stat in scan["scan_stats"]["sheets"]:
        assert sheet_stat["elapsed_ms"] is None


def test_runtime_metadata_recorded_when_requested(tmp_path):
    _write_input(tmp_path)
    scan = _scan(tmp_path, "audit", runtime_metadata=True)

    assert isinstance(scan["scanned_at"], str) and scan["scanned_at"]
    assert isinstance(scan["scan_stats"]["elapsed_ms"], (int, float))
    for file_stat in scan["scan_stats"]["files"]:
        assert isinstance(file_stat["elapsed_ms"], (int, float))


def test_default_scan_json_is_byte_deterministic(tmp_path):
    """The headline win: no wall clock leaks into the default output."""
    _write_input(tmp_path)
    first = (tmp_path / "a")
    second = (tmp_path / "b")
    scan_dir(str(tmp_path), str(first), write_html=False, write_json=True)
    scan_dir(str(tmp_path), str(second), write_html=False, write_json=True)
    assert (first / "scan.json").read_bytes() == (second / "scan.json").read_bytes()


def test_cli_runtime_metadata_flag(tmp_path):
    _write_input(tmp_path)
    env = dict(os.environ)
    base = [sys.executable, "-m", "paperconan._audit", str(tmp_path), "--no-html"]

    subprocess.run(base + ["--out", str(tmp_path / "off")], check=True, env=env)
    off = json.loads((tmp_path / "off" / "scan.json").read_text())
    assert off["scanned_at"] is None

    subprocess.run(
        base + ["--out", str(tmp_path / "on"), "--runtime-metadata"],
        check=True,
        env=env,
    )
    on = json.loads((tmp_path / "on" / "scan.json").read_text())
    assert on["scanned_at"] is not None
