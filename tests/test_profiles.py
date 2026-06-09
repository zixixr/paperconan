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
        ("supplementary_table.xlsx", "Supplementary Table 1"): gb,
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
