from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from paperconan import scan_dir, write_html_report
from paperconan._audit import detect_collisions
from paperconan.schema import PaperconanInputError


def _csv(rows):
    return "\n".join(rows) + "\n"


def _all_block_findings(scan):
    groups = ("relations", "progressions", "equal_pairs",
              "within_col", "identical_after_rounding", "grim")
    for blk in scan.get("relations_blocks") or []:
        for group in groups:
            yield from blk.get(group, []) or []


def test_scan_dir_empty_input_raises_library_error(tmp_path):
    with pytest.raises(PaperconanInputError):
        scan_dir(str(tmp_path), str(tmp_path / "out"), write_html=False)


def test_review_profile_demotes_boundary_value_duplication(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    rows = ["gene,pvalue,logFC"]
    for i in range(12):
        p = 1.0 if i < 9 else round(0.01 + i * 0.003, 4)
        rows.append(f"g{i},{p},{round(-2 + i * 0.31, 4)}")
    (data / "omics.csv").write_text(_csv(rows), encoding="utf-8")

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    dupes = [f for f in _all_block_findings(scan)
             if f["kind"] == "within_col_value_duplication"]

    assert dupes, "expected zero/one-heavy omics duplication finding"
    assert any("censoring_or_boundary_value" in f.get("false_positive_context", [])
               and f["severity"] == "low"
               and f["profile_action"] == "demoted"
               for f in dupes)


def test_forensic_profile_keeps_original_boundary_value_high(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    rows = ["gene,pvalue,logFC"]
    for i in range(12):
        p = 1.0 if i < 9 else round(0.01 + i * 0.003, 4)
        rows.append(f"g{i},{p},{round(-2 + i * 0.31, 4)}")
    (data / "omics.csv").write_text(_csv(rows), encoding="utf-8")

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False,
                    profile="forensic")
    dupes = [f for f in _all_block_findings(scan)
             if f["kind"] == "within_col_value_duplication"]

    assert dupes
    assert all(f["profile_action"] == "kept" for f in dupes)
    assert any(f["severity"] == "high" for f in dupes)


def test_review_profile_demotes_unit_conversion_relation(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    rows = ["sample,ng,ug"]
    for i in range(8):
        ng = 1000 + i * 125.0
        rows.append(f"s{i},{ng},{ng / 1000}")
    (data / "units.csv").write_text(_csv(rows), encoding="utf-8")

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    relations = [f for f in _all_block_findings(scan)
                 if f["kind"] in {"constant_ratio", "exact_linear"}]

    assert relations
    assert any("derived_or_unit_conversion" in f.get("false_positive_context", [])
               and f["severity"] == "low"
               and f["profile_action"] == "demoted"
               for f in relations)


def test_review_profile_demotes_a_summary_statistic_pair_stored_at_seven_figures(tmp_path):
    """SEM = SD / sqrt(n): exactly proportional, and benign for a textbook reason.

    The ratio arm reaches this pair only at a tolerance loose enough for values stored at
    a finite number of significant figures, which is the ordinary case for an exported
    table. Nothing about the numbers distinguishes it from a column copied and scaled --
    the labels do, and this pins that the profile still acts on them once the arm reports
    the pair directly instead of as a line through the origin.
    """
    data = tmp_path / "d"
    data.mkdir()
    sd = [4.183627, 9.520418, 2.746085, 7.318294, 5.902471, 8.164039,
          3.475912, 6.038756, 1.927463, 9.114208]
    root_n = 6.0 ** 0.5
    rows = ["group,SD,SEM"]
    for i, v in enumerate(sd):
        rows.append(f"g{i},{v!r},{float(f'{v / root_n:.7g}')!r}")
    (data / "summary.csv").write_text(_csv(rows), encoding="utf-8")

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    ratios = [f for f in _all_block_findings(scan) if f["kind"] == "constant_ratio"]

    assert ratios, "seven stored figures must not put an exact ratio out of reach"
    assert all(f["severity"] == "low" and f["profile_action"] == "demoted"
               for f in ratios), \
        "a labelled summary-statistic pair must not reach a report at high severity"


def test_review_profile_demotes_sparse_counts_against_their_normalised_twin(tmp_path):
    """A count column beside its library-size-normalised twin, sharing a zero support.

    The ratio arm reaches this pair only because a row that is zero on BOTH sides is
    dropped as uninformative rather than voiding the pair -- before that it was reported,
    if at all, as a line through the origin. It is exactly proportional for an ordinary
    reason, so what keeps it out of the way is the profile, not the detector: the labels
    say what the second column is, and the finding must arrive demoted and carrying that
    explanation rather than as a high-severity relation.
    """
    data = tmp_path / "d"
    data.mkdir()
    counts = [0.0, 41.0, 0.0, 7.0, 128.0, 0.0, 19.0, 63.0, 0.0, 204.0]
    per_million = 1e6 / sum(counts)
    rows = ["gene,Read count,Normalized count"]
    for i, c in enumerate(counts):
        rows.append(f"g{i},{c!r},{c * per_million!r}")
    (data / "counts.csv").write_text(_csv(rows), encoding="utf-8")

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    ratios = [f for f in _all_block_findings(scan) if f["kind"] == "constant_ratio"]

    assert ratios, "the shared zero support must no longer void the pair"
    assert all(f["n_informative"] == sum(1 for c in counts if c) for f in ratios), \
        "the finding must say how many rows the ratio actually rests on"
    assert all("derived_or_unit_conversion" in f.get("false_positive_context", [])
               and f["severity"] == "low"
               and f["profile_action"] == "demoted"
               for f in ratios)


def test_review_profile_demotes_explicit_formula_relation(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    rows = ["sample,signal,signal x 100"]
    for i in range(8):
        signal = 1.25 + i * 0.37
        rows.append(f"s{i},{signal},{signal * 100}")
    (data / "formula.csv").write_text(_csv(rows), encoding="utf-8")

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    relations = [f for f in _all_block_findings(scan)
                 if f["kind"] in {"constant_ratio", "exact_linear"}]

    assert relations
    assert any("deterministic_relation_prefilter" in f.get("false_positive_context", [])
               and f.get("prefilter_reason") == "explicit_formula_or_unit_conversion"
               and f["severity"] == "low"
               and f["profile_action"] == "demoted"
               for f in relations)


def test_review_profile_keeps_independent_condition_transform(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    rows = ["sample,Control,Treatment"]
    for i, control in enumerate([1.25, 1.91, 2.44, 3.78, 5.12, 8.03, 13.7, 21.4]):
        rows.append(f"s{i},{control},{control * 1.337}")
    (data / "conditions.csv").write_text(_csv(rows), encoding="utf-8")

    scan = scan_dir(str(data), str(tmp_path / "out"), write_html=False)
    relations = [f for f in _all_block_findings(scan)
                 if f["kind"] in {"constant_ratio", "exact_linear"}]

    assert relations
    assert all(f["profile_action"] == "kept" for f in relations)
    assert all("deterministic_relation_prefilter" not in f.get("false_positive_context", [])
               for f in relations)


def test_triage_profile_hides_noisy_boundary_findings_from_html(tmp_path):
    scan = {
        "input_dir": "/tmp/x", "n_files": 1, "n_blocks_with_findings": 1,
        "relations_blocks": [{
            "file": "omics.csv", "sheet": "omics",
            "block": {"rows": "2-13", "cols": "1-3", "header": ["gene", "pvalue", "logFC"]},
            "relations": [], "progressions": [], "equal_pairs": [],
            "identical_after_rounding": [], "grim": [],
            "within_col": [{
                "kind": "within_col_value_duplication", "col": "pvalue",
                "col_idx": 1, "n": 12, "dup_value": 1.0, "dup_count": 9,
                "severity": "low", "rule": "col[1] has value 1.0 repeated 9/12 times",
                "profile_action": "hidden",
                "false_positive_context": ["censoring_or_boundary_value"],
                "likely_benign": "boundary value",
                "evidence": {"headers": ["pvalue"], "col_offset": 1,
                             "highlight_cols": [1], "highlight_rows": [],
                             "rows": [{"row_idx": 2, "is_context": False, "values": [1.0]}]},
            }],
        }],
        "digit_distribution": [], "decimal_endings": [], "cross_sheet_findings": [],
    }
    out = tmp_path / "r.html"
    write_html_report(scan, str(out))

    html = out.read_text(encoding="utf-8")
    assert 'data-profile-action="hidden"' in html
    assert 'style="display:none"' in html
    assert "show noisy" in html.lower()


def test_review_profile_marks_source_data_duplicate_replot():
    ga = {(r, c): round(1.2345 + r + c * 0.1, 4)
          for r in range(6) for c in range(2)}
    gb = dict(ga)
    findings = detect_collisions({
        ("source_data.xlsx", "Figure 2a source data"): ga,
        ("source_data.xlsx", "Figure 2b source data"): gb,
    })

    cf = findings[0]
    assert cf["delta"]["pattern"] == "perfect_dup"
    assert cf["severity"] == "low"
    assert cf["profile_action"] == "demoted"
    assert "same_data_replot_or_duplicate_upload" in cf["false_positive_context"]


def test_true_copy_then_tweak_survives_review_profile():
    values = [1.2345, 4.8912, 2.1177, 9.4501, 3.8765, 8.2234, 5.0099, 7.7312]
    ga = {}
    for r, v in enumerate(values):
        ga[(r, 0)] = v
        ga[(r, 1)] = round(v * 1.337 + (r % 3) * 0.071, 4)
    gb = dict(ga)
    gb[(0, 1)] = 99.1234
    gb[(7, 1)] = 88.1234
    findings = detect_collisions({
        ("a.xlsx", "Figure 2a"): ga,
        ("a.xlsx", "Figure 7b"): gb,
    })

    cf = findings[0]
    assert cf["delta"]["pattern"] == "value_tweaked"
    assert cf["severity"] == "high"
    assert cf["profile_action"] == "kept"


def test_cross_figure_perfect_duplicate_is_not_demoted_as_source_replot():
    ga = {(r, c): round(1.2345 + r + c * 0.1, 4)
          for r in range(6) for c in range(2)}
    gb = dict(ga)
    findings = detect_collisions({
        ("source_data.xlsx", "ExtFig 8c source data"): ga,
        ("source_data.xlsx", "ExtFig 10a source data"): gb,
    })

    cf = findings[0]
    assert cf["delta"]["pattern"] == "perfect_dup"
    assert cf["same_figure"] is False
    assert cf["severity"] == "high"
    assert cf["profile_action"] == "kept"
    assert "same_data_replot_or_duplicate_upload" not in cf["false_positive_context"]


def test_cross_file_perfect_duplicate_is_not_demoted_as_same_figure_replot():
    ga = {(r, c): round(1.2345 + r + c * 0.1, 4)
          for r in range(6) for c in range(2)}
    gb = dict(ga)
    findings = detect_collisions({
        ("main_source_data.xlsx", "Figure 2a source data"): ga,
        ("supplementary_table.xlsx", "Figure 2b source data"): gb,
    })

    cf = findings[0]
    assert cf["delta"]["pattern"] == "perfect_dup"
    assert cf["same_file"] is False
    assert cf["same_figure"] is False
    assert cf["severity"] == "high"
    assert cf["profile_action"] == "kept"
    assert "same_data_replot_or_duplicate_upload" not in cf["false_positive_context"]


def test_cli_accepts_profile_flag(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    (data / "t.csv").write_text("a,b\n1,1\n2,2\n3,3\n", encoding="utf-8")

    cmd = [sys.executable, "-m", "paperconan", str(data), "--profile", "triage",
           "--no-html", "--out", str(tmp_path / "out")]
    res = subprocess.run(cmd, cwd=os.getcwd(), text=True, capture_output=True)

    assert res.returncode == 0, res.stderr
    scan = json.loads((tmp_path / "out" / "scan.json").read_text())
    assert scan["profile"] == "triage"


def test_axis_regex_excludes_collision_prone_measurement_headers():
    # regression: _AXIS_RE was broadened with min/point/year/month/minute, which match real
    # measurement/summary headers ('Min' statistic, 'melting point', date columns). Those must
    # NOT be treated as axes, or a real column that is a perfect progression gets demoted.
    from paperconan._profiles import _AXIS_RE, _is_axis_finding
    for h in ("Min", "melting point", "set point", "Year", "minute", "month"):
        assert not _AXIS_RE.search(h), f"{h!r} must not read as an axis"
    for h in ("week 3", "day", "time (h)", "dose", "passage"):
        assert _AXIS_RE.search(h), f"{h!r} is a genuine axis word"
    # a 'Min' column that is a perfect non-integer progression (off-leftmost) stays HIGH
    assert _is_axis_finding({"kind": "arithmetic_progression", "col": "Min",
                             "step": 2.5, "col_idx": 3, "block_c0": 0}) is False
    # a genuine 'week' axis is still demoted regardless of position
    assert _is_axis_finding({"kind": "arithmetic_progression", "col": "week after treatment",
                             "step": 2.5, "col_idx": 3, "block_c0": 0}) is True
