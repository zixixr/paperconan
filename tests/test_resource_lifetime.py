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
from paperconan._resources import BoundedFindingCollector
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

    entries = list(extract.iter_docx_tables("tables.docx"))

    assert len(entries) == 1
    sheet = entries[0][1]
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


EXPECTED_DENSE_STATES = {
    "relations": {
        "mask",
        "mask_rhs_workspace",
        "filtered_values",
        "abs_scale_workspace",
        "diff",
        "nonzero_workspace",
        "relation_close_workspace",
        "ratio",
        "ratio_stats_workspace",
        "sum",
        "sum_compare_workspace",
        "linear_fit_workspace",
        "fitted",
        "fitted_build_workspace",
        "fitted_relation_workspace",
        "integer_shift_workspace",
        "diff_is_int",
        "fractional_workspace",
        "frac_x",
        "hp_rows",
        "high_precision_unique_workspace",
        "high_precision_unique",
        "integer_diff_round_workspace",
        "int_diff_rounded",
        "integer_diff_unique_workspace",
        "int_diffs",
        "diff_rounded",
        "diff_unique_workspace",
        "unique_diffs",
    },
    "arithmetic_progression": {
        "column",
        "numeric_mask",
        "values",
        "diffs",
        "progression_abs_workspace",
        "progression_close_workspace",
    },
    "within_column": {
        "column",
        "numeric_mask",
        "values",
        "rounded",
        "frequency_workspace",
        "unique",
        "counts",
        "order",
        "integer_workspace",
    },
    "dispersed_repeats": {
        "numeric_mask",
        "rows",
        "values",
        "integer_gate_workspace",
        "rounded",
        "frequency_workspace",
        "unique_all",
        "counts_all",
        "order_all",
        "core_mask",
        "core_rows",
        "core_values",
        "decimal_places",
        "precision_gate",
        "rounded_core",
        "unique_workspace",
        "unique_core",
        "first_core",
        "inverse",
        "counts",
        "partition_workspace",
        "sort_workspace",
        "sorted_positions",
        "group_start_workspace",
        "group_starts",
        "group_rows",
        "group_diffs",
        "group_gaps",
        "sample_rounded",
        "sample_frequency_workspace",
        "sample_unique",
        "sample_counts",
        "sample_order",
    },
    "identical_after_rounding": {
        "candidate_workspace",
        "candidate_mask",
        "bucket_workspace",
        "bucket_mask",
        "flat_indices",
        "values",
        "rounded",
        "unique_workspace",
        "rounded_values",
        "first_indices",
        "inverse",
        "counts",
        "sort_workspace",
        "sorted_positions",
        "group_start_workspace",
        "group_starts",
        "group_values",
        "precise_rounded",
        "precise_unique_workspace",
        "precise_values",
    },
}

RELATION_ALLOCATED_STATES = {
    "mask",
    "mask_rhs_workspace",
    "filtered_values",
    "diff",
    "ratio",
    "sum",
    "fitted",
    "diff_is_int",
    "frac_x",
    "hp_rows",
    "high_precision_unique",
    "int_diff_rounded",
    "int_diffs",
    "diff_rounded",
    "unique_diffs",
}


class _GuardedUfunc:
    def __init__(self, call, original):
        self._call = call
        self._original = original

    def __call__(self, *args, **kwargs):
        return self._call(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._original, name)


def _guard_numpy_workspaces(monkeypatch, resources, guards):
    observed = set()
    for function_name, variants in guards.items():
        original = getattr(audit.np, function_name)
        normalized = []
        for variant in variants:
            label, required, *optional_forbidden = variant
            forbidden = (
                optional_forbidden[0]
                if optional_forbidden
                else ()
            )
            normalized.append((
                label,
                frozenset(required),
                frozenset(forbidden),
            ))

        def guarded(
            *args,
            _original=original,
            _variants=tuple(normalized),
            _function_name=function_name,
            **kwargs,
        ):
            if (
                _function_name == "isnan"
                and args
                and np.ndim(args[0]) == 0
            ):
                return _original(*args, **kwargs)
            if (
                _function_name == "all"
                and args
                and np.size(args[0]) <= 2
            ):
                return _original(*args, **kwargs)
            live_names = resources.state.live_names
            matches = [
                label
                for label, required, forbidden in _variants
                if (
                    required <= live_names
                    and forbidden.isdisjoint(live_names)
                )
            ]
            assert len(matches) == 1, (
                f"{_function_name} required exactly one explicit "
                f"required/forbidden lease contract, got {matches}; "
                f"live={live_names}"
            )
            observed.add((_function_name, matches[0]))
            return _original(*args, **kwargs)

        replacement = (
            _GuardedUfunc(guarded, original)
            if isinstance(original, np.ufunc)
            else guarded
        )
        monkeypatch.setattr(audit.np, function_name, replacement)
    return observed


def _guard_callable_workspace(
    monkeypatch, owner, function_name, resources, required_variants
):
    original = getattr(owner, function_name)
    variants = tuple(
        frozenset(required) for required in required_variants
    )

    def guarded(*args, **kwargs):
        matches = [
            required
            for required in variants
            if required <= resources.state.live_names
        ]
        assert len(matches) == 1, (
            f"{function_name} required one complete lease variant, "
            f"got {matches}"
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, function_name, guarded)


WORKSPACE_GUARDS = {
    "relations": {
        "isnan": (
            (
                "mask",
                {"mask"},
                {"mask_rhs_workspace", "linear_fit_workspace"},
            ),
            (
                "mask_rhs",
                {"mask", "mask_rhs_workspace"},
                {"linear_fit_workspace"},
            ),
            (
                "linear_fit",
                {"linear_fit_workspace"},
                {"mask", "mask_rhs_workspace"},
            ),
        ),
        "logical_not": (
            ("mask", {"mask"}, {"mask_rhs_workspace"}),
            ("mask_rhs", {"mask", "mask_rhs_workspace"}),
        ),
        "logical_and": (
            ("mask", {"mask", "mask_rhs_workspace"}),
        ),
        "abs": (
            ("abs_scale", {"abs_scale_workspace"}),
            ("fractional", {"frac_x", "hp_rows", "fractional_workspace"}),
            ("fitted_build", {"fitted", "fitted_build_workspace"}),
            ("integer_shift", {"diff_is_int", "integer_shift_workspace"}),
            ("linear_fit", {"linear_fit_workspace"}),
            ("relation_close", {"relation_close_workspace"}),
            ("sum_compare", {"sum_compare_workspace"}),
            ("fitted_relation", {"fitted_relation_workspace"}),
        ),
        "all": (
            ("nonzero", {"nonzero_workspace"}),
            ("relation_close", {"relation_close_workspace"}),
            ("sum_compare", {"sum_compare_workspace"}),
            ("fitted_relation", {"fitted_relation_workspace"}),
        ),
        "std": (
            ("ratio_stats", {"ratio", "ratio_stats_workspace"}),
            ("fitted_build", {"fitted", "fitted_build_workspace"}),
            ("linear_fit", {"linear_fit_workspace"}),
        ),
        "full_like": (
            ("relation_close", {"relation_close_workspace"}),
            ("sum_compare", {"sum_compare_workspace"}),
            ("fitted_relation", {"fitted_relation_workspace"}),
            (
                "integer_shift",
                {"diff_is_int", "integer_shift_workspace"},
            ),
        ),
        "round": (
            ("fractional", {"frac_x", "fractional_workspace"}),
            (
                "integer_diff",
                {"int_diff_rounded", "integer_diff_round_workspace"},
            ),
            ("diff", {"diff_rounded"}),
        ),
        "unique": (
            (
                "high_precision",
                {
                    "frac_x",
                    "high_precision_unique_workspace",
                    "high_precision_unique",
                },
            ),
            (
                "integer_diff",
                {
                    "int_diff_rounded",
                    "integer_diff_unique_workspace",
                    "int_diffs",
                },
            ),
            (
                "diff",
                {
                    "diff_rounded",
                    "diff_unique_workspace",
                    "unique_diffs",
                },
            ),
        ),
    },
    "arithmetic_progression": {
        "isnan": (
            ("numeric_mask", {"column", "numeric_mask"}),
        ),
        "logical_not": (
            ("numeric_mask", {"column", "numeric_mask"}),
        ),
        "abs": (
            ("scale", {"values", "progression_abs_workspace"}),
            ("close", {"diffs", "progression_close_workspace"}),
        ),
        "allclose": (
            ("close", {"diffs", "progression_close_workspace"}),
        ),
    },
    "within_column": {
        "isnan": (
            ("numeric_mask", {"column", "numeric_mask"}),
        ),
        "logical_not": (
            ("numeric_mask", {"column", "numeric_mask"}),
        ),
        "unique": (
            (
                "frequency",
                {
                    "rounded",
                    "frequency_workspace",
                    "unique",
                    "counts",
                    "order",
                },
            ),
        ),
        "lexsort": (
            (
                "frequency",
                {
                    "frequency_workspace",
                    "unique",
                    "counts",
                    "order",
                },
            ),
        ),
    },
    "dispersed_repeats": {
        "isnan": (
            ("numeric_mask", {"numeric_mask"}),
        ),
        "logical_not": (
            ("numeric_mask", {"numeric_mask"}),
        ),
        "flatnonzero": (
            ("rows", {"numeric_mask", "rows"}),
        ),
        "unique": (
            (
                "frequency",
                {
                    "rounded",
                    "frequency_workspace",
                    "unique_all",
                    "counts_all",
                    "order_all",
                },
            ),
            (
                "core",
                {
                    "rounded_core",
                    "unique_workspace",
                    "unique_core",
                    "first_core",
                    "inverse",
                    "counts",
                },
            ),
            (
                "sample",
                {
                    "sample_rounded",
                    "sample_frequency_workspace",
                    "sample_unique",
                    "sample_counts",
                    "sample_order",
                },
            ),
        ),
        "lexsort": (
            (
                "frequency",
                {
                    "frequency_workspace",
                    "unique_all",
                    "counts_all",
                    "order_all",
                },
            ),
            (
                "sample",
                {
                    "sample_frequency_workspace",
                    "sample_unique",
                    "sample_counts",
                    "sample_order",
                },
            ),
        ),
        "partition": (
            ("median", {"decimal_places", "partition_workspace"}),
        ),
        "greater_equal": (
            (
                "precision_gate",
                {"decimal_places", "precision_gate"},
            ),
        ),
        "argsort": (
            ("groups", {"inverse", "sort_workspace", "sorted_positions"}),
        ),
        "concatenate": (
            (
                "frequency_unique",
                {
                    "rounded",
                    "frequency_workspace",
                    "unique_all",
                    "counts_all",
                    "order_all",
                },
            ),
            (
                "core_unique",
                {
                    "rounded_core",
                    "unique_workspace",
                    "unique_core",
                    "first_core",
                    "inverse",
                    "counts",
                },
            ),
            (
                "sample_unique",
                {
                    "sample_rounded",
                    "sample_frequency_workspace",
                    "sample_unique",
                    "sample_counts",
                    "sample_order",
                },
            ),
            (
                "group_starts",
                {"counts", "group_start_workspace", "group_starts"},
            ),
        ),
        "cumsum": (
            (
                "core_unique",
                {
                    "rounded_core",
                    "unique_workspace",
                    "unique_core",
                    "first_core",
                    "inverse",
                    "counts",
                },
            ),
            (
                "group_starts",
                {"counts", "group_start_workspace", "group_starts"},
            ),
        ),
        "diff": (
            (
                "frequency_unique",
                {
                    "rounded",
                    "frequency_workspace",
                    "unique_all",
                    "counts_all",
                    "order_all",
                },
            ),
            (
                "core_unique",
                {
                    "rounded_core",
                    "unique_workspace",
                    "unique_core",
                    "first_core",
                    "inverse",
                    "counts",
                },
            ),
            (
                "sample_unique",
                {
                    "sample_rounded",
                    "sample_frequency_workspace",
                    "sample_unique",
                    "sample_counts",
                    "sample_order",
                },
            ),
            ("group_diffs", {"group_rows", "group_diffs"}),
        ),
        "greater": (
            (
                "group_gaps",
                {"group_diffs", "group_gaps"},
            ),
        ),
    },
    "identical_after_rounding": {
        "isnan": (
            (
                "candidate_mask",
                {
                    "candidate_workspace",
                    "candidate_mask",
                },
            ),
        ),
        "flatnonzero": (
            (
                "flat_indices",
                {"bucket_mask", "flat_indices"},
                {"candidate_workspace"},
            ),
        ),
        "unique": (
            (
                "rounded",
                {
                    "rounded",
                    "unique_workspace",
                    "rounded_values",
                    "first_indices",
                    "inverse",
                    "counts",
                },
                {"candidate_workspace"},
            ),
            (
                "precise",
                {
                    "precise_rounded",
                    "precise_unique_workspace",
                    "precise_values",
                },
                {"candidate_workspace"},
            ),
        ),
        "argsort": (
            (
                "groups",
                {"inverse", "sort_workspace", "sorted_positions"},
                {"candidate_workspace"},
            ),
        ),
        "concatenate": (
            (
                "rounded_unique",
                {
                    "rounded",
                    "unique_workspace",
                    "rounded_values",
                    "first_indices",
                    "inverse",
                    "counts",
                },
                {"candidate_workspace"},
            ),
            (
                "precise_unique",
                {
                    "precise_rounded",
                    "precise_unique_workspace",
                    "precise_values",
                },
                {"candidate_workspace"},
            ),
            (
                "group_starts",
                {"counts", "group_start_workspace", "group_starts"},
                {"candidate_workspace"},
            ),
        ),
        "cumsum": (
            (
                "rounded_unique",
                {
                    "rounded",
                    "unique_workspace",
                    "rounded_values",
                    "first_indices",
                    "inverse",
                    "counts",
                },
                {"candidate_workspace"},
            ),
            (
                "group_starts",
                {"counts", "group_start_workspace", "group_starts"},
                {"candidate_workspace"},
            ),
        ),
    },
}

EXPECTED_MULTI_OUTPUT_CALLS = {
    "within_column": {
        ("unique", "frequency"),
        ("lexsort", "frequency"),
    },
    "dispersed_repeats": {
        ("unique", "frequency"),
        ("lexsort", "frequency"),
        ("greater_equal", "precision_gate"),
        ("unique", "core"),
        ("argsort", "groups"),
        ("concatenate", "group_starts"),
        ("cumsum", "group_starts"),
        ("diff", "group_diffs"),
        ("greater", "group_gaps"),
        ("unique", "sample"),
        ("lexsort", "sample"),
    },
    "identical_after_rounding": {
        ("unique", "rounded"),
        ("argsort", "groups"),
        ("concatenate", "group_starts"),
        ("cumsum", "group_starts"),
        ("unique", "precise"),
    },
}


@pytest.mark.parametrize(
    ("family", "detector_name", "sheet", "bounds", "expected_states"),
    [
        (
            "arithmetic_progression",
            "detect_arithmetic_progression",
            Sheet.from_rows(
                [["value"]]
                + [[1.125 + row * 0.375] for row in range(40)]
            ),
            (1, 41, 0, 1, ["value"]),
            EXPECTED_DENSE_STATES["arithmetic_progression"],
        ),
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
            EXPECTED_DENSE_STATES["dispersed_repeats"],
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
            EXPECTED_DENSE_STATES["identical_after_rounding"],
        ),
    ],
)
def test_dense_detector_owned_state_covers_actual_live_arrays(
    family,
    detector_name,
    sheet,
    bounds,
    expected_states,
    monkeypatch,
):
    detector = getattr(audit, detector_name)
    baseline = detector(sheet, *bounds)
    resources = audit._DenseFamilyResources(
        family=family,
        max_rows=10_000,
        work_limit=10_000_000,
        state_limit=10_000_000,
    )
    observed_numpy_calls = _guard_numpy_workspaces(
        monkeypatch,
        resources,
        WORKSPACE_GUARDS[family],
    )

    instrumented = detector(sheet, *bounds, _resources=resources)
    result = resources.result()

    assert instrumented == baseline
    assert result.candidates_examined == result.candidates_total
    assert result.candidates_skipped == 0
    assert result.peak_state_units <= resources.state.limit_units
    assert resources.state.live_units == 0
    assert expected_states <= resources.state.seen_names
    assert (
        EXPECTED_MULTI_OUTPUT_CALLS.get(family, set())
        <= observed_numpy_calls
    )


@pytest.mark.parametrize(
    "values",
    [
        [1000.1234 + row * 0.7317 for row in range(120)],
        [7.125] * 90
        + [1000.1234 + row * 0.7317 for row in range(30)],
    ],
    ids=["all-distinct", "high-duplication"],
)
def test_within_column_owned_state_covers_actual_live_arrays(
    values, monkeypatch
):
    sheet = Sheet.from_rows(
        [["value"]] + [[value] for value in values]
    )
    bounds = (1, sheet.nrows, 0, 1, ["value"])
    baseline = audit.detect_within_column_patterns(sheet, *bounds)
    resources = audit._DenseFamilyResources(
        family="within_column",
        max_rows=10_000,
        work_limit=10_000_000,
        state_limit=10_000_000,
    )
    observed_numpy_calls = _guard_numpy_workspaces(
        monkeypatch,
        resources,
        WORKSPACE_GUARDS["within_column"],
    )

    instrumented = audit.detect_within_column_patterns(
        sheet, *bounds, _resources=resources
    )
    result = resources.result()

    assert instrumented == baseline
    assert result.candidates_examined == result.candidates_total
    assert result.candidates_skipped == 0
    assert result.peak_state_units <= resources.state.limit_units
    assert resources.state.live_units == 0
    assert (
        EXPECTED_DENSE_STATES["within_column"]
        <= resources.state.seen_names
    )
    assert (
        EXPECTED_MULTI_OUTPUT_CALLS["within_column"]
        <= observed_numpy_calls
    )


def test_equal_pairs_consumes_work_without_allocating_dense_state():
    rows = [[row + 0.125, row + 0.125] for row in range(20)]
    sheet = Sheet.from_rows([["left", "right"], *rows])
    resources = audit._DenseFamilyResources(
        family="equal_pairs",
        max_rows=100,
        work_limit=100,
        state_limit=0,
    )
    baseline = audit.detect_equal_pairs(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"]
    )

    instrumented = audit.detect_equal_pairs(
        sheet,
        1,
        sheet.nrows,
        0,
        2,
        ["left", "right"],
        _resources=resources,
    )
    result = resources.result()

    assert instrumented == baseline
    assert result.candidates_examined == 1
    assert result.candidates_skipped == 0
    assert result.work_examined == 40
    assert result.peak_state_units == 0
    assert resources.state.live_units == 0


@pytest.mark.parametrize(
    ("family", "detector_name", "sheet", "bounds"),
    [
        (
            "within_column",
            "detect_within_column_patterns",
            Sheet.from_rows(
                [["value"]]
                + [
                    [1000.1234 + row * 0.7317]
                    for row in range(120)
                ]
            ),
            (1, 121, 0, 1, ["value"]),
        ),
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
        ),
    ],
)
def test_dense_state_admission_boundary_is_deterministic(
    family, detector_name, sheet, bounds
):
    detector = getattr(audit, detector_name)
    baseline = detector(sheet, *bounds)
    probe = audit._DenseFamilyResources(
        family=family,
        max_rows=10_000,
        work_limit=10_000_000,
        state_limit=10_000_000,
    )
    detector(sheet, *bounds, _resources=probe)
    required = probe.result().peak_state_units
    if family == "dispersed_repeats":
        assert {
            "precision_gate",
            "group_diffs",
            "group_gaps",
        } <= probe.state.seen_names

    limited = audit._DenseFamilyResources(
        family=family,
        max_rows=10_000,
        work_limit=10_000_000,
        state_limit=required - 1,
    )
    limited_findings = detector(
        sheet, *bounds, _resources=limited
    )
    limited_result = limited.result()

    assert limited_findings == []
    assert limited_result.candidates_examined == 0
    assert "state" in limited_result.limits_reached
    assert limited.state.live_units == 0

    exact = audit._DenseFamilyResources(
        family=family,
        max_rows=10_000,
        work_limit=10_000_000,
        state_limit=required,
    )
    exact_findings = detector(sheet, *bounds, _resources=exact)

    assert exact_findings == baseline
    assert exact.result().limits_reached == ()
    assert exact.state.live_units == 0


RELATION_BRANCH_CASES = [
    (
        "offset",
        [[i + 0.125, i + 0.375] for i in range(40)],
        "relation_close_workspace",
    ),
    (
        "ratio",
        [[i + 0.125, 2 * (i + 0.125)] for i in range(40)],
        "ratio",
    ),
    (
        "sum",
        [[i + 0.125, 100 - (i + 0.125)] for i in range(40)],
        "sum_compare_workspace",
    ),
    (
        "linear",
        [[i + 0.125, 3 * (i + 0.125) + 7] for i in range(40)],
        "linear_fit_workspace",
    ),
    (
        "fractional-shift",
        [[
            i + 0.12345,
            i + 0.12345 + (10 if i % 2 else 20),
        ] for i in range(40)],
        "high_precision_unique_workspace",
    ),
    (
        "discrete-difference",
        [[
            i + 0.2,
            i + 0.2 + (0.1111 if i % 2 else 0.2222),
        ] for i in range(40)],
        "diff_unique_workspace",
    ),
]


@pytest.mark.parametrize(
    "rows,branch_state",
    [
        (rows, branch_state)
        for _case_id, rows, branch_state in RELATION_BRANCH_CASES
    ],
    ids=[case_id for case_id, _rows, _state in RELATION_BRANCH_CASES],
)
def test_relation_allocations_are_reserved_and_released(
    rows, branch_state, monkeypatch
):
    sheet = Sheet.from_rows([["left", "right"], *rows])
    resources = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=100_000,
        state_limit=100_000,
    )
    baseline = audit.detect_relations(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"]
    )
    observed_numpy_calls = _guard_numpy_workspaces(
        monkeypatch,
        resources,
        WORKSPACE_GUARDS["relations"],
    )
    for owner, function_name, required_variants in (
        (
            audit,
            "relation_close",
            (
                {"relation_close_workspace"},
                {"sum_compare_workspace"},
                {"fitted_relation_workspace"},
            ),
        ),
        (
            audit,
            "integer_shift_close",
            ({"integer_shift_workspace", "diff_is_int"},),
        ),
        (
            audit.stats,
            "linregress",
            ({"linear_fit_workspace"},),
        ),
    ):
        _guard_callable_workspace(
            monkeypatch,
            owner,
            function_name,
            resources,
            required_variants,
        )

    instrumented = audit.detect_relations(
        sheet,
        1,
        sheet.nrows,
        0,
        2,
        ["left", "right"],
        _resources=resources,
    )
    result = resources.result()

    assert instrumented == baseline
    assert result.candidates_examined == 1
    assert result.candidates_skipped == 0
    assert result.peak_state_units > 0
    assert resources.state.live_units == 0
    assert {
        "mask",
        "mask_rhs_workspace",
        "filtered_values",
        "diff",
        branch_state,
    } <= resources.state.seen_names
    assert {
        ("isnan", "mask"),
        ("isnan", "mask_rhs"),
    } <= observed_numpy_calls
    if branch_state == "high_precision_unique_workspace":
        assert ("unique", "high_precision") in observed_numpy_calls


def test_relation_branch_inventory_reserves_every_declared_state(
    monkeypatch
):
    seen_names = set()
    allocation_names = set()
    original_allocate = audit._DenseCandidate.allocate

    def tracked_allocate(self, name, units, factory):
        allocation_names.add(name)
        return original_allocate(self, name, units, factory)

    monkeypatch.setattr(
        audit._DenseCandidate,
        "allocate",
        tracked_allocate,
    )

    for _case_id, rows, _branch_state in RELATION_BRANCH_CASES:
        sheet = Sheet.from_rows([["left", "right"], *rows])
        resources = audit._DenseFamilyResources(
            family="relations",
            max_rows=100,
            work_limit=100_000,
            state_limit=100_000,
        )
        audit.detect_relations(
            sheet,
            1,
            sheet.nrows,
            0,
            2,
            ["left", "right"],
            _resources=resources,
        )
        seen_names.update(resources.state.seen_names)
        assert resources.state.live_units == 0

    assert EXPECTED_DENSE_STATES["relations"] <= seen_names
    assert RELATION_ALLOCATED_STATES <= allocation_names


@pytest.mark.parametrize(
    "rows,live_name,reservation_name",
    [
        (
            [
                [1.125, 9.0],
                [2.25, 1.2],
                [3.375, 8.1],
                [4.5, 4.7],
                [5.625, 12.2],
                [6.75, 2.5],
            ],
            "ratio",
            "relation_close_workspace",
        ),
        (
            [
                [value, 7.25]
                for value in (
                    1.125,
                    2.25,
                    3.375,
                    4.5,
                    5.625,
                    6.75,
                )
            ],
            "fitted",
            "fitted_relation_workspace",
        ),
    ],
    ids=["unstable-ratio", "constant-response"],
)
def test_relation_ineligible_comparisons_short_circuit_before_reservation(
    rows, live_name, reservation_name, monkeypatch
):
    sheet = Sheet.from_rows([["left", "right"], *rows])
    resources = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=100_000,
        state_limit=100_000,
    )
    comparison_states = []
    reservation_states = []
    original_relation_close = audit.relation_close
    original_reserve = audit._DenseCandidate.reserve

    def tracked_relation_close(*args, **kwargs):
        live_names = resources.state.live_names
        if live_name in live_names:
            comparison_states.append(live_names)
        return original_relation_close(*args, **kwargs)

    def tracked_reserve(self, name, units):
        live_names = resources.state.live_names
        if name == reservation_name and live_name in live_names:
            reservation_states.append(live_names)
        return original_reserve(self, name, units)

    monkeypatch.setattr(
        audit, "relation_close", tracked_relation_close
    )
    monkeypatch.setattr(
        audit._DenseCandidate, "reserve", tracked_reserve
    )

    audit.detect_relations(
        sheet,
        1,
        sheet.nrows,
        0,
        2,
        ["left", "right"],
        _resources=resources,
    )

    assert comparison_states == []
    assert reservation_states == []
    assert resources.state.live_units == 0


@pytest.mark.parametrize(
    "rows,expected_kinds",
    [
        (
            [[1.1, 7.3], [2.2, 4.8], [3.3, 9.1]],
            [],
        ),
        (
            [[i + 0.125, i + 0.125] for i in range(5)],
            ["identical_column"],
        ),
        (
            [[i, i + 5] for i in range(5)],
            ["constant_offset"],
        ),
        (
            [[10**400 + i, 10**400 + i * i] for i in range(5)],
            [],
        ),
        (
            [
                [1.1, 9.3],
                [2.4, 1.7],
                [3.9, 7.1],
                [5.8, 4.4],
                [8.2, 12.6],
                [11.7, 2.2],
            ],
            [],
        ),
        (
            [[i + 0.125, 2 * (i + 0.125)] for i in range(8)],
            ["constant_ratio"],
        ),
    ],
    ids=[
        "short",
        "identical",
        "integer-offset",
        "wide-integer",
        "normal-empty",
        "normal-finding",
    ],
)
def test_relation_normal_exits_complete_candidate_once(
    rows, expected_kinds
):
    sheet = Sheet.from_rows([["left", "right"], *rows])
    resources = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=100_000,
        state_limit=100_000,
    )

    findings = audit.detect_relations(
        sheet,
        1,
        sheet.nrows,
        0,
        2,
        ["left", "right"],
        _resources=resources,
    )
    result = resources.result()

    assert [finding["kind"] for finding in findings] == expected_kinds
    assert result.candidates_total == 1
    assert result.candidates_examined == 1
    assert result.candidates_skipped == 0
    assert result.work_examined == 2 * len(rows)
    assert resources.state.live_units == 0


def test_relation_proportional_arrays_die_before_candidate_finalizer(
    monkeypatch
):
    rows = [
        [
            row + 0.125,
            2.75 * (row + 0.125) + 3.5,
            (row + 0.375) ** 2 + 0.625,
        ]
        for row in range(40)
    ]
    sheet = Sheet.from_rows([["a", "b", "c"], *rows])
    resources = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=1_000_000,
        state_limit=1_000_000,
    )
    refs_by_candidate = {}
    finalized = []
    original_allocate = audit._DenseCandidate.allocate
    original_exit = audit._DenseCandidate.__exit__

    def array_refs(value):
        if isinstance(value, np.ndarray):
            return [weakref.ref(value)]
        if isinstance(value, tuple):
            return [
                ref
                for item in value
                for ref in array_refs(item)
            ]
        return []

    def tracked_allocate(self, name, units, factory):
        value, lease = original_allocate(self, name, units, factory)
        refs_by_candidate.setdefault(id(self), []).extend(
            array_refs(value)
        )
        return value, lease

    def tracked_exit(self, exc_type, exc, traceback):
        refs = refs_by_candidate.get(id(self), ())
        assert refs
        assert all(ref() is None for ref in refs)
        finalized.append(id(self))
        return original_exit(self, exc_type, exc, traceback)

    monkeypatch.setattr(
        audit._DenseCandidate, "allocate", tracked_allocate
    )
    monkeypatch.setattr(
        audit._DenseCandidate, "__exit__", tracked_exit
    )

    audit.detect_relations(
        sheet,
        1,
        sheet.nrows,
        0,
        3,
        ["a", "b", "c"],
        _resources=resources,
    )

    assert len(finalized) == 3
    assert resources.result().candidates_examined == 3
    assert resources.state.live_units == 0


def test_relation_later_state_rejection_keeps_completed_candidate():
    rows = [[i, i + 5, i + 0.125] for i in range(8)]
    sheet = Sheet.from_rows([["a", "b", "c"], *rows])
    resources = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=100_000,
        state_limit=0,
    )

    findings = audit.detect_relations(
        sheet,
        1,
        sheet.nrows,
        0,
        3,
        ["a", "b", "c"],
        _resources=resources,
    )
    result = resources.result()

    assert [finding["kind"] for finding in findings] == [
        "constant_offset"
    ]
    assert result.candidates_total == 3
    assert result.candidates_examined == 1
    assert result.candidates_skipped == 2
    assert result.work_examined == 4 * len(rows)
    assert result.state_required_lower_bound > 0
    assert result.peak_state_units == 0
    assert result.limits_reached == ("state",)
    assert resources.state.live_units == 0


def test_relation_state_boundary_stops_before_rejected_allocation():
    rows = [[i + 0.125, 3 * (i + 0.125) + 7] for i in range(60)]
    sheet = Sheet.from_rows([["left", "right"], *rows])
    probe = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=100_000,
        state_limit=100_000,
    )
    audit.detect_relations(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"],
        _resources=probe,
    )
    required = probe.result().peak_state_units
    assert {
        "fitted_build_workspace",
        "fitted_relation_workspace",
    } <= probe.state.seen_names

    limited = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=100_000,
        state_limit=required - 1,
    )
    findings = audit.detect_relations(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"],
        _resources=limited,
    )
    result = limited.result()

    assert findings == []
    assert result.candidates_examined == 0
    assert result.candidates_skipped == 1
    assert "state" in result.limits_reached
    assert result.peak_state_units <= required - 1
    assert limited.state.live_units == 0

    exact = audit._DenseFamilyResources(
        family="relations",
        max_rows=100,
        work_limit=100_000,
        state_limit=required,
    )
    exact_findings = audit.detect_relations(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"],
        _resources=exact,
    )
    assert exact_findings == audit.detect_relations(
        sheet, 1, sheet.nrows, 0, 2, ["left", "right"]
    )
    assert exact.result().limits_reached == ()
    assert exact.state.live_units == 0


def test_dense_candidate_factory_runs_only_inside_entered_transaction():
    resources = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=None,
        state_limit=1,
    )
    assert resources.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=0,
        state_required=1,
    )
    live_names_seen = []

    candidate = resources.start_candidate(0, lambda *_args: None)
    assert candidate is not None
    with pytest.raises(AssertionError):
        candidate.allocate(
            "too_early",
            1,
            lambda: pytest.fail("pre-transaction factory ran"),
        )
    with candidate:
        value, lease = candidate.allocate(
            "probe_array",
            1,
            lambda: (
                live_names_seen.append(resources.state.live_names),
                np.zeros(1, dtype=np.float64),
            )[1],
        )
        assert value.shape == (1,)
        assert lease is not None
        candidate.release(lease)
        assert candidate.live_lease_count == 0

    assert live_names_seen == [frozenset({"probe_array"})]
    assert resources.state.live_names == frozenset()

    blocked = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=None,
        state_limit=0,
    )
    assert blocked.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=0,
        state_required=1,
    )
    candidate = blocked.start_candidate(0, lambda *_args: None)
    assert candidate is not None
    with candidate:
        candidate.allocate(
            "blocked_array",
            1,
            lambda: pytest.fail("factory ran before state admission"),
        )
        pytest.fail("resource rejection did not unwind candidate")
    assert candidate.rejected is True
    assert candidate.closed is True
    assert blocked.result().candidates_examined == 0
    assert not hasattr(audit._DenseFamilyResources, "allocate")

    work_blocked = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=0,
        state_limit=1,
    )
    candidate, lease, initial_leases = (
        work_blocked.start_allocated_candidate(
            "candidate_array",
            1,
            1,
            lambda *_args: None,
        )
    )
    assert candidate is None
    assert lease is None
    assert initial_leases == ()
    assert work_blocked.work_examined == 0
    assert work_blocked.state.live_units == 0

    state_blocked_candidate = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=10,
        state_limit=0,
    )
    candidate, lease, initial_leases = (
        state_blocked_candidate.start_allocated_candidate(
            "candidate_array",
            1,
            1,
            lambda *_args: None,
        )
    )
    assert candidate is None
    assert lease is None
    assert initial_leases == ()
    assert state_blocked_candidate.candidates_started == 0
    assert state_blocked_candidate.work_examined == 0
    assert state_blocked_candidate.state.live_units == 0


def test_dense_candidate_registry_tracks_only_live_leases():
    resources = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=None,
        state_limit=1,
    )
    assert resources.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=0,
        state_required=1,
    )
    candidate = resources.start_candidate(0, lambda *_args: None)
    assert candidate is not None

    with candidate:
        for _ in range(5_000):
            _value, lease = candidate.allocate(
                "group_probe",
                1,
                lambda: np.zeros(1, dtype=np.float64),
            )
            assert candidate.live_lease_count == 1
            del _value
            candidate.release(lease)
            assert candidate.live_lease_count == 0
        assert candidate.peak_lease_count == 1

    assert candidate.live_lease_count == 0
    assert resources.state.live_units == 0


def test_dense_candidate_scoped_helper_drops_array_before_release():
    resources = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=None,
        state_limit=1,
    )
    assert resources.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=0,
        state_required=1,
    )
    candidate = resources.start_candidate(0, lambda *_args: None)
    assert candidate is not None

    def run_candidate_body():
        value, lease = candidate.allocate(
            "scoped_array",
            1,
            lambda: np.zeros(1, dtype=np.float64),
        )
        return weakref.ref(value), lease

    with candidate:
        value_ref, lease = run_candidate_body()
        assert value_ref() is None
        candidate.release(lease)

    assert candidate.live_lease_count == 0
    assert resources.state.live_units == 0


def test_dense_source_factory_exception_uses_candidate_finalizer(
    monkeypatch
):
    resources = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=10,
        state_limit=2,
    )
    assert resources.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=1,
        state_required=2,
    )
    candidate, source_lease, initial_leases = (
        resources.start_allocated_candidate(
            "candidate_array",
            1,
            1,
            lambda *_args: None,
            initial_reservations=(("candidate_workspace", 1),),
        )
    )
    assert candidate is not None
    assert source_lease is not None
    assert len(initial_leases) == 1
    exit_snapshots = []
    original_exit = audit._DenseCandidate.__exit__

    def tracked_exit(self, exc_type, exc, traceback):
        exit_snapshots.append((
            self.live_lease_count,
            resources.state.live_names,
        ))
        return original_exit(self, exc_type, exc, traceback)

    monkeypatch.setattr(
        audit._DenseCandidate, "__exit__", tracked_exit
    )

    with pytest.raises(RuntimeError, match="source factory"):
        with candidate:
            candidate.materialize(
                source_lease,
                lambda: (_ for _ in ()).throw(
                    RuntimeError("source factory")
                ),
                release_after=initial_leases,
            )

    result = resources.result()
    assert exit_snapshots == [(
        2,
        frozenset({"candidate_workspace", "candidate_array"}),
    )]
    assert candidate.closed is True
    assert candidate.live_lease_count == 0
    assert result.candidates_examined == 0
    assert result.candidates_skipped == 1
    assert result.work_examined == 0
    assert resources.state.live_names == frozenset()
    assert resources.state.live_units == 0


def test_dense_source_validation_exception_uses_candidate_finalizer(
    monkeypatch
):
    resources = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=10,
        state_limit=2,
    )
    assert resources.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=1,
        state_required=2,
    )
    candidate, source_lease, initial_leases = (
        resources.start_allocated_candidate(
            "candidate_array",
            1,
            1,
            lambda *_args: None,
            initial_reservations=(("candidate_workspace", 1),),
        )
    )
    assert candidate is not None
    assert source_lease is not None
    assert len(initial_leases) == 1
    exit_snapshots = []
    original_exit = audit._DenseCandidate.__exit__

    def tracked_exit(self, exc_type, exc, traceback):
        exit_snapshots.append((
            self.live_lease_count,
            resources.state.live_names,
        ))
        return original_exit(self, exc_type, exc, traceback)

    monkeypatch.setattr(
        audit._DenseCandidate, "__exit__", tracked_exit
    )

    with pytest.raises(
        AssertionError, match="used 2 units but reserved 1"
    ):
        with candidate:
            candidate.materialize(
                source_lease,
                lambda: np.zeros(2, dtype=np.float64),
                release_after=initial_leases,
            )

    assert exit_snapshots == [(
        2,
        frozenset({"candidate_workspace", "candidate_array"}),
    )]
    assert candidate.closed is True
    assert candidate.live_lease_count == 0
    result = resources.result()
    assert result.candidates_examined == 0
    assert result.candidates_skipped == 1
    assert result.work_examined == 0
    assert resources.state.live_names == frozenset()
    assert resources.state.live_units == 0


def test_dense_first_materialization_releases_initial_workspace():
    resources = audit._DenseFamilyResources(
        family="probe",
        max_rows=None,
        work_limit=10,
        state_limit=2,
    )
    assert resources.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=1,
        state_required=2,
    )
    live_names_during_factory = []
    candidate, source_lease, initial_leases = (
        resources.start_allocated_candidate(
            "candidate_array",
            1,
            1,
            lambda *_args: None,
            initial_reservations=(("candidate_workspace", 1),),
        )
    )
    assert candidate is not None
    assert source_lease is not None
    assert len(initial_leases) == 1

    with candidate:
        value = candidate.materialize(
            source_lease,
            lambda: (
                live_names_during_factory.append(
                    resources.state.live_names
                ),
                np.zeros(1, dtype=np.float64),
            )[1],
            release_after=initial_leases,
        )
        assert live_names_during_factory == [frozenset({
            "candidate_workspace",
            "candidate_array",
        })]
        assert resources.state.live_names == frozenset({
            "candidate_array"
        })
        assert candidate.live_lease_count == 1
        del value
        candidate.release(source_lease)

    assert candidate.live_lease_count == 0
    assert resources.state.live_units == 0


ARRAY_SOURCE_CASES = (
    (
        "arithmetic_progression",
        "detect_arithmetic_progression",
        audit,
        "col_array",
        {"column"},
    ),
    (
        "within_column",
        "detect_within_column_patterns",
        audit,
        "col_array",
        {"column"},
    ),
    (
        "dispersed_repeats",
        "detect_dispersed_repeats",
        audit.np,
        "isnan",
        {"numeric_mask"},
    ),
    (
        "identical_after_rounding",
        "detect_identical_after_rounding",
        audit.np,
        "isnan",
        {"candidate_workspace", "candidate_mask"},
    ),
)


@pytest.mark.parametrize(
    "family,detector_name,owner,source_name,expected_leases",
    ARRAY_SOURCE_CASES,
    ids=[case[0] for case in ARRAY_SOURCE_CASES],
)
def test_array_family_work_rejection_precedes_source_factory(
    family,
    detector_name,
    owner,
    source_name,
    expected_leases,
    monkeypatch,
):
    sheet = Sheet.from_rows(
        [["left", "right"]]
        + [[row + 0.125, row + 0.375] for row in range(40)]
    )
    resources = audit._DenseFamilyResources(
        family=family,
        max_rows=100,
        work_limit=0,
        state_limit=100_000,
    )

    monkeypatch.setattr(
        owner,
        source_name,
        lambda *_args, **_kwargs: pytest.fail(
            "source factory ran before work admission"
        ),
    )
    findings = getattr(audit, detector_name)(
        sheet,
        1,
        sheet.nrows,
        0,
        2,
        ["left", "right"],
        _resources=resources,
    )

    result = resources.result()
    assert findings == []
    assert resources.candidates_started == 0
    assert result.candidates_examined == 0
    assert result.work_examined == 0
    assert expected_leases <= resources.state.seen_names
    assert resources.state.live_names == frozenset()


@pytest.mark.parametrize(
    "family,detector_name,owner,source_name,expected_leases",
    ARRAY_SOURCE_CASES,
    ids=[case[0] for case in ARRAY_SOURCE_CASES],
)
def test_array_family_source_exception_uses_candidate_finalizer(
    family,
    detector_name,
    owner,
    source_name,
    expected_leases,
    monkeypatch,
):
    sheet = Sheet.from_rows(
        [["left", "right"]]
        + [[row + 0.125, row + 0.375] for row in range(40)]
    )
    resources = audit._DenseFamilyResources(
        family=family,
        max_rows=100,
        work_limit=100_000,
        state_limit=100_000,
    )

    def fail_source_factory(*_args, **_kwargs):
        raise RuntimeError(f"{family} source factory")

    monkeypatch.setattr(owner, source_name, fail_source_factory)
    with pytest.raises(RuntimeError, match=f"{family} source factory"):
        getattr(audit, detector_name)(
            sheet,
            1,
            sheet.nrows,
            0,
            2,
            ["left", "right"],
            _resources=resources,
        )

    result = resources.result()
    assert resources.candidates_started == 1
    assert result.candidates_examined == 0
    assert result.work_examined == 0
    assert expected_leases <= resources.state.seen_names
    assert resources.state.live_names == frozenset()
    assert resources.state.live_units == 0


GROUP_REJECTION_CASES = (
    (
        "dispersed-group-rows",
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
        "group_rows",
        {"group_diffs", "group_gaps", "sample_rounded"},
    ),
    (
        "dispersed-group-diffs",
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
        "group_diffs",
        {"group_gaps", "sample_rounded"},
    ),
    (
        "dispersed-group-gaps",
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
        "group_gaps",
        {"sample_rounded"},
    ),
    (
        "rounding-group-values",
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
        "group_values",
        {"precise_rounded", "precise_values"},
    ),
)


@pytest.mark.parametrize(
    (
        "case_id",
        "family",
        "detector_name",
        "sheet",
        "bounds",
        "reject_name",
        "forbidden_later",
    ),
    GROUP_REJECTION_CASES,
    ids=[case[0] for case in GROUP_REJECTION_CASES],
)
def test_dense_group_rejection_unwinds_complete_candidate(
    case_id,
    family,
    detector_name,
    sheet,
    bounds,
    reject_name,
    forbidden_later,
    monkeypatch,
):
    resources = audit._DenseFamilyResources(
        family=family,
        max_rows=10_000,
        work_limit=10_000_000,
        state_limit=10_000_000,
    )
    attempts = []
    candidates = []
    original_try_reserve = audit.StateBudget.try_reserve
    original_start = resources.start_allocated_candidate

    def reject_selected(state, name, units):
        if state is resources.state:
            attempts.append(name)
            if name == reject_name:
                return None
        return original_try_reserve(state, name, units)

    def tracked_start(*args, **kwargs):
        candidate, lease, initial_leases = original_start(
            *args, **kwargs
        )
        if candidate is not None:
            candidates.append(candidate)
        return candidate, lease, initial_leases

    monkeypatch.setattr(
        audit.StateBudget, "try_reserve", reject_selected
    )
    monkeypatch.setattr(
        resources, "start_allocated_candidate", tracked_start
    )

    findings = getattr(audit, detector_name)(
        sheet, *bounds, _resources=resources
    )
    result = resources.result()

    assert findings == [], case_id
    assert reject_name in attempts
    assert forbidden_later.isdisjoint(attempts)
    assert len(candidates) == 1
    assert candidates[0].rejected is True
    assert candidates[0].closed is True
    assert candidates[0].live_lease_count == 0
    assert result.candidates_examined == 0
    assert "state" in result.limits_reached
    assert resources.state.live_units == 0


SCALAR_PAIR_SOURCE_CASES = (
    ("relations", "detect_relations"),
    ("equal_pairs", "detect_equal_pairs"),
)


@pytest.mark.parametrize(
    "family,detector_name",
    SCALAR_PAIR_SOURCE_CASES,
    ids=[case[0] for case in SCALAR_PAIR_SOURCE_CASES],
)
def test_scalar_pair_work_rejection_precedes_source_scan(
    family, detector_name, monkeypatch
):
    sheet = Sheet.from_rows(
        [["left", "right"]]
        + [[row + 0.125, row + 0.375] for row in range(40)]
    )
    resources = audit._DenseFamilyResources(
        family=family,
        max_rows=100,
        work_limit=0,
        state_limit=100_000,
    )
    monkeypatch.setattr(
        audit,
        "_numeric_pair_stats",
        lambda *_args, **_kwargs: pytest.fail(
            "scalar pair source ran before work admission"
        ),
    )

    findings = getattr(audit, detector_name)(
        sheet,
        1,
        sheet.nrows,
        0,
        2,
        ["left", "right"],
        _resources=resources,
    )

    result = resources.result()
    assert findings == []
    assert resources.candidates_started == 0
    assert result.candidates_examined == 0
    assert result.candidates_skipped == 1
    assert result.work_examined == 0
    assert resources.state.live_names == frozenset()


@pytest.mark.parametrize(
    "family,detector_name",
    SCALAR_PAIR_SOURCE_CASES,
    ids=[case[0] for case in SCALAR_PAIR_SOURCE_CASES],
)
def test_scalar_pair_source_exception_uses_entered_finalizer(
    family, detector_name, monkeypatch
):
    sheet = Sheet.from_rows(
        [["left", "right"]]
        + [[row + 0.125, row + 0.375] for row in range(40)]
    )
    resources = audit._DenseFamilyResources(
        family=family,
        max_rows=100,
        work_limit=100_000,
        state_limit=100_000,
    )
    candidates = []
    original_start = resources.start_candidate

    def tracked_start(source_visits, emit):
        candidate = original_start(source_visits, emit)
        if candidate is not None:
            candidates.append(candidate)
        return candidate

    monkeypatch.setattr(resources, "start_candidate", tracked_start)

    def fail_source(*_args, **_kwargs):
        raise RuntimeError(f"{family} scalar source")

    monkeypatch.setattr(audit, "_numeric_pair_stats", fail_source)
    with pytest.raises(RuntimeError, match=f"{family} scalar source"):
        getattr(audit, detector_name)(
            sheet,
            1,
            sheet.nrows,
            0,
            2,
            ["left", "right"],
            _resources=resources,
        )

    result = resources.result()
    assert len(candidates) == 1
    assert candidates[0].entered is True
    assert candidates[0].closed is True
    assert resources.candidates_started == 1
    assert result.candidates_examined == 0
    assert result.candidates_skipped == 1
    assert result.work_examined == 0
    assert resources.state.live_names == frozenset()


def test_declared_state_requirement_survives_every_rejection_path():
    row_limited = audit._DenseFamilyResources(
        family="probe",
        max_rows=0,
        work_limit=10,
        state_limit=10,
    )
    assert not row_limited.begin(
        row_count=1,
        candidates_total=2,
        minimum_candidate_work=3,
        state_required=7,
    )

    work_limited = audit._DenseFamilyResources(
        family="probe",
        max_rows=10,
        work_limit=2,
        state_limit=10,
    )
    assert work_limited.begin(
        row_count=1,
        candidates_total=2,
        minimum_candidate_work=3,
        state_required=7,
    )
    assert work_limited.start_candidate(
        3, lambda *_args: None
    ) is None

    state_limited = audit._DenseFamilyResources(
        family="probe",
        max_rows=10,
        work_limit=10,
        state_limit=1,
    )
    assert state_limited.begin(
        row_count=1,
        candidates_total=2,
        minimum_candidate_work=3,
        state_required=7,
    )
    candidate, lease, initial_leases = (
        state_limited.start_allocated_candidate(
            "rejected",
            2,
            0,
            lambda *_args: None,
        )
    )
    assert candidate is None
    assert lease is None
    assert initial_leases == ()

    results = [
        row_limited.result(),
        work_limited.result(),
        state_limited.result(),
    ]
    assert [result.state_required for result in results] == [7, 7, 7]
    assert [
        result.state_required_lower_bound for result in results
    ] == [0, 0, 2]
    assert [result.peak_state_units for result in results] == [0, 0, 0]
    assert [result.limits_reached for result in results] == [
        ("row",),
        ("work",),
        ("state",),
    ]


def test_dense_candidate_finalizer_commits_or_discards_atomically():
    emitted = []
    completed = audit._DenseFamilyResources(
        family="probe",
        max_rows=10,
        work_limit=10,
        state_limit=1,
    )
    assert completed.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=1,
        state_required=1,
    )
    candidate = completed.start_candidate(
        1,
        lambda severity, builder: emitted.append(
            (severity, builder())
        ),
    )
    assert candidate is not None
    with candidate:
        candidate.offer("high", lambda: {"id": "kept"})

    assert emitted == [("high", {"id": "kept"})]
    assert candidate.closed is True
    assert completed.result().candidates_examined == 1
    assert completed.state.live_units == 0

    rejected_calls = []
    rejected = audit._DenseFamilyResources(
        family="probe",
        max_rows=10,
        work_limit=10,
        state_limit=0,
    )
    assert rejected.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=1,
        state_required=1,
    )
    candidate = rejected.start_candidate(
        1,
        lambda severity, builder: rejected_calls.append(
            (severity, builder())
        ),
    )
    assert candidate is not None
    with candidate:
        candidate.offer(
            "high",
            lambda: pytest.fail("rejected candidate was materialized"),
        )
        candidate.allocate(
            "blocked",
            1,
            lambda: pytest.fail("factory ran before reservation"),
        )
        pytest.fail("resource rejection did not unwind candidate")

    result = rejected.result()
    assert candidate.rejected is True
    assert candidate.closed is True
    assert rejected_calls == []
    assert result.candidates_examined == 0
    assert result.candidates_skipped == 1
    assert rejected.state.live_units == 0


def test_dense_candidate_builder_failure_rolls_back_shared_sink_atomically():
    collector = BoundedFindingCollector(
        ("relations",),
        cap=4,
        severity_rank={"high": 0, "low": 1},
    )
    local, emit = audit._finding_emitter(
        "relations", collector
    )
    resources = audit._DenseFamilyResources(
        family="probe",
        max_rows=10,
        work_limit=10,
        state_limit=1,
    )
    assert resources.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=1,
        state_required=1,
    )
    candidate = resources.start_candidate(1, emit)
    assert candidate is not None
    built = []

    def build(identifier, *, fail=False):
        def builder():
            built.append(identifier)
            if fail:
                raise RuntimeError("candidate builder failed")
            return {"id": identifier, "severity": "high"}

        return builder

    with pytest.raises(
        RuntimeError, match="candidate builder failed"
    ):
        with candidate:
            candidate.reserve("held", 1)
            candidate.offer("high", build("first"))
            candidate.offer(
                "high", build("second", fail=True)
            )

    result = resources.result()
    assert built == ["first", "second"]
    assert local == []
    assert collector.materialize() == {"relations": []}
    assert collector.offered == 0
    assert collector.retained == 0
    assert collector.evicted == 0
    assert collector.omitted == 0
    assert candidate.closed is True
    assert result.candidates_examined == 0
    assert result.candidates_skipped == 1
    assert resources.state.live_units == 0


def test_dense_candidate_atomic_commit_preserves_shared_cap_laziness():
    collector = BoundedFindingCollector(
        ("relations",),
        cap=1,
        severity_rank={"high": 0, "low": 1},
    )
    assert collector.offer(
        "relations",
        "low",
        lambda: {"id": "seed", "severity": "low"},
    )
    _local, emit = audit._finding_emitter(
        "relations", collector
    )
    resources = audit._DenseFamilyResources(
        family="probe",
        max_rows=10,
        work_limit=10,
        state_limit=0,
    )
    assert resources.begin(
        row_count=1,
        candidates_total=1,
        minimum_candidate_work=1,
        state_required=0,
    )
    candidate = resources.start_candidate(1, emit)
    assert candidate is not None
    built = []

    def kept_builder():
        built.append("kept")
        return {"id": "kept", "severity": "high"}

    with candidate:
        candidate.offer("high", kept_builder)
        candidate.offer(
            "low",
            lambda: pytest.fail(
                "shared-cap rejection built a payload"
            ),
        )

    assert built == ["kept"]
    assert collector.materialize() == {
        "relations": [{"id": "kept", "severity": "high"}],
    }
    assert collector.offered == 3
    assert collector.retained == 1
    assert collector.evicted == 1
    assert collector.omitted == 2
    assert resources.result().candidates_examined == 1
    assert resources.state.live_units == 0


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
        distinct_limit=100,
        column_limit=column_limit,
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


def test_fingerprint_capacity_is_checked_before_source_rows_are_touched():
    class GuardedSource:
        nrows = 40
        ncols = 2
        _text = {}

        def cell(self, row, col):
            return None

        def exact_numeric(self, row, col):
            raise AssertionError(
                "fingerprint candidate started without capacity"
            )

    budget = audit.CrossSheetSummaryBudget(
        summary_limit=10,
        grid_cell_limit=100,
        label_cell_limit=100,
        label_byte_limit=100,
        column_fingerprint_limit=0,
    )

    summary, limitations = audit.build_cross_sheet_summary(
        "wide.csv",
        "Figure 1",
        GuardedSource(),
        blocks=[(0, 40, 0, 2)],
        budget=budget,
    )

    assert summary is None
    assert limitations == []
    metadata = budget.limitation_metadata()
    assert metadata["exhausted_dimensions"] == [
        "column_fingerprints"
    ]
    assert metadata["dimensions"]["column_fingerprints"][
        "candidate_columns_skipped"
    ] == 2
    assert metadata["dimensions"]["column_fingerprints"][
        "skipped_items"
    ] == 2
