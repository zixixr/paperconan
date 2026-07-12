import json
import os
import subprocess
import sys

import paperconan._audit as audit
from paperconan._audit import scan_dir, write_markdown_report
from paperconan._html import write_html_report
from paperconan._input import TableLoadResult
from paperconan._sheet import Sheet


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


def test_runtime_metadata_attributes_deferred_evidence_to_its_source(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    for name in ("a.csv", "b.csv"):
        (data / name).write_text("placeholder", encoding="utf-8")
    now = [0.0]
    load_counts = {"a.csv": 0, "b.csv": 0}

    def perf_counter():
        return now[0]

    def load_table(path):
        name = os.path.basename(path)
        load_counts[name] += 1
        if name == "a.csv":
            now[0] += 2.0 if load_counts[name] == 1 else 5.0
            return TableLoadResult({
                "affected": Sheet.from_rows([
                    ["left", "right"],
                    [1.125, 7.375],
                    [2.625, 4.875],
                    [5.375, 3.125],
                ]),
                "unaffected": Sheet.from_rows([
                    ["label"],
                    ["text"],
                ]),
            })
        now[0] += 4.0
        return TableLoadResult({
            "other": Sheet.from_rows([
                ["label"],
                ["text"],
            ])
        })

    def relation_finding(*_args, **_kwargs):
        return [{
            "kind": "constant_offset",
            "severity": "medium",
            "rule": "runtime attribution",
        }]

    for name in (
        "detect_arithmetic_progression",
        "detect_equal_pairs",
        "detect_within_column_patterns",
        "detect_dispersed_repeats",
        "detect_identical_after_rounding",
        "detect_grim_grimmer",
    ):
        monkeypatch.setattr(
            audit, name, lambda *_args, **_kwargs: []
        )
    monkeypatch.setattr(audit, "detect_relations", relation_finding)
    monkeypatch.setattr(
        audit,
        "detect_row_pair_digit_coupling",
        lambda *_args, **_kwargs: ([], {"findings_omitted": 0}),
    )
    monkeypatch.setattr(
        audit,
        "apply_profile_to_findings",
        lambda *_args, **_kwargs: None,
    )
    original_block_evidence = audit._block_evidence

    def timed_block_evidence(*args, **kwargs):
        now[0] += 3.0
        return original_block_evidence(*args, **kwargs)

    monkeypatch.setattr(audit.time, "perf_counter", perf_counter)
    monkeypatch.setattr(audit, "load_table_result", load_table)
    monkeypatch.setattr(
        audit, "_block_evidence", timed_block_evidence
    )

    scan = scan_dir(
        str(data),
        str(tmp_path / "out"),
        write_html=False,
        include_runtime=True,
    )

    files = {
        item["file"]: item for item in scan["scan_stats"]["files"]
    }
    sheets = {
        (item["file"], item["sheet"]): item
        for item in scan["scan_stats"]["sheets"]
    }
    assert load_counts == {"a.csv": 2, "b.csv": 1}
    assert files["a.csv"]["elapsed_ms"] == 10000.0
    assert files["b.csv"]["elapsed_ms"] == 4000.0
    assert sheets[("a.csv", "affected")]["elapsed_ms"] == 3000.0
    assert sheets[("a.csv", "unaffected")]["elapsed_ms"] == 0.0
    assert sheets[("b.csv", "other")]["elapsed_ms"] == 0.0
    assert scan["scan_stats"]["elapsed_ms"] == 14000.0


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


def test_reports_render_zero_elapsed_runtime(tmp_path):
    data = _data(tmp_path)
    scan = scan_dir(
        str(data),
        str(tmp_path / "scan"),
        write_html=False,
    )
    scan["scanned_at"] = None
    scan["scan_stats"]["elapsed_ms"] = 0

    html_path = tmp_path / "zero.html"
    markdown_path = tmp_path / "zero.md"
    write_html_report(scan, str(html_path))
    write_markdown_report(scan, str(markdown_path))

    html = html_path.read_text(encoding="utf-8")
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "elapsed: <code>0 ms</code>" in html
    assert "- Elapsed: `0 ms`" in markdown
    assert "Scanned at:" not in markdown
