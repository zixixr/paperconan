import gc
import types
import weakref
from collections.abc import Mapping, Sequence, Set
from dataclasses import fields, is_dataclass

import numpy as np
import paperconan._audit as audit
import pytest
from paperconan._coverage import ScanCoverage
from paperconan._input import TableLoadResult
from paperconan._sheet import Sheet
from paperconan._summaries import RecurringRowIndex


class _WeakNumericList(list):
    pass


class _WeakSheet(Sheet):
    pass


def _walk(value, seen=None):
    if seen is None:
        seen = set()
    if id(value) in seen:
        return
    seen.add(id(value))
    yield value
    if isinstance(value, types.ModuleType) or isinstance(value, type):
        return
    if is_dataclass(value):
        for field in fields(value):
            yield from _walk(getattr(value, field.name), seen)
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _walk(key, seen)
            yield from _walk(item, seen)
    if isinstance(value, (Sequence, Set)) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for item in value:
            yield from _walk(item, seen)
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        yield from _walk(attributes, seen)
    if callable(value):
        for cell in getattr(value, "__closure__", None) or ():
            try:
                retained = cell.cell_contents
            except ValueError:
                continue
            yield from _walk(retained, seen)


def _assert_compact(*roots):
    retained = list(_walk(roots))
    disallowed = [
        value
        for value in retained
        if isinstance(value, (Sheet, np.ndarray, _WeakNumericList))
    ]
    assert not disallowed, [
        type(value).__name__
        for value in disallowed
    ]
    return retained


def test_previous_file_sheet_is_released_before_next_load(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.csv").write_text("x\n1\n2\n3\n", encoding="utf-8")
    (data / "b.csv").write_text("x\n4\n5\n6\n", encoding="utf-8")
    records = []
    records_by_sheet_id = {}
    original_numeric_values = Sheet.numeric_values

    def tracked_numeric_values(sheet):
        values = _WeakNumericList(original_numeric_values(sheet))
        records_by_sheet_id[id(sheet)]["values"] = weakref.ref(values)
        return values

    def stub_load(path):
        if records:
            gc.collect()
            prior = records[-1]
            assert prior["sheet"]() is None
            assert prior["numeric"]() is None
            assert prior["values"] is not None
            assert prior["values"]() is None
        sheet = _WeakSheet.from_rows([["x"], [1.1], [2.2], [3.3]])
        record = {
            "sheet": weakref.ref(sheet),
            "numeric": weakref.ref(sheet.numeric),
            "values": None,
        }
        records.append(record)
        records_by_sheet_id[id(sheet)] = record
        return TableLoadResult({path: sheet})

    monkeypatch.setattr(Sheet, "numeric_values", tracked_numeric_values)
    monkeypatch.setattr(audit, "load_table_result", stub_load)
    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )
    assert scan["coverage"]["files_succeeded"] == 2


def test_compactness_walker_detects_attribute_closure_and_list_retention():
    class Holder:
        pass

    holder = Holder()
    sheet = Sheet.from_rows([["x"], [1.25]])
    numeric = sheet.numeric
    values = _WeakNumericList([1.25])
    holder.sheet = sheet
    holder.values = values

    def retained_numeric():
        return numeric

    holder.callback = retained_numeric

    walked = list(_walk(holder))
    assert any(value is sheet for value in walked)
    assert any(value is numeric for value in walked)
    assert any(value is values for value in walked)
    with pytest.raises(AssertionError):
        _assert_compact(holder)


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
        "left,right\n"
        + "\n".join(f"{value},{value}" for value in values)
        + "\n",
        encoding="utf-8",
    )
    state = audit.ScanBudgetState(
        coverage=ScanCoverage(files_discovered=1),
        recurring_index=RecurringRowIndex(),
        profile="review",
        evidence=True,
    )

    result = audit._process_file(
        str(path),
        input_dir=str(data),
        state=state,
    )

    assert result.report_blocks
    findings = [
        finding
        for block in result.report_blocks
        for group in audit.BLOCK_FINDING_GROUPS
        for finding in block[group]
    ]
    assert findings
    assert all("evidence" in finding for finding in findings)
    walked = _assert_compact(result, state)
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
    complete_values = tuple(
        item
        for value in values
        for item in (value, value)
    )
    assert complete_values not in numeric_sequences


def test_loaded_sheet_helper_returns_compact_state_without_closures():
    sheet = Sheet.from_rows([
        ["left", "right"],
        [1.125, 1.125],
        [2.375, 2.375],
        [3.625, 3.625],
    ])
    state = audit.ScanBudgetState(
        coverage=ScanCoverage(files_discovered=1),
        recurring_index=RecurringRowIndex(),
        profile="review",
        evidence=True,
    )

    result = audit._process_loaded_sheet(
        sheet,
        file_name="source.csv",
        sheet_name="source",
        sheet_start=audit.time.perf_counter(),
        state=state,
    )

    _assert_compact(result, state)
    assert audit._analyze_numeric_blocks.__closure__ is None
    assert audit._process_loaded_sheet.__closure__ is None
    assert audit._process_file.__closure__ is None


def test_process_file_reports_actual_custom_recurring_budget(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "Figure 1.csv"
    path.write_text(
        "a,b,c,d,e\n"
        "11.25,7.5,19.75,3.125,14.5\n"
        "21.25,17.5,29.75,13.125,24.5\n"
        "31.25,27.5,39.75,23.125,34.5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "_RECURRING_ROW_VECTOR_BUDGET", 999)
    state = audit.ScanBudgetState(
        coverage=ScanCoverage(files_discovered=1),
        recurring_index=RecurringRowIndex(budget=1),
        profile="review",
        evidence=False,
    )

    audit._process_file(
        str(path),
        input_dir=str(data),
        state=state,
    )

    limitation = next(
        item
        for item in state.coverage.limitations
        if item["reason"] == "recurring_row_vector_budget"
    )
    assert limitation["limit"] == 1


def test_recurring_index_initial_budget_is_read_only():
    index = RecurringRowIndex(budget=7)

    assert index.initial_budget == 7
    with pytest.raises(AttributeError):
        index.initial_budget = 9


def test_sheet_and_file_elapsed_cover_loading_conversion_and_analysis(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "timing.csv"
    path.write_text(
        "a,b\n"
        "1.125,7.375\n"
        "2.625,4.875\n"
        "5.375,3.125\n",
        encoding="utf-8",
    )
    now = [0.0]

    def perf_counter():
        return now[0]

    original_from_rows = Sheet.from_rows

    def load_table(_path):
        now[0] += 2.0
        return TableLoadResult({
            "timing": [
                ["a", "b"],
                [1.125, 7.375],
                [2.625, 4.875],
                [5.375, 3.125],
            ],
        })

    def from_rows(rows):
        now[0] += 1.0
        return original_from_rows(rows)

    def block_detector(*_args, **_kwargs):
        now[0] += 3.0
        return []

    def digit_report(*_args, **_kwargs):
        now[0] += 2.0
        return None

    monkeypatch.setattr(audit.time, "perf_counter", perf_counter)
    monkeypatch.setattr(audit, "load_table_result", load_table)
    monkeypatch.setattr(Sheet, "from_rows", from_rows)
    monkeypatch.setattr(audit, "detect_relations", block_detector)
    monkeypatch.setattr(audit, "detect_last_digit", digit_report)
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

    assert result.stats["sheets"][0]["elapsed_ms"] == 6000.0
    assert result.stats["files"][0]["elapsed_ms"] == 8000.0


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
