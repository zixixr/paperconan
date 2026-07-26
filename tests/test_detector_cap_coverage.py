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

    Uses max_findings=1 against a fixture rich enough that several of these
    detectors produce more than one finding. Detectors the fixture cannot drive
    past one are skipped rather than asserted vacuously — a detector that never
    reaches its cap correctly records nothing.
    """
    import paperconan._audit as audit

    coverage = ScanCoverage(files_discovered=1)

    natural = len(getattr(audit, name)(_two_sheets(), profile="review",
                                       max_findings=10**6))
    if natural < 2:
        pytest.skip(f"{name} yields {natural} finding(s) on this fixture; "
                    "cannot drive it past its cap")

    getattr(audit, name)(_two_sheets(), profile="review", max_findings=1,
                         coverage=coverage)

    reasons = [item["reason"] for item in coverage.to_dict()["limitations"]]
    assert "detector_finding_limit" in reasons, (
        f"{name} was cut short without recording it: {reasons}"
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

def test_the_unreported_cap_caveat_stays_until_every_cap_is_wired():
    """Result caps are wired; many resource caps are not.

    An audit of the detector layer found 17 that still report nowhere —
    _MAX_BLOCK_COLS drops detect_relations entirely on a wide block while the
    scan calls itself complete. Flipping this flag on partial wiring replaced an
    over-broad but true caveat with a false all-clear, which is strictly worse
    for a tool whose job is to not miss things.
    """
    from paperconan._workflow import DETECTOR_CAPS_REPORTED

    assert DETECTOR_CAPS_REPORTED is False


def test_a_block_wider_than_the_column_cap_is_still_an_unreported_gap():
    """Pins the gap the caveat exists for, so it cannot be quietly forgotten.

    A planted identical column is found at 110 columns and lost at 130, and the
    scan reports complete either way. When this starts failing, the cap has been
    wired and DETECTOR_CAPS_REPORTED can be revisited.
    """
    import tempfile
    from pathlib import Path

    from paperconan import BLOCK_FINDING_GROUPS

    def kinds_at(width):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "d"
            d.mkdir()
            header = ",".join(f"c{j}" for j in range(width))
            lines = [header]
            for i in range(15):
                vals = [round((i + 1) * (j + 1) * 1.017, 6) for j in range(width)]
                vals[3] = vals[1]          # an exactly duplicated column
                lines.append(",".join(str(v) for v in vals))
            (d / "p.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
            scan = scan_dir(str(d), str(Path(td) / "out"), write_html=False)
        found = {f.get("kind") for b in scan["relations_blocks"]
                 for g in BLOCK_FINDING_GROUPS for f in (b.get(g) or [])}
        return found, scan["scan_status"]

    narrow_kinds, narrow_status = kinds_at(110)
    wide_kinds, wide_status = kinds_at(130)

    assert "identical_column" in narrow_kinds, "fixture no longer plants a signal"
    assert "identical_column" not in wide_kinds, (
        "the wide-block cap appears to have been wired — revisit "
        "DETECTOR_CAPS_REPORTED and this test"
    )
    assert narrow_status == "complete"
    assert wide_status == "complete", (
        "the wide-block skip now reaches coverage; update DETECTOR_CAPS_REPORTED"
    )


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


def test_an_ordinary_table_is_not_reported_as_truncated(tmp_path):
    """The commonest supplementary shape must not read as a partial scan.

    A branch added and then withdrawn here fired on 61x14 purely for being tall,
    which would have made "partial" the near-universal state and taught readers
    to skip the coverage line entirely.
    """
    import random
    random.seed(3)
    d = tmp_path / "d"
    d.mkdir(parents=True, exist_ok=True)
    rows = [",".join(f"c{j}" for j in range(14))]
    for _ in range(200):
        rows.append(",".join(str(round(random.uniform(1, 999), 4)) for _ in range(14)))
    (d / "p.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    scan = scan_dir(str(d), str(tmp_path / "out"), write_html=False)

    assert scan["scan_status"] == "complete", _reasons(scan)
    assert scan["coverage"]["truncated"] is False


def test_a_dense_table_that_really_fills_a_cap_is_reported(tmp_path):
    """The other side of the same coin: when a cap genuinely bites, say so."""
    _panel(tmp_path / "d" / "p.csv", rows=200, cols=14)

    scan = scan_dir(str(tmp_path / "d"), str(tmp_path / "out"), write_html=False)

    assert "detector_finding_limit" in _reasons(scan), _reasons(scan)
    assert scan["scan_status"] == "partial"


def test_a_result_cap_equal_to_the_natural_output_still_reports(tmp_path):
    """Documents a known boundary rather than leaving it to be rediscovered.

    Reaching the cap on the final iteration is indistinguishable, at the break
    site, from being cut short. It errs toward "there may be more", which is the
    safe direction here — a false all-clear is the failure this tool cannot
    afford. Narrowing it needs per-break knowledge of what remained unexamined.
    """
    import paperconan._audit as audit

    natural = len(audit.detect_scaled_row_reuse(_two_sheets(), profile="review",
                                                max_findings=10**6))
    coverage = ScanCoverage(files_discovered=1)

    audit.detect_scaled_row_reuse(_two_sheets(), profile="review",
                                  max_findings=natural, coverage=coverage)

    reasons = [i["reason"] for i in coverage.to_dict()["limitations"]]
    assert "detector_finding_limit" in reasons, (
        "if this stops firing the boundary was narrowed — update the docstring "
        "on _note_detector_cap"
    )
