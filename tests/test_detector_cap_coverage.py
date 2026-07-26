"""Detector-level caps have to reach the scan's coverage.

Several detectors stop at their own `max_findings` or compute budget. Until now
they reported that to stderr at best, and for some paths to nothing at all — so
`scan_status` stayed "complete" and every consumer downstream, including the
layered views and the Agent workflow, reported full coverage over a block whose
enumeration had been cut short.

For a tool used to decide whether a paper's numbers need author clarification,
a silently shortened search is the worst failure mode available: it looks
exactly like a clean result.
"""
from __future__ import annotations

import pytest

from paperconan import scan_dir
from paperconan._coverage import ScanCoverage


def _panel(path, rows=40, cols=14):
    """A block dense enough that the relation detectors have plenty to chew on."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ",".join(f"c{j}" for j in range(cols))
    lines = [header]
    for i in range(rows):
        vals = [round((i + 1) * (j + 1) * 1.017, 6) for j in range(cols)]
        if cols > 5:
            vals[5] = vals[2]
        if cols > 9:
            vals[9] = round(vals[3] * 1.13, 6)
        lines.append(",".join(str(v) for v in vals))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _reasons(scan):
    return [item.get("reason") for item in (scan.get("coverage") or {}).get("limitations") or []]


# ---------- the compute budgets ----------

def test_an_exhausted_row_relation_budget_reaches_coverage(tmp_path, monkeypatch):
    """Previously printed "coverage bounded" to stderr and nowhere else."""
    import paperconan._audit as audit
    monkeypatch.setattr(audit, "_ROW_REL_BUDGET", 1)

    _panel(tmp_path / "d" / "p.csv")
    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)

    assert "detector_compute_budget_limit" in _reasons(scan), _reasons(scan)
    assert scan["scan_status"] != "complete"
    assert scan["coverage"]["truncated"] is True


def test_a_block_too_tall_for_row_relations_is_recorded(tmp_path, monkeypatch):
    """This path returned [] with no notice on any channel at all."""
    import paperconan._audit as audit
    monkeypatch.setattr(audit, "_ROW_REL_MAX_ROWS", 3)

    _panel(tmp_path / "d" / "p.csv")
    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)

    assert "detector_block_rows_limit" in _reasons(scan), _reasons(scan)
    assert scan["scan_status"] != "complete"


# ---------- the per-detector result caps ----------

_CAPPED_DETECTORS = (
    "detect_recurring_row_vectors",
    "detect_scaled_row_reuse",
    "detect_short_row_reuse",
    "detect_within_row_shared_fraction",
    "detect_row_pair_shared_fraction",
)


def _two_sheets():
    from paperconan._sheet import Sheet

    grids = {}
    for sheet in ("a", "b"):
        rows = [[f"c{j}" for j in range(12)]]
        base = [round(0.1234567 + j * 0.0173219, 7) for j in range(12)]
        for i in range(24):
            if i % 4 == 0:
                rows.append(list(base))                          # exact repeat
            elif i % 4 == 1:
                rows.append([round(v * 1.13, 7) for v in base])   # constant ratio
            elif i % 4 == 2:
                rows.append([round(v + 100, 7) for v in base])    # shared tail
            else:
                rows.append([round(v * (i + 1) * 1.017, 7) for v in base])
        grids[(f"{sheet}.csv", sheet)] = Sheet.from_rows(rows)
    return grids


@pytest.mark.parametrize("name", _CAPPED_DETECTORS)
def test_each_capped_detector_reports_reaching_its_finding_limit(name):
    """Every capped detector is wired to the coverage object.

    Uses max_findings=0 so the limit is reached regardless of what the fixture
    happens to trigger. That verifies the wiring, not that a real corpus trips
    it — the end-to-end case is covered separately below, for the one detector a
    synthetic fixture can currently drive to its cap.
    """
    import paperconan._audit as audit

    coverage = ScanCoverage(files_discovered=1)

    getattr(audit, name)(_two_sheets(), profile="review", max_findings=0,
                         coverage=coverage)

    reasons = [item["reason"] for item in coverage.to_dict()["limitations"]]
    assert "detector_finding_limit" in reasons, (
        f"{name} reached its cap without recording it: {reasons}"
    )


def test_a_detector_that_actually_fills_its_cap_records_it():
    """The end-to-end half: real findings, a real cap, a real limitation."""
    import paperconan._audit as audit

    coverage = ScanCoverage(files_discovered=1)

    found = audit.detect_scaled_row_reuse(_two_sheets(), profile="review",
                                          max_findings=1, coverage=coverage)

    assert len(found) >= 1, "fixture no longer produces findings for this detector"
    reasons = [item["reason"] for item in coverage.to_dict()["limitations"]]
    assert "detector_finding_limit" in reasons


@pytest.mark.parametrize("name", _CAPPED_DETECTORS)
def test_a_detector_below_its_cap_records_nothing(name):
    """The notice has to mean something: no cap reached, no limitation."""
    import paperconan._audit as audit

    coverage = ScanCoverage(files_discovered=1)

    getattr(audit, name)(_two_sheets(), profile="review", max_findings=10**6,
                         coverage=coverage)

    assert not coverage.to_dict()["limitations"], (
        f"{name} reported a cap it never reached"
    )


def test_an_uncapped_scan_records_no_detector_limitation(tmp_path):
    """The notice must mean something: a normal scan must stay clean."""
    _panel(tmp_path / "d" / "p.csv", rows=12, cols=4)

    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)

    detector_reasons = [r for r in _reasons(scan)
                        if "detector" in (r or "") or "cap" in (r or "")]
    assert not detector_reasons, detector_reasons
    assert scan["scan_status"] == "complete"


# ---------- the workflow caveat can now be retired ----------

def test_the_workflow_no_longer_declares_caps_unreported():
    """With the caps wired through, the blanket caveat becomes false and the
    packet can report real completeness again."""
    from paperconan._workflow import DETECTOR_CAPS_REPORTED

    assert DETECTOR_CAPS_REPORTED is True


def test_a_scan_limitation_reads_as_a_sentence_not_a_python_repr():
    """These land in a terminal; a dict repr is not something to hand a reader."""
    from paperconan._workflow import _describe_scan_limitation

    text = _describe_scan_limitation({
        "scope": "detector", "reason": "detector_compute_budget_limit",
        "detector": "detect_row_relations", "rows": 60, "cols": 14,
    })

    assert "{" not in text and "'" not in text
    assert "detector compute budget limit" in text
    assert "detect_row_relations" in text
    assert "rows=60" in text


def test_a_clean_scan_now_reaches_complete_coverage(tmp_path):
    """The end of the chain: nothing dropped anywhere, so the view may say so."""
    from paperconan._drill import overview

    _panel(tmp_path / "d" / "p.csv", rows=12, cols=4)
    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)

    view = overview(scan)

    assert view["coverage"]["detector_caps_reported"] is True
    assert not any("detector-level caps" in x for x in view["coverage"]["limitations"])
