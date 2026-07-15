import builtins
import json

import paperconan._audit as audit
import pytest
from paperconan._coverage import ScanCoverage
from paperconan._input import InputLimitation, TableLoadResult
from paperconan._sheet import Sheet
from paperconan._summaries import RecurringRowIndex
from paperconan._audit import _block_evidence, scan_dir


def _grid(nr, nc):
    return Sheet.from_rows([[float(r * 1000 + c) for c in range(nc)] for r in range(nr)])


def _deferred_block(path, *, file_name=None, sheet_name="Target"):
    finding = {
        "kind": "constant_offset",
        "severity": "medium",
        "rule": "deferred evidence",
        "col_a_idx": 0,
        "col_b_idx": 1,
    }
    groups = {name: [] for name in audit.BLOCK_FINDING_GROUPS}
    groups["relations"] = [finding]
    block = {
        "file": file_name or path.name,
        "sheet": sheet_name,
        "block": {
            "rows": "1-4",
            "cols": "1-2",
            "header": ["left", "right"],
        },
        **groups,
        "_evidence_path": str(path),
        "_evidence_context": (0, 4, 0, 2),
    }
    return block, finding


def _evidence_sheet():
    return Sheet.from_rows([
        ["left", "right"],
        [1.25, 2.25],
        [2.5, 3.5],
        [3.75, 4.75],
    ])


def test_small_block_untruncated():
    s = _grid(8, 5)
    ev = _block_evidence(s, 0, 8, 0, 5, ["a", "b", "c", "d", "e"], [1])
    assert "truncated" not in ev
    assert "col_indices" not in ev
    assert len(ev["rows"]) >= 8 and all(len(r["values"]) == 5 for r in ev["rows"])


def test_big_block_truncated_keeps_highlight():
    s = _grid(300, 200)
    hi = [150, 151]
    ev = _block_evidence(s, 0, 300, 0, 200, [f"h{c}" for c in range(200)], hi)
    assert ev.get("truncated") is True
    assert len(ev["rows"]) <= 52                      # <= _MAX_EV_ROWS (+1 ctx each side)
    assert all(len(r["values"]) <= 30 for r in ev["rows"])   # <= _MAX_EV_COLS
    # the highlighted columns are within the emitted window
    assert {150, 151} <= set(ev["col_indices"])
    assert len(ev["headers"]) == len(ev["rows"][0]["values"])


def test_truncated_evidence_keeps_distant_highlighted_columns(monkeypatch):
    monkeypatch.setattr(audit, "_MAX_EV_COLS", 3)
    s = _grid(5, 100)

    ev = _block_evidence(
        s,
        0,
        5,
        0,
        100,
        [f"h{c}" for c in range(100)],
        [0, 99],
    )

    assert ev["truncated"] is True
    assert ev["col_indices"] == [0, 1, 99]
    assert ev["headers"] == ["h0", "h1", "h99"]
    assert ev["rows"][0]["values"] == [0.0, 1.0, 99.0]


def test_truncated_evidence_keeps_distant_highlighted_rows(monkeypatch):
    monkeypatch.setattr(audit, "_MAX_EV_ROWS", 3)
    s = _grid(100, 3)

    ev = _block_evidence(
        s,
        0,
        100,
        0,
        3,
        ["a", "b", "c"],
        [1],
        highlight_rows=[1, 100],
    )

    assert ev["truncated"] is True
    assert [row["row_idx"] for row in ev["rows"]] == [1, 2, 100]


def test_evidence_selection_keeps_logical_ranges_lazy(monkeypatch):
    iterations = []

    class GuardedRange:
        def __init__(self, *args):
            self._range = builtins.range(*args)

        def __len__(self):
            return len(self._range)

        def __iter__(self):
            for index, value in enumerate(self._range):
                iterations.append(value)
                if index >= 20:
                    raise AssertionError(
                        "evidence selection iterated the full logical range"
                    )
                yield value

        def __getitem__(self, index):
            return self._range[index]

    class VirtualHeader:
        def __getitem__(self, index):
            return f"h{index}"

    class VirtualSheet:
        nrows = 10**9
        ncols = 10**9

        def __init__(self):
            self.cell_calls = 0

        def cell(self, row, col):
            self.cell_calls += 1
            return float((row % 1000) + (col % 100))

    monkeypatch.setattr(audit, "range", GuardedRange, raising=False)
    monkeypatch.setattr(audit, "_MAX_EV_ROWS", 3)
    monkeypatch.setattr(audit, "_MAX_EV_COLS", 3)
    sheet = VirtualSheet()

    evidence = _block_evidence(
        sheet,
        1,
        10**9 - 1,
        2,
        10**9 - 2,
        VirtualHeader(),
        [10**9 - 3],
        highlight_rows=[10**9 - 2],
    )

    assert evidence["truncated"] is True
    assert evidence["col_indices"] == [2, 3, 10**9 - 3]
    assert [row["row_idx"] for row in evidence["rows"]] == [
        1,
        2,
        10**9 - 2,
    ]
    assert sheet.cell_calls <= 9
    assert len(iterations) <= 20
    assert audit._bounded_evidence_indices(0, 6, [5], 3) == [
        0,
        1,
        5,
    ]


def test_many_highlighted_cells_use_bounded_windows_without_cross_product(
    monkeypatch,
):
    monkeypatch.setattr(audit, "_MAX_EV_ROWS", 2)
    monkeypatch.setattr(audit, "_MAX_EV_COLS", 2)
    s = _grid(12, 12)
    highlighted = [(index, index) for index in range(1, 8)]
    findings = [{
        "kind": "identical_after_rounding",
        "severity": "medium",
        "rule": "bounded highlighted cells",
        "example_cells": highlighted,
    }]

    audit._attach_evidence(
        findings,
        s,
        0,
        s.nrows,
        0,
        s.ncols,
        [f"h{index}" for index in range(s.ncols)],
    )

    evidence = findings[0]["evidence"]
    windows = evidence["windows"]
    assert windows
    assert all(len(window["rows"]) <= 2 for window in windows)
    assert all(
        len(row["values"]) <= 2
        for window in windows
        for row in window["rows"]
    )
    represented = {
        (row["row_idx"], col_index + 1)
        for window in windows
        for row in window["rows"]
        for col_index in window["col_indices"]
    }
    assert set(highlighted) <= represented
    assert sum(
        len(row["values"])
        for window in windows
        for row in window["rows"]
    ) <= len(highlighted) * 4


def test_evidence_limit_is_recorded_once_per_affected_block(monkeypatch):
    monkeypatch.setattr(audit, "_MAX_EV_ROWS", 3)
    monkeypatch.setattr(audit, "_MAX_EV_COLS", 3)
    monkeypatch.setattr(audit, "_MAX_FINDINGS_PER_BLOCK", 0)
    monkeypatch.setattr(audit, "_MAX_TOTAL_FINDINGS", 0)
    findings = [
        {
            "kind": "constant_offset",
            "severity": "high",
            "rule": "first",
            "col_a_idx": 0,
            "col_b_idx": 39,
        },
        {
            "kind": "constant_ratio",
            "severity": "medium",
            "rule": "second",
            "col_a_idx": 1,
            "col_b_idx": 38,
        },
    ]

    def emit_findings(*_args, _finding_sink=None, **_kwargs):
        for finding in findings:
            _finding_sink.offer(
                "relations",
                finding["severity"],
                lambda finding=finding: finding,
            )
        return []

    monkeypatch.setattr(
        audit, "detect_relations", emit_findings
    )
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
    coverage = ScanCoverage(files_discovered=1)
    state = audit.ScanBudgetState(
        coverage=coverage,
        recurring_index=RecurringRowIndex(),
        profile="review",
        evidence=True,
    )
    sheet = _grid(8, 40)

    blocks = audit._analyze_numeric_blocks(
        sheet,
        file_name="wide.csv",
        sheet_name="wide",
        blocks=[(0, 8, 0, 40)],
        state=state,
    )

    assert len(blocks) == 1
    limitations = [
        item for item in coverage.limitations
        if item["reason"] == "evidence_limit"
    ]
    assert limitations == [{
        "scope": "block",
        "reason": "evidence_limit",
        "file": "wide.csv",
        "sheet": "wide",
        "rows": "1-8",
        "cols": "1-40",
        "max_rows": 3,
        "max_cols": 3,
    }]
    assert all(
        finding["evidence"]["truncated"] is True
        for finding in findings
    )


def test_deferred_spreadsheet_reload_attaches_evidence_and_cleans_keys(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.xlsx"
    block, finding = _deferred_block(path)
    block["_evidence_sentinel"] = object()
    monkeypatch.setattr(
        audit,
        "load_table_result",
        lambda _path: TableLoadResult({"Target": _evidence_sheet()}),
    )
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert finding["evidence"]["rows"]
    assert coverage.limitations == []
    assert not any(key.startswith("_evidence_") for key in block)


def test_deferred_reload_attaches_evidence_to_every_retained_finding(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.xlsx"
    block, first = _deferred_block(path)
    second = {
        "kind": "constant_ratio",
        "severity": "medium",
        "rule": "second retained finding",
        "col_a_idx": 0,
        "col_b_idx": 1,
    }
    third = {
        "kind": "identical_column",
        "severity": "medium",
        "rule": "retained finding in another group",
        "col_a_idx": 0,
        "col_b_idx": 1,
    }
    block["relations"].append(second)
    block["equal_pairs"].append(third)
    monkeypatch.setattr(
        audit,
        "load_table_result",
        lambda _path: TableLoadResult({"Target": _evidence_sheet()}),
    )
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert coverage.limitations == []
    assert all(
        finding["evidence"]["rows"]
        for finding in (first, second, third)
    )


def test_deferred_reload_processes_every_retained_block_and_target(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.xlsx"
    path.write_bytes(b"placeholder")
    alpha, alpha_finding = _deferred_block(
        path, sheet_name="Alpha"
    )
    beta, beta_finding = _deferred_block(
        path, sheet_name="Beta"
    )
    missing, missing_finding = _deferred_block(
        path, sheet_name="Missing"
    )
    monkeypatch.setattr(
        audit,
        "load_table_result",
        lambda _path: TableLoadResult({
            "Alpha": _evidence_sheet(),
            "Beta": _evidence_sheet(),
        }),
    )
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence(
        [alpha, beta, missing], coverage
    )

    assert alpha_finding["evidence"]["rows"]
    assert beta_finding["evidence"]["rows"]
    assert "evidence" not in missing_finding
    assert coverage.limitations == [{
        "scope": "sheet",
        "reason": "evidence_reload_missing_sheet",
        "file": "source.xlsx",
        "sheet": "Missing",
    }]
    assert all(
        not any(key.startswith("_evidence_") for key in block)
        for block in (alpha, beta, missing)
    )


@pytest.mark.parametrize(
    ("suffix", "reason"),
    [
        (".xlsx", "evidence_reload_missing_sheet"),
        (".pdf", "evidence_reload_missing_table"),
    ],
)
def test_deferred_reload_records_missing_target(
    tmp_path, monkeypatch, suffix, reason
):
    path = tmp_path / f"source{suffix}"
    block, finding = _deferred_block(path)
    if suffix == ".pdf":
        monkeypatch.setattr(
            audit, "_iter_extracted_sheets", lambda _path: iter(())
        )
    else:
        monkeypatch.setattr(
            audit,
            "load_table_result",
            lambda _path: TableLoadResult({}),
        )
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert "evidence" not in finding
    assert coverage.limitations == [{
        "scope": "sheet",
        "reason": reason,
        "file": path.name,
        "sheet": "Target",
    }]
    assert not any(key.startswith("_evidence_") for key in block)


def test_deferred_spreadsheet_reload_surfaces_loader_limitation(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.xlsx"
    block, finding = _deferred_block(path)
    limitation = InputLimitation(
        scope="sheet",
        reason="cell_limit",
        sheet="Target",
        details={"cells": 12, "max_cells": 10},
    )
    monkeypatch.setattr(
        audit,
        "load_table_result",
        lambda _path: TableLoadResult(
            {"Target": None}, [limitation]
        ),
    )
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert "evidence" not in finding
    assert coverage.limitations == [{
        "scope": "sheet",
        "reason": "evidence_reload_cell_limit",
        "file": "source.xlsx",
        "sheet": "Target",
        "cells": 12,
        "max_cells": 10,
    }]


def test_deferred_extractor_reload_surfaces_table_limitation(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.pdf"
    block, finding = _deferred_block(path)
    limitation = InputLimitation(
        scope="sheet",
        reason="sparse_cell_limit",
        sheet="Target",
        details={"sparse_cells": 12, "max_sparse_cells": 10},
    )
    monkeypatch.setattr(
        audit,
        "_iter_extracted_sheets",
        lambda _path: iter([("Target", None, [limitation])]),
    )
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert "evidence" not in finding
    assert coverage.limitations == [{
        "scope": "sheet",
        "reason": "evidence_reload_sparse_cell_limit",
        "file": "source.pdf",
        "sheet": "Target",
        "max_sparse_cells": 10,
        "sparse_cells": 12,
    }]


def test_deferred_reload_exception_isolated_per_source_and_bounded(
    tmp_path, monkeypatch
):
    failed_path = tmp_path / "failed.xlsx"
    good_path = tmp_path / "good.xlsx"
    failed_path.write_bytes(b"placeholder")
    good_path.write_bytes(b"placeholder")
    failed_block, failed_finding = _deferred_block(failed_path)
    good_block, good_finding = _deferred_block(good_path)
    secret = "/private/source.xlsx?credential=not-for-output"

    def load(path):
        if path == str(failed_path):
            raise RuntimeError(secret)
        return TableLoadResult({"Target": _evidence_sheet()})

    monkeypatch.setattr(audit, "load_table_result", load)
    coverage = ScanCoverage(files_discovered=2)

    audit._attach_deferred_evidence(
        [failed_block, good_block], coverage
    )

    assert "evidence" not in failed_finding
    assert good_finding["evidence"]["rows"]
    assert coverage.limitations == [{
        "scope": "file",
        "reason": "evidence_reload_error",
        "file": "failed.xlsx",
        "error_type": "RuntimeError",
    }]
    assert secret not in json.dumps(coverage.to_dict())
    assert not any(
        key.startswith("_evidence_")
        for block in (failed_block, good_block)
        for key in block
    )


def test_deferred_loader_value_error_text_does_not_escape(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.xlsx"
    path.write_bytes(b"placeholder")
    block, finding = _deferred_block(path)

    def fail(_path):
        raise ValueError("details contains reserved key: ordinary reload")

    monkeypatch.setattr(audit, "load_table_result", fail)
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert "evidence" not in finding
    assert coverage.limitations == [{
        "scope": "file",
        "reason": "evidence_reload_error",
        "file": "source.xlsx",
        "error_type": "ValueError",
    }]


def test_deferred_extractor_exception_does_not_require_stringification(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.pdf"
    path.write_bytes(b"placeholder")
    block, finding = _deferred_block(path)

    class UnprintableValueError(ValueError):
        def __str__(self):
            raise RuntimeError("exception text must not be inspected")

    def fail(_path):
        raise UnprintableValueError()
        yield

    monkeypatch.setattr(audit, "_iter_extracted_sheets", fail)
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert "evidence" not in finding
    assert coverage.limitations == [{
        "scope": "file",
        "reason": "evidence_reload_error",
        "file": "source.pdf",
        "error_type": "UnprintableValueError",
    }]


def test_deferred_reload_records_missing_file_without_path_detail(
    tmp_path, monkeypatch
):
    path = tmp_path / "missing.xlsx"
    block, finding = _deferred_block(path)

    def missing(_path):
        raise FileNotFoundError(str(path))

    monkeypatch.setattr(audit, "load_table_result", missing)
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert "evidence" not in finding
    assert coverage.limitations == [{
        "scope": "file",
        "reason": "evidence_reload_missing_file",
        "file": "missing.xlsx",
    }]
    assert str(tmp_path) not in json.dumps(coverage.to_dict())


def test_deferred_inaccessible_source_is_reload_error(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.xlsx"
    block, finding = _deferred_block(path)
    real_stat = audit.os.stat

    def inaccessible(_path):
        raise PermissionError("source load denied")

    def deny_stat(probed_path, *args, **kwargs):
        if probed_path == str(path):
            raise PermissionError("source metadata denied")
        return real_stat(probed_path, *args, **kwargs)

    monkeypatch.setattr(audit, "load_table_result", inaccessible)
    monkeypatch.setattr(audit.os, "stat", deny_stat)
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert "evidence" not in finding
    assert coverage.limitations == [{
        "scope": "file",
        "reason": "evidence_reload_error",
        "file": "source.xlsx",
        "error_type": "PermissionError",
    }]


def test_deferred_not_a_directory_source_is_missing_file(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.xlsx" / "table.xlsx"
    block, finding = _deferred_block(path)
    real_stat = audit.os.stat

    def fail_load(_path):
        raise RuntimeError("source load failed")

    def fail_stat(probed_path, *args, **kwargs):
        if probed_path == str(path):
            raise NotADirectoryError("source parent is not a directory")
        return real_stat(probed_path, *args, **kwargs)

    monkeypatch.setattr(audit, "load_table_result", fail_load)
    monkeypatch.setattr(audit.os, "stat", fail_stat)
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert "evidence" not in finding
    assert coverage.limitations == [{
        "scope": "file",
        "reason": "evidence_reload_missing_file",
        "file": "table.xlsx",
    }]


def test_deferred_real_missing_pdf_uses_source_reason(tmp_path):
    pytest.importorskip("pdfplumber")
    path = tmp_path / "missing.pdf"
    block, finding = _deferred_block(path)
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert "evidence" not in finding
    assert coverage.limitations == [{
        "scope": "file",
        "reason": "evidence_reload_missing_file",
        "file": "missing.pdf",
    }]


def test_deferred_real_missing_docx_uses_source_reason(tmp_path):
    pytest.importorskip("docx")
    path = tmp_path / "missing.docx"
    block, finding = _deferred_block(path)
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert "evidence" not in finding
    assert coverage.limitations == [{
        "scope": "file",
        "reason": "evidence_reload_missing_file",
        "file": "missing.docx",
    }]


def test_deferred_existing_extractor_internal_missing_file_is_reload_error(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.pdf"
    path.write_bytes(b"placeholder")
    block, finding = _deferred_block(path)

    def fail(_path):
        raise FileNotFoundError("extractor dependency unavailable")
        yield

    monkeypatch.setattr(audit, "_iter_extracted_sheets", fail)
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert "evidence" not in finding
    assert coverage.limitations == [{
        "scope": "file",
        "reason": "evidence_reload_error",
        "file": "source.pdf",
        "error_type": "FileNotFoundError",
    }]


def test_deferred_attachment_missing_file_is_reload_error(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.xlsx"
    block, finding = _deferred_block(path)
    monkeypatch.setattr(
        audit,
        "load_table_result",
        lambda _path: TableLoadResult({"Target": _evidence_sheet()}),
    )

    def fail_attachment(*_args, **_kwargs):
        raise FileNotFoundError("attachment dependency unavailable")

    monkeypatch.setattr(audit, "_attach_evidence", fail_attachment)
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert "evidence" not in finding
    assert coverage.limitations == [{
        "scope": "file",
        "reason": "evidence_reload_error",
        "file": "source.xlsx",
        "error_type": "FileNotFoundError",
    }]


def test_deferred_extractor_close_missing_file_is_reload_error(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.pdf"
    block, finding = _deferred_block(path)

    class CloseMissingIterator:
        def __init__(self):
            self._entries = iter([
                ("Target", _evidence_sheet(), []),
            ])

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._entries)

        def close(self):
            raise FileNotFoundError("cleanup dependency unavailable")

    monkeypatch.setattr(
        audit,
        "_iter_extracted_sheets",
        lambda _path: CloseMissingIterator(),
    )
    coverage = ScanCoverage(files_discovered=1)

    audit._attach_deferred_evidence([block], coverage)

    assert finding["evidence"]["rows"]
    assert coverage.limitations == [{
        "scope": "file",
        "reason": "evidence_reload_error",
        "file": "source.pdf",
        "error_type": "FileNotFoundError",
    }]


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit])
def test_deferred_reload_propagates_base_exception_controls(
    tmp_path, monkeypatch, control
):
    path = tmp_path / "source.xlsx"
    block, _finding = _deferred_block(path)
    block["_evidence_sentinel"] = object()

    def interrupt(_path):
        raise control()

    monkeypatch.setattr(audit, "load_table_result", interrupt)
    coverage = ScanCoverage(files_discovered=1)

    with pytest.raises(control):
        audit._attach_deferred_evidence([block], coverage)

    assert coverage.limitations == []
    assert not any(key.startswith("_evidence_") for key in block)


def test_write_json_false_skips_file(tmp_path):
    from tests.build_fixture import build as build_tiny

    ind = tmp_path / "in"; out = tmp_path / "out"; ind.mkdir()
    build_tiny(str(ind))
    res = scan_dir(str(ind), str(out), write_md=False, write_html=False, write_json=False)
    assert res is not None and "relations_blocks" in res
    assert not (out / "scan.json").exists()
    # default writes it
    out2 = tmp_path / "out2"
    scan_dir(str(ind), str(out2), write_md=False, write_html=False)
    assert (out2 / "scan.json").exists()
