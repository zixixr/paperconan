import gc
import weakref
from dataclasses import fields, is_dataclass

import numpy as np
import paperconan._audit as audit
from paperconan._coverage import ScanCoverage
from paperconan._input import TableLoadResult
from paperconan._sheet import Sheet
from paperconan._summaries import RecurringRowIndex


def test_previous_file_sheet_is_released_before_next_load(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.csv").write_text("x\n1\n2\n3\n", encoding="utf-8")
    (data / "b.csv").write_text("x\n4\n5\n6\n", encoding="utf-8")
    refs = []

    def stub_load(path):
        if refs:
            gc.collect()
            assert refs[-1]() is None
        sheet = Sheet.from_rows([["x"], [1.1], [2.2], [3.3]])
        refs.append(weakref.ref(sheet.numeric))
        return TableLoadResult({path: sheet})

    monkeypatch.setattr(audit, "load_table_result", stub_load)
    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )
    assert scan["coverage"]["files_succeeded"] == 2


def _walk(value, seen=None):
    if seen is None:
        seen = set()
    if id(value) in seen:
        return
    seen.add(id(value))
    yield value
    if is_dataclass(value):
        for field in fields(value):
            yield from _walk(getattr(value, field.name), seen)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(key, seen)
            yield from _walk(item, seen)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _walk(item, seen)


def test_file_scan_result_contains_only_compact_state(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "values.csv"
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
    path.write_text(
        "value\n" + "\n".join(str(value) for value in values) + "\n",
        encoding="utf-8",
    )
    state = audit.ScanBudgetState(
        coverage=ScanCoverage(files_discovered=1),
        recurring_index=RecurringRowIndex(),
        profile="review",
        evidence=False,
    )

    result = audit._process_file(
        str(path),
        input_dir=str(data),
        state=state,
    )

    walked = list(_walk(result))
    assert not any(isinstance(value, (Sheet, np.ndarray)) for value in walked)
    numeric_sequences = {
        tuple(sequence)
        for sequence in walked
        if isinstance(sequence, (list, tuple))
        and sequence
        and all(
            isinstance(item, (int, float)) and not isinstance(item, bool)
            for item in sequence
        )
    }
    assert tuple(values) not in numeric_sequences


def test_scan_maps_collision_grid_cell_limit_with_counts(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    (data / "grid.csv").write_text(
        "a,b\n"
        "1.1234,2.2345\n"
        "3.3456,4.4567\n"
        "5.5678,6.6789\n",
        encoding="utf-8",
    )
    original = audit.build_cross_sheet_summary

    def bounded_summary(*args, **kwargs):
        kwargs["collision_max_cells"] = 2
        return original(*args, **kwargs)

    monkeypatch.setattr(audit, "build_cross_sheet_summary", bounded_summary)

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    limitations = [
        item
        for item in scan["coverage"]["limitations"]
        if item["reason"] == "collision_grid_cell_limit"
    ]
    assert limitations == [{
        "scope": "sheet",
        "reason": "collision_grid_cell_limit",
        "file": "grid.csv",
        "sheet": "grid",
        "cells_used": 2,
        "max_cells": 2,
    }]


def test_scan_reports_exact_recurring_window_budget_once(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    (data / "Figure 1.csv").write_text(
        "a,b,c,d,e\n"
        "11.25,7.5,19.75,3.125,14.5\n"
        "21.25,17.5,29.75,13.125,24.5\n"
        "31.25,27.5,39.75,23.125,34.5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        audit, "_RECURRING_ROW_VECTOR_BUDGET", 1, raising=False
    )

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    limitations = [
        item
        for item in scan["coverage"]["limitations"]
        if item["reason"] == "recurring_row_vector_budget"
    ]
    assert limitations == [{
        "scope": "sheet",
        "reason": "recurring_row_vector_budget",
        "file": "Figure 1.csv",
        "sheet": "Figure 1",
        "windows_skipped": 8,
        "limit": 1,
    }]
    assert scan["coverage"]["truncated"] is True


def test_scan_counts_recurring_finding_omissions_at_top_level(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    vectors = [
        [220, 188, 122, 166, 128, 166],
        [311, 277, 203, 255, 199, 241],
    ]
    for figure in range(1, 4):
        rows = [
            "a,b,c,d,e,f",
            *((",".join(str(value) for value in vector)) for vector in vectors),
            ",".join(
                str(figure * 100 + offset + 0.125)
                for offset in range(6)
            ),
        ]
        (data / f"Figure {figure}.csv").write_text(
            "\n".join(rows) + "\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(
        audit, "_RECURRING_ROW_VECTOR_MAX_FINDINGS", 1, raising=False
    )

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    limitations = [
        item
        for item in scan["coverage"]["limitations"]
        if item["reason"] == "recurring_row_vector_finding_limit"
    ]
    assert limitations == [{
        "scope": "scan",
        "reason": "recurring_row_vector_finding_limit",
        "limit": 1,
        "omitted_findings": 1,
    }]
    assert scan["findings_omitted"] == 1
    assert sum(
        finding["kind"] == "recurring_row_vector"
        for finding in scan["cross_sheet_findings"]
    ) == 1


def test_report_block_limit_remains_directory_wide(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    payload = "a,b\n1,1\n2,2\n3,3\n4,4\n5,5\n6,6\n"
    (data / "a.csv").write_text(payload, encoding="utf-8")
    (data / "b.csv").write_text(payload, encoding="utf-8")
    monkeypatch.setattr(audit, "_MAX_REPORT_BLOCKS", 1)
    monkeypatch.setattr(audit, "_MAX_TOTAL_FINDINGS", 0)

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    assert len(scan["relations_blocks"]) == 1
    assert scan["coverage"]["blocks_analyzed"] == 1
    assert scan["coverage"]["blocks_skipped"] == 1
    assert [
        item
        for item in scan["coverage"]["limitations"]
        if item["reason"] == "report_block_limit"
    ] == [{
        "scope": "sheet",
        "reason": "report_block_limit",
        "count": 1,
        "file": "b.csv",
        "sheet": "b",
    }]
