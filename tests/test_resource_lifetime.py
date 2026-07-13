import gc
import os
import types
import weakref
from collections.abc import Mapping, Sequence, Set
from dataclasses import fields, is_dataclass

import numpy as np
import paperconan._audit as audit
import paperconan._extract as extract
import pytest
from paperconan._coverage import ScanCoverage
from paperconan._input import TableLoadResult
from paperconan._sheet import Sheet
from paperconan._sheet import SheetBuilder
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


def test_scan_streams_numeric_values_and_releases_previous_sheet(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.csv").write_text("x\n1\n2\n3\n", encoding="utf-8")
    (data / "b.csv").write_text("x\n4\n5\n6\n", encoding="utf-8")
    records = []

    def stub_load(path):
        if records:
            gc.collect()
            prior = records[-1]
            assert prior["sheet"]() is None
            assert prior["numeric"]() is None
        sheet = _WeakSheet.from_rows([["x"], [1.1], [2.2], [3.3]])
        record = {
            "sheet": weakref.ref(sheet),
            "numeric": weakref.ref(sheet.numeric),
        }
        records.append(record)
        return TableLoadResult({path: sheet})

    def fail_numeric_values(_sheet):
        raise AssertionError("scan path must stream numeric values")

    monkeypatch.setattr(Sheet, "numeric_values", fail_numeric_values)
    monkeypatch.setattr(audit, "load_table_result", stub_load)
    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )
    assert scan["coverage"]["files_succeeded"] == 2


def test_pdf_scan_releases_completed_table_before_extracting_next(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "tables.pdf"
    path.write_bytes(b"placeholder")
    sheet_refs = []
    original_finish = SheetBuilder.finish

    def tracked_finish(builder):
        sheet = original_finish(builder)
        sheet_refs.append(weakref.ref(sheet.numeric))
        return sheet

    class StubTable:
        def __init__(self, index):
            self.index = index

        def extract(self):
            if self.index == 2:
                gc.collect()
                assert sheet_refs[-1]() is None
            return [
                ["a", "b"],
                *[
                    [
                        str(self.index + offset),
                        str(self.index + offset),
                    ]
                    for offset in range(6)
                ],
            ]

    class StubPage:
        def find_tables(self):
            return [StubTable(1), StubTable(2)]

    class StubPdf:
        pages = [StubPage()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    pdfplumber = __import__("pdfplumber")
    monkeypatch.setattr(SheetBuilder, "finish", tracked_finish)
    monkeypatch.setattr(
        pdfplumber, "open", lambda _path: StubPdf()
    )

    scan = audit.scan_dir(
        str(data),
        str(tmp_path / "out"),
        write_html=False,
        evidence=True,
    )

    assert scan["coverage"]["files_succeeded"] == 1
    assert len(sheet_refs) == 4


def test_docx_scan_releases_completed_table_before_extracting_next(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "tables.docx"
    path.write_bytes(b"placeholder")
    sheet_refs = []
    original_finish = SheetBuilder.finish

    def tracked_finish(builder):
        sheet = original_finish(builder)
        sheet_refs.append(weakref.ref(sheet.numeric))
        return sheet

    class Identity:
        pass

    class StubCell:
        def __init__(self, text):
            self.text = text
            self._tc = Identity()

    class StubRow:
        def __init__(self, index):
            self.cells = [
                StubCell(str(index)),
                StubCell(str(index)),
            ]

    class StubTable:
        def __init__(self, index):
            self.index = index

        @property
        def rows(self):
            if self.index == 2:
                gc.collect()
                assert sheet_refs[-1]() is None
            return [
                StubRow(self.index + offset)
                for offset in range(7)
            ]

    class StubDocument:
        tables = [StubTable(1), StubTable(2)]

    docx = __import__("docx")
    monkeypatch.setattr(SheetBuilder, "finish", tracked_finish)
    monkeypatch.setattr(
        docx, "Document", lambda _path: StubDocument()
    )

    scan = audit.scan_dir(
        str(data),
        str(tmp_path / "out"),
        write_html=False,
        evidence=True,
    )

    assert scan["coverage"]["files_succeeded"] == 1
    assert len(sheet_refs) == 4


def test_docx_merged_identity_state_releases_rows_older_than_previous(
    monkeypatch,
):
    identity_refs = []

    class Identity:
        pass

    class StubCell:
        def __init__(self, identity, text):
            self._tc = identity
            self.text = text

    class StubRow:
        def __init__(self, identity, text):
            self.cells = [StubCell(identity, text)]

    class StubTable:
        @property
        def rows(self):
            for index in range(8):
                if index >= 2:
                    gc.collect()
                    assert identity_refs[index - 2]() is None
                identity = Identity()
                identity_refs.append(weakref.ref(identity))
                row = StubRow(identity, str(index))
                yield row
                del row
                del identity

    class StubDocument:
        tables = [StubTable()]

    docx = __import__("docx")
    monkeypatch.setattr(
        docx, "Document", lambda _path: StubDocument()
    )

    sheets = extract.load_docx_tables("tables.docx")

    sheet = sheets["tables!t1"]
    assert sheet.nrows == 8
    assert [sheet.cell(row, 0) for row in range(8)] == list(range(8))


def test_large_single_column_sheet_keeps_retained_state_bounded(
    monkeypatch,
):
    row_count = 100_000
    row_numbers = np.arange(row_count, dtype=float)
    numeric = (
        row_numbers + (row_numbers % 7) * 0.1234
    ).reshape(row_count, 1)
    sheet = Sheet(
        row_count,
        1,
        numeric,
        {},
        set(),
    )

    def fail_numeric_values(_sheet):
        raise AssertionError("scan path must stream numeric values")

    class NoopRecurringIndex:
        initial_budget = 0

        def add_sheet(self, *args, **kwargs):
            return {
                "budget_exhausted": False,
                "windows_skipped": 0,
            }

    monkeypatch.setattr(Sheet, "numeric_values", fail_numeric_values)
    monkeypatch.setattr(
        audit, "_analyze_numeric_blocks", lambda *args, **kwargs: []
    )
    monkeypatch.setattr(
        audit,
        "_COLUMN_FINGERPRINT_DISTINCT_LIMIT",
        16,
        raising=False,
    )
    state = audit.ScanBudgetState(
        coverage=ScanCoverage(files_discovered=1),
        recurring_index=NoopRecurringIndex(),
        profile="review",
        evidence=False,
    )

    result = audit._process_loaded_sheet(
        sheet,
        file_name="large.csv",
        sheet_name="large",
        sheet_start=None,
        state=state,
    )

    assert result.stats["numeric_cells"] == row_count
    assert [
        item
        for item in state.coverage.limitations
        if item["reason"] == "column_fingerprint_distinct_limit"
    ] == [{
        "scope": "sheet",
        "reason": "column_fingerprint_distinct_limit",
        "file": "large.csv",
        "sheet": "large",
        "detector": "cross_sheet_column_duplicate",
        "affected_columns": 1,
        "examples": [{
            "column": 1,
            "rows": f"1-{row_count}",
            "numeric_cells": row_count,
        }],
        "limit": 16,
    }]
    _assert_compact(result, state)


def test_dense_detector_aggregation_avoids_row_sized_python_lists(
    monkeypatch,
):
    original_counter = audit.Counter

    def bounded_counter(values=None, *args, **kwargs):
        if isinstance(values, list) and len(values) > 8:
            raise AssertionError(
                "dense detectors must not feed row-sized lists to Counter"
            )
        if values is None:
            return original_counter(*args, **kwargs)
        return original_counter(values, *args, **kwargs)

    monkeypatch.setattr(audit, "Counter", bounded_counter)
    values = [
        1.1234 + (row % 17) * 0.137 + row * 0.0001
        for row in range(120)
    ]
    sheet = Sheet.from_rows([["value"]] + [[value] for value in values])

    audit.detect_within_column_patterns(
        sheet, 1, sheet.nrows, 0, 1, ["value"]
    )
    audit.detect_dispersed_repeats(
        sheet, 1, sheet.nrows, 0, 1, ["value"]
    )
    audit.detect_identical_after_rounding(
        sheet, 1, sheet.nrows, 0, 1, ["value"]
    )

    assert not hasattr(audit, "_numeric_pairs")


def test_dispersed_and_rounding_detectors_use_compact_dense_state():
    values = [
        1.2345 + (row % 13) * 0.1111 + row * 0.00001
        for row in range(120)
    ]
    source = Sheet.from_rows(
        [["left", "right"]]
        + [
            [value, value + (row % 5) * 0.00002]
            for row, value in enumerate(values)
        ]
    )

    class DenseOnlySheet(Sheet):
        def cell(self, _row, _col):
            raise AssertionError(
                "dense detector must not materialize per-cell Python tuples"
            )

    sheet = DenseOnlySheet(
        source.nrows,
        source.ncols,
        source.numeric,
        source._text,
        source._ints,
        source._wide_ints,
    )

    audit.detect_dispersed_repeats(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"]
    )
    audit.detect_identical_after_rounding(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"]
    )


@pytest.mark.parametrize(
    ("family", "detector_name", "sheet", "bounds", "expected_states"),
    [
        (
            "dispersed_repeats",
            "detect_dispersed_repeats",
            Sheet.from_rows(
                [["value"]]
                + [
                    [1000.1234567 + (row % 60) * 0.7312345]
                    for row in range(120)
                ]
            ),
            (1, 121, 0, 1, ["value"]),
            {
                "numeric_mask",
                "rows",
                "values",
                "rounded",
                "inverse",
                "counts",
                "sorted_positions",
            },
        ),
        (
            "identical_after_rounding",
            "detect_identical_after_rounding",
            Sheet.from_rows(
                [["left", "right"]]
                + [
                    [
                        1.001 + (row % 20) * 0.0021,
                        2.001 + (row % 20) * 0.0021,
                    ]
                    for row in range(60)
                ]
            ),
            (1, 61, 0, 2, ["left", "right"]),
            {
                "candidate_mask",
                "bucket_mask",
                "flat_indices",
                "values",
                "rounded",
                "inverse",
                "counts",
                "sorted_positions",
            },
        ),
    ],
)
def test_dense_detector_declared_state_bounds_cover_actual_live_arrays(
    family, detector_name, sheet, bounds, expected_states
):
    detector = getattr(audit, detector_name)
    baseline = detector(sheet, *bounds)
    tracker = audit._DenseStateTracker()

    instrumented = detector(sheet, *bounds, _state_tracker=tracker)

    requirement = next(
        item
        for item in audit._dense_detector_requirements(
            bounds[1] - bounds[0], bounds[3] - bounds[2]
        )
        if item["family"] == family
    )
    assert instrumented == baseline
    assert tracker.live_units == 0
    assert tracker.peak_units <= requirement["state_required"]
    assert expected_states <= tracker.seen_names


def test_very_wide_column_fingerprints_touch_only_fixed_budget(
    monkeypatch,
):
    row_count = 40
    column_count = 1_000_000
    column_limit = 6

    class VirtualWideSource:
        nrows = row_count
        ncols = column_count

        def __init__(self):
            self.exact_numeric_calls = 0

        def cell(self, row, col):
            return None

        def exact_numeric(self, row, col):
            self.exact_numeric_calls += 1
            return float(row * row + 3 * row) + col / 1000

    monkeypatch.setattr(
        audit,
        "_COLUMN_FINGERPRINT_MAX_COLUMNS",
        column_limit,
        raising=False,
    )
    monkeypatch.setattr(
        audit,
        "_COLUMN_FINGERPRINT_DISTINCT_LIMIT",
        100,
    )
    source = VirtualWideSource()

    columns, limitations = audit._column_fingerprints(
        "wide.csv",
        "wide",
        source,
        [(0, row_count, 0, column_count)],
        min_column_length=12,
    )

    assert len(columns) == column_limit
    assert source.exact_numeric_calls == row_count * column_limit
    assert limitations == [
        audit.InputLimitation(
            scope="sheet",
            reason="column_fingerprint_column_limit",
            sheet="wide",
            details={
                "detector": "cross_sheet_column_duplicate",
                "columns_total": column_count,
                "columns_used": column_limit,
                "columns_skipped": column_count - column_limit,
                "limit": column_limit,
            },
        )
    ]
    _assert_compact(columns, limitations)


def test_deferred_reload_releases_each_source_before_next_load(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    for name in ("a.csv", "b.csv"):
        (data / name).write_text("placeholder", encoding="utf-8")
    records = []
    calls = []
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

    def stub_load(path):
        if records:
            gc.collect()
            assert records[-1]() is None
        sheet = _WeakSheet.from_rows(
            [["left", "right"]]
            + [[value, value] for value in values]
        )
        records.append(weakref.ref(sheet))
        calls.append(path)
        return TableLoadResult({
            os.path.splitext(os.path.basename(path))[0]: sheet
        })

    monkeypatch.setattr(audit, "load_table_result", stub_load)

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    assert calls == [
        str(data / "a.csv"),
        str(data / "b.csv"),
        str(data / "a.csv"),
        str(data / "b.csv"),
    ]
    assert scan["coverage"]["files_succeeded"] == 2
    gc.collect()
    assert all(record() is None for record in records)


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


def test_scan_reports_recurring_unique_vector_exhaustion_once(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    for figure in range(1, 4):
        rows = [
            ",".join(
                str(figure * 100 + row * 10 + col + 0.125)
                for col in range(6)
            )
            for row in range(3)
        ]
        (data / f"Figure {figure}.csv").write_text(
            "a,b,c,d,e,f\n" + "\n".join(rows) + "\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(
        audit,
        "_RECURRING_ROW_VECTOR_UNIQUE_BUDGET",
        1,
        raising=False,
    )

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    limitations = [
        item
        for item in scan["coverage"]["limitations"]
        if item["reason"] == "recurring_row_unique_vector_limit"
    ]
    assert limitations == [{
        "scope": "scan",
        "reason": "recurring_row_unique_vector_limit",
        "limit": 1,
        "vectors_retained": 1,
        "skipped_new_vector_windows": 53,
        "skipped_new_vectors_lower_bound": 1,
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
