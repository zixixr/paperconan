from __future__ import annotations

import numpy as np
import pytest

from paperconan import _audit as audit
from paperconan import _summaries as summaries_module
from paperconan._audit import (
    _grid_from_rows,
    build_cross_sheet_summary,
    detect_cross_sheet_column_duplicates,
)
from paperconan._input import InputLimitation
from paperconan._sheet import Sheet
from paperconan._summaries import (
    CrossSheetSummary,
    RecurringRowIndex,
    SparseLabelContext,
)


def _sheet(offset=0.0):
    rows = [["group", "a", "b"]]
    for i in range(14):
        rows.append([
            f"g{i}",
            offset + 1.2345 + i * 0.731 + (i % 4) * 0.137,
            offset + 4.8765 + i * 0.413 + (i % 5) * 0.211,
        ])
    return Sheet.from_rows(rows)


def test_summary_contains_no_sheet_or_ndarray():
    summary, limits = build_cross_sheet_summary(
        "a.xlsx",
        "Figure 1",
        _sheet(),
        collision_max_rows=200,
        collision_max_cells=1000,
    )
    assert limits == []
    assert not any(
        isinstance(value, (Sheet, np.ndarray))
        for value in vars(summary).values()
    )
    assert not isinstance(summary.labels.text, np.ndarray)
    assert all(
        not isinstance(value, (Sheet, np.ndarray))
        for column in summary.columns
        for value in vars(column).values()
    )


def test_column_fingerprint_path_matches_compatibility_wrapper():
    sheets = {
        ("a.xlsx", "Figure 1"): _sheet(),
        ("b.xlsx", "Figure 2"): _sheet(),
    }
    summaries = [
        build_cross_sheet_summary(
            file,
            name,
            sheet,
            collision_max_rows=200,
            collision_max_cells=1000,
        )[0]
        for (file, name), sheet in sheets.items()
    ]
    direct = detect_cross_sheet_column_duplicates(sheets)
    compact = detect_cross_sheet_column_duplicates(summaries)
    assert direct
    assert compact == direct


def test_summary_grid_and_label_context_are_bounded():
    source = Sheet.from_rows([
        [f"label-{row}", row + 0.1234, row + 0.5678]
        for row in range(8)
    ])

    summary, limits = build_cross_sheet_summary(
        "a.xlsx",
        "Figure 1",
        source,
        collision_max_rows=4,
        collision_max_cells=3,
    )

    assert list(summary.grid) == [(0, 1), (0, 2), (1, 1)]
    assert summary.labels.cell(6, 0) == "label-6"
    assert summary.labels.cell(7, 0) is None
    assert {limit.reason for limit in limits} == {
        "collision_row_limit",
        "collision_cell_limit",
    }
    cell_limit = next(
        limit for limit in limits if limit.reason == "collision_cell_limit"
    )
    assert cell_limit.details["cells_used"] == 3
    assert cell_limit.details["max_cells"] == 3


@pytest.mark.parametrize(
    ("dimension", "limit_overrides"),
    [
        ("summaries", {"summary_limit": 1}),
        ("grid_cells", {"grid_cell_limit": 28}),
        ("label_cells", {"label_cell_limit": 17}),
        ("label_bytes", {"label_byte_limit": 39}),
        ("column_fingerprints", {"column_fingerprint_limit": 2}),
    ],
)
def test_scan_summary_budget_rejects_whole_later_summary(
    dimension, limit_overrides
):
    limits = {
        "summary_limit": 100,
        "grid_cell_limit": 100_000,
        "label_cell_limit": 100_000,
        "label_byte_limit": 100_000,
        "column_fingerprint_limit": 100_000,
    }
    limits.update(limit_overrides)
    budget = audit.CrossSheetSummaryBudget(**limits)

    first, first_limitations = build_cross_sheet_summary(
        "a.xlsx",
        "Figure 1",
        _sheet(),
        budget=budget,
    )
    retained_after_first = budget.retained_metadata()
    second, second_limitations = build_cross_sheet_summary(
        "b.xlsx",
        "Figure 2",
        _sheet(),
        budget=budget,
    )

    assert first is not None
    assert first_limitations == []
    assert second is None
    assert second_limitations == []
    assert budget.retained_metadata() == retained_after_first
    metadata = budget.limitation_metadata()
    assert metadata["summaries_considered"] == 2
    assert metadata["summaries_retained"] == 1
    assert metadata["summaries_skipped"] == 1
    assert metadata["summary_pairs_unavailable"] == 1
    assert metadata["exhausted_dimensions"] == [dimension]
    exhausted = metadata["dimensions"][dimension]
    assert exhausted["retained"] <= exhausted["limit"]
    assert exhausted["skipped_sheets"] == 1
    assert exhausted["skipped_items"] >= 1


def test_scan_summary_budget_selection_is_deterministic_and_bounded():
    def run():
        budget = audit.CrossSheetSummaryBudget(
            summary_limit=2,
            grid_cell_limit=56,
            label_cell_limit=34,
            label_byte_limit=78,
            column_fingerprint_limit=4,
        )
        retained = []
        for number in range(5):
            summary, _ = build_cross_sheet_summary(
                f"{number}.xlsx",
                f"Figure {number}",
                _sheet(offset=number * 0.001),
                budget=budget,
            )
            if summary is not None:
                retained.append((summary.file, summary.sheet))
        return retained, budget.retained_metadata(), (
            budget.limitation_metadata()
        )

    first = run()
    second = run()

    assert first == second
    retained, retained_metadata, limitation = first
    assert retained == [
        ("0.xlsx", "Figure 0"),
        ("1.xlsx", "Figure 1"),
    ]
    assert retained_metadata == {
        "summaries": 2,
        "grid_cells": 56,
        "label_cells": 34,
        "label_bytes": 78,
        "column_fingerprints": 4,
    }
    assert limitation["summaries_skipped"] == 3
    assert limitation["summary_pairs_unavailable"] == 9


def test_column_fingerprint_uses_exact_values_and_preserves_wide_integer():
    wide = 10**400 + 1
    values = [
        wide,
        1.2345678901,
        4.1111111111,
        2.9876543219,
        9.2222222222,
        3.1357913579,
        8.246802468,
        6.1122334455,
        7.9988776655,
        2.468013579,
        5.975318642,
        1.864209753,
        4.753186429,
        9.642075318,
    ]
    changed = list(values)
    changed[0] = wide + 2
    source_a = Sheet.from_rows([["value"]] + [[value] for value in values])
    source_b = Sheet.from_rows([["value"]] + [[value] for value in changed])

    summary_a, _ = build_cross_sheet_summary("a.xlsx", "Figure 1", source_a)
    summary_b, _ = build_cross_sheet_summary("b.xlsx", "Figure 2", source_b)
    summary_c, _ = build_cross_sheet_summary("c.xlsx", "Figure 3", source_a)

    assert summary_a.columns[0].sample[0] == wide
    assert summary_a.columns[0].digest != summary_b.columns[0].digest
    finding = detect_cross_sheet_column_duplicates([summary_a, summary_c])[0]
    assert finding["examples"][0]["value"] == wide


def test_exact_fingerprints_do_not_merge_values_that_only_round_equal():
    values = [
        1.2345678901,
        4.1111111111,
        2.9876543219,
        9.2222222222,
        3.1357913579,
        8.246802468,
        6.1122334455,
        7.9988776655,
        2.468013579,
        5.975318642,
        1.864209753,
        4.753186429,
        9.642075318,
        3.531864297,
    ]
    changed = list(values)
    changed[4] += 1e-10
    source_a = Sheet.from_rows([["value"]] + [[value] for value in values])
    source_b = Sheet.from_rows([["value"]] + [[value] for value in changed])
    summaries = [
        build_cross_sheet_summary("a.xlsx", "Figure 1", source_a)[0],
        build_cross_sheet_summary("b.xlsx", "Figure 2", source_b)[0],
    ]

    assert summaries[0].columns[0].digest != summaries[1].columns[0].digest
    assert detect_cross_sheet_column_duplicates(summaries) == []


def test_column_fingerprint_distinct_aggregation_is_bounded_and_disclosed(
    monkeypatch,
):
    limit = 7
    maximum_sizes = []
    original = audit._BoundedDistinctValues

    class TrackingDistinctValues(original):
        def add(self, value):
            result = super().add(value)
            maximum_sizes.append(len(self.values))
            return result

    monkeypatch.setattr(
        audit, "_BoundedDistinctValues", TrackingDistinctValues
    )
    monkeypatch.setattr(
        audit, "_COLUMN_FINGERPRINT_DISTINCT_LIMIT", limit
    )
    values = [
        row + (row % 7) * 0.1234
        for row in range(40)
    ]
    source = Sheet.from_rows([["value"]] + [[value] for value in values])

    summary, limitations = build_cross_sheet_summary(
        "large.csv",
        "Figure 1",
        source,
    )

    assert summary.columns == ()
    assert maximum_sizes
    assert max(maximum_sizes) <= limit
    assert limitations == [InputLimitation(
        scope="sheet",
        reason="column_fingerprint_distinct_limit",
        sheet="Figure 1",
        details={
            "detector": "cross_sheet_column_duplicate",
            "affected_columns": 1,
            "examples": [{
                "column": 1,
                "rows": "2-41",
                "numeric_cells": 40,
            }],
            "limit": limit,
        },
    )]


def test_column_fingerprint_sheet_budget_is_fixed_exact_and_deterministic(
    monkeypatch,
):
    row_count = 40
    column_count = 250_000
    column_limit = 7
    distinct_limit = 25

    class VirtualWideSource:
        nrows = row_count
        ncols = column_count
        _text = {}

        def __init__(self):
            self.exact_numeric_calls = 0

        def cell(self, row, col):
            return self.exact_numeric(row, col)

        def exact_numeric(self, row, col):
            self.exact_numeric_calls += 1
            if col < 3:
                return float((row * 7) % 20) + col / 1000
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
        distinct_limit,
    )
    source = VirtualWideSource()
    blocks = [(0, row_count, 0, column_count)]

    first = audit._column_fingerprints(
        "wide.csv",
        "Figure 1",
        source,
        blocks,
        min_column_length=12,
    )
    second = audit._column_fingerprints(
        "wide.csv",
        "Figure 1",
        source,
        blocks,
        min_column_length=12,
    )

    assert first == second
    columns, limitations = first
    assert [column.col_idx for column in columns] == [0, 1, 2]
    assert len(columns) <= column_limit
    assert len(limitations) == 2
    assert limitations == [
        InputLimitation(
            scope="sheet",
            reason="column_fingerprint_distinct_limit",
            sheet="Figure 1",
            details={
                "detector": "cross_sheet_column_duplicate",
                "affected_columns": 4,
                "examples": [
                    {
                        "column": column,
                        "rows": "1-40",
                        "numeric_cells": 40,
                    }
                    for column in range(4, 8)
                ],
                "limit": distinct_limit,
            },
        ),
        InputLimitation(
            scope="sheet",
            reason="column_fingerprint_column_limit",
            sheet="Figure 1",
            details={
                "detector": "cross_sheet_column_duplicate",
                "columns_total": column_count,
                "columns_used": column_limit,
                "columns_skipped": column_count - column_limit,
                "limit": column_limit,
            },
        ),
    ]
    assert source.exact_numeric_calls == 2 * row_count * column_limit


def test_float_convertible_wide_integers_use_exact_qualification():
    base = 2**54
    offsets = [
        0, 17, 3, 21, 8, 14, 1, 24, 6, 19, 11, 4, 22,
        9, 16, 2, 20, 7, 13, 5, 23, 10, 18, 12, 15,
    ]
    values = [base + offset for offset in offsets]
    changed = list(values)
    changed[7] += 1
    source_a = Sheet.from_rows([["value"]] + [[value] for value in values])
    source_b = Sheet.from_rows([["value"]] + [[value] for value in values])
    source_c = Sheet.from_rows([["value"]] + [[value] for value in changed])

    summary_a, _ = build_cross_sheet_summary("a.xlsx", "Figure 1", source_a)
    summary_b, _ = build_cross_sheet_summary("b.xlsx", "Figure 2", source_b)
    summary_c, _ = build_cross_sheet_summary("c.xlsx", "Figure 3", source_c)

    assert len(summary_a.columns) == 1
    assert detect_cross_sheet_column_duplicates([summary_a, summary_b])
    assert detect_cross_sheet_column_duplicates([summary_a, summary_c]) == []


def test_recurring_index_reports_budget_exhaustion():
    source = Sheet.from_rows([
        ["a", "b", "c", "d", "e"],
        [11.25, 7.5, 19.75, 3.125, 14.5],
    ])
    index = RecurringRowIndex(budget=1)
    meta = index.add_sheet(
        "a.xlsx",
        "Figure 1",
        source,
        blocks=[(1, source.nrows, 0, source.ncols)],
        figure_id="main:1",
    )
    assert meta == {"budget_exhausted": True, "windows_skipped": 2}


def test_recurring_index_does_not_spend_budget_on_invalid_windows():
    source = Sheet.from_rows([
        ["a", "b", "c", "d", "e"],
        [11.25, 7.5, "not numeric", 3.125, 14.5],
    ])
    index = RecurringRowIndex(budget=1)
    meta = index.add_sheet(
        "a.xlsx",
        "Figure 1",
        source,
        blocks=[(1, source.nrows, 0, source.ncols)],
        figure_id="main:1",
    )
    assert meta == {"budget_exhausted": False, "windows_skipped": 0}


def test_recurring_budget_stops_window_materialization_but_counts_skips(
    monkeypatch,
):
    class CountingSource:
        nrows = 2
        ncols = 10

        def __init__(self):
            self.cell_calls = 0

        def cell(self, row, col):
            self.cell_calls += 1
            return row * 100.0 + col + 0.125

    source = CountingSource()
    materialized = []
    iterator_limits = []
    original_iterator = summaries_module._iter_valid_window_specs

    def materialize(row, start, width):
        materialized.append((start, width))
        return tuple(round(float(value), 6) for value in row[start:start + width])

    def iter_specs(run_lengths, min_k, max_k, limit):
        iterator_limits.append(limit)
        yield from original_iterator(run_lengths, min_k, max_k, limit)

    monkeypatch.setattr(
        summaries_module,
        "_materialize_window",
        materialize,
    )
    monkeypatch.setattr(
        summaries_module,
        "_iter_valid_window_specs",
        iter_specs,
    )
    index = RecurringRowIndex(budget=3)

    meta = index.add_sheet(
        "a.xlsx",
        "Figure 1",
        source,
        blocks=[(0, source.nrows, 0, source.ncols)],
        figure_id="main:1",
    )

    assert source.cell_calls == 20
    assert len(materialized) == 3
    assert iterator_limits == [3]
    assert meta == {"budget_exhausted": True, "windows_skipped": 47}


def test_recurring_unique_vector_budget_bounds_keys_and_reports_lower_bound():
    source = Sheet.from_rows([
        [11.25, 7.5, 19.75, 3.125],
        [21.25, 17.5, 29.75, 13.125],
        [31.25, 27.5, 39.75, 23.125],
    ])
    index = RecurringRowIndex(budget=100, unique_budget=1)

    index.add_sheet(
        "a.xlsx",
        "Figure 1",
        source,
        blocks=[(0, source.nrows, 0, source.ncols)],
        figure_id="main:1",
        min_k=4,
        max_k=4,
    )

    assert len(index._vectors) == 1
    assert index.unique_budget_metadata() == {
        "budget_exhausted": True,
        "limit": 1,
        "vectors_retained": 1,
        "skipped_new_vector_windows": 2,
        "skipped_new_vectors_lower_bound": 1,
    }
    record = next(iter(index._vectors.values()))
    assert not isinstance(record, dict)
    assert not hasattr(record, "vector")


def test_recurring_unique_budget_continues_updating_known_vector():
    vector = [220.0, 188.0, 122.0, 166.0, 128.0, 166.0]
    other = [311.0, 277.0, 203.0, 255.0, 199.0, 241.0]
    index = RecurringRowIndex(budget=100, unique_budget=1)

    for number, values in (
        (1, vector),
        (2, other),
        (3, vector),
        (4, vector),
    ):
        source = Sheet.from_rows([values])
        index.add_sheet(
            f"M{number}.xlsx",
            f"Figure {number}",
            source,
            blocks=[(0, 1, 0, source.ncols)],
            figure_id=f"main:{number}",
            min_k=6,
            max_k=6,
        )

    findings, _meta = index.findings()

    match = next(
        finding for finding in findings
        if finding["vector"] == vector
    )
    assert match["n_occurrences"] == 3
    assert match["n_figures"] == 3
    assert index.unique_budget_metadata()[
        "skipped_new_vector_windows"
    ] == 1


def _synthetic_recurring_record(index, vector, sites):
    files = [site[0] for site in sites]
    sheets = [site[1] for site in sites]
    record = summaries_module._RecurringVectorRecord(
        site_count=len(sites),
        file_min=min(files),
        file_max=max(files),
        sheet_min=min(sheets),
        sheet_max=max(sheets),
        sites=list(sites),
        figures={"main:1", "main:2", "main:3"},
    )
    index._vectors[tuple(vector)] = record


def _nonpattern_vector(number):
    base = 100 * number
    return (
        base + 3,
        base + 17,
        base + 8,
        base + 29,
        base + 11,
        base + 23,
    )


def test_recurring_finalization_candidate_state_is_bounded_and_deterministic():
    index = RecurringRowIndex(
        unique_budget=10,
        finalization_candidate_budget=3,
        finalization_pair_budget=100,
        finalization_cell_budget=1_000,
    )
    for number in range(10):
        _synthetic_recurring_record(
            index,
            _nonpattern_vector(number + 1),
            [
                (
                    f"f{figure}.xlsx",
                    f"Figure {figure}",
                    number,
                    0,
                )
                for figure in range(1, 4)
            ],
        )

    first_findings, first_meta = index.findings(max_findings=1)
    second_findings, second_meta = index.findings(max_findings=1)

    assert first_findings == second_findings
    assert first_meta == second_meta
    assert [finding["vector"] for finding in first_findings] == [
        list(_nonpattern_vector(1))
    ]
    assert first_meta == {
        "findings_omitted": 2,
        "findings_omitted_is_lower_bound": True,
        "finalization_limitation": {
            "candidate_limit": 3,
            "pair_limit": 100,
            "cell_limit": 1_000,
            "qualifying_candidates": 10,
            "candidates_retained": 3,
            "candidates_omitted": 7,
            "candidates_processed": 3,
            "pair_comparisons": 0,
            "cell_references_retained": 54,
            "limits_reached": ["candidate"],
            "omitted_findings_lower_bound": 2,
        },
    }


def test_recurring_finalization_pair_work_is_hard_capped():
    index = RecurringRowIndex(
        unique_budget=4,
        finalization_candidate_budget=4,
        finalization_pair_budget=2,
        finalization_cell_budget=1_000,
    )
    for number in range(4):
        _synthetic_recurring_record(
            index,
            _nonpattern_vector(number + 1),
            [
                ("common.xlsx", "Figure 1", 0, 0),
                (
                    f"unique-{number}-2.xlsx",
                    "Figure 2",
                    number,
                    20,
                ),
                (
                    f"unique-{number}-3.xlsx",
                    "Figure 3",
                    number,
                    40,
                ),
            ],
        )

    findings, meta = index.findings(max_findings=20)

    assert len(findings) == 2
    assert meta == {
        "findings_omitted": 0,
        "findings_omitted_is_lower_bound": True,
        "finalization_limitation": {
            "candidate_limit": 4,
            "pair_limit": 2,
            "cell_limit": 1_000,
            "qualifying_candidates": 4,
            "candidates_retained": 4,
            "candidates_omitted": 0,
            "candidates_processed": 2,
            "pair_comparisons": 2,
            "cell_references_retained": 36,
            "limits_reached": ["pair"],
            "omitted_findings_lower_bound": 0,
        },
    }


def test_recurring_finalization_cell_state_cap_reports_definite_omission():
    index = RecurringRowIndex(
        unique_budget=3,
        finalization_candidate_budget=3,
        finalization_pair_budget=100,
        finalization_cell_budget=20,
    )
    for number in range(3):
        _synthetic_recurring_record(
            index,
            _nonpattern_vector(number + 1),
            [
                (
                    f"f{figure}.xlsx",
                    f"Figure {figure}",
                    number,
                    0,
                )
                for figure in range(1, 4)
            ],
        )

    findings, meta = index.findings(max_findings=20)

    assert len(findings) == 1
    assert meta == {
        "findings_omitted": 1,
        "findings_omitted_is_lower_bound": True,
        "finalization_limitation": {
            "candidate_limit": 3,
            "pair_limit": 100,
            "cell_limit": 20,
            "qualifying_candidates": 3,
            "candidates_retained": 3,
            "candidates_omitted": 0,
            "candidates_processed": 1,
            "pair_comparisons": 0,
            "cell_references_retained": 18,
            "limits_reached": ["cell"],
            "omitted_findings_lower_bound": 1,
        },
    }


def test_recurring_index_skips_sheet_without_figure_namespace():
    class CountingSource:
        nrows = 1
        ncols = 6

        def __init__(self):
            self.cell_calls = 0

        def cell(self, row, col):
            self.cell_calls += 1
            return float(col) + 0.125

    source = CountingSource()
    index = RecurringRowIndex(budget=100, unique_budget=10)

    meta = index.add_sheet(
        "a.xlsx",
        "Sheet1",
        source,
        blocks=[(0, 1, 0, 6)],
        figure_id=None,
    )

    assert source.cell_calls == 0
    assert len(index._vectors) == 0
    assert meta == {"budget_exhausted": False, "windows_skipped": 0}


def test_negative_collision_row_limit_uses_zero_rows():
    source = Sheet.from_rows([
        [1.1234, 2.2345],
        [3.3456, 4.4567],
    ])

    grid, meta = _grid_from_rows(
        source,
        max_rows=-3,
        with_coverage=True,
    )

    assert grid == {}
    assert meta == {
        "rows_total": 2,
        "rows_used": 0,
        "row_limited": True,
    }


def test_patterned_vector_helper_has_single_implementation():
    assert not hasattr(audit, "_vector_is_patterned")


def test_scan_uses_compact_cross_sheet_state(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    rows = ["label,a,b,c,d,e"]
    for row in range(14):
        values = [
            row + 1.1234 + (row % 3) * 0.17,
            row + 2.2345 + (row % 4) * 0.19,
            row + 3.3456 + (row % 5) * 0.23,
            row + 4.4567 + (row % 3) * 0.29,
            row + 5.5678 + (row % 4) * 0.31,
        ]
        rows.append(
            ",".join([f"g{row}", *(str(value) for value in values)])
        )
    payload = "\n".join(rows) + "\n"
    (data / "a.csv").write_text(payload, encoding="utf-8")
    (data / "b.csv").write_text(payload, encoding="utf-8")

    captured = {}
    original_collisions = audit.detect_collisions
    original_columns = audit.detect_cross_sheet_column_duplicates
    original_within = audit.detect_within_sheet_fraction_reuse

    def capture_collisions(
        grids, profile="review", sheets=None, budget=None
    ):
        captured["labels"] = sheets
        captured["collision_budget"] = budget
        return original_collisions(
            grids,
            profile=profile,
            sheets=sheets,
            budget=budget,
        )

    def capture_columns(
        summaries, profile="review", min_len=12, budget=None
    ):
        captured["summaries"] = summaries
        captured["column_budget"] = budget
        return original_columns(
            summaries,
            profile=profile,
            min_len=min_len,
            budget=budget,
        )

    within_sizes = []

    def capture_within(
        sheets,
        profile="review",
        min_cells=10,
        *,
        with_coverage=False,
    ):
        within_sizes.append(len(sheets))
        return original_within(
            sheets,
            profile=profile,
            min_cells=min_cells,
            with_coverage=with_coverage,
        )

    indexes = []

    class TrackingIndex(RecurringRowIndex):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            indexes.append(self)

        def add_sheet(self, *args, **kwargs):
            captured.setdefault("recurring_sources", []).append(args[2])
            return super().add_sheet(*args, **kwargs)

    monkeypatch.setattr(audit, "detect_collisions", capture_collisions)
    monkeypatch.setattr(
        audit,
        "detect_cross_sheet_column_duplicates",
        capture_columns,
    )
    monkeypatch.setattr(
        audit,
        "detect_within_sheet_fraction_reuse",
        capture_within,
    )
    monkeypatch.setattr(audit, "RecurringRowIndex", TrackingIndex)

    audit.scan_dir(
        str(data),
        str(tmp_path / "out"),
        write_html=False,
        write_json=False,
    )

    assert all(
        isinstance(summary, CrossSheetSummary)
        for summary in captured["summaries"]
    )
    assert all(
        isinstance(labels, SparseLabelContext)
        for labels in captured["labels"].values()
    )
    assert isinstance(
        captured["collision_budget"], audit.CrossSheetWorkBudget
    )
    assert captured["column_budget"] is captured["collision_budget"]
    assert len(indexes) == 1
    assert len(captured["recurring_sources"]) == 2
    assert within_sizes == [1, 1]
