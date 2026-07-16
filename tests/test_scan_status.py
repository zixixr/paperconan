import json
import os
import subprocess
import sys

import paperconan._audit as audit
import pytest

from paperconan._audit import scan_dir
from paperconan._input import InputLimitation, TableLoadResult
from paperconan._sheet import Sheet


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


def test_tiny_finite_relation_is_not_reported_as_parse_failure(
    tmp_path,
):
    data = tmp_path / "data"
    data.mkdir()
    (data / "tiny.csv").write_text(
        "x,y\n"
        "1e-170,2e-170\n"
        "2e-170,4e-170\n"
        "3e-170,6e-170\n"
        "4e-170,8e-170\n",
        encoding="utf-8",
    )

    scan = scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    assert scan["scan_status"] == "complete"
    assert scan["coverage"]["files_succeeded"] == 1
    assert scan["coverage"]["files_failed"] == 0
    assert all(
        item["reason"] != "parse_error"
        for item in scan["coverage"]["limitations"]
    )


def test_non_float_convertible_integer_does_not_abort_full_scan(
    tmp_path,
):
    data = tmp_path / "data"
    data.mkdir()
    wide = 10**400
    first_block = [
        f"{wide},1.125",
        *[
            f"{index + 0.125},{index + 1.375}"
            for index in range(1, 13)
        ],
    ]
    second_block = [
        f"{wide + 1},2.625",
        *[
            f"{index + 10.234},{index + 20.456}"
            for index in range(1, 13)
        ],
    ]
    (data / "wide.csv").write_text(
        "\n".join([
            "a,b",
            *first_block,
            "",
            "c,d",
            *second_block,
            "",
        ]),
        encoding="utf-8",
    )

    scan = scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    affected_detectors = [
        "relations",
        "equal_pairs",
        "row_pairs",
        "row_relations",
        "arithmetic_progression",
        "within_column",
        "dispersed_repeats",
        "identical_after_rounding",
        "grim_grimmer",
    ]
    assert scan["scan_status"] == "partial"
    assert scan["coverage"]["files_succeeded"] == 1
    assert scan["coverage"]["files_failed"] == 0
    assert scan["coverage"]["sheets_succeeded"] == 1
    assert scan["coverage"]["limitations"] == [
        {
            "scope": "block",
            "reason": "wide_integer_detector_limit",
            "file": "wide.csv",
            "sheet": "wide",
            "rows": "2-14",
            "cols": "1-2",
            "affected_cells": 1,
            "detectors": affected_detectors,
        },
        {
            "scope": "block",
            "reason": "wide_integer_detector_limit",
            "file": "wide.csv",
            "sheet": "wide",
            "rows": "17-29",
            "cols": "1-2",
            "affected_cells": 1,
            "detectors": affected_detectors,
        },
    ]


@pytest.mark.parametrize("row_count", [8, 24])
def test_extreme_relation_is_not_reported_as_parse_failure(
    tmp_path, row_count, monkeypatch
):
    # Other finite-extreme detector paths belong to separate hardening work.
    for detector_name in (
        "detect_arithmetic_progression",
        "detect_within_column_patterns",
        "detect_dispersed_repeats",
        "detect_identical_after_rounding",
    ):
        monkeypatch.setattr(
            audit,
            detector_name,
            lambda *_args, **_kwargs: [],
        )
    data = tmp_path / "data"
    data.mkdir()
    rows = "\n".join(
        f"0.1,{'1e308' if index % 2 == 0 else '-1e308'}"
        for index in range(row_count)
    )
    (data / "extreme.csv").write_text(
        f"x,y\n{rows}\n",
        encoding="utf-8",
    )

    scan = scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    assert scan["scan_status"] == "complete"
    assert scan["coverage"]["files_succeeded"] == 1
    assert scan["coverage"]["files_failed"] == 0
    assert all(
        item["reason"] != "parse_error"
        for item in scan["coverage"]["limitations"]
    )


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


def _write_cross_table_only_inputs(data):
    payload = (
        "a,b,c\n"
        "1.123456,2.234567,3.345678\n"
        "4.456789,5.567891,6.678912\n"
    )
    (data / "first.csv").write_text(payload, encoding="utf-8")
    (data / "second.csv").write_text(payload, encoding="utf-8")


def test_cross_table_only_analysis_is_partial(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write_cross_table_only_inputs(data)

    scan = scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    assert scan["cross_sheet_findings"][0]["kind"] == (
        "cross_sheet_position_identical"
    )
    assert scan["coverage"]["blocks_analyzed"] == 0
    assert scan["scan_status"] == "partial"


def test_cli_cross_table_only_analysis_returns_zero(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write_cross_table_only_inputs(data)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "paperconan",
            str(data),
            "--no-html",
        ],
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr
    scan = json.loads(
        (data / "audit" / "scan.json").read_text(encoding="utf-8")
    )
    assert scan["scan_status"] == "partial"


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


def test_missing_deferred_evidence_makes_scan_partial_deterministically(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "source.csv"
    path.write_text("placeholder", encoding="utf-8")
    values = [
        11.125,
        7.375,
        19.625,
        3.875,
        14.125,
        8.625,
        17.375,
        5.125,
        13.875,
        9.625,
        16.125,
        6.375,
        12.625,
        10.875,
    ]
    calls = 0

    def load_table(_path, *, inspect_formulas=True):
        nonlocal calls
        calls += 1
        if calls > 1:
            assert inspect_formulas is False
        if calls == 1:
            return TableLoadResult({
                "source": Sheet.from_rows(
                    [["left", "right"]]
                    + [[value, value] for value in values]
                )
            })
        return TableLoadResult(
            {"source": None},
            [InputLimitation(
                scope="sheet",
                reason="cell_limit",
                sheet="source",
                details={"cells": 30, "max_cells": 20},
            )],
        )

    monkeypatch.setattr(audit, "load_table_result", load_table)

    scan = scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    expected = {
        "scope": "sheet",
        "reason": "evidence_reload_cell_limit",
        "file": "source.csv",
        "sheet": "source",
        "cells": 30,
        "max_cells": 20,
    }
    assert scan["relations_blocks"]
    assert scan["scan_status"] == "partial"
    assert scan["coverage"]["limitations"] == [expected]
    assert list(scan["coverage"]["limitations"][0]) == [
        "scope",
        "reason",
        "file",
        "sheet",
        "cells",
        "max_cells",
    ]
    serialized = json.loads(
        (tmp_path / "out" / "scan.json").read_text(encoding="utf-8")
    )
    assert serialized["scan_status"] == "partial"
    assert serialized["coverage"]["limitations"] == [expected]
    assert all(
        not any(key.startswith("_evidence_") for key in block)
        for block in serialized["relations_blocks"]
    )


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


@pytest.mark.parametrize(
    ("path_kind", "diagnostic"),
    [
        ("missing", "input directory does not exist"),
        ("file", "input path is not a directory"),
    ],
)
def test_cli_invalid_input_path_reports_domain_error_without_traceback(
    tmp_path, path_kind, diagnostic
):
    path = tmp_path / path_kind
    if path_kind == "file":
        path.write_text("value\n1\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "paperconan", str(path), "--no-html"],
        text=True,
        capture_output=True,
    )

    assert proc.returncode != 0
    assert diagnostic in proc.stderr
    assert str(path) in proc.stderr
    assert "Traceback" not in proc.stderr
    assert "Traceback" not in proc.stdout


def test_cli_enumeration_error_reports_domain_error_without_traceback(
    tmp_path,
):
    data = tmp_path / "data"
    data.mkdir()
    hook_dir = tmp_path / "hook"
    hook_dir.mkdir()
    (hook_dir / "sitecustomize.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "\n"
        "_target = os.path.abspath(\n"
        "    os.environ['PAPERCONAN_TEST_ITERDIR_FAILURE']\n"
        ")\n"
        "_original_iterdir = Path.iterdir\n"
        "\n"
        "def _fail_target_iterdir(path):\n"
        "    if os.path.abspath(path) == _target:\n"
        "        raise PermissionError('injected enumeration failure')\n"
        "    return _original_iterdir(path)\n"
        "\n"
        "Path.iterdir = _fail_target_iterdir\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PAPERCONAN_TEST_ITERDIR_FAILURE"] = str(data)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [
        str(hook_dir),
        env.get("PYTHONPATH"),
    ]))

    proc = subprocess.run(
        [sys.executable, "-m", "paperconan", str(data), "--no-html"],
        text=True,
        capture_output=True,
        env=env,
    )

    assert proc.returncode != 0
    assert "could not enumerate input directory" in proc.stderr
    assert str(data) in proc.stderr
    assert "injected enumeration failure" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert "Traceback" not in proc.stdout


def test_cli_help_uses_neutral_signal_language():
    proc = subprocess.run(
        [sys.executable, "-m", "paperconan", "--help"],
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0
    assert "statistical signals and data inconsistencies" in proc.stdout.lower()
