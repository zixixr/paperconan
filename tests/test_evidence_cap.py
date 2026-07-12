import paperconan._audit as audit
from paperconan._coverage import ScanCoverage
from paperconan._sheet import Sheet
from paperconan._summaries import RecurringRowIndex
from paperconan._audit import _block_evidence, scan_dir


def _grid(nr, nc):
    return Sheet.from_rows([[float(r * 1000 + c) for c in range(nc)] for r in range(nr)])


def test_small_block_untruncated():
    s = _grid(8, 5)
    ev = _block_evidence(s, 0, 8, 0, 5, ["a", "b", "c", "d", "e"], [1])
    assert "truncated" not in ev
    assert "col_indices" not in ev
    assert len(ev["rows"]) >= 8 and all(len(r["values"]) == 5 for r in ev["rows"])


def test_big_block_truncated_keeps_highlight():
    s = _grid(300, 200)
    hi = [150, 151]
    ev = _block_evidence(s, 0, 300, 0, 200, [f"h{c}" for c in range(200)], hi)
    assert ev.get("truncated") is True
    assert len(ev["rows"]) <= 52                      # <= _MAX_EV_ROWS (+1 ctx each side)
    assert all(len(r["values"]) <= 30 for r in ev["rows"])   # <= _MAX_EV_COLS
    # the highlighted columns are within the emitted window
    assert {150, 151} <= set(ev["col_indices"])
    assert len(ev["headers"]) == len(ev["rows"][0]["values"])


def test_truncated_evidence_keeps_distant_highlighted_columns(monkeypatch):
    monkeypatch.setattr(audit, "_MAX_EV_COLS", 3)
    s = _grid(5, 100)

    ev = _block_evidence(
        s,
        0,
        5,
        0,
        100,
        [f"h{c}" for c in range(100)],
        [0, 99],
    )

    assert ev["truncated"] is True
    assert ev["col_indices"] == [0, 1, 99]
    assert ev["headers"] == ["h0", "h1", "h99"]
    assert ev["rows"][0]["values"] == [0.0, 1.0, 99.0]


def test_truncated_evidence_keeps_distant_highlighted_rows(monkeypatch):
    monkeypatch.setattr(audit, "_MAX_EV_ROWS", 3)
    s = _grid(100, 3)

    ev = _block_evidence(
        s,
        0,
        100,
        0,
        3,
        ["a", "b", "c"],
        [1],
        highlight_rows=[1, 100],
    )

    assert ev["truncated"] is True
    assert [row["row_idx"] for row in ev["rows"]] == [1, 2, 100]


def test_many_highlighted_cells_use_bounded_windows_without_cross_product(
    monkeypatch,
):
    monkeypatch.setattr(audit, "_MAX_EV_ROWS", 2)
    monkeypatch.setattr(audit, "_MAX_EV_COLS", 2)
    s = _grid(12, 12)
    highlighted = [(index, index) for index in range(1, 8)]
    findings = [{
        "kind": "identical_after_rounding",
        "severity": "medium",
        "rule": "bounded highlighted cells",
        "example_cells": highlighted,
    }]

    audit._attach_evidence(
        findings,
        s,
        0,
        s.nrows,
        0,
        s.ncols,
        [f"h{index}" for index in range(s.ncols)],
    )

    evidence = findings[0]["evidence"]
    windows = evidence["windows"]
    assert windows
    assert all(len(window["rows"]) <= 2 for window in windows)
    assert all(
        len(row["values"]) <= 2
        for window in windows
        for row in window["rows"]
    )
    represented = {
        (row["row_idx"], col_index + 1)
        for window in windows
        for row in window["rows"]
        for col_index in window["col_indices"]
    }
    assert set(highlighted) <= represented
    assert sum(
        len(row["values"])
        for window in windows
        for row in window["rows"]
    ) <= len(highlighted) * 4


def test_evidence_limit_is_recorded_once_per_affected_block(monkeypatch):
    monkeypatch.setattr(audit, "_MAX_EV_ROWS", 3)
    monkeypatch.setattr(audit, "_MAX_EV_COLS", 3)
    monkeypatch.setattr(audit, "_MAX_FINDINGS_PER_BLOCK", 0)
    monkeypatch.setattr(audit, "_MAX_TOTAL_FINDINGS", 0)
    findings = [
        {
            "kind": "constant_offset",
            "severity": "high",
            "rule": "first",
            "col_a_idx": 0,
            "col_b_idx": 39,
        },
        {
            "kind": "constant_ratio",
            "severity": "medium",
            "rule": "second",
            "col_a_idx": 1,
            "col_b_idx": 38,
        },
    ]
    monkeypatch.setattr(
        audit, "detect_relations", lambda *_args, **_kwargs: findings
    )
    for name in (
        "detect_arithmetic_progression",
        "detect_equal_pairs",
        "detect_within_column_patterns",
        "detect_dispersed_repeats",
        "detect_identical_after_rounding",
        "detect_grim_grimmer",
    ):
        monkeypatch.setattr(
            audit, name, lambda *_args, **_kwargs: []
        )
    monkeypatch.setattr(
        audit,
        "detect_row_pair_digit_coupling",
        lambda *_args, **_kwargs: ([], {"findings_omitted": 0}),
    )
    monkeypatch.setattr(
        audit,
        "apply_profile_to_findings",
        lambda *_args, **_kwargs: None,
    )
    coverage = ScanCoverage(files_discovered=1)
    state = audit.ScanBudgetState(
        coverage=coverage,
        recurring_index=RecurringRowIndex(),
        profile="review",
        evidence=True,
    )
    sheet = _grid(8, 40)

    blocks = audit._analyze_numeric_blocks(
        sheet,
        file_name="wide.csv",
        sheet_name="wide",
        blocks=[(0, 8, 0, 40)],
        state=state,
    )

    assert len(blocks) == 1
    limitations = [
        item for item in coverage.limitations
        if item["reason"] == "evidence_limit"
    ]
    assert limitations == [{
        "scope": "block",
        "reason": "evidence_limit",
        "file": "wide.csv",
        "sheet": "wide",
        "rows": "1-8",
        "cols": "1-40",
        "max_rows": 3,
        "max_cols": 3,
    }]
    assert all(
        finding["evidence"]["truncated"] is True
        for finding in findings
    )


def test_write_json_false_skips_file(tmp_path):
    from tests.build_fixture import build as build_tiny

    ind = tmp_path / "in"; out = tmp_path / "out"; ind.mkdir()
    build_tiny(str(ind))
    res = scan_dir(str(ind), str(out), write_md=False, write_html=False, write_json=False)
    assert res is not None and "relations_blocks" in res
    assert not (out / "scan.json").exists()
    # default writes it
    out2 = tmp_path / "out2"
    scan_dir(str(ind), str(out2), write_md=False, write_html=False)
    assert (out2 / "scan.json").exists()
