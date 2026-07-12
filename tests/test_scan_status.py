import json
import subprocess
import sys

from paperconan._audit import scan_dir


def _write_good_csv(path):
    path.write_text("a,b\n1,2\n2,3\n3,4\n4,5\n", encoding="utf-8")


def _write_text_csv(path):
    path.write_text("label\nalpha\nbeta\n", encoding="utf-8")


def test_complete_scan_status(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write_good_csv(data / "good.csv")
    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert scan["schema_version"] == 2
    assert scan["scan_status"] == "complete"
    assert scan["coverage"]["files_succeeded"] == 1
    assert scan["coverage"]["sheets_succeeded"] == 1


def test_text_only_sheet_fails_with_no_numeric_data_reason(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write_text_csv(data / "notes.csv")

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    assert scan["scan_status"] == "failed"
    assert scan["coverage"]["sheets_succeeded"] == 0
    assert scan["coverage"]["sheets_skipped"] == 1
    assert scan["coverage"]["blocks_analyzed"] == 0
    assert scan["coverage"]["limitations"] == [{
        "scope": "sheet",
        "reason": "no_numeric_data",
        "file": "notes.csv",
        "sheet": "notes",
    }]


def test_numeric_sheet_without_qualifying_block_has_distinct_reason(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "short.csv").write_text(
        "value\n1\n2\n",
        encoding="utf-8",
    )

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    assert scan["scan_status"] == "failed"
    assert scan["coverage"]["sheets_succeeded"] == 0
    assert scan["coverage"]["sheets_skipped"] == 1
    assert scan["coverage"]["limitations"] == [{
        "scope": "sheet",
        "reason": "no_qualifying_numeric_block",
        "file": "short.csv",
        "sheet": "short",
    }]


def test_mixed_analyzed_and_text_only_sheets_are_partial(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write_good_csv(data / "good.csv")
    _write_text_csv(data / "notes.csv")

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    assert scan["scan_status"] == "partial"
    assert scan["coverage"]["sheets_succeeded"] == 1
    assert scan["coverage"]["sheets_skipped"] == 1
    assert scan["coverage"]["blocks_analyzed"] == 1


def test_qualifying_block_prevented_by_cap_keeps_cap_reason(
    tmp_path, monkeypatch
):
    import paperconan._audit as audit

    data = tmp_path / "data"
    data.mkdir()
    _write_good_csv(data / "limited.csv")
    monkeypatch.setattr(audit, "_MAX_REPORT_BLOCKS", 0)
    monkeypatch.setattr(audit, "_MAX_TOTAL_FINDINGS", 0)

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    assert scan["scan_status"] == "failed"
    reasons = [
        item["reason"] for item in scan["coverage"]["limitations"]
    ]
    assert reasons == ["report_block_limit"]
    assert "no_numeric_data" not in reasons
    assert "no_qualifying_numeric_block" not in reasons


def test_partial_scan_status_when_one_file_fails(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write_good_csv(data / "good.csv")
    (data / "bad.xlsx").write_bytes(b"not a workbook")
    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert scan["scan_status"] == "partial"
    assert scan["coverage"]["files_succeeded"] == 1
    assert scan["coverage"]["files_failed"] == 1


def test_failed_scan_status_when_every_file_fails(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "bad.xlsx").write_bytes(b"not a workbook")
    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert scan["scan_status"] == "failed"
    assert scan["coverage"]["sheets_succeeded"] == 0


def test_cli_writes_failed_scan_then_returns_nonzero(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "bad.xlsx").write_bytes(b"not a workbook")
    proc = subprocess.run(
        [sys.executable, "-m", "paperconan", str(data), "--no-html"],
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0
    scan = json.loads((data / "audit" / "scan.json").read_text())
    assert scan["scan_status"] == "failed"


def test_cli_text_only_input_writes_reason_then_returns_nonzero(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write_text_csv(data / "notes.csv")

    proc = subprocess.run(
        [sys.executable, "-m", "paperconan", str(data), "--no-html"],
        text=True,
        capture_output=True,
    )

    assert proc.returncode != 0
    scan = json.loads((data / "audit" / "scan.json").read_text())
    assert scan["scan_status"] == "failed"
    assert scan["coverage"]["limitations"][0]["reason"] == "no_numeric_data"


def test_cli_partial_scan_returns_zero(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write_good_csv(data / "good.csv")
    (data / "bad.xlsx").write_bytes(b"not a workbook")
    proc = subprocess.run(
        [sys.executable, "-m", "paperconan", str(data), "--no-html"],
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    scan = json.loads((data / "audit" / "scan.json").read_text())
    assert scan["scan_status"] == "partial"


def test_cli_empty_input_writes_failed_scan_then_returns_nonzero(tmp_path):
    data = tmp_path / "empty"
    data.mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "paperconan", str(data), "--no-html"],
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0
    scan = json.loads((data / "audit" / "scan.json").read_text())
    assert scan["scan_status"] == "failed"
    assert scan["coverage"]["files_discovered"] == 0


def test_cli_help_uses_neutral_signal_language():
    proc = subprocess.run(
        [sys.executable, "-m", "paperconan", "--help"],
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0
    assert "statistical signals and data inconsistencies" in proc.stdout.lower()
