"""End-to-end recall regression: a synthetic corpus reproducing every anomaly CLASS a
heavily-duplicated Nature paper exhibited must be (1) fully READ (incl. legacy .xls), (2) surface
each detector at HIGH, (3) demote genuine axis columns to low, and (4) flow the HIGH findings
through the packet distiller. Guards the exact gaps that let the real paper go unaudited."""
from __future__ import annotations

import collections

import paperconan._audit as audit
from paperconan._audit import scan_dir, BLOCK_FINDING_GROUPS
from paperconan.packet import distill_findings_for_review
from tests import build_nbs1_regression as B

_TARGETS = {
    "identical_column",
    "cross_sheet_column_duplicate",       # B1
    "integer_diff_shared_fraction",       # B5
    "partial_constant_offset",            # B4
    "within_table_fraction_reuse",        # B3
    "recurring_row_vector",               # B2
}


def _scan(tmp_path):
    B.build(str(tmp_path))
    return scan_dir(str(tmp_path), str(tmp_path / "out"), write_md=False, write_html=False)


def _high_kinds(scan):
    k = collections.Counter()
    for b in scan.get("relations_blocks", []):
        for g in BLOCK_FINDING_GROUPS:
            for f in b.get(g, []):
                if f.get("severity") == "high":
                    k[f.get("kind")] += 1
    for f in scan.get("cross_sheet_findings", []):
        if f.get("severity") == "high":
            k[f.get("kind")] += 1
    return k


def test_corpus_is_fully_read_including_xls(tmp_path):
    scan = _scan(tmp_path)
    real_errors = [e for e in scan["scan_errors"] if "oversized" not in e.get("error", "")]
    assert not real_errors, real_errors
    files = {f["file"] for f in scan["scan_stats"]["files"] if not f.get("error")}
    assert any(f.endswith(".xls") for f in files), f"the legacy .xls must be read: {files}"


def test_all_anomaly_classes_surface_at_high(tmp_path):
    kinds = _high_kinds(_scan(tmp_path))
    missing = _TARGETS - set(kinds)
    assert not missing, f"missing detector(s): {missing}; got {dict(kinds)}"


def test_week_axis_progression_is_demoted_to_low(tmp_path):
    scan = _scan(tmp_path)
    highs = [f for b in scan.get("relations_blocks", [])
             for f in b.get("progressions", [])
             if f.get("severity") == "high"]
    # no HIGH arithmetic progression should be a leftmost/'week' axis column
    for f in highs:
        assert not (f.get("col_idx") == f.get("block_c0")), f"leftmost axis progression left HIGH: {f}"
        assert "week" not in str(f.get("col", "")).lower()


def test_distill_surfaces_new_high_kinds_but_not_axis(tmp_path):
    scan = _scan(tmp_path)
    dist = distill_findings_for_review(scan)
    surfaced = {f.get("kind") for f in dist if f.get("prefilter") != "drop"}
    # cross-sheet classes are renamed to cross_sheet:<pattern> by the distiller; relation
    # classes keep their kind. Both must reach the packet.
    for k in ("cross_sheet:column_duplicate", "cross_sheet:fraction_reuse",
              "cross_sheet:recurring_row_vector", "integer_diff_shared_fraction",
              "partial_constant_offset"):
        assert k in surfaced, f"{k} did not reach the packet distiller; got {surfaced}"
    # an axis progression must NEVER be surfaced as a packet finding
    assert "arithmetic_progression" not in {
        f.get("kind") for f in dist
        if f.get("prefilter") != "drop" and f.get("rule", "").find("week") >= 0
    }


def test_file_scoped_pipeline_preserves_detection_recall(
    tmp_path, monkeypatch
):
    calls = []
    original = audit._process_file

    def tracked(path, *, input_dir, state):
        result = original(path, input_dir=input_dir, state=state)
        calls.append((path, result))
        return result

    monkeypatch.setattr(audit, "_process_file", tracked)

    scan = _scan(tmp_path)

    assert len(calls) == scan["n_files"]
    kinds = _high_kinds(scan)
    assert _TARGETS <= set(kinds)
