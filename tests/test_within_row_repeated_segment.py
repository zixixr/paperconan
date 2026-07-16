"""Within-row repeated-segment statistical-signal coverage."""
from __future__ import annotations

import paperconan._audit as audit
import paperconan._summaries as summaries
from paperconan import scan_dir
from paperconan._audit import detect_recurring_row_vectors
from paperconan._sheet import Sheet
from paperconan._summaries import WithinRowRepeatIndex

SEG = [3.238866, 1.724138, 3.418803, 0.727273, 2.380952]
WIDE_SEG = [
    coefficient * 2**53 + offset
    for coefficient, offset in (
        (1, 0),
        (3, 17),
        (2, 3),
        (5, 21),
        (4, 8),
    )
]


def _fill(n, seed):
    return [11.0 + ((k * 13 + seed) % 71) + ((k * 7919 + seed * 104729) % 1000000) / 1000003.7
            for k in range(n)]


def _row_sheet(row, name="Supplemental Figure 2"):
    # >=2 data rows so find_numeric_blocks forms a block; the repeat lives in ONE of them.
    n = len(row)
    return Sheet.from_rows([
        [name] + [f"c{i}" for i in range(n)],
        ["Veh", *_fill(n, 3)],
        ["CNO", *row],
        ["X", *_fill(n, 7)],
    ])


def test_detects_within_row_repeated_segment():
    # The same segment appears at two non-overlapping positions.
    row = [1.785714, *SEG, 5.714286, *SEG]
    findings = detect_recurring_row_vectors({
        ("synthetic.xlsx", "Supplemental Figure 2"):
            _row_sheet(row)
    })
    wr = [f for f in findings if f["kind"] == "within_row_repeated_segment"]
    assert len(wr) == 1, f"expected one within-row repeated segment, got {findings}"
    assert wr[0]["severity"] == "high"


def test_no_false_positive_on_non_repeating_row():
    row = [1.785714, *SEG, 5.714286, 9.111111, 8.222222, 7.333333, 6.444444, 5.555556]
    assert not [f for f in detect_recurring_row_vectors(
        {("f.xlsx", "Fig 1"): _row_sheet(row)}) if f["kind"] == "within_row_repeated_segment"]


def test_no_false_positive_on_overlapping_window():
    # A run like [x, x, x, x, x] repeats overlapping windows but is a single constant block,
    # not two non-overlapping copies; and it is low-information (patterned). Must not fire.
    row = [2.5] * 10
    assert not [f for f in detect_recurring_row_vectors(
        {("f.xlsx", "Fig 1"): _row_sheet(row)}) if f["kind"] == "within_row_repeated_segment"]


def test_no_false_positive_on_short_or_low_info_repeat():
    # A 3-value low-info repeat (below min_k / patterned) must not fire.
    row = [1.0, 2.0, 1.0, 2.0, 1.0, 2.0]
    assert not [f for f in detect_recurring_row_vectors(
        {("f.xlsx", "Fig 1"): _row_sheet(row)}) if f["kind"] == "within_row_repeated_segment"]


def test_no_false_positive_on_quantized_grid_row():
    # A small quantized value pool can repeat a tuple without a localized segment recurrence.
    a, b, c, d = 110.5263, 94.73684, 105.2632, 89.47368
    sp = 999.1111                                       # distinct spacer, breaks up the tuple
    row = [a, b, c, d] + [a, sp, b, sp, c, sp, d, sp] * 3 + [a, b, c, d]  # a,b,c,d each 5x
    assert not [f for f in detect_recurring_row_vectors(
        {("synthetic.xlsx", "Fig.11"): _row_sheet(row)})
        if f["kind"] == "within_row_repeated_segment"]


def test_no_false_positive_on_small_magnitude_quantized_pool():
    # Same quantized-pool structure but at ~1e-4 magnitude (molar concentrations / proportions):
    # the frequency bucket must use the SAME quantization as the window key, or the lookup
    # misses and the gate leaks (review I2).
    a, b, c, d = 0.0001105263, 0.00009473684, 0.0001052632, 0.00008947368
    sp = 0.0009991111
    row = [a, b, c, d] + [a, sp, b, sp, c, sp, d, sp] * 3 + [a, b, c, d]  # a,b,c,d each 5x
    assert not [f for f in detect_recurring_row_vectors(
        {("f.xlsx", "Fig 1"): _row_sheet(row)})
        if f["kind"] == "within_row_repeated_segment"]


def test_no_signal_on_three_or_four_use_low_cardinality_pool():
    base = [0.125137, 0.250271, 0.375409, 0.500557]

    for scale in (1.0, 0.0001):
        values = [value * scale for value in base]
        for copies in (3, 4):
            row = values * copies
            findings = detect_recurring_row_vectors({
                ("pool.csv", "Figure 1"): _row_sheet(row)
            })
            assert not [
                finding for finding in findings
                if finding["kind"]
                == "within_row_repeated_segment"
            ]


def test_genuine_repeat_kept_despite_a_few_incidental_extras():
    # Incidental extra values elsewhere in the row do not erase a localized repeat.
    seg = [1.724138, 3.418803, 3.238866, 0.727273, 2.380952]
    row = [*seg, 9.111111, 3.238866, 8.222222, 3.238866, 7.333333, *seg]  # 3.238866 freq = 4
    wr = [f for f in detect_recurring_row_vectors({("f.xlsx", "Fig 1"): _row_sheet(row)})
          if f["kind"] == "within_row_repeated_segment"]
    assert len(wr) == 1, f"genuine repeat wrongly suppressed: {wr}"


def test_within_row_index_preserves_wide_integer_identity():
    row = [*WIDE_SEG, 17, *WIDE_SEG]
    sheet = Sheet.from_rows([row])
    index = WithinRowRepeatIndex()

    metadata = index.add_sheet(
        "wide.xlsx",
        "Figure 1",
        sheet,
        figure_id="main:1",
    )
    findings, finalization = index.findings()

    assert metadata["windows_examined"] > 0
    assert finalization == {
        "candidate_findings_omitted": 0,
        "output_findings_omitted": 0,
    }
    match = next(
        finding for finding in findings
        if finding["kind"] == "within_row_repeated_segment"
    )
    assert match["vector"] == WIDE_SEG
    assert all(isinstance(value, int) for value in match["vector"])
    assert [
        example["value"] for example in match["examples"]
    ] == WIDE_SEG
    assert match["row"] == 1
    assert match["start_cols"] == [1, 7]
    assert "columns 1, 7" in match["rule"]


def test_within_row_index_reports_physical_columns_across_text_gaps():
    segment = [1.123451, 2.234562, 4.456784, 3.345673]
    row = [
        segment[0], "left", *segment[1:],
        99.125,
        segment[0], "right", *segment[1:],
    ]
    index = WithinRowRepeatIndex()

    index.add_sheet(
        "gaps.csv",
        "Data",
        Sheet.from_rows([row]),
        figure_id=None,
        min_k=4,
        max_k=4,
    )
    findings, _metadata = index.findings()

    match = next(
        finding for finding in findings
        if finding["vector"] == segment
    )
    assert match["row"] == 1
    assert match["start_cols"] == [1, 7]


def test_within_row_index_bounds_unique_window_state():
    sheet = Sheet.from_rows([[
        1.125, 2.375, 3.625, 4.875, 5.125,
        6.375, 7.625, 8.875, 9.125, 10.375,
    ]])
    index = WithinRowRepeatIndex(
        budget=100,
        unique_budget=1,
        candidate_budget=10,
        row_cell_limit=100,
    )

    metadata = index.add_sheet(
        "unique.csv",
        "Data",
        sheet,
        figure_id=None,
    )

    assert metadata["unique_budget_exhausted"] is True
    assert metadata["unique_limit"] == 1
    assert metadata["max_unique_vectors_retained"] == 1
    assert metadata["skipped_new_windows"] > 0


def test_within_row_index_updates_known_vector_after_unique_limit():
    sheet = Sheet.from_rows([[
        *SEG, 99.125, *SEG,
    ]])
    index = WithinRowRepeatIndex(
        unique_budget=1,
        candidate_budget=10,
    )

    metadata = index.add_sheet(
        "known.csv",
        "Data",
        sheet,
        figure_id=None,
        min_k=5,
        max_k=5,
    )
    findings, finalization = index.findings()

    assert metadata["unique_budget_exhausted"] is True
    assert finalization["candidate_findings_omitted"] == 0
    assert any(
        finding["vector"] == SEG
        for finding in findings
    )


def test_within_row_index_bounds_row_cell_state():
    sheet = Sheet.from_rows([[
        *SEG, 99.125, *SEG,
    ]])
    index = WithinRowRepeatIndex(row_cell_limit=5)

    metadata = index.add_sheet(
        "wide.csv",
        "Data",
        sheet,
        figure_id=None,
    )
    findings, _finalization = index.findings()

    assert metadata["row_cell_limit_exhausted"] is True
    assert metadata["row_cell_limit"] == 5
    assert metadata["rows_limited"] == 1
    assert metadata["numeric_cells_skipped_lower_bound"] == 1
    assert findings == []


def test_truncated_row_does_not_emit_prefix_only_signal():
    suffix = []
    for index, value in enumerate(SEG):
        suffix.extend([
            value,
            100.125 + index,
            value,
            200.375 + index,
            value,
        ])
    row = [*SEG, 99.125, *SEG, *suffix]

    complete = WithinRowRepeatIndex(row_cell_limit=len(row) + 1)
    complete.add_sheet(
        "complete.csv",
        "Data",
        Sheet.from_rows([row]),
        figure_id=None,
    )
    complete_findings, _complete_metadata = complete.findings()
    assert complete_findings == []

    limited = WithinRowRepeatIndex(row_cell_limit=11)
    metadata = limited.add_sheet(
        "limited.csv",
        "Data",
        Sheet.from_rows([row]),
        figure_id=None,
    )
    findings, _finalization = limited.findings()

    assert metadata["row_cell_limit_exhausted"] is True
    assert metadata["windows_examined"] == 0
    assert findings == []


def test_zero_work_budget_ignores_rows_without_candidate_window():
    index = WithinRowRepeatIndex(budget=0)

    metadata = index.add_sheet(
        "short.csv",
        "Data",
        Sheet.from_rows([[1.125, 2.375, 3.625]]),
        figure_id=None,
    )

    assert metadata["budget_exhausted"] is False
    assert metadata["windows_skipped_is_lower_bound"] is False


def test_zero_candidate_budget_skips_overlap_finalization(
    monkeypatch,
):
    def fail_overlap_finalization(*_args, **_kwargs):
        raise AssertionError(
            "zero candidate budget entered overlap finalization"
        )

    monkeypatch.setattr(
        summaries,
        "_iter_indexed_candidate_ids",
        fail_overlap_finalization,
    )
    index = WithinRowRepeatIndex(candidate_budget=0)

    index.add_sheet(
        "bounded.csv",
        "Data",
        Sheet.from_rows([[*SEG, 99.125, *SEG]]),
        figure_id=None,
    )
    findings, metadata = index.findings()

    assert findings == []
    assert metadata["candidate_limit"] == 0
    assert metadata["candidates_retained"] == 0


def test_candidate_limit_precedes_candidate_object_allocation(
    monkeypatch,
):
    real_candidate = summaries._WithinRowCandidate
    allocations = []

    def tracking_candidate(*args, **kwargs):
        allocations.append(kwargs["row_idx"])
        return real_candidate(*args, **kwargs)

    monkeypatch.setattr(
        summaries,
        "_WithinRowCandidate",
        tracking_candidate,
    )
    other = [8.238866, 6.724138, 9.418803, 4.727273, 7.380952]
    index = WithinRowRepeatIndex(candidate_budget=1)

    index.add_sheet(
        "allocation.csv",
        "Data",
        Sheet.from_rows([
            [*SEG, 99.125, *SEG],
            [*other, 88.375, *other],
        ]),
        figure_id=None,
    )
    findings, metadata = index.findings()

    assert len(findings) == 1
    assert allocations == [0]
    assert metadata["candidate_findings_omitted"] == 1


def test_within_row_index_bounds_overlap_pair_work():
    index = WithinRowRepeatIndex(
        candidate_budget=10,
        finalization_pair_budget=0,
        finalization_cell_budget=100,
    )

    index.add_sheet(
        "pairs.csv",
        "Data",
        Sheet.from_rows([[*SEG, 99.125, *SEG]]),
        figure_id=None,
    )
    findings, metadata = index.findings()

    assert len(findings) == 1
    assert metadata[
        "candidate_findings_omitted_is_lower_bound"
    ] is True
    limitation = metadata["finalization_limitation"]
    assert limitation["limits_reached"] == ["pair"]
    assert limitation["pair_limit"] == 0
    assert limitation["pair_comparisons"] == 0


def test_within_row_index_bounds_finalization_cell_state():
    index = WithinRowRepeatIndex(
        candidate_budget=10,
        finalization_pair_budget=100,
        finalization_cell_budget=0,
    )

    index.add_sheet(
        "cells.csv",
        "Data",
        Sheet.from_rows([[*SEG, 99.125, *SEG]]),
        figure_id=None,
    )
    findings, metadata = index.findings()

    assert findings == []
    assert metadata[
        "candidate_findings_omitted_is_lower_bound"
    ] is True
    limitation = metadata["finalization_limitation"]
    assert limitation["limits_reached"] == ["cell"]
    assert limitation["cell_limit"] == 0
    assert limitation["cell_references_retained"] == 0


def test_within_row_index_bounds_candidate_state():
    other = [8.238866, 6.724138, 9.418803, 4.727273, 7.380952]
    sheet = Sheet.from_rows([
        [*SEG, 99.125, *SEG],
        [*other, 88.375, *other],
    ])
    index = WithinRowRepeatIndex(candidate_budget=1)

    index.add_sheet(
        "candidates.csv",
        "Data",
        sheet,
        figure_id=None,
    )
    findings, metadata = index.findings(max_findings=20)

    assert len(findings) == 1
    assert metadata == {
        "candidate_findings_omitted": 1,
        "output_findings_omitted": 0,
        "candidate_limit": 1,
        "candidates_seen": 2,
        "candidates_retained": 1,
        "candidate_findings_omitted_is_lower_bound": True,
    }


def test_within_row_index_reports_output_limit_separately():
    other = [8.238866, 6.724138, 9.418803, 4.727273, 7.380952]
    sheet = Sheet.from_rows([
        [*SEG, 99.125, *SEG],
        [*other, 88.375, *other],
    ])
    index = WithinRowRepeatIndex(candidate_budget=10)

    index.add_sheet(
        "output.csv",
        "Data",
        sheet,
        figure_id=None,
    )
    findings, metadata = index.findings(max_findings=1)

    assert len(findings) == 1
    assert metadata == {
        "candidate_findings_omitted": 0,
        "output_findings_omitted": 1,
        "output_limit": 1,
        "candidates_retained": 2,
    }


def test_scan_discloses_within_row_work_budget(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    row = [1.785714, *SEG, 5.714286, *SEG]
    (data / "bounded.csv").write_text(
        ",".join(str(value) for value in row) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        audit,
        "_WITHIN_ROW_REPEATED_SEGMENT_BUDGET",
        0,
        raising=False,
    )

    scan = scan_dir(
        str(data),
        str(tmp_path / "out"),
        write_html=False,
    )

    assert [
        item
        for item in scan["coverage"]["limitations"]
        if item["reason"] == "within_row_repeated_segment_budget"
    ] == [{
        "scope": "sheet",
        "reason": "within_row_repeated_segment_budget",
        "file": "bounded.csv",
        "sheet": "bounded",
        "limit": 0,
        "windows_skipped": 0,
        "windows_skipped_is_lower_bound": True,
    }]
    assert scan["scan_status"] == "partial"
    assert scan["findings_omitted_is_lower_bound"] is True


def test_scan_counts_within_row_candidate_omissions(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    other = [8.238866, 6.724138, 9.418803, 4.727273, 7.380952]
    rows = [
        [*SEG, 99.125, *SEG],
        [*other, 88.375, *other],
        [value + 20.125 for value in range(11)],
    ]
    (data / "candidates.csv").write_text(
        "\n".join(
            ",".join(str(value) for value in row)
            for row in rows
        ) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        audit,
        "_WITHIN_ROW_REPEATED_SEGMENT_CANDIDATE_BUDGET",
        1,
        raising=False,
    )

    scan = scan_dir(
        str(data),
        str(tmp_path / "out"),
        write_html=False,
    )

    assert [
        item
        for item in scan["coverage"]["limitations"]
        if item["reason"]
        == "within_row_repeated_segment_candidate_limit"
    ] == [{
        "scope": "scan",
        "reason": "within_row_repeated_segment_candidate_limit",
        "limit": 1,
        "candidates_seen": 2,
        "candidates_retained": 1,
        "candidate_findings_omitted": 1,
        "omitted_findings": 1,
        "candidate_findings_omitted_is_lower_bound": True,
    }]
    assert scan["findings_omitted"] == 1
    assert scan["findings_omitted_is_lower_bound"] is True


def test_scan_discloses_within_row_finalization_pair_limit(
    tmp_path,
    monkeypatch,
):
    data = tmp_path / "data"
    data.mkdir()
    (data / "pairs.csv").write_text(
        ",".join(str(value) for value in [*SEG, 99.125, *SEG])
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        audit,
        "_WITHIN_ROW_REPEATED_SEGMENT_FINALIZATION_PAIR_BUDGET",
        0,
        raising=False,
    )

    scan = scan_dir(
        str(data),
        str(tmp_path / "out"),
        write_html=False,
    )

    limitations = [
        item
        for item in scan["coverage"]["limitations"]
        if item["reason"]
        == "within_row_repeated_segment_finalization_limit"
    ]
    assert len(limitations) == 1
    assert limitations[0]["pair_limit"] == 0
    assert limitations[0]["limits_reached"] == ["pair"]
    assert limitations[0]["pair_comparisons"] == 0
    assert scan["findings_omitted_is_lower_bound"] is True


def test_scan_discloses_within_row_cell_limit(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    row = [*SEG, 99.125, *SEG]
    (data / "wide.csv").write_text(
        ",".join(str(value) for value in row) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        audit,
        "_WITHIN_ROW_REPEATED_SEGMENT_ROW_CELL_LIMIT",
        5,
        raising=False,
    )

    scan = scan_dir(
        str(data),
        str(tmp_path / "out"),
        write_html=False,
    )

    assert [
        item
        for item in scan["coverage"]["limitations"]
        if item["reason"]
        == "within_row_repeated_segment_row_cell_limit"
    ] == [{
        "scope": "sheet",
        "reason": "within_row_repeated_segment_row_cell_limit",
        "file": "wide.csv",
        "sheet": "wide",
        "limit": 5,
        "rows_limited": 1,
        "numeric_cells_skipped_lower_bound": 1,
        "omitted_findings_lower_bound": 0,
    }]
    assert scan["findings_omitted"] == 0
    assert scan["findings_omitted_is_lower_bound"] is True


def test_single_row_repeat_prevents_failed_scan_status(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    row = [*SEG, 99.125, *SEG]
    (data / "single.csv").write_text(
        ",".join(str(value) for value in row) + "\n",
        encoding="utf-8",
    )

    scan = scan_dir(
        str(data),
        str(tmp_path / "out"),
        write_html=False,
    )

    assert scan["scan_status"] == "partial"
    assert any(
        limitation["reason"] == "no_qualifying_numeric_block"
        for limitation in scan["coverage"]["limitations"]
    )
    assert any(
        finding["kind"] == "within_row_repeated_segment"
        for finding in scan["cross_sheet_findings"]
    )


def test_scan_dir_surfaces_within_row_repeated_segment(tmp_path):
    row = [1.785714, *SEG, 5.714286, *SEG]
    data = tmp_path / "data"
    data.mkdir()
    lines = ["Veh," + ",".join(str(v) for v in _fill(len(row), 3)),
             "CNO," + ",".join(str(v) for v in row),
             "X," + ",".join(str(v) for v in _fill(len(row), 7))]
    (data / "s.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    res = scan_dir(str(data), str(tmp_path / "out"), write_html=True)
    assert [f for f in res.get("cross_sheet_findings", []) or []
            if f.get("kind") == "within_row_repeated_segment"]


def test_shared_family_cap_prioritizes_recurring_member(
    tmp_path,
    monkeypatch,
):
    data = tmp_path / "data"
    data.mkdir()
    recurring = [220, 188, 122, 166, 128, 166]
    for figure in range(1, 4):
        rows = [
            [*recurring, *[
                figure * 100 + offset + 0.125
                for offset in range(5)
            ]],
            (
                [*SEG, 99.125, *SEG]
                if figure == 1
                else [
                    figure * 1000 + offset + 0.375
                    for offset in range(11)
                ]
            ),
            [
                figure * 2000 + offset + 0.625
                for offset in range(11)
            ],
        ]
        (data / f"Figure {figure}.csv").write_text(
            ",".join(f"c{index}" for index in range(11))
            + "\n"
            + "\n".join(
                ",".join(str(value) for value in row)
                for row in rows
            )
            + "\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(
        audit,
        "_RECURRING_ROW_VECTOR_MAX_FINDINGS",
        1,
        raising=False,
    )

    scan = scan_dir(
        str(data),
        str(tmp_path / "out"),
        write_html=False,
    )

    family = [
        finding["kind"]
        for finding in scan["cross_sheet_findings"]
        if finding["kind"] in {
            "recurring_row_vector",
            "within_row_repeated_segment",
        }
    ]
    assert family == ["recurring_row_vector"]
    assert [
        item
        for item in scan["coverage"]["limitations"]
        if item["reason"]
        == "within_row_repeated_segment_finding_limit"
    ] == [{
        "scope": "scan",
        "reason": "within_row_repeated_segment_finding_limit",
        "limit": 0,
        "output_findings_omitted": 1,
        "omitted_findings": 1,
    }]
