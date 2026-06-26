from __future__ import annotations

from paperconan.packet import distill_findings_for_review


def test_distill_review_findings_preserves_cross_sheet_context():
    scan = {
        "cross_sheet_findings": [{
            "severity": "high",
            "delta": {"pattern": "value_tweaked"},
            "sheet_a": "Fig. 1",
            "sheet_b": "Fig. 2",
            "same_position_count": 8,
            "fraction_of_smaller": 0.5,
            "rule": "Fig. 1 and Fig. 2 share 8/16 values",
            "figure_a": "main:1",
            "figure_b": "main:2",
            "same_figure": False,
            "file_a": "a.xlsx",
            "file_b": "b.xlsx",
            "label_context_a": {"text": "control baseline"},
            "label_context_b": {"text": "vehicle control"},
            "shared_context": {
                "shared_control_or_baseline": True,
                "shared_axis_or_coordinate": False,
            },
        }],
        "relations_blocks": [],
    }

    findings = distill_findings_for_review(scan)

    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == "cross_sheet:value_tweaked"
    assert f["fraction_of_smaller"] == 0.5
    assert f["label_context_a"]["text"] == "control baseline"
    assert f["label_context_b"]["text"] == "vehicle control"
    assert f["shared_context"]["shared_control_or_baseline"] is True
    assert f["prefilter"] == "drop"
    assert f["prefilter_reason"] == "shared_control_or_baseline"


def test_distill_review_findings_includes_relation_samples_and_within_col():
    scan = {
        "cross_sheet_findings": [],
        "relations_blocks": [{
            "sheet": "Fig1",
            "file": "source.xlsx",
            "figure_label": "Fig. 1",
            "relations": [{
                "severity": "high",
                "kind": "constant_ratio",
                "col_a": "signal ng",
                "col_b": "signal ug",
                "n": 5,
                "rule": "col[2] = col[1] * 0.001",
                "col_a_sample": [1000.0, 2000.0, 3000.0, 4000.0, 5000.0],
                "col_b_sample": [1.0, 2.0, 3.0, 4.0, 5.0],
                "slope": 0.001,
                "intercept": 0,
            }],
            "equal_pairs": [],
            "within_col": [{
                "severity": "high",
                "kind": "within_col_value_duplication",
                "col": "score",
                "n": 12,
                "rule": "score has value repeated",
                "all_integer": False,
                "value_sample": [0.1234, 0.1234, 0.1234, 0.5, 0.7],
                "dup_value": 0.1234,
                "frac_repeat": 0.5,
                "n_distinct": 6,
            }],
        }],
    }

    findings = distill_findings_for_review(scan)

    assert [f["kind"] for f in findings] == ["constant_ratio", "within_col_value_duplication"]
    assert findings[0]["top5_a"] == [1000.0, 2000.0, 3000.0, 4000.0, 5000.0]
    assert findings[0]["sheet"] == "Fig1"
    assert findings[1]["within_col"] is True
    assert findings[1]["col_a"] == "score"
    assert findings[1]["prefilter"] == "keep"


def test_decimal_tail_reuse_keeps_identity_and_is_protected():
    # A long fractional-tail match is near-impossible by chance, so even a SMALL fraction /
    # small n must keep its identity and survive (not be relabeled to value_tweaked nor
    # downweighted as a benign partial overlap).
    scan = {
        "cross_sheet_findings": [{
            "severity": "high",
            "kind": "cross_sheet_decimal_tail_reuse",
            "delta": {"pattern": "value_tweaked"},
            "sheet_a": "Figure 5",
            "sheet_b": "sp Figure 6",
            "same_position_count": 8,
            "fraction_of_smaller": 0.1,
            "rule": "Figure 5 and sp Figure 6 share 8 cells with the same long fractional tail",
        }],
        "relations_blocks": [],
    }
    findings = distill_findings_for_review(scan)
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == "cross_sheet:decimal_tail_reuse"   # identity preserved
    assert f["prefilter"] == "keep"                        # protected, not downweighted
