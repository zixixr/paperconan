import paperconan._audit as audit
from paperconan._sheet import Sheet


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
        "column": 1,
        "rows": "2-41",
        "numeric_cells": 40,
        "limit": 7,
    }]
    assert scan["coverage"]["truncated"] is True


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
