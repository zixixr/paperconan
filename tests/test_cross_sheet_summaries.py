from __future__ import annotations

import numpy as np

from paperconan import _audit as audit
from paperconan import _summaries as summaries_module
from paperconan._audit import (
    _grid_from_rows,
    build_cross_sheet_summary,
    detect_cross_sheet_column_duplicates,
)
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

    def capture_collisions(grids, profile="review", sheets=None):
        captured["labels"] = sheets
        return original_collisions(grids, profile=profile, sheets=sheets)

    def capture_columns(summaries, profile="review", min_len=12):
        captured["summaries"] = summaries
        return original_columns(summaries, profile=profile, min_len=min_len)

    within_sizes = []

    def capture_within(sheets, profile="review", min_cells=10):
        within_sizes.append(len(sheets))
        return original_within(sheets, profile=profile, min_cells=min_cells)

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
    assert len(indexes) == 1
    assert len(captured["recurring_sources"]) == 2
    assert within_sizes == [1, 1]
