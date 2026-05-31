"""Findings should carry a machine-readable likely_benign note where a common
innocent explanation exists, so the agent never forgets to surface it and the
HTML report can show it inline."""
from __future__ import annotations

from paperconan._audit import benign_reason, scan_dir
from paperconan import write_html_report


def test_benign_integer_step_progression_is_axis():
    r = benign_reason({"kind": "arithmetic_progression", "step": 2.0})
    assert r and "axis" in r.lower()


def test_benign_non_integer_step_progression_has_no_canned_reason():
    assert benign_reason({"kind": "arithmetic_progression", "step": 2.5}) is None


def test_benign_rounded_to_half():
    r = benign_reason({"kind": "rounded_to_half_or_int"})
    assert r and ("rounded" in r.lower() or "derived" in r.lower())


def test_benign_identical_after_rounding():
    r = benign_reason({"kind": "identical_after_rounding"})
    assert r and "precision" in r.lower()


def test_benign_cross_file_overlap_suggests_shared_cohort():
    r = benign_reason({"kind": "cross_sheet_value_overlap",
                       "same_file": False, "same_figure": False})
    assert r and "legend" in r.lower()


def test_benign_same_figure_uses_context():
    r = benign_reason({"kind": "cross_sheet_value_overlap",
                       "same_figure": True, "context": "same display item re-plot"})
    assert r == "same display item re-plot"


def _int_step_csv():
    rows = ["day,vehicle,treated"]
    for i, day in enumerate(range(2, 24, 2)):  # 2,4,...,22 -> integer-step axis
        rows.append(f"{day},{10.111 + i*1.7:.4f},{8.222 + i*1.3:.4f}")
    return "\n".join(rows) + "\n"


def test_scan_attaches_likely_benign_to_axis_progression(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    (data / "growth.csv").write_text(_int_step_csv(), encoding="utf-8")
    res = scan_dir(str(data), str(tmp_path / "out"), write_html=False)

    progressions = [f for blk in res["relations_blocks"] for f in blk.get("progressions", [])]
    assert progressions, "expected an arithmetic_progression finding from the day axis"
    assert any(f.get("likely_benign") for f in progressions), \
        "integer-step progression should carry a likely_benign note"


def test_html_renders_likely_benign_note(tmp_path):
    scan = {
        "input_dir": "/tmp/x", "n_files": 1, "n_blocks_with_findings": 1,
        "relations_blocks": [{
            "file": "f.xlsx", "sheet": "S1",
            "block": {"rows": "1-6", "cols": "1-3", "header": ["day", "a", "b"]},
            "relations": [], "equal_pairs": [], "within_col": [],
            "identical_after_rounding": [],
            "progressions": [{
                "kind": "arithmetic_progression", "col": "day", "col_idx": 0,
                "n": 6, "step": 2.0, "first": 2.0, "severity": "medium",
                "rule": "col[0] = arithmetic progression, step=2",
                "likely_benign": "an integer-step progression is usually an axis",
                "evidence": {"headers": ["day"], "col_offset": 0,
                             "highlight_cols": [0], "highlight_rows": [],
                             "rows": [{"row_idx": 1, "is_context": False, "values": [2.0]}]},
            }],
        }],
        "digit_distribution": [], "decimal_endings": [], "cross_sheet_findings": [],
    }
    out = tmp_path / "r.html"
    write_html_report(scan, str(out))
    html = out.read_text(encoding="utf-8")
    assert "integer-step progression is usually an axis" in html
