import paperconan._audit as audit
from paperconan._sheet import Sheet


def _reasons(scan):
    return {item["reason"] for item in scan["coverage"]["limitations"]}


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
    assert "wide_block_detector_limit" in _reasons(scan)


def test_row_pair_dimension_skip_is_disclosed(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "_ROW_PAIR_MAX_ROWS", 3)
    data = tmp_path / "data"
    data.mkdir()
    (data / "rows.csv").write_text(
        "a,b\n1.1,2.1\n2.2,3.2\n3.3,4.3\n4.4,5.4\n",
        encoding="utf-8",
    )
    scan = audit.scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert scan["coverage"]["blocks_skipped"] == 0
    assert "row_pair_dimension_limit" in _reasons(scan)


def test_row_pair_finding_cap_is_disclosed(tmp_path, monkeypatch):
    original = audit.detect_row_pair_digit_coupling

    def capped(*args, **kwargs):
        if kwargs.get("with_coverage"):
            return (
                [{
                    "kind": "row_pair_digit_coupling",
                    "severity": "high",
                    "rule": "synthetic capped row pair",
                    "n": 3,
                    "row_a_idx": 1,
                    "row_b_idx": 2,
                    "example_cells": [],
                }],
                {"findings_omitted": 3},
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(audit, "detect_row_pair_digit_coupling", capped)
    data = tmp_path / "data"
    data.mkdir()
    (data / "rows.csv").write_text(
        "a,b,c\n1.1,2.1,3.1\n2.2,3.2,4.2\n3.3,4.3,5.3\n",
        encoding="utf-8",
    )
    scan = audit.scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    assert scan["coverage"]["blocks_skipped"] == 0
    assert "row_pair_finding_limit" in _reasons(scan)


def test_collision_row_limit_is_disclosed(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    rows = ["a,b"] + [f"{i + 0.123},{i + 0.456}" for i in range(201)]
    (data / "rows.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    scan = audit.scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    limits = [
        item for item in scan["coverage"]["limitations"]
        if item["reason"] == "collision_row_limit"
    ]
    assert scan["coverage"]["blocks_skipped"] == 0
    assert limits == [{
        "scope": "sheet",
        "reason": "collision_row_limit",
        "file": "rows.csv",
        "sheet": "rows",
        "rows_total": 202,
        "rows_used": 200,
    }]


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
