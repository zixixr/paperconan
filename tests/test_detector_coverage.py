import paperconan._audit as audit
import pytest
from paperconan._coverage import ScanCoverage
from paperconan._sheet import Sheet
from paperconan._summaries import RecurringRowIndex


def _limitations(scan, reason):
    return [
        item
        for item in scan["coverage"]["limitations"]
        if item["reason"] == reason
    ]


def _qualifying_row_pair_rows():
    header = [f"c{col}" for col in range(12)]
    base = [100 + col + (col + 1) / 100 for col in range(12)]
    return [
        header,
        base,
        [value + 10 for value in base],
        [value + 20 for value in base],
    ]


@pytest.mark.parametrize(
    ("detector_name", "group_name"),
    [
        ("detect_relations", "relations"),
        ("detect_equal_pairs", "equal_pairs"),
        ("detect_arithmetic_progression", "progressions"),
        ("detect_within_column_patterns", "within_col"),
        ("detect_dispersed_repeats", "within_col"),
        (
            "detect_identical_after_rounding",
            "identical_after_rounding",
        ),
    ],
)
def test_wide_integer_block_skips_affected_detector_with_one_limitation(
    monkeypatch, detector_name, group_name
):
    wide_values = [10**400, 2**53 + 1]
    sheet = Sheet.from_rows([
        ["a", "b"],
        [1.125, 1.125],
        [2.25, 2.25],
        [3.375, 3.375],
        [4.5, 4.5],
        [wide_values[0], wide_values[1]],
        [6.75, 6.75],
    ])
    called = []

    def fail_if_called(*_args, **_kwargs):
        called.append(detector_name)
        return [{
            "kind": "must_not_be_emitted",
            "severity": "high",
            "rule": detector_name,
        }]

    monkeypatch.setattr(audit, detector_name, fail_if_called)
    for name in (
        "detect_relations",
        "detect_arithmetic_progression",
        "detect_equal_pairs",
        "detect_within_column_patterns",
        "detect_dispersed_repeats",
        "detect_identical_after_rounding",
        "detect_grim_grimmer",
    ):
        if name != detector_name:
            monkeypatch.setattr(
                audit, name, lambda *_args, **_kwargs: []
            )
    monkeypatch.setattr(
        audit,
        "detect_row_pair_digit_coupling",
        lambda *_args, **_kwargs: ([], {"findings_omitted": 0}),
    )
    state = audit.ScanBudgetState(
        coverage=ScanCoverage(files_discovered=1),
        recurring_index=RecurringRowIndex(),
        profile="review",
        evidence=False,
    )

    blocks = audit._analyze_numeric_blocks(
        sheet,
        file_name="wide.csv",
        sheet_name="wide",
        blocks=[(1, sheet.nrows, 0, sheet.ncols)],
        state=state,
    )

    assert called == []
    assert all(
        finding["kind"] != "must_not_be_emitted"
        for block in blocks
        for finding in block[group_name]
    )
    assert [
        item for item in state.coverage.limitations
        if item["reason"] == "wide_integer_detector_limit"
    ] == [{
        "scope": "block",
        "reason": "wide_integer_detector_limit",
        "file": "wide.csv",
        "sheet": "wide",
        "rows": "2-7",
        "cols": "1-2",
        "affected_cells": 2,
        "detectors": [
            "relations",
            "equal_pairs",
            "row_pairs",
            "arithmetic_progression",
            "within_column",
            "dispersed_repeats",
            "identical_after_rounding",
            "grim_grimmer",
        ],
    }]


def test_wide_integer_block_counts_use_one_near_linear_sheet_sweep():
    row_count = 120
    col_count = 12
    rows = [
        [
            (
                10**100 + row * col_count + col
                if (row + col) % 2 == 0
                else row + col + 0.125
            )
            for col in range(col_count)
        ]
        for row in range(row_count)
    ]
    sheet = Sheet.from_rows(rows)
    blocks = [
        (row, row + 3, col, col + 4)
        for row in range(0, row_count, 3)
        for col in range(0, col_count, 4)
    ]
    expected = [
        sum(
            1
            for wide_row, wide_col in sheet._wide_ints
            if r0 <= wide_row < r1 and c0 <= wide_col < c1
        )
        for r0, r1, c0, c1 in blocks
    ]

    state_limit = 100_000
    counts, metadata = audit._wide_integer_counts_by_block(
        sheet,
        blocks,
        state_limit=state_limit,
        with_coverage=True,
    )

    assert counts.tolist() == expected
    assert metadata["coordinates_total"] == len(sheet._wide_ints)
    assert metadata["coordinate_visits"] <= 2 * len(sheet._wide_ints)
    assert metadata["event_cells"] == 4 * len(blocks)
    assert metadata["python_event_records"] == 0
    assert metadata["column_index_cells"] <= col_count
    assert metadata["coordinate_copy_cells"] == 0
    assert metadata["state_exhausted"] is False
    assert metadata["peak_state_units"] <= state_limit


def test_wide_integer_block_index_exhaustion_skips_before_state_grows(
    monkeypatch,
):
    sheet = Sheet.from_rows([
        [10**100 if row == 0 else row + 0.125]
        for row in range(60)
    ])
    blocks = [
        (row, row + 3, 0, 1)
        for row in range(0, 60, 3)
    ]
    counts, bounded_meta = audit._wide_integer_counts_by_block(
        sheet,
        blocks,
        state_limit=1_000,
        with_coverage=True,
    )
    assert counts.tolist() == [1] + [0] * (len(blocks) - 1)
    assert bounded_meta["state_exhausted"] is False
    assert (
        bounded_meta["peak_state_units"]
        <= bounded_meta["state_units_required"]
        <= 1_000
    )
    assert bounded_meta["python_event_records"] == 0

    called = []

    def fail_if_called(name):
        def detector(*_args, **_kwargs):
            called.append(name)
            return []
        return detector

    for name in (
        "detect_relations",
        "detect_arithmetic_progression",
        "detect_equal_pairs",
        "detect_within_column_patterns",
        "detect_dispersed_repeats",
        "detect_identical_after_rounding",
        "detect_grim_grimmer",
    ):
        monkeypatch.setattr(audit, name, fail_if_called(name))
    monkeypatch.setattr(
        audit,
        "detect_row_pair_digit_coupling",
        lambda *_args, **_kwargs: ([], {"findings_omitted": 0}),
    )
    monkeypatch.setattr(
        audit, "_DENSE_BLOCK_STATE_CELL_LIMIT", 10
    )
    state = audit.ScanBudgetState(
        coverage=ScanCoverage(files_discovered=1),
        recurring_index=RecurringRowIndex(),
        profile="review",
        evidence=False,
    )

    audit._analyze_numeric_blocks(
        sheet,
        file_name="fragmented.csv",
        sheet_name="fragmented",
        blocks=blocks,
        state=state,
    )

    assert called == []
    limitations = [
        item for item in state.coverage.limitations
        if item["reason"] == "wide_integer_block_index_limit"
    ]
    assert len(limitations) == 1
    limitation = limitations[0]
    state_required = audit._wide_integer_index_state_required(
        len(blocks), 1, ordered=True
    )
    assert limitation == {
        "scope": "sheet",
        "reason": "wide_integer_block_index_limit",
        "file": "fragmented.csv",
        "sheet": "fragmented",
        "state_unit_limit": 10,
        "state_units_required": state_required,
        "peak_state_units": 0,
        "blocks_total": len(blocks),
        "detector_blocks_skipped": len(blocks),
        "wide_integer_cells": 1,
        "affected_blocks_lower_bound": 0,
        "detectors": [
            "relations",
            "equal_pairs",
            "row_pairs",
            "arithmetic_progression",
            "within_column",
            "dispersed_repeats",
            "identical_after_rounding",
            "grim_grimmer",
        ],
    }
    assert state_required > 10
    assert state.findings_omitted_is_lower_bound is True


def test_wide_integer_state_rejection_does_not_traverse_coordinates():
    class RejectIteration(dict):
        def __iter__(self):
            raise AssertionError(
                "state rejection traversed wide-integer coordinates"
            )

    sheet = Sheet(
        1,
        1,
        audit.np.full((1, 1), audit.np.nan),
        {},
        audit.np.zeros((1, 1), dtype=audit.np.bool_),
        RejectIteration({(0, 0): 10**100}),
    )

    counts, metadata = audit._wide_integer_counts_by_block(
        sheet,
        [(0, 1, 0, 1)],
        state_limit=0,
        with_coverage=True,
    )

    assert counts is None
    assert metadata["coordinates_total"] == 1
    assert metadata["coordinate_visits"] == 0
    assert metadata["state_units_required"] == (
        audit._wide_integer_index_state_required(
            1, 1, ordered=False
        )
    )
    assert metadata["peak_state_units"] == 0
    assert metadata["state_exhausted"] is True


def test_direct_sheet_subclass_uses_conservative_wide_integer_ordering():
    class DirectSheet(Sheet):
        pass

    wide_ints = {
        (2, 1): 10**100 + 2,
        (0, 0): 10**100,
    }
    sheet = DirectSheet(
        3,
        2,
        audit.np.full((3, 2), audit.np.nan),
        {},
        audit.np.zeros((3, 2), dtype=audit.np.bool_),
        wide_ints,
    )
    blocks = [
        (0, 2, 0, 2),
        (2, 3, 0, 2),
    ]

    counts, metadata = audit._wide_integer_counts_by_block(
        sheet,
        blocks,
        state_limit=1_000,
        with_coverage=True,
    )

    assert counts.tolist() == [1, 1]
    assert metadata["state_units_required"] == (
        audit._wide_integer_index_state_required(
            len(blocks), len(wide_ints), ordered=False
        )
    )
    assert metadata["coordinate_visits"] <= 2 * len(wide_ints)
    assert metadata["coordinate_copy_cells"] == 2 * len(wide_ints)
    assert metadata["state_exhausted"] is False


def test_dense_row_limit_rejects_inside_every_real_detector(
    monkeypatch,
):
    sheet = Sheet.from_rows([
        [float(row * 10 + col) + 0.125 for col in range(4)]
        for row in range(12)
    ])
    called = []
    detector_names = (
        "detect_relations",
        "detect_equal_pairs",
        "detect_arithmetic_progression",
        "detect_within_column_patterns",
        "detect_dispersed_repeats",
        "detect_identical_after_rounding",
    )
    expected_totals = {
        "relations": 6,
        "equal_pairs": 6,
        "arithmetic_progression": 4,
        "within_column": 4,
        "dispersed_repeats": 4,
        "identical_after_rounding": 1,
    }

    for detector_name in detector_names:
        original = getattr(audit, detector_name)

        def wrapped(
            *args,
            _original=original,
            **kwargs,
        ):
            resources = kwargs["_resources"]
            called.append(resources.family)
            return _original(*args, **kwargs)

        monkeypatch.setattr(audit, detector_name, wrapped)

    def fail_source_or_allocation(*_args, **_kwargs):
        pytest.fail("row-limited detector performed source/allocation work")

    monkeypatch.setattr(
        audit, "_numeric_pair_stats", fail_source_or_allocation
    )
    monkeypatch.setattr(audit, "col_array", fail_source_or_allocation)
    monkeypatch.setattr(audit.np, "isnan", fail_source_or_allocation)
    for method_name in (
        "_reserve",
        "start_allocated_candidate",
    ):
        monkeypatch.setattr(
            audit._DenseFamilyResources,
            method_name,
            fail_source_or_allocation,
        )
    monkeypatch.setattr(
        audit, "detect_grim_grimmer", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        audit,
        "detect_row_pair_digit_coupling",
        lambda *_args, **_kwargs: ([], {"findings_omitted": 0}),
    )
    monkeypatch.setattr(
        audit, "_DENSE_BLOCK_MAX_ROWS", 8
    )
    monkeypatch.setattr(audit, "_DENSE_BLOCK_CELL_WORK_LIMIT", 1_000_000)
    monkeypatch.setattr(audit, "_DENSE_BLOCK_STATE_CELL_LIMIT", 1_000_000)
    state = audit.ScanBudgetState(
        coverage=ScanCoverage(files_discovered=1),
        recurring_index=RecurringRowIndex(),
        profile="review",
        evidence=False,
    )

    audit._analyze_numeric_blocks(
        sheet,
        file_name="large.csv",
        sheet_name="large",
        blocks=[(0, sheet.nrows, 0, sheet.ncols)],
        state=state,
    )

    assert sorted(called) == sorted(expected_totals)
    limitation = next(
        item
        for item in state.coverage.limitations
        if item["reason"] == "dense_block_detector_limit"
    )
    detectors = {
        item["family"]: item for item in limitation["detectors"]
    }
    assert set(detectors) == set(expected_totals)
    for family, candidates_total in expected_totals.items():
        result = detectors[family]
        assert result["candidates_total"] == candidates_total
        assert result["candidates_examined"] == 0
        assert result["candidates_skipped"] == candidates_total
        assert result["work_examined"] == 0
        assert result["work_skipped"] == result["work_required"]
        assert result["state_required_lower_bound"] == 0
        assert result["peak_state_units"] == 0
        assert result["limits_reached"] == ["row"]
    assert detectors["equal_pairs"]["state_required"] == 0
    assert all(
        item["state_required"] > 0
        for family, item in detectors.items()
        if family != "equal_pairs"
    )
    assert state.findings_omitted_is_lower_bound is True


def test_dense_resource_exhaustion_reports_detector_owned_counters(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    (data / "values.csv").write_text(
        "left,right\n"
        + "\n".join(
            f"{i + 0.125},{3 * (i + 0.125) + 7}"
            for i in range(40)
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "_DENSE_BLOCK_STATE_CELL_LIMIT", 1)

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )
    limitation = next(
        item for item in scan["coverage"]["limitations"]
        if item["reason"] == "dense_block_detector_limit"
    )

    relation = next(
        item for item in limitation["detectors"]
        if item["family"] == "relations"
    )
    assert relation["candidates_examined"] == 0
    assert relation["candidates_skipped"] == 1
    assert relation["state_required"] > 1
    assert relation["state_required_lower_bound"] > 1
    assert relation["peak_state_units"] <= 1
    assert relation["limits_reached"] == ["state"]
    assert scan["scan_status"] == "partial"
    assert scan["findings_omitted_is_lower_bound"] is True


def test_wide_block_detector_skip_is_disclosed(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "_MAX_BLOCK_COLS", 2)
    data = tmp_path / "data"
    data.mkdir()
    (data / "wide.csv").write_text(
        "a,b,c\n1,2,3\n2,3,4\n3,4,5\n4,5,6\n",
        encoding="utf-8",
    )
    scan = audit.scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    assert scan["scan_status"] == "partial"
    assert scan["coverage"]["blocks_skipped"] == 0
    assert _limitations(scan, "wide_block_detector_limit") == [{
        "scope": "block",
        "reason": "wide_block_detector_limit",
        "file": "wide.csv",
        "sheet": "wide",
        "rows": "2-5",
        "cols": "1-3",
        "detectors": ["relations", "equal_pairs", "row_pairs"],
        "max_cols": 2,
    }]


def test_row_pair_dimension_skip_is_disclosed(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "_ROW_PAIR_MAX_ROWS", 3)
    data = tmp_path / "data"
    data.mkdir()
    (data / "rows.csv").write_text(
        "a,b\n1.1,2.1\n2.2,3.2\n3.3,4.3\n4.4,5.4\n",
        encoding="utf-8",
    )
    scan = audit.scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    assert scan["scan_status"] == "partial"
    assert scan["coverage"]["blocks_skipped"] == 0
    assert _limitations(scan, "row_pair_dimension_limit") == [{
        "scope": "block",
        "reason": "row_pair_dimension_limit",
        "file": "rows.csv",
        "sheet": "rows",
        "rows": 4,
        "cols": 2,
        "max_rows": 3,
        "max_cols": 200,
    }]


def test_row_pair_finding_cap_is_disclosed(tmp_path, monkeypatch):
    rows = _qualifying_row_pair_rows()
    sheet = Sheet.from_rows(rows)
    all_findings = audit.detect_row_pair_digit_coupling(
        sheet, 1, 4, 0, 12, rows[0]
    )
    assert len(all_findings) == 3

    monkeypatch.setattr(audit, "_ROW_PAIR_MAX_FINDINGS_PER_BLOCK", 1)
    kept, meta = audit.detect_row_pair_digit_coupling(
        sheet, 1, 4, 0, 12, rows[0], with_coverage=True
    )
    assert kept == all_findings[:1]
    assert meta == {"findings_omitted": 2}

    data = tmp_path / "data"
    data.mkdir()
    (data / "rows.csv").write_text(
        "\n".join(",".join(str(value) for value in row) for row in rows) + "\n",
        encoding="utf-8",
    )
    scan = audit.scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    row_pair_findings = [
        finding
        for block in scan["relations_blocks"]
        for finding in block["row_pairs"]
    ]
    assert len(scan["relations_blocks"]) == 1
    assert scan["relations_blocks"][0]["findings_omitted"] == 2
    assert scan["findings_omitted"] == 2
    assert scan["scan_status"] == "partial"
    assert scan["coverage"]["blocks_skipped"] == 0
    assert len(row_pair_findings) == 1
    assert [
        (
            finding["kind"],
            finding["row_a_idx"],
            finding["row_b_idx"],
            finding["rule"],
        )
        for finding in row_pair_findings
    ] == [
        (
            finding["kind"],
            finding["row_a_idx"],
            finding["row_b_idx"],
            finding["rule"],
        )
        for finding in kept
    ]
    assert _limitations(scan, "row_pair_finding_limit") == [{
        "scope": "block",
        "reason": "row_pair_finding_limit",
        "file": "rows.csv",
        "sheet": "rows",
        "rows": "2-4",
        "cols": "1-12",
        "limit": 1,
        "omitted_findings": 2,
    }]


def test_collision_row_limit_is_disclosed(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    rows = ["a,b"] + [f"{i + 0.123},{i + 0.456}" for i in range(201)]
    (data / "rows.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    scan = audit.scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    assert scan["coverage"]["blocks_skipped"] == 0
    assert _limitations(scan, "collision_row_limit") == [{
        "scope": "sheet",
        "reason": "collision_row_limit",
        "file": "rows.csv",
        "sheet": "rows",
        "rows_total": 202,
        "rows_used": 200,
    }]


def test_column_fingerprint_distinct_limit_is_disclosed_exactly(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        audit,
        "_COLUMN_FINGERPRINT_DISTINCT_LIMIT",
        7,
        raising=False,
    )
    data = tmp_path / "data"
    data.mkdir()
    rows = ["value"] + [
        str(row + (row % 7) * 0.1234)
        for row in range(40)
    ]
    (data / "large.csv").write_text(
        "\n".join(rows) + "\n",
        encoding="utf-8",
    )

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    assert _limitations(
        scan, "column_fingerprint_distinct_limit"
    ) == [{
        "scope": "sheet",
        "reason": "column_fingerprint_distinct_limit",
        "file": "large.csv",
        "sheet": "large",
        "detector": "cross_sheet_column_duplicate",
        "affected_columns": 1,
        "examples": [{
            "column": 1,
            "rows": "2-41",
            "numeric_cells": 40,
        }],
        "limit": 7,
    }]
    assert scan["coverage"]["truncated"] is True


def test_column_fingerprint_column_limit_is_disclosed_exactly(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        audit,
        "_COLUMN_FINGERPRINT_MAX_COLUMNS",
        3,
        raising=False,
    )
    monkeypatch.setattr(
        audit,
        "_COLUMN_FINGERPRINT_DISTINCT_LIMIT",
        25,
    )
    data = tmp_path / "data"
    data.mkdir()
    rows = [",".join(f"c{column}" for column in range(8))]
    rows.extend(
        ",".join(
            str(row * row + 3 * row + column / 1000)
            for column in range(8)
        )
        for row in range(40)
    )
    (data / "wide.csv").write_text(
        "\n".join(rows) + "\n",
        encoding="utf-8",
    )

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    assert _limitations(
        scan, "column_fingerprint_distinct_limit"
    ) == [{
        "scope": "sheet",
        "reason": "column_fingerprint_distinct_limit",
        "file": "wide.csv",
        "sheet": "wide",
        "detector": "cross_sheet_column_duplicate",
        "affected_columns": 3,
        "examples": [
            {
                "column": column,
                "rows": "2-41",
                "numeric_cells": 40,
            }
            for column in range(1, 4)
        ],
        "limit": 25,
    }]
    assert _limitations(
        scan, "column_fingerprint_column_limit"
    ) == [{
        "scope": "sheet",
        "reason": "column_fingerprint_column_limit",
        "file": "wide.csv",
        "sheet": "wide",
        "detector": "cross_sheet_column_duplicate",
        "columns_total": 8,
        "columns_used": 3,
        "columns_skipped": 5,
        "limit": 3,
    }]
    assert scan["coverage"]["truncated"] is True


def test_fraction_reuse_work_limit_is_disclosed_once(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        audit, "_FRACTION_REUSE_PAIR_BUDGET", 1, raising=False
    )
    monkeypatch.setattr(
        audit, "_FRACTION_REUSE_CELL_BUDGET", 100, raising=False
    )
    data = tmp_path / "data"
    data.mkdir()
    rows = ["value"]
    for block in range(4):
        rows.extend(
            str(block * 100 + row + 0.12345)
            for row in range(10)
        )
        if block < 3:
            rows.append("")
    (data / "many.csv").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    assert _limitations(scan, "fraction_reuse_work_limit") == [{
        "scope": "sheet",
        "reason": "fraction_reuse_work_limit",
        "file": "many.csv",
        "sheet": "many",
        "pair_limit": 1,
        "cell_limit": 100,
        "pairs_examined": 1,
        "cells_examined": 10,
        "pairs_skipped": 5,
        "limits_reached": ["pair"],
    }]
    assert scan["coverage"]["truncated"] is True


def test_recurring_finalization_limit_is_disclosed_at_scan_level(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        audit,
        "_RECURRING_ROW_VECTOR_FINALIZATION_CANDIDATE_BUDGET",
        0,
        raising=False,
    )
    monkeypatch.setattr(
        audit,
        "_RECURRING_ROW_VECTOR_FINALIZATION_PAIR_BUDGET",
        100,
        raising=False,
    )
    monkeypatch.setattr(
        audit,
        "_RECURRING_ROW_VECTOR_FINALIZATION_CELL_BUDGET",
        1_000,
        raising=False,
    )
    data = tmp_path / "data"
    data.mkdir()
    for figure in range(1, 4):
        (data / f"Figure {figure}.csv").write_text(
            (
                "a,b,c,d,e,f\n"
                "203,217,208,229,211,223\n"
                f"{300 + figure},317,308,329,311,323\n"
                f"{400 + figure},417,408,429,411,423\n"
            ),
            encoding="utf-8",
        )

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    limitations = _limitations(
        scan, "recurring_row_vector_finalization_limit"
    )
    assert len(limitations) == 1
    limitation = limitations[0]
    assert limitation["scope"] == "scan"
    assert limitation["candidate_limit"] == 0
    assert limitation["pair_limit"] == 100
    assert limitation["cell_limit"] == 1_000
    assert limitation["qualifying_candidates"] >= 1
    assert limitation["candidates_retained"] == 0
    assert limitation["candidates_omitted"] == (
        limitation["qualifying_candidates"]
    )
    assert limitation["candidates_processed"] == 0
    assert limitation["pair_comparisons"] == 0
    assert limitation["cell_references_retained"] == 0
    assert limitation["limits_reached"] == ["candidate"]
    assert limitation["omitted_findings_lower_bound"] == 0
    assert scan["findings_omitted"] == 0
    assert scan["findings_omitted_is_lower_bound"] is True
    assert scan["scan_status"] == "partial"
    assert scan["coverage"]["truncated"] is True


def test_scan_wide_summary_limit_is_aggregated_and_truthful(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    payload = (
        "label,a,b\n"
        + "\n".join(
            f"g{row},{row + 1.1234},{row + 4.5678}"
            for row in range(14)
        )
        + "\n"
    )
    for number in range(3):
        (data / f"{number}.csv").write_text(
            payload, encoding="utf-8"
        )
    monkeypatch.setattr(
        audit, "_CROSS_SHEET_SUMMARY_LIMIT", 1, raising=False
    )
    monkeypatch.setattr(
        audit, "_CROSS_SHEET_GRID_CELL_LIMIT", 100_000, raising=False
    )
    monkeypatch.setattr(
        audit, "_CROSS_SHEET_LABEL_CELL_LIMIT", 100_000, raising=False
    )
    monkeypatch.setattr(
        audit, "_CROSS_SHEET_LABEL_BYTE_LIMIT", 100_000, raising=False
    )
    monkeypatch.setattr(
        audit,
        "_CROSS_SHEET_COLUMN_FINGERPRINT_LIMIT",
        100_000,
        raising=False,
    )

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    assert _limitations(
        scan, "cross_sheet_summary_count_limit"
    ) == [{
        "scope": "scan",
        "reason": "cross_sheet_summary_count_limit",
        "dimension": "summaries",
        "limit": 1,
        "retained": 1,
        "skipped_sheets": 2,
        "skipped_items": 2,
        "summary_pairs_unavailable": 3,
        "omitted_findings_lower_bound": 0,
    }]
    assert scan["cross_sheet_findings"] == []
    assert scan["findings_omitted"] == 0
    assert scan["findings_omitted_is_lower_bound"] is True
    assert scan["scan_status"] == "partial"


def test_scan_zero_recurring_budget_reports_zero_lower_bound_as_partial(
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
        audit, "_RECURRING_ROW_VECTOR_BUDGET", 0, raising=False
    )

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    assert _limitations(scan, "recurring_row_vector_budget") == [{
        "scope": "sheet",
        "reason": "recurring_row_vector_budget",
        "file": "Figure 1.csv",
        "sheet": "Figure 1",
        "windows_skipped": 0,
        "windows_skipped_is_lower_bound": True,
        "limit": 0,
    }]
    assert scan["findings_omitted"] == 0
    assert scan["findings_omitted_is_lower_bound"] is True
    assert scan["scan_status"] == "partial"
    assert scan["coverage"]["truncated"] is True


def test_scan_cross_sheet_work_limit_feeds_omission_lower_bound(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    payload = (
        "a,b,c\n"
        + "\n".join(
            f"{row + 1.1234},{row + 4.5678},{row + 8.9012}"
            for row in range(10)
        )
        + "\n"
    )
    for number in range(4):
        (data / f"{number}.csv").write_text(
            payload, encoding="utf-8"
        )
    monkeypatch.setattr(
        audit, "_CROSS_SHEET_PAIR_BUDGET", 1, raising=False
    )
    monkeypatch.setattr(
        audit, "_CROSS_SHEET_VALUE_BUDGET", 1_000_000, raising=False
    )
    monkeypatch.setattr(
        audit,
        "_CROSS_SHEET_TAIL_MATCH_BUDGET",
        1_000_000,
        raising=False,
    )
    monkeypatch.setattr(
        audit, "_CROSS_SHEET_FINDING_BUDGET", 0, raising=False
    )

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    limitations = _limitations(scan, "cross_sheet_work_limit")
    assert len(limitations) == 1
    limitation = limitations[0]
    assert limitation["scope"] == "scan"
    assert limitation["pair_limit"] == 1
    assert limitation["pairs_examined"] == 1
    assert limitation["pairs_skipped"] == 11
    assert limitation["finding_limit"] == 0
    assert limitation["findings_retained"] == 0
    assert limitation["findings_skipped"] >= 1
    assert limitation["limits_reached"] == ["pair", "finding"]
    assert scan["cross_sheet_findings"] == []
    assert scan["findings_omitted"] == limitation[
        "findings_skipped"
    ]
    assert scan["findings_omitted_is_lower_bound"] is True
    assert scan["scan_status"] == "partial"


def test_detector_helpers_expose_coverage_without_changing_default_shapes():
    grid_sheet = Sheet.from_rows([
        ["a", "b"],
        [1.123, 2.456],
        [3.123, 4.456],
    ])
    row_pair_sheet = Sheet.from_rows([
        [float(col) + 0.123 for col in range(10)],
        [float(col) + 1.456 for col in range(10)],
    ])
    header = [f"c{col}" for col in range(10)]

    grid = audit._grid_from_rows(grid_sheet)
    covered_grid, grid_meta = audit._grid_from_rows(grid_sheet, with_coverage=True)
    row_pairs = audit.detect_row_pair_digit_coupling(
        row_pair_sheet, 0, 2, 0, 10, header
    )
    covered_row_pairs, row_pair_meta = audit.detect_row_pair_digit_coupling(
        row_pair_sheet, 0, 2, 0, 10, header, with_coverage=True
    )

    assert isinstance(grid, dict)
    assert covered_grid == grid
    assert grid_meta == {"rows_total": 3, "rows_used": 3, "row_limited": False}
    assert isinstance(row_pairs, list)
    assert covered_row_pairs == row_pairs
    assert row_pair_meta == {"findings_omitted": 0}


def test_cross_sheet_budget_reports_axis_work_and_state_coverage():
    budget = audit.CrossSheetWorkBudget(
        pair_limit=10,
        value_limit=100,
        tail_match_limit=10,
        finding_limit=10,
    )

    budget.record_axis_coverage(
        available=False,
        loading_visits=8,
        grouping_visits=8,
        progression_visits=0,
        fingerprint_visits=0,
        recurrence_order_visits=0,
        recurrence_group_visits=0,
        recurrence_comparison_visits=0,
        recurrence_mark_visits=0,
        output_visits=0,
        work_skipped_lower_bound=16,
        work_skipped_is_lower_bound=True,
        state_unit_limit=512,
        peak_state_units=384,
    )
    metadata = budget.limitation_metadata()

    assert metadata["axis_context_available"] is False
    assert metadata["axis_loading_visits"] == 8
    assert metadata["axis_grouping_visits"] == 8
    assert metadata["axis_progression_visits"] == 0
    assert metadata["axis_fingerprint_visits"] == 0
    assert metadata["axis_recurrence_order_visits"] == 0
    assert metadata["axis_recurrence_group_visits"] == 0
    assert metadata["axis_recurrence_comparison_visits"] == 0
    assert metadata["axis_recurrence_mark_visits"] == 0
    assert metadata["axis_output_visits"] == 0
    assert metadata["axis_work_skipped_lower_bound"] == 16
    assert metadata["axis_work_skipped_is_lower_bound"] is True
    assert metadata["axis_state_unit_limit"] == 512
    assert metadata["axis_peak_state_units"] == 384
    assert metadata["limits_reached"] == ["axis"]


def test_cross_sheet_budget_aggregates_repeated_axis_coverage():
    budget = audit.CrossSheetWorkBudget(
        pair_limit=10,
        value_limit=100,
        tail_match_limit=10,
        finding_limit=10,
    )

    budget.record_axis_coverage(
        available=False,
        loading_visits=1,
        grouping_visits=2,
        progression_visits=3,
        fingerprint_visits=4,
        recurrence_order_visits=5,
        recurrence_group_visits=6,
        recurrence_comparison_visits=7,
        recurrence_mark_visits=8,
        output_visits=9,
        work_skipped_lower_bound=10,
        work_skipped_is_lower_bound=True,
        state_unit_limit=900,
        peak_state_units=400,
    )
    budget.record_axis_coverage(
        available=True,
        loading_visits=10,
        grouping_visits=20,
        progression_visits=30,
        fingerprint_visits=40,
        recurrence_order_visits=50,
        recurrence_group_visits=60,
        recurrence_comparison_visits=70,
        recurrence_mark_visits=80,
        output_visits=90,
        work_skipped_lower_bound=100,
        work_skipped_is_lower_bound=False,
        state_unit_limit=800,
        peak_state_units=700,
    )

    metadata = budget.limitation_metadata()
    assert metadata["axis_context_available"] is False
    assert metadata["axis_loading_visits"] == 11
    assert metadata["axis_grouping_visits"] == 22
    assert metadata["axis_progression_visits"] == 33
    assert metadata["axis_fingerprint_visits"] == 44
    assert metadata["axis_recurrence_order_visits"] == 55
    assert metadata["axis_recurrence_group_visits"] == 66
    assert metadata["axis_recurrence_comparison_visits"] == 77
    assert metadata["axis_recurrence_mark_visits"] == 88
    assert metadata["axis_output_visits"] == 99
    assert metadata["axis_work_skipped_lower_bound"] == 110
    assert metadata["axis_work_skipped_is_lower_bound"] is True
    assert metadata["axis_state_unit_limit"] == 900
    assert metadata["axis_peak_state_units"] == 700
    assert metadata["limits_reached"] == ["axis"]


def test_scan_fingerprint_capacity_rejection_reports_candidates_truthfully(
    tmp_path, monkeypatch
):
    data = tmp_path / "data"
    data.mkdir()
    payload = (
        "label,a,b\n"
        + "\n".join(
            f"g{row},{row + 1.1234},{row + 4.5678}"
            for row in range(14)
        )
        + "\n"
    )
    (data / "wide.csv").write_text(payload, encoding="utf-8")
    monkeypatch.setattr(
        audit, "_CROSS_SHEET_SUMMARY_LIMIT", 10, raising=False
    )
    monkeypatch.setattr(
        audit, "_CROSS_SHEET_GRID_CELL_LIMIT", 100_000, raising=False
    )
    monkeypatch.setattr(
        audit, "_CROSS_SHEET_LABEL_CELL_LIMIT", 100_000, raising=False
    )
    monkeypatch.setattr(
        audit, "_CROSS_SHEET_LABEL_BYTE_LIMIT", 100_000, raising=False
    )
    monkeypatch.setattr(
        audit,
        "_CROSS_SHEET_COLUMN_FINGERPRINT_LIMIT",
        0,
        raising=False,
    )

    scan = audit.scan_dir(
        str(data), str(tmp_path / "out"), write_html=False
    )

    assert _limitations(
        scan, "cross_sheet_column_fingerprint_limit"
    ) == [{
        "scope": "scan",
        "reason": "cross_sheet_column_fingerprint_limit",
        "dimension": "column_fingerprints",
        "limit": 0,
        "retained": 0,
        "skipped_sheets": 1,
        "skipped_items": 2,
        "candidate_columns_skipped": 2,
        "candidate_columns_may_qualify": True,
        "summary_pairs_unavailable": 0,
        "omitted_findings_lower_bound": 0,
    }]
    assert scan["cross_sheet_findings"] == []
    assert scan["findings_omitted"] == 0
    assert scan["findings_omitted_is_lower_bound"] is True
    assert scan["scan_status"] == "partial"
