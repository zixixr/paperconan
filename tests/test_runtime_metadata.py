import json
import os
import subprocess
import sys

import paperconan._audit as audit
from paperconan._audit import scan_dir, write_markdown_report
from paperconan._html import write_html_report


def _data(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "t.csv").write_text(
        "a,b\n1.1,2.2\n2.2,3.3\n3.3,4.4\n",
        encoding="utf-8",
    )
    return data


def test_default_scan_json_is_byte_deterministic(tmp_path):
    data = _data(tmp_path)
    out = tmp_path / "out"
    scan_dir(str(data), str(out), write_html=False)
    first = (out / "scan.json").read_bytes()
    scan_dir(str(data), str(out), write_html=False)
    second = (out / "scan.json").read_bytes()
    assert second == first
    scan = json.loads(first)
    assert scan["scanned_at"] is None
    assert scan["scan_stats"]["elapsed_ms"] is None
    assert all(
        item["elapsed_ms"] is None
        for item in scan["scan_stats"]["files"]
    )
    assert all(
        item["elapsed_ms"] is None
        for item in scan["scan_stats"]["sheets"]
    )
    assert all(
        not os.path.isabs(item["path"])
        for item in scan["scan_stats"]["files"]
    )


def test_runtime_metadata_is_opt_in(tmp_path):
    data = _data(tmp_path)
    scan = scan_dir(
        str(data),
        str(tmp_path / "out"),
        write_html=False,
        include_runtime=True,
    )
    assert isinstance(scan["scanned_at"], str)
    assert scan["scan_stats"]["elapsed_ms"] >= 0
    assert all(
        item["elapsed_ms"] >= 0
        for item in scan["scan_stats"]["files"]
    )
    assert all(
        item["elapsed_ms"] >= 0
        for item in scan["scan_stats"]["sheets"]
    )


def test_cli_runtime_metadata_switch(tmp_path):
    data = _data(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "paperconan",
            str(data),
            "--no-html",
            "--runtime-metadata",
        ],
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    scan = json.loads(
        (data / "audit" / "scan.json").read_text(encoding="utf-8")
    )
    assert isinstance(scan["scanned_at"], str)


def test_file_size_limit_uses_relative_path_and_runtime_gate(
    tmp_path, monkeypatch
):
    data = _data(tmp_path)
    monkeypatch.setattr(audit, "_MAX_FILE_BYTES", 0)

    scan = scan_dir(
        str(data),
        str(tmp_path / "default"),
        write_html=False,
    )
    file_stat = scan["scan_stats"]["files"][0]
    assert file_stat["path"] == "t.csv"
    assert file_stat["elapsed_ms"] is None

    runtime_scan = scan_dir(
        str(data),
        str(tmp_path / "runtime"),
        write_html=False,
        include_runtime=True,
    )
    runtime_file_stat = runtime_scan["scan_stats"]["files"][0]
    assert runtime_file_stat["path"] == "t.csv"
    assert runtime_file_stat["elapsed_ms"] >= 0


def test_reports_hide_null_runtime_and_render_archived_values(tmp_path):
    data = _data(tmp_path)
    scan = scan_dir(
        str(data),
        str(tmp_path / "scan"),
        write_html=False,
    )
    scan["scanned_at"] = None
    scan["scan_stats"]["elapsed_ms"] = None

    null_html = tmp_path / "null.html"
    null_md = tmp_path / "null.md"
    write_html_report(scan, str(null_html))
    write_markdown_report(scan, str(null_md))
    null_html_text = null_html.read_text(encoding="utf-8")
    null_md_text = null_md.read_text(encoding="utf-8")
    assert "elapsed:" not in null_html_text
    assert "Scanned at:" not in null_md_text
    assert "Elapsed:" not in null_md_text

    scan["scanned_at"] = "2026-07-12T01:02:03+00:00"
    scan["scan_stats"]["elapsed_ms"] = 12.5
    archived_html = tmp_path / "archived.html"
    archived_md = tmp_path / "archived.md"
    write_html_report(scan, str(archived_html))
    write_markdown_report(scan, str(archived_md))
    archived_html_text = archived_html.read_text(encoding="utf-8")
    archived_md_text = archived_md.read_text(encoding="utf-8")
    assert "2026-07-12T01:02:03+00:00" in archived_html_text
    assert "elapsed: <code>12.5 ms</code>" in archived_html_text
    assert (
        "- Scanned at: `2026-07-12T01:02:03+00:00`"
        in archived_md_text
    )
    assert "- Elapsed: `12.5 ms`" in archived_md_text
